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
from .db import Database, masked

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
        if t == "ogni":
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            d = parse_duration(nxt) or (int(nxt) * 60 if nxt.isdigit() else None)
            if d:
                every = d
            i += 2 if d is not None or nxt.isdigit() else 1  # "ogni" non è mai una carta
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


def resolve_card(index: CardIndex, db: Database, card_id: str):
    """Carta e indice da usare per cercarla: di casa (indice del bot) o di un'altra collezione scaricata."""
    card = index.by_id.get(card_id)
    if card:
        return card, index
    from . import collections as coll
    set_id = card_id.rsplit("-", 1)[0]
    cs = coll.get_set(db, set_id, download=False)
    if not cs:
        return None, index
    card = next((c for c in cs.cards if c.id == card_id), None)
    if not card:
        return None, index
    return card, CardIndex(list(index.sets) + [cs], index.aliases)


def code_for(index: CardIndex, card) -> str:
    """Codice per i comandi: numero/c4 per la collezione di casa, set:numero per le altre."""
    return index.code_of.get(card.id) or f"{card.set_id}:{card.number}"


# ---- stato (kv "watches:<chat>": {card_id: {...}}) ---------------------------------
def _chat(db: Database, chat_id: str) -> str:
    return chat_id or db.owner_chat_id() or "me"


def list_watches(db: Database, chat_id: str = "") -> dict[str, dict]:
    """Inseguimenti di una persona (ognuno ha i suoi)."""
    w = db.get_kv(f"watches:{_chat(db, chat_id)}", {}) or {}
    return w if isinstance(w, dict) else {}


def all_watches(db: Database) -> list[tuple[str, dict[str, dict]]]:
    """[(chat_id, inseguimenti)] di tutte le persone collegate."""
    return [(c, list_watches(db, c)) for c in (db.chat_ids() or ["me"])]


def add_watch(db: Database, card_id: str, duration_s: int = DURATION_S, every_s: int = EVERY_S, chat_id: str = "") -> dict:
    chat_id = _chat(db, chat_id)
    watches = list_watches(db, chat_id)
    now = time.time()
    watches[card_id] = {"started": now, "until": now + duration_s, "every": every_s, "last": 0, "found": 0, "checks": 0}
    db.set_kv(f"watches:{chat_id}", watches)
    return watches[card_id]


def remove_watch(db: Database, card_id: str, chat_id: str = "") -> bool:
    chat_id = _chat(db, chat_id)
    watches = list_watches(db, chat_id)
    if card_id not in watches:
        return False
    del watches[card_id]
    db.set_kv(f"watches:{chat_id}", watches)
    return True


def clear_watches(db: Database, chat_id: str = "") -> int:
    chat_id = _chat(db, chat_id)
    n = len(list_watches(db, chat_id))
    db.set_kv(f"watches:{chat_id}", {})
    return n


MAX_FOUND_LOG = 300


def record_found(db: Database, card_id: str, listings: list, chat_id: str = "") -> None:
    """Tiene l'elenco (ultimi 300) degli annunci scovati dagli inseguimenti di una persona, per la Mini App."""
    chat_id = _chat(db, chat_id)
    log_ = db.get_kv(f"watch_found:{chat_id}", []) or []
    now = time.time()
    for lst in listings:
        log_.append({"key": lst.key, "card": card_id, "ts": now, "title": lst.title, "url": lst.url, "source": lst.source,
                     "price": lst.price_text or (f"{lst.price:.2f} €" if lst.price is not None else ""),
                     "price_num": lst.price, "image": lst.image or "", "location": lst.location or ""})
    db.set_kv(f"watch_found:{chat_id}", log_[-MAX_FOUND_LOG:])


def sent_keys(db: Database, chat_id: str) -> set[str]:
    """Annunci che gli inseguimenti hanno già mandato a quella persona (la ricerca completa non li rimanda)."""
    keys = {f.get("key") for f in found_log(db, chat_id)}
    for w in list_watches(db, chat_id).values():
        keys.update(w.get("sent") or [])
    keys.discard(None)
    return keys


