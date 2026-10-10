"""Novità del bot in chat: quando cambia il testo di data/novita.txt, arriva una volta a tutte le persone collegate.

Chi pubblica una versione nuova scrive lì, in poche righe, cosa cambia di visibile (file vuoto: nessun messaggio);
così nessuno pensa che il bot sia fermo quando invece è cambiato. Si spedisce solo di giorno (ore 9-21 italiane):
di notte aspetta il giro successivo. L'impronta dell'ultimo testo spedito sta nel kv `news_sent`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import logging
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import config
from .db import Database, masked

log = logging.getLogger(__name__)

NEWS_FILE = config.DATA_DIR / "novita.txt"
SEND_HOURS = range(9, 21)  # ora italiana


def pending(db: Database, path: Path | None = None) -> tuple[str, str] | None:
    """(testo, impronta) delle novità non ancora spedite; None se il file manca, è vuoto o è già stato spedito."""
    try:
        text = (path or NEWS_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return None if db.get_kv("news_sent") == digest else (text, digest)


def send(db: Database, client, now: float | None = None, path: Path | None = None) -> str | None:
    """Spedisce le novità a tutte le persone collegate. Una riga per il log; None se non c'era nulla da fare."""
    p = pending(db, path)
    if not p:
        return None
    text, digest = p
    if dt.datetime.fromtimestamp(now or time.time(), ZoneInfo(config.TIMEZONE)).hour not in SEND_HOURS:
        return "novità pronte: si spediscono di giorno"
    sent = 0
    for chat in db.chat_ids():
        try:
            sent += bool(client.send(chat, "📣 <b>Novità di Pokébot</b>\n" + html.escape(text)))
        except requests.RequestException as exc:  # una chat che non riceve non ferma le altre
            log.warning("novità non consegnate a %s: %s", masked(chat), type(exc).__name__)
    db.set_kv("news_sent", digest)  # anche se qualcuno non ha ricevuto: niente raffiche al giro dopo
    return f"novità inviate a {sent} persone"
