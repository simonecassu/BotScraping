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
CREATE TABLE IF NOT EXISTS albums (
    id TEXT PRIMARY KEY,
    set_id TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS album_members (
    album_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    PRIMARY KEY (album_id, chat_id)
);
CREATE TABLE IF NOT EXISTS album_cards (
    album_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    added_at REAL NOT NULL,
    PRIMARY KEY (album_id, card_id)
);
CREATE TABLE IF NOT EXISTS queue (
    chat_id TEXT NOT NULL,
    found_id INTEGER NOT NULL,
    PRIMARY KEY (chat_id, found_id)
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
        self._migrate_albums()

    def _migrate_albums(self) -> None:
        """Versioni precedenti: una sola lista di mancanti (tabella wanted) → album del proprietario, uno per set."""
        with self.connect() as c:
            rows = [r["card_id"] for r in c.execute("SELECT card_id FROM wanted")]
            if not rows:
                return
            owner = self.owner_chat_id() or "me"
            now = time.time()
            for cid in rows:
                album = f"{owner}:{cid.rsplit('-', 1)[0]}"
                c.execute("INSERT OR IGNORE INTO albums(id, set_id, created) VALUES (?, ?, ?)", (album, cid.rsplit('-', 1)[0], now))
                c.execute("INSERT OR IGNORE INTO album_members(album_id, chat_id) VALUES (?, ?)", (album, owner))
                c.execute("INSERT OR IGNORE INTO album_cards(album_id, card_id, added_at) VALUES (?, ?, ?)", (album, cid, now))
            c.execute("DELETE FROM wanted")
            # anche le preferenze "globali" di prima diventano del proprietario
            for key in ("watches", "watch_found", "copies"):
                v = c.execute("SELECT value FROM settings WHERE key = ?", ("_" + key,)).fetchone()
                if v is not None:
                    c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (f"_{key}:{owner}", v["value"]))
                    c.execute("DELETE FROM settings WHERE key = ?", ("_" + key,))

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

    # ---- album (una checklist per collezione; può essere condiviso tra più persone) ----------------
    @staticmethod
    def set_of(card_id: str) -> str:
        return card_id.rsplit("-", 1)[0]

    def albums_of(self, chat_id: str) -> dict[str, str]:
        """set_id → album_id degli album a cui la persona partecipa."""
        with self.connect() as c:
            rows = c.execute("SELECT a.id, a.set_id FROM albums a JOIN album_members m ON m.album_id = a.id WHERE m.chat_id = ?",
                             (chat_id,)).fetchall()
        return {r["set_id"]: r["id"] for r in rows}

    def album_set(self, album_id: str) -> str | None:
        with self.connect() as c:
            r = c.execute("SELECT set_id FROM albums WHERE id = ?", (album_id,)).fetchone()
        return r["set_id"] if r else None

    def album_members(self, album_id: str) -> list[str]:
        with self.connect() as c:
            return [r["chat_id"] for r in c.execute("SELECT chat_id FROM album_members WHERE album_id = ? ORDER BY rowid", (album_id,))]

    def all_albums(self) -> list[tuple[str, str]]:
        """[(album_id, set_id)] di tutti."""
        with self.connect() as c:
            return [(r["id"], r["set_id"]) for r in c.execute("SELECT id, set_id FROM albums")]

    def ensure_album(self, chat_id: str, set_id: str) -> tuple[str, bool]:
        """L'album della persona per quella collezione; lo crea se manca. Restituisce (album_id, creato_adesso)."""
        existing = self.albums_of(chat_id).get(set_id)
        if existing:
            return existing, False
        album = f"{chat_id}:{set_id}"
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO albums(id, set_id, created) VALUES (?, ?, ?)", (album, set_id, time.time()))
            c.execute("INSERT OR IGNORE INTO album_members(album_id, chat_id) VALUES (?, ?)", (album, chat_id))
        return album, True

    def join_album(self, chat_id: str, album_id: str) -> None:
        """Entra in un album condiviso: se aveva già un album suo per quel set, le carte possedute da uno dei due
        contano come possedute (mancanti = mancanti in entrambi) e il vecchio album viene lasciato."""
        set_id = self.album_set(album_id)
        if not set_id:
            raise KeyError(album_id)
        old = self.albums_of(chat_id).get(set_id)
        with self.connect() as c:
            if old and old != album_id:
                shared = {r["card_id"] for r in c.execute("SELECT card_id FROM album_cards WHERE album_id = ?", (album_id,))}
                mine = {r["card_id"] for r in c.execute("SELECT card_id FROM album_cards WHERE album_id = ?", (old,))}
                c.executemany("DELETE FROM album_cards WHERE album_id = ? AND card_id = ?", [(album_id, cid) for cid in shared - mine])
                c.execute("DELETE FROM album_members WHERE album_id = ? AND chat_id = ?", (old, chat_id))
                if not c.execute("SELECT 1 FROM album_members WHERE album_id = ?", (old,)).fetchone():
                    c.execute("DELETE FROM album_cards WHERE album_id = ?", (old,))
                    c.execute("DELETE FROM albums WHERE id = ?", (old,))
            c.execute("INSERT OR IGNORE INTO album_members(album_id, chat_id) VALUES (?, ?)", (album_id, chat_id))

    def leave_album(self, chat_id: str, album_id: str) -> None:
        """Esce da un album condiviso portandosi via una copia della checklist (album tutto suo)."""
        set_id = self.album_set(album_id)
        if not set_id or len(self.album_members(album_id)) < 2:
            return
        own = f"{chat_id}:{set_id}"
        with self.connect() as c:
            c.execute("DELETE FROM album_members WHERE album_id = ? AND chat_id = ?", (album_id, chat_id))
            c.execute("INSERT OR IGNORE INTO albums(id, set_id, created) VALUES (?, ?, ?)", (own, set_id, time.time()))
            c.execute("INSERT OR IGNORE INTO album_members(album_id, chat_id) VALUES (?, ?)", (own, chat_id))
            c.execute("DELETE FROM album_cards WHERE album_id = ?", (own,))
            c.execute("INSERT INTO album_cards(album_id, card_id, added_at) SELECT ?, card_id, added_at FROM album_cards WHERE album_id = ?",
                      (own, album_id))

    def _default_album(self, card_id: str) -> str:
        return self.ensure_album(self.owner_chat_id() or "me", self.set_of(card_id))[0]

    # ---- carte mancanti (per album) -------------------------------------------------
    def wanted_ids(self, album_ids: list[str] | None = None) -> set[str]:
        """Mancanti degli album indicati; senza argomento, di tutti gli album (per la ricerca)."""
        with self.connect() as c:
            if album_ids is None:
                return {r["card_id"] for r in c.execute("SELECT DISTINCT card_id FROM album_cards")}
            if not album_ids:
                return set()
            q = ",".join("?" * len(album_ids))
            return {r["card_id"] for r in c.execute(f"SELECT DISTINCT card_id FROM album_cards WHERE album_id IN ({q})", album_ids)}

    def wanted_for(self, chat_id: str) -> set[str]:
        return self.wanted_ids(list(self.albums_of(chat_id).values()))

    def set_wanted(self, card_id: str, wanted: bool, album_id: str | None = None) -> None:
        self.set_wanted_bulk([card_id], wanted, album_id)

    def set_wanted_bulk(self, card_ids: list[str], wanted: bool, album_id: str | None = None) -> None:
        if not card_ids:
            return
        now = time.time()
        by_album: dict[str, list[str]] = {}
        for cid in card_ids:
            by_album.setdefault(album_id or self._default_album(cid), []).append(cid)
        with self.connect() as c:
            for album, ids in by_album.items():
                if wanted:
                    c.executemany("INSERT OR IGNORE INTO album_cards(album_id, card_id, added_at) VALUES (?, ?, ?)",
                                  [(album, cid, now) for cid in ids])
                else:
                    c.executemany("DELETE FROM album_cards WHERE album_id = ? AND card_id = ?", [(album, cid) for cid in ids])

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

    # ---- persone: nome visualizzato, amici, codici -----------------------------------
    def user_name(self, chat_id: str) -> str:
        u = self.get_kv(f"user:{chat_id}") or {}
        return str(u.get("name") or chat_id)

    def set_user_name(self, chat_id: str, name: str) -> None:
        u = self.get_kv(f"user:{chat_id}") or {}
        if name and u.get("name") != name:
            u["name"] = name
            self.set_kv(f"user:{chat_id}", u)

    def friends(self, chat_id: str) -> list[str]:
        return [str(c) for c in (self.get_kv(f"friends:{chat_id}", []) or [])]

    def add_friend(self, a: str, b: str) -> None:
        for x, y in ((a, b), (b, a)):
            fr = self.friends(x)
            if y not in fr and x != y:
                self.set_kv(f"friends:{x}", fr + [y])

    def remove_friend(self, a: str, b: str) -> None:
        for x, y in ((a, b), (b, a)):
            self.set_kv(f"friends:{x}", [c for c in self.friends(x) if c != y])

    # ---- preferenze personali (ogni persona collegata ha le sue notifiche) ----------
    def user_prefs(self, chat_id: str) -> dict[str, Any]:
        v = self.get_kv(f"prefs:{chat_id}") if chat_id else None
        return dict(v) if isinstance(v, dict) else {}

    def save_user_prefs(self, chat_id: str, values: dict[str, Any]) -> None:
        prefs = self.user_prefs(chat_id)
        prefs.update(values)
        self.set_kv(f"prefs:{chat_id}", prefs)

    def settings_for(self, chat_id: str) -> dict[str, Any]:
        """Impostazioni condivise più le preferenze personali di quella chat."""
        out = self.get_settings()
        out.update({k: v for k, v in self.user_prefs(chat_id).items() if k in config.PERSONAL_SETTINGS})
        return out

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
    def active_sets(self, chat_id: str = "") -> list[str]:
        """Collezioni che quella persona fa cercare (senza chat: unione di tutte le persone, per la ricerca)."""
        if not chat_id:
            out: list[str] = []
            for chat in self.chat_ids() or [""]:
                for sid in self.active_sets(chat or "me"):
                    if sid not in out:
                        out.append(sid)
            legacy = self.get_kv("active_sets")
            for sid in (legacy or []):
                if str(sid) not in out:
                    out.append(str(sid))
            return out
        v = self.get_kv(f"active_sets:{chat_id}")
        if v is None:
            legacy = self.get_kv("active_sets")  # versione precedente: un'unica lista, del proprietario
            if legacy is not None and chat_id in (self.owner_chat_id(), "me"):
                return [str(x) for x in legacy]
            return list(config.HOME_SET_IDS)
        return [str(x) for x in v]

    def set_active(self, set_id: str, on: bool, chat_id: str = "") -> None:
        chat_id = chat_id or self.owner_chat_id() or "me"
        ids = [i for i in self.active_sets(chat_id) if i != set_id]
        if on:
            ids.append(set_id)
        self.set_kv(f"active_sets:{chat_id}", ids)

    def current_set(self, chat_id: str = "") -> str:
        """Collezione su cui lavorano i comandi: personale per chat, altrimenti quella generale."""
        if chat_id:
            v = self.get_kv(f"current_set:{chat_id}")
            if v:
                return str(v)
        return str(self.get_kv("current_set") or config.HOME_SET_IDS[0])

    def set_current(self, set_id: str, chat_id: str = "") -> None:
        self.set_kv(f"current_set:{chat_id}" if chat_id else "current_set", set_id)

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

    # ---- lista d'attesa (chi scrive /start senza invito) ----------------------
    def waitlist(self) -> dict:
        """chat_id → {name, ts, source}: persone che hanno chiesto di entrare e aspettano l'approvazione."""
        return dict(self.get_kv("waitlist", {}) or {})

    def add_to_waitlist(self, chat_id: str, name: str = "", source: str = "") -> bool:
        """Mette in lista; False se era già in lista. Conta ogni nuova richiesta per il traguardo di lancio."""
        chat_id = str(chat_id).strip()
        wl = self.waitlist()
        if not chat_id or chat_id in wl or chat_id in self.chat_ids():
            return False
        wl[chat_id] = {"name": name[:40], "ts": time.time(), "source": source[:20]}
        self.set_kv("waitlist", wl)
        self.set_kv("signups_total", int(self.get_kv("signups_total", 0) or 0) + 1)
        return True

    def remove_from_waitlist(self, chat_id: str) -> dict | None:
        wl = self.waitlist()
        entry = wl.pop(str(chat_id).strip(), None)
        if entry is not None:
            self.set_kv("waitlist", wl)
        return entry

    def prune(self, seen_days: int = 45, keep_found: int = 2000, keep_runs: int = 50) -> None:
        """Mantiene il database piccolo (utile quando viene salvato su GitHub a ogni esecuzione)."""
        with self.connect() as c:
            c.execute("DELETE FROM seen WHERE first_seen < ?", (time.time() - seen_days * 86400,))
            c.execute("DELETE FROM found WHERE id NOT IN (SELECT id FROM found ORDER BY created_at DESC LIMIT ?)", (keep_found,))
            c.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY started_at DESC LIMIT ?)", (keep_runs,))
            c.execute("DELETE FROM prices WHERE ts < ?", (time.time() - 120 * 86400,))
            c.execute("DELETE FROM queue WHERE found_id NOT IN (SELECT id FROM found)")
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
    def copies(self, chat_id: str = "") -> dict[str, int]:
        chat_id = chat_id or self.owner_chat_id() or "me"
        v = self.get_kv(f"copies:{chat_id}", {}) or {}
        return {str(k): int(n) for k, n in v.items() if int(n) > 0}

    def set_copies(self, card_id: str, n: int, chat_id: str = "") -> None:
        chat_id = chat_id or self.owner_chat_id() or "me"
        v = self.copies(chat_id)
        if n > 0:
            v[card_id] = n
        else:
            v.pop(card_id, None)
        self.set_kv(f"copies:{chat_id}", v)

    def enqueue(self, chat_id: str, found_ids: list[int]) -> None:
        """Annunci da inviare a quella persona quando le sue notifiche riprendono (pausa / ore notturne)."""
        with self.connect() as c:
            c.executemany("INSERT OR IGNORE INTO queue(chat_id, found_id) VALUES (?, ?)", [(chat_id, i) for i in found_ids])

    def queued_found(self, chat_id: str | None = None) -> list[dict]:
        """Annunci in coda (per una persona, o di chiunque)."""
        with self.connect() as c:
            if chat_id is None:
                rows = c.execute("SELECT f.*, q.chat_id AS queue_chat FROM queue q JOIN found f ON f.id = q.found_id "
                                 "ORDER BY f.created_at").fetchall()
            else:
                rows = c.execute("SELECT f.*, q.chat_id AS queue_chat FROM queue q JOIN found f ON f.id = q.found_id "
                                 "WHERE q.chat_id = ? ORDER BY f.created_at", (chat_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["matched"] = json.loads(d["matched"])
            out.append(d)
        return out

    def queued_chats(self) -> list[str]:
        with self.connect() as c:
            return [r["chat_id"] for r in c.execute("SELECT DISTINCT chat_id FROM queue")]

    def mark_sent(self, ids: list[int], sent: bool, chat_id: str | None = None) -> None:
        """Toglie dalla coda (di una persona o di tutte) e segna come inviato se lo è stato."""
        if not ids:
            return
        with self.connect() as c:
            if chat_id is None:
                c.executemany("DELETE FROM queue WHERE found_id = ?", [(i,) for i in ids])
            else:
                c.executemany("DELETE FROM queue WHERE found_id = ? AND chat_id = ?", [(i, chat_id) for i in ids])
            if sent:
                c.executemany("UPDATE found SET notified = 1, queued = 0 WHERE id = ?", [(i,) for i in ids])

    def mark_deal(self, ids: list[int]) -> None:
        if ids:
            with self.connect() as c:
                c.executemany("UPDATE found SET deal = 1 WHERE id = ?", [(i,) for i in ids])

    def mark_notified(self, ids: list[int]) -> None:
        if ids:
            with self.connect() as c:
                c.executemany("UPDATE found SET notified = 1 WHERE id = ?", [(i,) for i in ids])

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
