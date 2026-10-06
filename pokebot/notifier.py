"""Invio delle notifiche su Telegram (Bot API, senza librerie esterne)."""
from __future__ import annotations

import html
import logging

import requests

from . import config
from .matcher import MatchResult
from .scrapers.base import Listing

log = logging.getLogger(__name__)

SOURCE_LABELS = {"wallapop": "Wallapop", "vinted": "Vinted", "ebay": "eBay.it"}


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None, chat_ids: list[str] | None = None):
        self.token = (token or config.TELEGRAM_BOT_TOKEN).strip()
        ids = [str(c).strip() for c in (chat_ids or []) if str(c).strip()]
        first = (chat_id or config.TELEGRAM_CHAT_ID).strip()
        if first and first not in ids:
            ids.insert(0, first)
        self.chat_ids = ids

    @classmethod
    def from_db(cls, db) -> "TelegramNotifier":
        """Token dall'ambiente; destinatari: proprietario (dall'ambiente o dal primo /start) più gli invitati."""
        return cls(chat_ids=db.chat_ids())

    @property
    def chat_id(self) -> str:
        return self.chat_ids[0] if self.chat_ids else ""

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_ids)

    def _post(self, method: str, payload: dict, files: dict | None = None, timeout_mult: float = 1.0,
              log_level: int = logging.ERROR) -> bool:
        """Invia lo stesso messaggio a ogni chat autorizzata; True se almeno una consegna è riuscita."""
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        ok_any = False
        for cid in self.chat_ids:
            body = dict(payload, chat_id=cid)
            try:
                if files:
                    resp = requests.post(url, data=body, files=files, timeout=config.HTTP_TIMEOUT * timeout_mult)
                else:
                    resp = requests.post(url, json=body, timeout=config.HTTP_TIMEOUT * timeout_mult)
            except requests.RequestException as exc:
                log.log(log_level, "Telegram %s (chat %s): errore di rete: %s", method, cid, exc)
                continue
            if resp.status_code != 200:
                log.log(log_level, "Telegram %s (chat %s): HTTP %s %s", method, cid, resp.status_code, resp.text[:200])
                continue
            ok_any = True
        return ok_any

    def send(self, text: str, disable_preview: bool = False) -> bool:
        if not self.configured:
            log.warning("Telegram non configurato: imposta TELEGRAM_BOT_TOKEN e scrivi /start al bot")
            return False
        return self._post("sendMessage", {"text": text, "parse_mode": "HTML", "disable_web_page_preview": disable_preview})

    def notify_listing(self, listing: Listing, result: MatchResult) -> bool:
        return self.send(format_listing(listing, result))

    def send_photo(self, photo_url: str, caption: str) -> bool:
        """Foto con didascalia (max 1024 caratteri); False se Telegram rifiuta (si ripiega sul testo)."""
        if not self.configured or not photo_url:
            return False
        return self._post("sendPhoto", {"photo": photo_url, "caption": caption[:1024], "parse_mode": "HTML"},
                          log_level=logging.INFO)

    def send_photo_bytes(self, png: bytes, caption: str) -> bool:
        """Foto caricata direttamente (collage) con didascalia."""
        if not self.configured or not png:
            return False
        return self._post("sendPhoto", {"caption": caption[:1024], "parse_mode": "HTML"},
                          files={"photo": ("annunci.png", png, "image/png")}, timeout_mult=2, log_level=logging.INFO)

    def send_album(self, photo_urls: list[str], caption: str) -> bool:
        """Album (2-10 foto) con didascalia sulla prima; False se Telegram rifiuta."""
        if not self.configured or len(photo_urls) < 2:
            return False
        media = [{"type": "photo", "media": u} for u in photo_urls[:10]]
        media[0]["caption"] = caption[:1024]
        media[0]["parse_mode"] = "HTML"
        return self._post("sendMediaGroup", {"media": media}, timeout_mult=2, log_level=logging.INFO)

    _collage_rows: list[dict] | None = None
    _collage_title: str = ""

    def send_group_with_photos(self, text: str, listing_photos: list[str], card_image: str | None, images: bool) -> bool:
        """Gruppo di annunci: album con le foto dei venditori (nell'ordine dell'elenco) + testo.

        Con una sola foto usa sendPhoto; se le foto mancano o Telegram le rifiuta, ripiega sull'immagine della carta.
        """
        photos = [u for u in listing_photos if u]
        if images and photos and self._collage_rows is not None:
            # modalità "collage": un'unica immagine con la miniatura accanto a ogni riga
            try:
                from .collage import build_collage
                png = build_collage(self._collage_title, self._collage_rows)
            except Exception as exc:  # noqa: BLE001
                log.warning("Collage non generato: %s", exc)
                png = None
            if png:
                if len(text) <= 1024 and self.send_photo_bytes(png, text):
                    return True
                if len(text) > 1024 and self.send_photo_bytes(png, text.split("\n", 1)[0]):
                    return self.send(text, disable_preview=True)
        if images and photos:
            if len(photos) >= 2:
                if len(text) <= 1024 and self.send_album(photos, text):
                    return True
                if len(text) > 1024 and self.send_album(photos, text.split("\n", 1)[0]):
                    return self.send(text, disable_preview=True)
            elif self.send_photo(photos[0], text if len(text) <= 1024 else text.split("\n", 1)[0]):
                return True if len(text) <= 1024 else self.send(text, disable_preview=True)
        return self.send_with_image(text, card_image, images)

    def send_with_image(self, text: str, image_url: str | None, images: bool) -> bool:
        """Testo con immagine della carta se abilitata; se il testo è lungo, prima la foto poi il testo."""
        if images and image_url:
            if len(text) <= 1024 and self.send_photo(image_url, text):
                return True
            if len(text) > 1024 and self.send_photo(image_url, text.split("\n", 1)[0]):
                return self.send(text, disable_preview=True)
        return self.send(text, disable_preview=True)

    def notify_many(self, items: list[tuple[Listing, MatchResult]], max_per_card: int = 5, images: bool = True) -> list[bool]:
        """Un messaggio per carta con i `max_per_card` annunci più economici del ciclo; i lotti a parte.

        Restituisce, per ogni elemento di `items`, True se è stato inviato.
        """
        groups = group_matches(items)
        sent_keys: set[str] = set()
        for key, title, members in groups:
            chosen = members[: max(1, max_per_card)]
            text = format_group(title, chosen, len(members))
            image = None
            if key != "lot" and not key.startswith("maybe:"):
                card = chosen[0][1].wanted[0] if chosen[0][1].wanted else None
                image = card.image if card else None
            # miniature dei venditori accanto a ogni riga (collage); in mancanza, album o immagine della carta
            listing_photos = [lst.image for lst, _ in chosen]
            self._collage_title = title.replace("🃏 ", "").replace("📦 ", "").replace("❔ ", "")
            self._collage_rows = [{"image": lst.image, "price": lst.price_text or (f"{lst.price:.2f} €" if lst.price is not None else ""),
                                   "source": lst.source, "title": lst.title, "location": lst.location,
                                   "lot": res.kind == "lot"} for lst, res in chosen]
            ok = any(listing_photos) and self.send_group_with_photos(text, listing_photos, image, images)
            self._collage_rows = None
            if ok or self.send_with_image(text, image, images):
                sent_keys.update(lst.key for lst, _ in chosen)
        return [lst.key in sent_keys for lst, _ in items]

    def notify_deal(self, listing: Listing, result: MatchResult, median: float, images: bool = True) -> bool:
        """Avviso immediato 🔥 per un prezzo molto sotto la mediana storica."""
        card = result.wanted[0]
        pct = 100.0 * (listing.price or 0) / median if median else 0
        esc = html.escape
        text = (f"🔥 <b>AFFARE</b> · {esc(card.label)}\n"
                f"<b>{esc(listing.price_text or f'{listing.price:.2f} €')}</b> · {esc(SOURCE_LABELS.get(listing.source, listing.source))}"
                f" · {pct:.0f}% della mediana ({median:.2f} €)\n"
                f'<a href="{esc(listing.url, quote=True)}">{esc(listing.title[:80])}</a>')
        # foto dell'annuncio (com'è messa la carta in vendita); se manca, l'immagine ufficiale della carta
        return self.send_with_image(text, listing.image or card.image, images)

    def test_message(self) -> bool:
        return self.send("✅ PokéBot 30th collegato: riceverai qui gli annunci delle carte mancanti.", True)