def found_log(db: Database, chat_id: str = "") -> list[dict]:
    log_ = db.get_kv(f"watch_found:{_chat(db, chat_id)}", []) or []
    return log_ if isinstance(log_, list) else []


def describe(index: CardIndex, db: Database, chat_id: str = "") -> str:
    watches = list_watches(db, chat_id)
    if not watches:
        return ("Nessun inseguimento attivo. Es. <code>/insegui 151</code>: per 6 ore cerco la 151 ogni 5 minuti. "
                "Varianti: <code>/insegui 151 2h</code>, <code>/insegui 151 2h ogni 10m</code>.")
    lines = [f"🏃 <b>Inseguimenti attivi</b> ({len(watches)})"]
    now = time.time()
    for cid, w in sorted(watches.items(), key=lambda kv: kv[1]["until"]):
        card, _ = resolve_card(index, db, cid)
        label = html.escape(card.label if card else cid)
        left = max(0, int(w["until"] - now))
        lines.append(f"• {label} · ogni {fmt_duration(int(w['every']))} · ancora {fmt_duration(left) if left >= 60 else 'pochi secondi'} "
                     f"(fino alle {fmt_time(w['until'])}) · controlli: {w.get('checks', 0)} · nuovi: {w.get('found', 0)}")
    lines.append("\n/insegui stop 151 per fermarne uno · /insegui stop per fermarli tutti")
    return "\n".join(lines)


# ---- esecuzione a ogni sveglia -----------------------------------------------------
def run_watches(index: CardIndex, db: Database, notifier=None, scrapers: dict | None = None) -> list[str]:
    """Esegue gli inseguimenti dovuti di ogni persona, con le sue impostazioni; notifica solo lei."""
    from .notifier import TelegramNotifier
    from .search import build_scrapers
    out: list[str] = []
    for chat, watches in all_watches(db):
        if not watches:
            continue
        if scrapers is None:  # una volta per giro, condivisi da tutti gli inseguimenti
            scrapers = build_scrapers(db.get_settings())
        out += _run_for(index, db, chat, watches, notifier or TelegramNotifier(chat_ids=[chat]), scrapers)
    return out


def _run_for(index: CardIndex, db: Database, chat: str, watches: dict, notifier, scrapers: dict | None) -> list[str]:
    from . import plans
    from .search import notifications_suppressed, search_card

    settings = db.settings_for(chat)
    if notifications_suppressed(settings):
        log.info("Inseguimenti di %s rimandati: notifiche in pausa / ore notturne", masked(chat))
        return ["rimandate (pausa/notte)"]
    now = time.time()
    log_lines: list[str] = []
    changed = False
    first_seen = {}
    for r in db.list_found():
        first_seen[r["listing_key"]] = min(float(r["created_at"]), first_seen.get(r["listing_key"], float("inf")))
    for cid, w in list(watches.items()):
        card, idx = resolve_card(index, db, cid)
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
        every = float(w["every"]) if plans.is_full(db, chat) else max(float(w["every"]), plans.LIGHT_WATCH_EVERY_S)
        if now - float(w.get("last", 0)) < every - 30:
            continue
        # nuovi per questa persona: mai mandati da questo inseguimento e non già visti dal bot prima che partisse
        older = {k for k, ts in first_seen.items() if ts < float(w["started"])}
        items, errors = search_card(idx, db, card, settings, scrapers, limit=20, exclude=older | set(w.get("sent") or []))
        w["last"] = now
        w["checks"] = int(w.get("checks", 0)) + 1
        w["sent"] = (list(w.get("sent") or []) + [lst.key for lst, _ in items])[-300:]
        changed = True
        if items:
            sent = notifier.notify_many(items, max_per_card=int(settings.get("max_per_card", 5) or 5),
                                        images=bool(settings.get("images", True)))
            w["found"] = int(w.get("found", 0)) + sum(1 for ok in sent if ok)
            record_found(db, cid, [lst for lst, _ in items], chat)
        log_lines.append(f"{card.label}: {len(items)} nuovi" + (f" (errori: {', '.join(errors)})" if errors else ""))
    if changed:
        db.set_kv(f"watches:{chat}", watches)
    return log_lines
