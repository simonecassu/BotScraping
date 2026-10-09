"""Canale pubblico degli affari: ogni giorno il bot pubblica le 3 migliori occasioni (prezzi confrontati con Cardmarket).

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
from . import cardmarket
from .cards import CardIndex
from .db import Database
from .notifier import SOURCE_LABELS
from .scrapers.base import parse_price

log = logging.getLogger(__name__)

DEFAULT_HOUR = 19  # ora locale del post giornaliero


def pick_deals(db: Database, index: CardIndex, now: float | None = None, hours: int = 24, limit: int = 3) -> list[dict]:
    """Le `limit` migliori occasioni, una per carta, giudicate solo sui prezzi Cardmarket.

    Prima gli affari veri (🔥), poi gli ottimi prezzi (💰), quelli sotto il valore (👍) e infine quelli vicini al valore
    (📌, fino al 30% sopra): così il post del giorno esce sempre, ma ogni voce dice onestamente di che tipo è.
    Le ultime 24 ore hanno la precedenza; i 3 giorni prima servono solo a riempire i posti rimasti.
    """
    now = now or time.time()
    rows = db.list_found()
    cm = cardmarket.all_prices(db)
    rank = {"deal": 0, "great": 1, "good": 2, "fair": 3}
    cands = []
    for r in rows:
        age = now - float(r.get("created_at") or 0)
        if r.get("kind") != "single" or age > 72 * 3600:
            continue
        sure = [m for m in r.get("matched", []) if m.get("sure", True) and m.get("id")]
        if len(sure) != 1:
            continue
        cid = sure[0]["id"]
        price = parse_price(r.get("price"))
        p = cm.get(cid)
        v, ratio = cardmarket.verdict(price, p, fair=True)
        if v:
            cands.append((age > hours * 3600, rank[v], ratio, r, cid, price, p, v))
    # prima le ultime 24 ore, migliori per categoria; i giorni prima servono solo a riempire i posti rimasti
    cands.sort(key=lambda t: (t[0], t[1], t[2]))
    out: list[dict] = []
    seen: set[str] = set()
    for _, _, ratio, r, cid, price, p, v in cands:
        if cid in seen:
            continue
        seen.add(cid)
        card = index.by_id.get(cid)
        out.append({"card": card.label if card else cid, "image": (card.image if card else "") or r.get("image") or "",
                    "price": price, "trend": p.ref, "low": p.low, "ratio": ratio, "verdict": v,
                    "source": r.get("source", ""), "url": r.get("url", ""), "title": r.get("title", "")})
        if len(out) >= limit:
            break
    return out


def format_post(deals: list[dict], bot_username: str, day: dt.date | None = None) -> tuple[str, list[list[tuple[str, str]]]]:
    """Testo HTML del post e pulsante che porta al bot."""
    day = day or dt.datetime.now(ZoneInfo(config.TIMEZONE)).date()
    esc = html.escape
    all_deals = all(d["verdict"] == "deal" for d in deals)
    title = "Affari Pokémon del" if all_deals else "Le migliori occasioni Pokémon del"
    lines = [f"🔥 <b>{title} {day.strftime('%d/%m')}</b>", ""]
    for d in deals:
        pct = round(100.0 * d["ratio"])
        low = f", minimo {d['low']:.2f} €" if d.get("low") else ""
        lines.append(f"{cardmarket.VERDICT_LABEL[d['verdict']]} · <b>{esc(d['card'])}</b>\n"
                     f"<b>{d['price']:.2f} €</b> su {esc(SOURCE_LABELS.get(d['source'], d['source']))} · "
                     f"{pct}% del valore Cardmarket (trend {d['trend']:.2f} €{low})\n"
                     f'<a href="{esc(d["url"], quote=True)}">{esc(d["title"][:70])}</a>\n')
    lines.append("Prezzi confrontati con Cardmarket. Trovati da Pokébot, che cerca su Wallapop, Vinted ed eBay le carte "
                 "che mancano alla tua collezione e ti avvisa appena spuntano. 5 giorni di prova con tutto, poi gratis in versione Light.")
    url = f"https://t.me/{bot_username}?start=canale" if bot_username else ""
    buttons = [[("🤖 Attiva Pokébot", url)]] if url else []
    return "\n".join(lines), buttons


def post_hour(db: Database) -> int:
    """Ora del post giornaliero (anche 0 = mezzanotte è valida)."""
    h = db.get_kv("deals_channel_hour")
    return DEFAULT_HOUR if h is None else int(h)


def due(db: Database, now: float | None = None) -> bool:
    """True se il canale è impostato, è passata l'ora del post e oggi non è ancora stato pubblicato."""
    if not db.get_kv("deals_channel"):
        return False
    local = dt.datetime.fromtimestamp(now or time.time(), ZoneInfo(config.TIMEZONE))
    return local.hour >= post_hour(db) and db.get_kv("deals_channel_last") != local.date().isoformat()


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
        return "nessuna occasione sotto il valore Cardmarket negli ultimi 3 giorni: niente post"
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