def _price_key(listing: Listing) -> float:
    return listing.price if listing.price is not None else float("inf")


def group_matches(items: list[tuple[Listing, MatchResult]]) -> list[tuple[str, str, list[tuple[Listing, MatchResult]]]]:
    """Raggruppa per carta (singole) o in "Lotti"; ogni gruppo ordinato dal più economico."""
    groups: dict[str, tuple[str, list]] = {}
    for lst, res in items:
        if res.kind == "lot":
            key, title = "lot", "📦 Lotti con carte mancanti"
        elif res.wanted:
            key, title = res.wanted[0].id, f"🃏 {res.wanted[0].label}"
        else:  # nome senza numero: possibile una delle versioni mancanti
            name = res.possible_wanted[0].name if res.possible_wanted else lst.title[:40]
            key, title = f"maybe:{name}", f"❔ {name} (numero non indicato, da verificare)"
        groups.setdefault(key, (title, []))[1].append((lst, res))
    out = []
    for key, (title, members) in groups.items():
        members.sort(key=lambda pair: _price_key(pair[0]))
        out.append((key, title, members))
    # prima le carte singole (per numero), poi i lotti
    out.sort(key=lambda g: (g[0] == "lot", g[1]))
    return out


def format_group(title: str, chosen: list[tuple[Listing, MatchResult]], total: int) -> str:
    esc = html.escape
    sub = f"{len(chosen)} più economici su {total} trovati oggi" if total > len(chosen) else f"{total} trovat{'o' if total == 1 else 'i'} oggi"
    lines = [f"<b>{esc(title)}</b> · {sub}"]
    for i, (lst, res) in enumerate(chosen, start=1):
        src = SOURCE_LABELS.get(lst.source, lst.source)
        price = esc(lst.price_text or (f"{lst.price:.2f} €" if lst.price is not None else "prezzo n.d."))
        extra = ""
        if res.kind == "lot":
            cards = ", ".join(c.label for c in res.wanted[:3]) + (", …" if len(res.wanted) > 3 else "")
            extra = f"\n    ✅ {esc(cards)} ({res.wanted_count}/{res.total_cards})"
        loc = f" · {esc(lst.location)}" if lst.location else ""
        lines.append(f'{i}. <b>{price}</b> · {esc(src)}{loc}\n    <a href="{esc(lst.url, quote=True)}">{esc(lst.title[:70])}</a>{extra}')
    return "\n".join(lines)


