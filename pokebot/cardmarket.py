"""Prezzi Cardmarket (via TCGdex, aggiornati ogni giorno): il riferimento per ogni valutazione di prezzo.

Il sito di Cardmarket blocca i bot; TCGdex (api.tcgdex.net, gratuito) pubblica per ogni carta i prezzi del
prodotto Cardmarket: minimo, trend, medie a 7 e 30 giorni. Le medie degli annunci Vinted/Wallapop/eBay restano
solo come ripiego per le carte che TCGdex non copre.

Regole di prezzo (verdict):
  🔥 affare        prezzo ≤ pct% del trend (70% di default), ≤ minimo Cardmarket, carta da almeno 5 € di trend
  💰 ottimo prezzo prezzo ≤ 85% del trend, carta da almeno 3 €
  👍 sotto valore  prezzo < trend, carta da almeno 2 €
  📌 vicino        fino al 30% sopra il trend, carta da almeno 2 € (solo per non lasciare vuoto il canale)
Sotto il 20% del trend è quasi sempre un errore di riconoscimento o un annuncio sospetto: scartato.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

from . import config
from .db import Database

log = logging.getLogger(__name__)

API = "https://api.tcgdex.net/v2/en"
# collezioni con nomi diversi tra pokemon-tcg-data e TCGdex
OVERRIDES = {"me55": "30th", "me55c": "30th-c"}
MAX_AGE_S = 20 * 3600
MAP_AGE_S = 7 * 86400

DEAL_PCT = 70
DEAL_MIN_TREND = 5.0
GREAT_PCT = 85
GREAT_MIN_TREND = 3.0
GOOD_MIN_TREND = 2.0
FAIR_MAX_RATIO = 1.30  # solo per riempire il canale: Vinted e Wallapop stanno spesso sopra Cardmarket
SUSPICIOUS_RATIO = 0.20

VERDICT_LABEL = {"deal": "🔥 Affare", "great": "💰 Ottimo prezzo", "good": "👍 Sotto il valore", "fair": "📌 Vicino al valore"}


@dataclass
class CMPrice:
    low: float | None
    trend: float | None
    avg7: float | None
    avg30: float | None
    updated: str = ""

    @property
    def ref(self) -> float | None:
        """Valore di riferimento della carta: il trend, altrimenti le medie."""
        for v in (self.trend, self.avg30, self.avg7):
            if v and v > 0:
                return v
        return None


def verdict(price: float | None, cm: CMPrice | None, pct: float = DEAL_PCT, fair: bool = False) -> tuple[str | None, float | None]:
    """('deal'|'great'|'good'|'fair'|None, prezzo/trend). 'fair' solo se richiesto (il canale)."""
    if price is None or price <= 0 or cm is None or not cm.ref:
        return None, None
    ref = cm.ref
    ratio = price / ref
    if ratio < SUSPICIOUS_RATIO:
        return None, ratio
    if ref >= DEAL_MIN_TREND and ratio <= pct / 100 and (not cm.low or price <= cm.low):
        return "deal", ratio
    if ref >= GREAT_MIN_TREND and ratio <= GREAT_PCT / 100:
        return "great", ratio
    if ref >= GOOD_MIN_TREND and ratio < 1:
        return "good", ratio
    if fair and ref >= GOOD_MIN_TREND and ratio <= FAIR_MAX_RATIO:
        return "fair", ratio
    return None, ratio


# ---- lettura -----------------------------------------------------------------------
def get(db: Database, card_id: str) -> CMPrice | None:
    with db.connect() as c:
        r = c.execute("SELECT * FROM cm_prices WHERE card_id = ?", (card_id,)).fetchone()
    return _row(r) if r else None


def all_prices(db: Database) -> dict[str, CMPrice]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM cm_prices WHERE trend IS NOT NULL OR avg30 IS NOT NULL OR avg7 IS NOT NULL").fetchall()
    return {r["card_id"]: _row(r) for r in rows}


def _row(r) -> CMPrice:
    return CMPrice(low=r["low"], trend=r["trend"], avg7=r["avg7"], avg30=r["avg30"], updated=r["updated"] or "")


# ---- aggiornamento -----------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _num(s: str) -> str:
    s = (s or "").strip().upper()
    return re.sub(r"^0+(?=\d)", "", s)


def _http_get(url: str):
    resp = requests.get(url, timeout=config.HTTP_TIMEOUT, headers={"User-Agent": "Pokebot/1.0"})
    resp.raise_for_status()
    return resp.json()


def set_map(db: Database, sets, get=_http_get) -> dict[str, str]:
    """Collezione nostra → collezione TCGdex (per nome e numero di carte; ricalcolata ogni settimana)."""
    cache = db.get_kv("tcgdex_setmap") or {}
    known = cache.get("map") or {}
    tried = set(cache.get("tried") or [])  # già cercate senza trovarle: si riprova solo con la mappa scaduta
    fresh = time.time() - float(cache.get("ts") or 0) < MAP_AGE_S
    if fresh and all(s.id in known or s.id in tried for s in sets):
        return known
    try:
        listing = get(f"{API}/sets")
    except Exception as exc:  # noqa: BLE001
        log.warning("TCGdex elenco collezioni: %s", exc)
        return known
    by_id = {x["id"]: x for x in listing}
    by_name: dict[str, list[dict]] = {}
    for x in listing:
        by_name.setdefault(_norm(x.get("name", "")), []).append(x)
    out = dict(known)
    for s in sets:
        if s.id in OVERRIDES and OVERRIDES[s.id] in by_id:
            out[s.id] = OVERRIDES[s.id]
            continue
        cands = by_name.get(_norm(s.name), [])
        if len(cands) > 1:  # stesso nome: decide il numero di carte
            total = len(s.cards) or s.total
            cands.sort(key=lambda x: abs(int((x.get("cardCount") or {}).get("total") or 0) - int(total or 0)))
        if cands:
            out[s.id] = cands[0]["id"]
        elif s.id in by_id:  # stesso id su TCGdex (nomi diversi, es. "Base" / "Base Set")
            out[s.id] = s.id
    db.set_kv("tcgdex_setmap", {"ts": cache.get("ts") if fresh else time.time(), "map": out,
                                "tried": sorted({s.id for s in sets} - set(out))})
    return out


def card_map(db: Database, tcg_set: str, get=_http_get) -> list[dict]:
    """Carte TCGdex di una collezione: [{id, num, name}] (ricalcolate ogni settimana)."""
    key = f"tcgdex_cards2:{tcg_set}"
    cache = db.get_kv(key) or {}
    if cache.get("cards") is not None and time.time() - float(cache.get("ts") or 0) < MAP_AGE_S:
        return cache["cards"]
    data = get(f"{API}/sets/{tcg_set}")
    cards = [{"id": c["id"], "num": _num(c.get("localId", "")), "name": _norm(c.get("name", ""))}
             for c in (data.get("cards") or []) if c.get("id")]
    db.set_kv(key, {"ts": time.time(), "cards": cards})
    return cards


def match_card(card, tcg_cards: list[dict], name_unique: bool = True) -> str | None:
    """Stesso nome e numero; se i numeri non coincidono (ristampe come la Classic Collection), il nome se è unico
    sia da TCGdex sia nella nostra collezione (`name_unique`: due Lapras diversi non prendono lo stesso prezzo).
    Prima il nome identico, poi quello simile ("Palkia LV.X" ↔ "Palkia"); senza nome, solo il numero."""
    name, num = _norm(card.name), _num(card.number)
    exact = [c for c in tcg_cards if c["name"] and c["name"] == name]
    similar = [c for c in tcg_cards if c["name"] and name and c not in exact
               and (c["name"].startswith(name) or name.startswith(c["name"]))]
    for group in (exact, similar):
        for c in group:
            if c["num"] == num:
                return c["id"]
        if len(group) == 1 and name_unique:
            return group[0]["id"]
        if group:
            return None  # più carte con quel nome e nessuna con quel numero: meglio nessun prezzo che uno sbagliato
    by_num = [c for c in tcg_cards if c["num"] == num and not c["name"]]
    return by_num[0]["id"] if len(by_num) == 1 else None


def _parse(pricing: dict | None) -> CMPrice | None:
    cm = (pricing or {}).get("cardmarket") or {}
    if not cm:
        return None

    def pick(*keys):
        for k in keys:
            v = cm.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return None
    return CMPrice(low=pick("low", "low-holo"), trend=pick("trend", "trend-holo"), avg7=pick("avg7", "avg7-holo"),
                   avg30=pick("avg30", "avg30-holo"), updated=str(cm.get("updated") or "")[:10])


def refresh(db: Database, index, budget: int = 250, wanted_first: set[str] | None = None, get=_http_get,
            now: float | None = None) -> str:
    """Aggiorna i prezzi delle carte più vecchie di 20 ore (prima le mancanti), al massimo `budget` richieste."""
    now = now or time.time()
    sets = list(index.sets)
    smap = set_map(db, sets, get)
    with db.connect() as c:
        fetched = {r["card_id"]: r["fetched"] for r in c.execute("SELECT card_id, fetched FROM cm_prices")}
    todo = [card for s in sets if s.id in smap for card in s.cards if now - float(fetched.get(card.id, 0)) > MAX_AGE_S]
    wanted_first = wanted_first or set()
    todo.sort(key=lambda card: (card.id not in wanted_first, float(fetched.get(card.id, 0))))
    todo = todo[:budget]
    if not todo:
        return ""
    cmaps: dict[str, list[dict]] = {}
    names = Counter((s.id, _norm(c.name)) for s in sets for c in s.cards)
    jobs = []
    for card in todo:
        tset = smap[card.set_id]
        if tset not in cmaps:
            try:
                cmaps[tset] = card_map(db, tset, get)
            except Exception as exc:  # noqa: BLE001 - si riprova al prossimo giro, senza toccare i prezzi che ci sono
                log.warning("TCGdex collezione %s: %s", tset, exc)
                cmaps[tset] = None
        if cmaps[tset] is not None:
            jobs.append((card, match_card(card, cmaps[tset], names[(card.set_id, _norm(card.name))] == 1)))

    def one(job):
        card, tid = job
        if not tid:
            return card.id, None, None
        try:
            return card.id, tid, _parse(get(f"{API}/cards/{tid}").get("pricing"))
        except Exception as exc:  # noqa: BLE001
            log.debug("TCGdex carta %s: %s", tid, exc)
            return card.id, tid, "errore"

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(one, jobs))
    ok = 0
    with db.connect() as c:
        for cid, tid, p in results:
            if p == "errore":
                continue  # si riprova al prossimo giro
            p = p or CMPrice(None, None, None, None)
            ok += int(bool(p.ref))
            c.execute("INSERT INTO cm_prices(card_id, tcgdex_id, low, trend, avg7, avg30, updated, fetched) VALUES (?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(card_id) DO UPDATE SET tcgdex_id=excluded.tcgdex_id, low=excluded.low, trend=excluded.trend, "
                      "avg7=excluded.avg7, avg30=excluded.avg30, updated=excluded.updated, fetched=excluded.fetched",
                      (cid, tid, p.low, p.trend, p.avg7, p.avg30, p.updated, now))
    return f"{len(results)} carte aggiornate, {ok} con prezzo Cardmarket"
