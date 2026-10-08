"""Collezioni: quelle "di casa" (data/sets) più qualsiasi altra del catalogo pubblico (pokemon-tcg-data).

Una collezione "seguita" ha le carte nell'indice del bot e le sue mancanti nella tabella `wanted` come tutte le altre.
Seguirla (= scaricarla) parte da "mi mancano tutte"; poi si segnano le possedute. Si cerca solo se è attiva
(`db.active_sets()`); i comandi in chat lavorano sulla collezione corrente (`db.current_set()`), cambiabile con /collezione.
"""
from __future__ import annotations

import logging
import time

import requests

from . import config
from .cards import CardIndex, CardSet, load_sets, set_from_raw
from .db import Database

log = logging.getLogger(__name__)

CATALOG_URLS = [
    "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master/sets/en.json",
    "https://cdn.jsdelivr.net/gh/PokemonTCG/pokemon-tcg-data@master/sets/en.json",
]
CARDS_URLS = [
    "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master/cards/en/{id}.json",
    "https://cdn.jsdelivr.net/gh/PokemonTCG/pokemon-tcg-data@master/cards/en/{id}.json",
]
CATALOG_TTL = 24 * 3600


def _get_json(urls: list[str]):
    last = None
    for u in urls:
        try:
            r = requests.get(u, timeout=config.HTTP_TIMEOUT, headers={"User-Agent": config.USER_AGENT})
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as exc:
            last = str(exc)
    raise RuntimeError(f"catalogo non raggiungibile ({last})")


def catalog(db: Database, refresh: bool = False) -> list[dict]:
    """Elenco di tutte le collezioni (id, nome, serie, totale, logo), dal catalogo pubblico; in cache 24 ore."""
    cached = db.get_kv("sets_catalog") or {}
    if not refresh and cached and time.time() - float(cached.get("ts", 0)) < CATALOG_TTL:
        return cached["sets"]
    raw = _get_json(CATALOG_URLS)
    sets = [{"id": s["id"], "name": s["name"], "series": s.get("series", ""), "total": s.get("total", 0),
             "printed_total": s.get("printedTotal"), "release": (s.get("releaseDate") or "").replace("/", "-"),
             "ptcgo": s.get("ptcgoCode", ""), "logo": (s.get("images") or {}).get("logo", ""),
             "symbol": (s.get("images") or {}).get("symbol", "")} for s in raw]
    db.set_kv("sets_catalog", {"ts": time.time(), "sets": sets})
    return sets


def fetch_set_raw(db: Database, set_id: str) -> dict:
    """Scarica una collezione dal catalogo pubblico e la converte nel nostro formato (parole chiave e query incluse)."""
    meta = next((s for s in catalog(db) if s["id"] == set_id), None)
    if not meta:
        raise KeyError(set_id)
    cards = _get_json([u.format(id=set_id) for u in CARDS_URLS])
    name = meta["name"]
    kws = [name.lower()]
    if meta.get("ptcgo"):
        kws.append(meta["ptcgo"].lower())
    return {
        "id": set_id, "name": name, "name_it": name, "printed_total": meta.get("printed_total"),
        "total": meta.get("total") or len(cards), "release": meta.get("release", ""), "series": meta.get("series", ""),
        "logo": meta.get("logo", ""), "symbol": meta.get("symbol", ""), "context_keywords": kws,
        "queries": [f"pokemon {name}", f"carte pokemon {name}"], "card_suffixes": [name], "card_query": f"{name} pokemon",
        "cards": [{"id": c["id"], "number": str(c["number"]), "name": c["name"], "rarity": c.get("rarity", ""),
                   "supertype": c.get("supertype", ""), "image": (c.get("images") or {}).get("small", ""),
                   "image_large": (c.get("images") or {}).get("large", "")} for c in cards],
    }


# ---- collezioni seguite (kv "extra_sets" + "set_json:<id>"; quelle di casa vengono dai file) ----------------
def loaded_ids(db: Database) -> list[str]:
    return [str(i) for i in (db.get_kv("extra_sets", []) or [])]


_HOME: dict[str, CardSet] | None = None


def home_sets() -> dict[str, CardSet]:
    global _HOME
    if _HOME is None:
        _HOME = {s.id: s for s in load_sets().sets}
    return _HOME


def get_set(db: Database, set_id: str, download: bool = True) -> CardSet | None:
    """La collezione, scaricandola se serve. Al primo download tutte le sue carte diventano mancanti."""
    home = home_sets()
    if set_id in home:
        return home[set_id]
    raw = db.get_kv(f"set_json:{set_id}")
    if raw is None:
        if not download:
            return None
        raw = fetch_set_raw(db, set_id)
        db.set_kv(f"set_json:{set_id}", raw)
        ids = loaded_ids(db)
        if set_id not in ids:
            db.set_kv("extra_sets", ids + [set_id])
        if not db.get_kv("collections_v2"):
            db.set_kv("collections_v2", True)  # un database che scarica con questo codice è già nel formato nuovo
        log.info("Collezione %s scaricata: %d carte", set_id, len(raw["cards"]))
    return set_from_raw(raw)


