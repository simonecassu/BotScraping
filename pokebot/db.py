"""Persistenza SQLite: carte mancanti, impostazioni, annunci visti e trovati."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS wanted (
    card_id TEXT PRIMARY KEY,
    added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    listing_key TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS found (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_key TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    price TEXT,
    location TEXT,
    kind TEXT NOT NULL,
    matched TEXT NOT NULL,
    ratio REAL,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS prices (
    card_id TEXT NOT NULL,
    listing_key TEXT NOT NULL,
    source TEXT NOT NULL,
    price REAL NOT NULL,
    seller TEXT,
    ts REAL NOT NULL,
    PRIMARY KEY (card_id, listing_key)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    finished_at REAL,
    listings_seen INTEGER DEFAULT 0,
    matches INTEGER DEFAULT 0,
    errors TEXT
);
CREATE TABLE IF NOT EXISTS card_state (
    card_id TEXT PRIMARY KEY,
    rotation_ts REAL NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            for stmt in ("ALTER TABLE runs ADD COLUMN per_source TEXT",
                         "ALTER TABLE found ADD COLUMN queued INTEGER NOT NULL DEFAULT 0",
                         "ALTER TABLE found ADD COLUMN image TEXT",
                         "ALTER TABLE found ADD COLUMN deal INTEGER NOT NULL DEFAULT 0",
                         "ALTER TABLE found ADD COLUMN seller TEXT"):
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # colonna già presente

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _lock:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    # ---- carte mancanti -------------------------------------------------
    def wanted_ids(self) -> set[str]:
        with self.connect() as c:
            return {r["card_id"] for r in c.execute("SELECT card_id FROM wanted")}

    def set_wanted(self, card_id: str, wanted: bool) -> None:
        with self.connect() as c:
            if wanted:
                c.execute(
                    "INSERT OR IGNORE INTO wanted(card_id, added_at) VALUES (?, ?)",
                    (card_id, time.time()),
                )
            else:
                c.execute("DELETE FROM wanted WHERE card_id = ?", (card_id,))

    def set_wanted_bulk(self, card_ids: list[str], wanted: bool) -> None:
        now = time.time()
        with self.connect() as c:
            if wanted:
                c.executemany(
                    "INSERT OR IGNORE INTO wanted(card_id, added_at) VALUES (?, ?)",
                    [(cid, now) for cid in card_ids],
                )
            else:
                c.executemany("DELETE FROM wanted WHERE card_id = ?", [(cid,) for cid in card_ids])

    # ---- impostazioni ---------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        out = dict(config.DEFAULT_SETTINGS)
        with self.connect() as c:
            for r in c.execute("SELECT key, value FROM settings WHERE substr(key, 1, 1) != '_'"):
                try:
                    out[r["key"]] = json.loads(r["value"])
                except json.JSONDecodeError:
                    out[r["key"]] = r["value"]
        return out

    def save_settings(self, values: dict[str, Any]) -> None:
        with self.connect() as c:
            c.executemany(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(k, json.dumps(v)) for k, v in values.items()],
            )

    # ---- valori interni (chat id Telegram, offset aggiornamenti...) --------
    def get_kv(self, key: str, default: Any = None) -> Any:
        with self.connect() as c:
            r = c.execute("SELECT value FROM settings WHERE key = ?", ("_" + key,)).fetchone()
        if r is None:
            return default
        try:
            return json.loads(r["value"])
        except json.JSONDecodeError:
            return r["value"]

    def set_kv(self, key: str, value: Any) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("_" + key, json.dumps(value)),
            )

    # ---- collezioni: quali si cercano, su quale lavorano i comandi ----------------
    def active_sets(self) -> list[str]:
        v = self.get_kv("active_sets")
        if v is None:  # mai impostato: si cercano le collezioni di casa
            return list(config.HOME_SET_IDS)
        return [str(x) for x in v]

    def set_active(self, set_id: str, on: bool) -> None:
        ids = [i for i in self.active_sets() if i != set_id]
        if on:
            ids.append(set_id)
        self.set_kv("active_sets", ids)

    def current_set(self) -> str:
        return str(self.get_kv("current_set") or config.HOME_SET_IDS[0])

    def set_current(self, set_id: str) -> None:
        self.set_kv("current_set", set_id)

    # ---- persone collegate (proprietario + invitati) --------------------------
    def owner_chat_id(self) -> str:
        return config.TELEGRAM_CHAT_ID or str(self.get_kv("telegram_chat_id", "") or "")

    def chat_ids(self) -> list[str]:
        """Chat che ricevono le notifiche e possono dare comandi: il proprietario per primo, poi gli invitati."""
        out: list[str] = []
        for cid in [self.owner_chat_id(), *(self.get_kv("telegram_extra_chat_ids", []) or [])]:
            cid = str(cid or "").strip()
            if cid and cid not in out:
                out.append(cid)
        return out

    def add_chat_id(self, chat_id: str) -> None:
        chat_id = str(chat_id).strip()
        if not chat_id or chat_id in self.chat_ids():
            return
        self.set_kv("telegram_extra_chat_ids", [str(c) for c in (self.get_kv("telegram_extra_chat_ids", []) or [])] + [chat_id])

    def remove_chat_id(self, chat_id: str) -> bool:
        chat_id = str(chat_id).strip()
        extra = [str(c) for c in (self.get_kv("telegram_extra_chat_ids", []) or [])]
        if chat_id not in extra:
            return False
        self.set_kv("telegram_extra_chat_ids", [c for c in extra if c != chat_id])
        return True

    def prune(self, seen_days: int = 45, keep_found: int = 2000, keep_runs: int = 50) -> None:
        """Mantiene il database piccolo (utile quando viene salvato su GitHub a ogni esecuzione)."""
        with self.connect() as c:
            c.execute("DELETE FROM seen WHERE first_seen < ?", (time.time() - seen_days * 86400,))
            c.execute("DELETE FROM found WHERE id NOT IN (SELECT id FROM found ORDER BY created_at DESC LIMIT ?)", (keep_found,))
            c.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY started_at DESC LIMIT ?)", (keep_runs,))
            c.execute("DELETE FROM prices WHERE ts < ?", (time.time() - 120 * 86400,))
        with _lock:
            conn = sqlite3.connect(self.path, isolation_level=None)
            try:
                conn.execute("VACUUM")
            finally:
                conn.close()

    # ---- annunci ----------------------------------------------------------
    def is_seen(self, listing_key: str) -> bool:
        with self.connect() as c:
            return c.execute("SELECT 1 FROM seen WHERE listing_key = ?", (listing_key,)).fetchone() is not None

    def mark_seen(self, listing_key: str, notified: bool = False) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO seen(listing_key, first_seen, notified) VALUES (?, ?, ?) "
                "ON CONFLICT(listing_key) DO UPDATE SET notified = MAX(notified, excluded.notified)",
                (listing_key, time.time(), int(notified)),
            )

    def add_found(self, listing_key: str, source: str, title: str, url: str, price: str | None,
                  location: str | None, kind: str, matched: list[dict], ratio: float | None,
                  notified: bool, queued: bool = False, image: str | None = None, deal: bool = False,
                  seller: str | None = None) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO found(listing_key, source, title, url, price, location, kind, matched, ratio, notified, "
                "created_at, queued, image, deal, seller) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (listing_key, source, title, url, price, location, kind, json.dumps(matched, ensure_ascii=False),
                 ratio, int(notified), time.time(), int(queued), image, int(deal), seller or None),
            )
            return int(cur.lastrowid)

    # ---- prezzi di tutte le carte riconosciute (anche quelle che possiedi: servono per il valore) ----------
    def add_price_point(self, card_id: str, listing_key: str, source: str, price: float, seller: str | None = None) -> None:
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO prices(card_id, listing_key, source, price, seller, ts) VALUES (?, ?, ?, ?, ?, ?)",
                      (card_id, listing_key, source, float(price), seller or None, time.time()))

    def price_rows(self, days: int = 120) -> list[dict]:
        """I punti prezzo nella stessa forma delle righe di `found` (per riusare le statistiche)."""
        with self.connect() as c:
            rows = c.execute("SELECT * FROM prices WHERE ts >= ? ORDER BY ts DESC", (time.time() - days * 86400,)).fetchall()
        return [{"listing_key": r["listing_key"], "source": r["source"], "price": f"{r['price']:.2f}", "kind": "single",
                 "matched": [{"id": r["card_id"], "sure": True}], "created_at": r["ts"], "seller": r["seller"],
                 "title": "", "url": "", "location": "", "notified": 0, "deal": 0, "image": ""} for r in rows]

    # ---- doppioni (copie in più, per gli scambi) ----------------------------------------------------------
    def copies(self) -> dict[str, int]:
        v = self.get_kv("copies", {}) or {}
        return {str(k): int(n) for k, n in v.items() if int(n) > 0}

    def set_copies(self, card_id: str, n: int) -> None:
        v = self.copies()
        if n > 0:
            v[card_id] = n
        else:
            v.pop(card_id, None)
        self.set_kv("copies", v)

    def queued_found(self) -> list[dict]:
        """Annunci trovati durante la pausa / le ore notturne, non ancora inviati."""
        with self.connect() as c:
            rows = c.execute("SELECT * FROM found WHERE queued = 1 ORDER BY created_at").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["matched"] = json.loads(d["matched"])
            out.append(d)
        return out

    def mark_sent(self, ids: list[int], sent: bool) -> None:
        if not ids:
            return
        with self.connect() as c:
            c.executemany("UPDATE found SET queued = 0, notified = ? WHERE id = ?", [(int(sent), i) for i in ids])

    def list_found(self, limit: int = 2000) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM found ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["matched"] = json.loads(d["matched"])
            out.append(d)
        return out

    def clear_found(self) -> None:
        with self.connect() as c:
            c.execute("DELETE FROM found")

    def forget_seen(self) -> None:
        with self.connect() as c:
            c.execute("DELETE FROM seen")

    # ---- esecuzioni -------------------------------------------------------
    def start_run(self) -> int:
        with self.connect() as c:
            cur = c.execute("INSERT INTO runs(started_at) VALUES (?)", (time.time(),))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, listings_seen: int, matches: int, errors: dict[str, str],
                   per_source: dict[str, int] | None = None) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE runs SET finished_at = ?, listings_seen = ?, matches = ?, errors = ?, per_source = ? WHERE id = ?",
                (time.time(), listings_seen, matches, json.dumps(errors, ensure_ascii=False),
                 json.dumps(per_source or {}), run_id),
            )

    def last_runs(self, limit: int = 10) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["errors"] = json.loads(d["errors"]) if d["errors"] else {}
            except json.JSONDecodeError:
                d["errors"] = {"?": d["errors"]}
            try:
                d["per_source"] = json.loads(d.get("per_source") or "{}")
            except json.JSONDecodeError:
                d["per_source"] = {}
            out.append(d)
        return out

    # ---- rotazione ricerche per carta ------------------------------------
    def next_rotation(self, card_ids: list[str], n: int) -> list[str]:
        """Restituisce le `n` carte cercate meno di recente e aggiorna il timestamp."""
        if not card_ids or n <= 0:
            return []
        with self.connect() as c:
            c.executemany(
                "INSERT OR IGNORE INTO card_state(card_id, rotation_ts) VALUES (?, 0)",
                [(cid,) for cid in card_ids],
            )
            placeholders = ",".join("?" * len(card_ids))
            rows = c.execute(
                f"SELECT card_id FROM card_state WHERE card_id IN ({placeholders}) "
                "ORDER BY rotation_ts ASC, card_id ASC LIMIT ?",
                (*card_ids, n),
            ).fetchall()
            chosen = [r["card_id"] for r in rows]
            now = time.time()
            c.executemany("UPDATE card_state SET rotation_ts = ? WHERE card_id = ?", [(now, cid) for cid in chosen])
        return chosen
