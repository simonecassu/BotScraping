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
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = (token or config.TELEGRAM_BOT_TOKEN).strip()
        self.chat_id = (chat_id or config.TELEGRAM_CHAT_ID).strip()

    @classmethod
    def from_db(cls, db) -> "TelegramNotifier":
        """Token dall'ambiente; chat id dall'ambiente oppure quello salvato dal comando /start."""
        chat_id = config.TELEGRAM_CHAT_ID or str(db.get_kv("telegram_chat_id", "") or "")
        return cls(chat_id=chat_id)

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, disable_preview: bool = False) -> bool:
        if not self.configured:
            log.warning("Telegram non configurato: imposta TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID")
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }
        try:
            resp = requests.post(url, json=payload, timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as exc:
            log.error("Telegram: errore di rete: %s", exc)
            return False
        if resp.status_code != 200:
            log.error("Telegram: HTTP %s %s", resp.status_code, resp.text[:300])
            return False
        return True

    def notify_listing(self, listing: Listing, result: MatchResult) -> bool:
        return self.send(format_listing(listing, result))

    def send_photo(self, photo_url: str, caption: str) -> bool:
        """Foto con didascalia (max 1024 caratteri); False se Telegram rifiuta (si ripiega sul testo)."""
        if not self.configured or not photo_url:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        payload = {"chat_id": self.chat_id, "photo": photo_url, "caption": caption[:1024], "parse_mode": "HTML"}
        try:
            resp = requests.post(url, json=payload, timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as exc:
            log.warning("Telegram sendPhoto: %s", exc)
            return False
        if resp.status_code != 200:
            log.info("Telegram sendPhoto rifiutata (%s), invio come testo", resp.status_code)
            return False
        return True

    def send_album(self, photo_urls: list[str], caption: str) -> bool:
        """Album (2-10 foto) con didascalia sulla prima; False se Telegram rifiuta."""
        if not self.configured or len(photo_urls) < 2:
            return False
        media = [{"type": "photo", "media": u} for u in photo_urls[:10]]
        media[0]["caption"] = caption[:1024]
        media[0]["parse_mode"] = "HTML"
        url = f"https://api.telegram.org/bot{self.token}/sendMediaGroup"
        try:
            resp = requests.post(url, json={"chat_id": self.chat_id, "media": media}, timeout=config.HTTP_TIMEOUT * 2)
        except requests.RequestException as exc:
            log.warning("Telegram sendMediaGroup: %s", exc)
            return False
        if resp.status_code != 200:
            log.info("Telegram sendMediaGroup rifiutato (%s): %s", resp.status_code, resp.text[:120])
            return False
        return True

    def send_group_with_photos(self, text: str, listing_photos: list[str], card_image: str | None, images: bool) -> bool:
        """Gruppo di annunci: album con le foto dei venditori (nell'ordine dell'elenco) + testo.

        Con una sola foto usa sendPhoto; se le foto mancano o Telegram le rifiuta, ripiega sull'immagine della carta.
        """
        photos = [u for u in listing_photos if u]
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
            # foto dei venditori nello stesso ordine dell'elenco: la foto n corrisponde all'annuncio n
            listing_photos = [lst.image for lst, _ in chosen]
            if all(listing_photos) and self.send_group_with_photos(text, listing_photos, image, images):
                sent_keys.update(lst.key for lst, _ in chosen)
            elif self.send_with_image(text, image, images):
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
    if len(chosen) >= 2 and all(lst.image for lst, _ in chosen):
        lines.append("<i>Le foto qui sopra seguono l'ordine dell'elenco</i>")
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
