"""Canale pubblico degli affari: ogni giorno il bot pubblica i 3 annunci migliori trovati nelle ultime 24 ore.

Il proprietario crea un canale Telegram, aggiunge il bot come amministratore e scrive `/canale @nomecanale`.
Ogni post finisce con un pulsante che porta al bot (deep link `?start=canale`, così si sa chi arriva da lì).
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import time
from zoneinfo import ZoneInfo

from . import config
from . import stats as pstats
from .cards import CardIndex
from .db import Database
from .notifier import SOURCE_LABELS
from .scrapers.base import parse_price

log = logging.getLogger(__name__)

DEFAULT_HOUR = 19  # ora locale del post giornaliero
MAX_RATIO = 0.75   # un annuncio è "affare" se costa al più il 75% della mediana storica della carta
MIN_RATIO = 0.25   # sotto il 25% è quasi sempre un errore di riconoscimento


def pick_deals(db: Database, index: CardIndex, now: float | None = None, hours: int = 24, limit: int = 3) -> list[dict]:
    """I `limit` annunci singoli delle ultime `hours` ore con il prezzo più basso rispetto alla mediana (una carta per voce)."""
    now = now or time.time()
    rows = db.list_found()
    out: list[dict] = []
    seen_cards: set[str] = set()
    cands = []
    for r in rows:
        if r.get("kind") != "single" or float(r.get("created_at") or 0) < now - hours * 3600:
            continue
        sure = [m for m in r.get("matched", []) if m.get("sure", True) and m.get("id")]
        if len(sure) != 1:
            continue
        cid = sure[0]["id"]
        price = parse_price(r.get("price"))
        if price is None or price < 1:
            continue
        median = pstats.median_for_deal(rows, cid, exclude_keys={r.get("listing_key")})
        if not median:
            continue
        ratio = price / median
        if MIN_RATIO <= ratio <= MAX_RATIO:
            cands.append((ratio, r, cid, price, median))
    cands.sort(key=lambda t: t[0])
    for ratio, r, cid, price, median in cands:
        if cid in seen_cards:
            continue
        seen_cards.add(cid)
        card = index.by_id.get(cid)
        out.append({"card": card.label if card else cid, "image": (card.image if card else "") or r.get("image") or "",
                    "price": price, "median": median, "ratio": ratio, "source": r.get("source", ""),
                    "url": r.get("url", ""), "title": r.get("title", "")})
        if len(out) >= limit:
            break
    return out


def format_post(deals: list[dict], bot_username: str, day: dt.date | None = None) -> tuple[str, list[list[tuple[str, str]]]]:
    """Testo HTML del post e pulsante che porta al bot."""
    day = day or dt.datetime.now(ZoneInfo(config.TIMEZONE)).date()
    esc = html.escape
    lines = [f"🔥 <b>Affari Pokémon del {day.strftime('%d/%m')}</b>", ""]
    for i, d in enumerate(deals, 1):
        pct = 100.0 * d["price"] / d["median"] if d["median"] else 0
        lines.append(f"{i}. <b>{esc(d['card'])}</b> · <b>{d['price']:.2f} €</b> "
                     f"({pct:.0f}% del prezzo medio visto, {d['median']:.0f} €) · {esc(SOURCE_LABELS.get(d['source'], d['source']))}\n"
                     f'   <a href="{esc(d["url"], quote=True)}">{esc(d["title"][:70])}</a>')
    lines.append("")
    lines.append("Trovati da Pokébot, che cerca su Wallapop, Vinted ed eBay le carte che mancano alla tua collezione "
                 "e ti avvisa appena spuntano. Gratis, su invito.")
    url = f"https://t.me/{bot_username}?start=canale" if bot_username else ""
    buttons = [[("🤖 Attiva Pokébot", url)]] if url else []
    return "\n".join(lines), buttons


def due(db: Database, now: float | None = None) -> bool:
    """True se il canale è impostato, è passata l'ora del post e oggi non è ancora stato pubblicato."""
    if not db.get_kv("deals_channel"):
        return False
    local = dt.datetime.fromtimestamp(now or time.time(), ZoneInfo(config.TIMEZONE))
    hour = int(db.get_kv("deals_channel_hour", DEFAULT_HOUR) or DEFAULT_HOUR)
    return local.hour >= hour and db.get_kv("deals_channel_last") != local.date().isoformat()


def post_daily(db: Database, index: CardIndex, client, now: float | None = None, force: bool = False) -> str:
    """Pubblica il post del giorno se dovuto. Restituisce una riga di log."""
    now = now or time.time()
    if not force and not due(db, now):
        return ""
    channel = str(db.get_kv("deals_channel") or "")
    if not channel:
        return "canale non impostato"
    today = dt.datetime.fromtimestamp(now, ZoneInfo(config.TIMEZONE)).date()
    deals = pick_deals(db, index, now)
    if not deals:
        if not force:
            db.set_kv("deals_channel_last", today.isoformat())  # niente da dire oggi: non si riprova a ogni giro
        return "nessun affare nelle ultime 24 ore: niente post"
    username = db.get_kv("bot_username") or ""
    if not username:
        try:
            username = client.get_me()
            db.set_kv("bot_username", username)
        except Exception as exc:  # noqa: BLE001 - il post si fa lo stesso, senza pulsante
            log.warning("getMe: %s", exc)
    text, buttons = format_post(deals, username, today)
    client.send(channel, text, buttons)
    db.set_kv("deals_channel_last", today.isoformat())
    return f"post pubblicato su {channel}: {len(deals)} affari"
