"""Altre collezioni: catalogo pubblico (pokemon-tcg-data), download su richiesta, possedute e attivazione per set.

Le collezioni "di casa" (data/sets) restano quelle del bot. Per tutte le altre il punto di partenza è "mi mancano
tutte": si segnano le carte possedute con /collezione <id> ho N..., e vengono cercate solo se attivate con
/collezione <id> attiva. La 30th si spegne/accende con /collezione me55 disattiva|attiva (impostazione home_active).
"""
from __future__ import annotations

import logging
import time

import requests

from . import config
from .cards import CardSet, set_from_raw
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
    """Scarica una collezione dal catalogo pubblico e la converte nel nostro formato."""
    meta = next((s for s in catalog(db) if s["id"] == set_id), None)
    if not meta:
        raise KeyError(set_id)
    cards = _get_json([u.format(id=set_id) for u in CARDS_URLS])
    kws = [meta["name"].lower()]
    if meta.get("ptcgo"):
        kws.append(meta["ptcgo"].lower())
    return {
        "id": set_id, "name": meta["name"], "name_it": meta["name"], "printed_total": meta.get("printed_total"),
        "total": meta.get("total") or len(cards), "release": meta.get("release", ""), "series": meta.get("series", ""),
        "logo": meta.get("logo", ""), "symbol": meta.get("symbol", ""), "context_keywords": kws,
        "cards": [{"id": c["id"], "number": str(c["number"]), "name": c["name"], "rarity": c.get("rarity", ""),
                   "supertype": c.get("supertype", ""), "image": (c.get("images") or {}).get("small", ""),
                   "image_large": (c.get("images") or {}).get("large", "")} for c in cards],
    }


# ---- stato in kv ------------------------------------------------------------------
def loaded_ids(db: Database) -> list[str]:
    ids = db.get_kv("extra_sets", []) or []
    return [str(i) for i in ids]


def get_set(db: Database, set_id: str, download: bool = True) -> CardSet | None:
    raw = db.get_kv(f"set_json:{set_id}")
    if raw is None:
        if not download:
            return None
        raw = fetch_set_raw(db, set_id)
        db.set_kv(f"set_json:{set_id}", raw)
        ids = loaded_ids(db)
        if set_id not in ids:
            db.set_kv("extra_sets", ids + [set_id])
        log.info("Collezione %s scaricata: %d carte", set_id, len(raw["cards"]))
    return set_from_raw(raw)


def loaded_sets(db: Database) -> list[CardSet]:
    out = []
    for sid in loaded_ids(db):
        cs = get_set(db, sid, download=False)
        if cs:
            out.append(cs)
    return out


def owned_numbers(db: Database, set_id: str) -> set[str]:
    """Carte possedute di una collezione extra. Il punto di partenza è "mi mancano tutte": si segnano quelle che si hanno."""
    o = db.get_kv("extra_owned", {}) or {}
    return {str(n) for n in o.get(set_id, [])}


def wanted_numbers(db: Database, set_id: str) -> set[str]:
    """Mancanti = tutte le carte della collezione meno quelle possedute (serve il set scaricato)."""
    cs = get_set(db, set_id, download=False)
    if not cs:
        return set()
    return {c.number for c in cs.cards} - owned_numbers(db, set_id)


def _sorted(nums) -> list[str]:
    return sorted(nums, key=lambda n: (0 if n.isdigit() else 1, int(n) if n.isdigit() else 0, n))


def mark(db: Database, set_id: str, numbers: list[str], wanted: bool) -> None:
    """wanted=True → mancanti (tolte dalle possedute); wanted=False → possedute."""
    o = db.get_kv("extra_owned", {}) or {}
    cur = {str(n) for n in o.get(set_id, [])}
    cur = cur - set(numbers) if wanted else cur | set(numbers)
    o[set_id] = _sorted(cur)
    db.set_kv("extra_owned", o)


def active_ids(db: Database) -> list[str]:
    return [str(i) for i in (db.get_kv("extra_active", []) or [])]


def set_active(db: Database, set_id: str, on: bool) -> None:
    ids = [i for i in active_ids(db) if i != set_id]
    if on:
        ids.append(set_id)
    db.set_kv("extra_active", ids)


def active_search_sets(db: Database) -> tuple[list[CardSet], set[str]]:
    """Collezioni extra attive e le loro carte mancanti (id completi), da aggiungere alla ricerca."""
    sets, ids = [], set()
    for sid in active_ids(db):
        cs = get_set(db, sid, download=False)
        if not cs:
            continue
        nums = wanted_numbers(db, sid)
        if not nums:
            continue
        sets.append(cs)
        ids |= {c.id for c in cs.cards if c.number in nums}
    return sets, ids


def export(db: Database) -> list[dict]:
    """Per la Mini App: collezioni extra scaricate, con mancanti e stato ricerca."""
    act = set(active_ids(db))
    out = []
    for cs in loaded_sets(db):
        out.append({"id": cs.id, "name": cs.name, "series": cs.series, "logo": cs.logo, "symbol": cs.symbol,
                    "printed_total": cs.printed_total, "total": cs.total, "release": cs.release, "active": cs.id in act,
                    "owned": _sorted(owned_numbers(db, cs.id)), "wanted": _sorted(wanted_numbers(db, cs.id)),
                    "cards": [{"id": c.id, "number": c.number, "name": c.name, "rarity": c.rarity, "image": c.image} for c in cs.cards]})
    return out