def follow(db: Database, chat_id: str, set_id: str) -> tuple[CardSet, str, bool]:
    """La persona segue la collezione: album suo (creato se manca, parte da "mi mancano tutte").
    Restituisce (set, album_id, creato_adesso)."""
    cs = get_set(db, set_id)
    album, created = db.ensure_album(chat_id, cs.id)
    if created:
        db.set_wanted_bulk([c.id for c in cs.cards], True, album)
    return cs, album, created


def loaded_sets(db: Database) -> list[CardSet]:
    out = []
    for sid in loaded_ids(db):
        cs = get_set(db, sid, download=False)
        if cs:
            out.append(cs)
    return out


def load_index(db: Database) -> CardIndex:
    """Indice di tutte le collezioni seguite da chiunque: quelle di casa (codici brevi) più quelle scaricate."""
    migrate(db)
    home = load_sets()
    return CardIndex(list(home.sets) + loaded_sets(db), home.aliases)


def album_for(db: Database, chat_id: str, set_id: str) -> str:
    """Album della persona per quella collezione (di casa: creato al volo, tutte mancanti)."""
    album, created = db.ensure_album(chat_id, set_id)
    if created:
        cs = get_set(db, set_id, download=False)
        if cs:
            db.set_wanted_bulk([c.id for c in cs.cards], True, album)
    return album


def migrate(db: Database) -> None:
    """Dati delle versioni precedenti: possedute per set (extra_owned) → mancanti in `wanted`; home_active → active_sets."""
    if db.get_kv("collections_v2"):
        return
    owned = db.get_kv("extra_owned", {}) or {}
    for sid in loaded_ids(db):  # collezioni scaricate dalla versione precedente: possedute per set → mancanti
        cs = get_set(db, sid, download=False)
        if not cs:
            continue
        have = {str(n) for n in owned.get(sid, [])}
        album = db.ensure_album(db.owner_chat_id() or "me", sid)[0]
        db.set_wanted_bulk([c.id for c in cs.cards if c.number not in have], True, album)
        db.set_wanted_bulk([c.id for c in cs.cards if c.number in have], False, album)
    if db.get_kv("active_sets") is None:
        settings = db.get_settings()
        active = list(config.HOME_SET_IDS) if settings.get("home_active", True) else []
        active += [str(i) for i in (db.get_kv("extra_active", []) or []) if str(i) not in active]
        db.set_kv("active_sets", active)
    db.set_kv("collections_v2", True)


# ---- stato per album ----------------------------------------------------------------------------------------
def wanted_numbers(db: Database, cs: CardSet, album: str | None = None) -> set[str]:
    wanted = db.wanted_ids([album]) if album else db.wanted_ids()
    return {c.number for c in cs.cards if c.id in wanted}


def owned_numbers(db: Database, cs: CardSet, album: str | None = None) -> set[str]:
    wanted = db.wanted_ids([album]) if album else db.wanted_ids()
    return {c.number for c in cs.cards if c.id not in wanted}


def mark(db: Database, cs: CardSet, numbers: list[str], wanted: bool, album: str | None = None) -> None:
    by_num = {c.number: c.id for c in cs.cards}
    db.set_wanted_bulk([by_num[n] for n in numbers if n in by_num], wanted, album)


def _sorted(nums) -> list[str]:
    return sorted(nums, key=lambda n: (0 if n.isdigit() else 1, int(n) if n.isdigit() else 0, n))


def export(db: Database, index: CardIndex, chat_id: str) -> list[dict]:
    """Per la Mini App di una persona: le sue collezioni non di casa, con possedute, mancanti, stato ricerca, condivisione."""
    act = set(db.active_sets(chat_id))
    albums = db.albums_of(chat_id)
    out = []
    for cs in index.sets:
        if cs.primary or cs.id not in albums:
            continue
        album = albums[cs.id]
        wanted = db.wanted_ids([album])
        members = [m for m in db.album_members(album) if m != chat_id]
        out.append({"id": cs.id, "name": cs.name, "series": cs.series, "logo": cs.logo, "symbol": cs.symbol,
                    "printed_total": cs.printed_total, "total": cs.total, "release": cs.release, "active": cs.id in act,
                    "album": album, "shared_with": [{"id": m, "name": db.user_name(m)} for m in members],
                    "owned": _sorted(c.number for c in cs.cards if c.id not in wanted),
                    "wanted": _sorted(c.number for c in cs.cards if c.id in wanted),
                    "cards": [{"id": c.id, "number": c.number, "name": c.name, "rarity": c.rarity, "image": c.image} for c in cs.cards]})
    return out
