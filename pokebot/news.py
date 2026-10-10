"""Novità nel bot: avvisi in chat per le categorie accese (/novita), «ti mancano» dei mazzi, /insegui mazzo N.

I dati (news.json, alerts.json) li scrive il workflow «Novità» (scripts/build_news.py) nel branch bot-news del
repository; qui si leggono dal sito raw di GitHub, con una cache di 5 minuti nel database.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from pokebot.db import Database

CATS = ("espansioni", "tornei", "live", "aggiornamenti")
DEFAULT = {"espansioni": True, "tornei": False, "live": False, "aggiornamenti": False}
CACHE_S = 300
MAX_SENT = 600


def repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or "simonecassu/BotScraping"


def raw_url(name: str) -> str:
    return f"https://raw.githubusercontent.com/{repo()}/bot-news/{name}"


def _fetch(name: str) -> dict | None:
    req = urllib.request.Request(raw_url(name), headers={"User-Agent": "Pescacarte/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def load(db: Database, name: str = "news.json", fresh: bool = False) -> dict | None:
    """news.json o alerts.json dal branch bot-news, con cache di 5 minuti; None se non ci sono (ancora)."""
    key = f"news_cache:{name}"
    c = db.get_kv(key) or {}
    if not fresh and c.get("data") is not None and time.time() - float(c.get("ts") or 0) < CACHE_S:
        return c["data"]
    try:
        data = _fetch(name)
    except Exception:  # noqa: BLE001 - senza rete o senza branch: l'ultima copia buona, se c'è
        return c.get("data")
    db.set_kv(key, {"ts": time.time(), "data": data})
    return data


def settings(db: Database, chat: str) -> dict:
    s = dict(DEFAULT)
    s.update({k: bool(v) for k, v in (db.get_kv(f"news:{chat}") or {}).items() if k in CATS})
    return s


def set_setting(db: Database, chat: str, cat: str, on: bool) -> dict:
    s = settings(db, chat)
    s[cat] = on
    db.set_kv(f"news:{chat}", s)
    return s


def user_view(db: Database, chat: str, data: dict | None) -> dict:
    """Per la Mini App: interruttori, carte mancanti per ogni mazzo della top 8, collezioni seguite."""
    albums = db.albums_of(chat)
    out = {"notif": settings(db, chat), "missing": {}, "missing_codes": {}, "followed": sorted(albums)}
    wanted_cache: dict[str, set] = {}
    for d in ((data or {}).get("decks") or {}).get("top") or []:
        if not d.get("cards"):
            continue
        miss, n = [], 0
        for q, code, tid in d["cards"]:
            sid = (tid or "").rsplit("-", 1)[0] if tid else ""
            album = albums.get(sid)
            if album:
                if album not in wanted_cache:
                    wanted_cache[album] = set(db.wanted_ids([album]))
                owned = tid not in wanted_cache[album]
            else:
                owned = False
            if not owned:
                miss.append(code)
                n += int(q)
        out["missing"][str(d["place"])] = n
        out["missing_codes"][str(d["place"])] = miss
    return out


def deck_missing_tokens(db: Database, chat: str, data: dict | None, place: int) -> tuple[str | None, list[str]]:
    """Le carte mancanti del mazzo N come «set:numero» per /insegui (solo quelle di set conosciuti)."""
    view = user_view(db, chat, data)
    deck = next((d for d in ((data or {}).get("decks") or {}).get("top") or [] if int(d.get("place") or 0) == place), None)
    if not deck:
        return None, []
    codes = set(view["missing_codes"].get(str(place), []))
    toks = []
    for _q, code, tid in deck.get("cards") or []:
        if code in codes and tid:
            sid, _, num = tid.rpartition("-")
            toks.append(f"{sid}:{num}")
    return deck.get("a") or "mazzo", toks


def send_alerts(db: Database, client, is_full) -> int:
    """Manda gli avvisi non ancora mandati alle persone con la categoria accesa (solo Completo e prova). Torna quanti messaggi."""
    alerts = (load(db, "alerts.json", fresh=True) or {}).get("alerts") or []
    if not alerts:
        return 0
    sent = list(db.get_kv("news_sent") or [])
    done = set(sent)
    members = db.chat_ids()
    n = 0
    for a in alerts:
        if a["id"] in done or a.get("cat") not in CATS:
            continue
        for chat in members:
            if not is_full(db, chat) or not settings(db, chat).get(a["cat"]):
                continue
            try:
                if client.send(chat, a["text"]):
                    n += 1
            except Exception:  # noqa: BLE001
                pass
        sent.append(a["id"])
        done.add(a["id"])
    db.set_kv("news_sent", sent[-MAX_SENT:])
    return n
