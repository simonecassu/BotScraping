"""Segnalazioni degli utenti: un problema o un'idea, al massimo una al giorno a testa; il proprietario le legge tutte.

Salvate nel kv `reports` (le ultime MAX_KEEP), dalla più vecchia alla più nuova: {chat, name, tier, text, ts}.
"""
from __future__ import annotations

import datetime as dt
import time
from zoneinfo import ZoneInfo

from . import config
from .db import Database

MAX_LEN = 1000
MAX_KEEP = 500


def _day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, ZoneInfo(config.TIMEZONE)).date().isoformat()


def all_reports(db: Database) -> list[dict]:
    v = db.get_kv("reports", []) or []
    return v if isinstance(v, list) else []


def mine(db: Database, chat: str) -> list[dict]:
    return [r for r in all_reports(db) if r.get("chat") == chat]


def sent_today(db: Database, chat: str, now: float | None = None) -> bool:
    today = _day(now or time.time())
    return any(_day(float(r.get("ts") or 0)) == today for r in mine(db, chat))


def add(db: Database, chat: str, name: str, tier: str, text: str, now: float | None = None) -> dict:
    r = {"chat": chat, "name": name, "tier": tier, "text": text.strip()[:MAX_LEN], "ts": now or time.time()}
    db.set_kv("reports", (all_reports(db) + [r])[-MAX_KEEP:])
    return r