def format_listing_compact(listing: Listing, result: MatchResult) -> str:
    esc = html.escape
    src = SOURCE_LABELS.get(listing.source, listing.source)
    kind = "📦" if result.kind == "lot" else "🃏"
    cards = ", ".join(c.label for c in (result.wanted or result.possible_wanted)[:3])
    if len(result.wanted or result.possible_wanted) > 3:
        cards += ", …"
    if result.kind == "lot" and result.total_cards:
        cards += f" ({result.wanted_count}/{result.total_cards})"
    price = esc(listing.price_text or (f"{listing.price:.2f} €" if listing.price is not None else "prezzo n.d."))
    verify = " ⚠️" if (result.possible_wanted and not result.wanted) else ""
    return (f'{kind} <a href="{esc(listing.url, quote=True)}">{esc(listing.title[:70])}</a>\n'
            f'   💶 {price} · {esc(src)}{verify}\n   ✅ {esc(cards)}')


def format_listing(listing: Listing, result: MatchResult) -> str:
    esc = html.escape
    src = SOURCE_LABELS.get(listing.source, listing.source)
    if result.kind == "lot":
        head = f"📦 <b>LOTTO</b> · {result.wanted_count}/{result.total_cards} carte mancanti"
        if result.ratio is not None:
            head += f" ({result.ratio:.0%})"
    else:
        head = "🃏 <b>CARTA SINGOLA</b>"
        if result.possible_wanted and not result.wanted:
            head += " · ⚠️ numero non indicato, da verificare"
    lines = [head, f"<b>{esc(listing.title)}</b>"]
    meta = []
    if listing.price_text or listing.price is not None:
        meta.append(f"💶 {esc(listing.price_text or f'{listing.price:.2f} €')}")
    meta.append(f"🛒 {esc(src)}")
    if listing.location:
        meta.append(f"📍 {esc(listing.location)}")
    lines.append(" · ".join(meta))
    cards = [c.label for c in result.wanted]
    poss = [c.label for c in result.possible_wanted]
    if cards:
        shown = cards[:12]
        more = f" (+{len(cards) - len(shown)} altre)" if len(cards) > len(shown) else ""
        lines.append("✅ " + esc(", ".join(shown)) + more)
    if poss:
        shown = poss[:8]
        more = f" (+{len(poss) - len(shown)})" if len(poss) > len(shown) else ""
        lines.append("❔ Potrebbe essere: " + esc(", ".join(shown)) + more)
    lines.append(f'🔗 <a href="{esc(listing.url, quote=True)}">Apri annuncio</a>')
    return "\n".join(lines)
