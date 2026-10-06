"""Inseguimenti: `/insegui 151` cerca quella carta a ogni sveglia del bot (5 minuti) per 6 ore, avvisando sui soli annunci nuovi.

Varianti: `/insegui 151 2h` (durata), `/insegui 151 2h ogni 10m` (frequenza), più carte insieme."""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config
from .cards import CardIndex
from .db import Database

log = logging.getLogger(__name__)

DURATION_S = 6 * 3600  # durata di default
MAX_DURATION_S = 48 * 3600
EVERY_S = 5 * 60  # frequenza di default e minima (il bot si sveglia ogni 5 minuti)
MAX_WATCHES = 10

_DURATION_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(m|min|minuti|h|ore|ora|g|gg|giorni|giorno|d)$", re.I)


def parse_duration(token: str) -> int | None:
    """'6h' → 21600, '30m' → 1800, '2g' → 172800. None se non è una durata."""
    m = _DURATION_RE.match(token.strip().lower())
    if not m:
        return None
    n = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit.startswith("m"):
        return int(n * 60)
    if unit.startswith(("h", "o")):
        return int(n * 3600)
    return int(n * 86400)


def parse_watch_args(args: str) -> tuple[str, int, int]:
    """Separa carte, durata e frequenza: '151 2h ogni 10m' → ('151', 7200, 600). Default 6 ore ogni 5 minuti."""
    tokens = args.replace(",", " ").split()
    cards: list[str] = []
    duration = DURATION_S
    every = EVERY_S
    i = 0
    while i < len(tokens):
        t = tokens[i].lower()
        if t == "ogni" and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            d = parse_duration(nxt) or (int(nxt) * 60 if nxt.isdigit() else None)
            if d:
                every = d
                i += 2
                continue
        d = parse_duration(t)
        if d is not None:
            duration = d
        else:
            cards.append(tokens[i])
        i += 1
    duration = max(EVERY_S, min(MAX_DURATION_S, duration))
    every = max(EVERY_S, min(duration, every))
    return " ".join(cards), duration, every

def fmt_duration(seconds: int) -> str:
    seconds = int(seconds)
    if seconds % 86400 == 0 and seconds >= 86400:
        n = seconds // 86400
        return f"{n} giorn{'o' if n == 1 else 'i'}"
    if seconds % 3600 == 0:
        n = seconds // 3600
        return f"{n} or{'a' if n == 1 else 'e'}"
    return f"{seconds // 60} min"


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(config.TIMEZONE)).strftime("%H:%M del %d/%m")


# ---- stato (kv "watches": {card_id: {...}}) -------------------------------------
def list_watches(db: Database) -> dict[str, dict]:
    w = db.get_kv("watches", {}) or {}
    return w if isinstance(w, dict) else {}


def add_watch(db: Database, card_id: str, duration_s: int = DURATION_S, every_s: int = EVERY_S) -> dict:
    watches = list_watches(db)
    now = time.time()
    watches[card_id] = {"started": now, "until": now + duration_s, "every": every_s, "last": 0, "found": 0, "checks": 0}
    db.set_kv("watches", watches)
    return watches[card_id]


def remove_watch(db: Database, card_id: str) -> bool:
    watches = list_watches(db)
    if card_id not in watches:
        return False
    del watches[card_id]
    db.set_kv("watches", watches)
    return True


def clear_watches(db: Database) -> int:
    n = len(list_watches(db))
    db.set_kv("watches", {})
    return n


def describe(index: CardIndex, db: Database) -> str:
    watches = list_watches(db)
    if not watches:
        return ("Nessun inseguimento attivo. Es. <code>/insegui 151</code>: per 6 ore cerco la 151 ogni 5 minuti. "
                "Varianti: <code>/insegui 151 2h</code>, <code>/insegui 151 2h ogni 10m</code>.")
    lines = [f"🏃 <b>Inseguimenti attivi</b> ({len(watches)})"]
    now = time.time()
    for cid, w in sorted(watches.items(), key=lambda kv: kv[1]["until"]):
        card = index.by_id.get(cid)
        label = html.escape(card.label if card else cid)
        left = max(0, int(w["until"] - now))
        lines.append(f"• {label} · ogni {fmt_duration(int(w['every']))} · ancora {fmt_duration(left) if left >= 60 else 'pochi secondi'} "
                     f"(fino alle {fmt_time(w['until'])}) · controlli: {w.get('checks', 0)} · nuovi: {w.get('found', 0)}")
    lines.append("\n/insegui stop 151 per fermarne uno · /insegui stop per fermarli tutti")
    return "\n".join(lines)


# ---- esecuzione a ogni sveglia -----------------------------------------------------
def run_watches(index: CardIndex, db: Database, notifier=None, scrapers: dict | None = None) -> list[str]:
    """Esegue gli inseguimenti dovuti; notifica subito i soli annunci nuovi; chiude quelli scaduti."""
    from .notifier import TelegramNotifier
    from .search import notifications_suppressed, search_card

    watches = list_watches(db)
    if not watches:
        return []
    settings = db.get_settings()
    if notifications_suppressed(settings):
        log.info("Inseguimenti rimandati: notifiche in pausa / ore notturne")
        return ["rimandate (pausa/notte)"]
    notifier = notifier or TelegramNotifier.from_db(db)
    now = time.time()
    log_lines: list[str] = []
    changed = False
    for cid, w in list(watches.items()):
        card = index.by_id.get(cid)
        if not card:
            del watches[cid]
            changed = True
            continue
        if now >= float(w["until"]):
            del watches[cid]
            changed = True
            notifier.send(f"🏁 Inseguimento di <b>{html.escape(card.label)}</b> finito: "
                          f"{w.get('checks', 0)} controlli, {w.get('found', 0)} annunci nuovi in {fmt_duration(int(w['until'] - w['started']))}.",
                          True)
            log_lines.append(f"{card.label}: terminata")
            continue
        if now - float(w.get("last", 0)) < float(w["every"]) - 30:
            continue
        items, errors = search_card(index, db, card, settings, scrapers, limit=20, only_new=True)
        w["last"] = now
        w["checks"] = int(w.get("checks", 0)) + 1
        changed = True
        if items:
            sent = notifier.notify_many(items, max_per_card=int(settings.get("max_per_card", 5) or 5),
                                        images=bool(settings.get("images", True)))
            w["found"] = int(w.get("found", 0)) + sum(1 for ok in sent if ok)
        log_lines.append(f"{card.label}: {len(items)} nuovi" + (f" (errori: {', '.join(errors)})" if errors else ""))
    if changed:
        db.set_kv("watches", watches)
    return log_lines
