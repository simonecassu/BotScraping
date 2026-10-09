"""Statistiche prezzi: annunci visti dal bot (tabelle found e prices) e, dove c'è, il valore Cardmarket."""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

from .cards import Card, CardIndex
from .scrapers.base import parse_price

SOURCE_ORDER = ["ebay", "vinted", "wallapop"]
SOURCE_LABELS = {"ebay": "eBay.it", "vinted": "Vinted", "wallapop": "Wallapop"}


@dataclass
class PriceStats:
    n: int = 0
    min: float | None = None
    median: float | None = None
    max: float | None = None
    last_seen: float | None = None  # timestamp dell'annuncio più recente


@dataclass
class CardPrices:
    card: Card
    overall: PriceStats = field(default_factory=PriceStats)
    by_source: dict[str, PriceStats] = field(default_factory=dict)
    recent_median: float | None = None  # mediana degli ultimi 7 giorni
    older_median: float | None = None   # mediana di quelli precedenti


def _stats(points: list[tuple[float, float]]) -> PriceStats:
    if not points:
        return PriceStats()
    prices = [p for p, _ in points]
    return PriceStats(n=len(prices), min=min(prices), median=statistics.median(prices), max=max(prices),
                      last_seen=max(ts for _, ts in points))


class PriceRows(list):
    """Righe di annunci (found + punti prezzo) con l'indice per carta calcolato una volta sola: le statistiche
    di centinaia di carte costano una passata sulle righe invece di una per carta."""
    _by_card: dict | None = None


def _build_index(rows: list[dict]) -> dict[str, dict[str, list[tuple[float, float]]]]:
    idx: dict[str, dict[str, list[tuple[float, float]]]] = {}
    for r in rows:
        if r.get("kind") == "lot":
            continue
        price = parse_price(r.get("price"))
        if price is None or price <= 0:
            continue
        for cid in {m.get("id") for m in r.get("matched", []) if m.get("sure", True)}:
            idx.setdefault(cid, {}).setdefault(r["source"], []).append((price, float(r["created_at"])))
    return idx


def price_points(rows: list[dict], card_id: str) -> dict[str, list[tuple[float, float]]]:
    """Per fonte: lista di (prezzo, timestamp) delle carte singole riconosciute con certezza."""
    if isinstance(rows, PriceRows):
        if rows._by_card is None:
            rows._by_card = _build_index(rows)
        return rows._by_card.get(card_id, {})
    return _build_index(rows).get(card_id, {})


def card_prices(rows: list[dict], card: Card) -> CardPrices:
    pts = price_points(rows, card.id)
    cp = CardPrices(card=card)
    all_pts = [p for lst in pts.values() for p in lst]
    cp.overall = _stats(all_pts)
    cp.by_source = {src: _stats(lst) for src, lst in pts.items()}
    cutoff = time.time() - 7 * 86400
    recent = [p for p, ts in all_pts if ts >= cutoff]
    older = [p for p, ts in all_pts if ts < cutoff]
    cp.recent_median = statistics.median(recent) if recent else None
    cp.older_median = statistics.median(older) if older else None
    return cp


def median_for_deal(rows: list[dict], card_id: str, exclude_keys: set[str] | None = None, min_points: int = 4) -> float | None:
    """Mediana storica usata per riconoscere un affare; None se i dati sono troppo pochi."""
    prices = []
    for r in rows:
        if r.get("kind") == "lot" or (exclude_keys and r.get("listing_key") in exclude_keys):
            continue
        if not any(m.get("id") == card_id and m.get("sure", True) for m in r.get("matched", [])):
            continue
        price = parse_price(r.get("price"))
        if price is not None and price > 0:
            prices.append(price)
    if len(prices) < min_points:
        return None
    return statistics.median(prices)


@dataclass
class Completion:
    total: int
    missing: int
    priced: int            # carte mancanti con un prezzo (Cardmarket o, in mancanza, annunci visti)
    cost_min: float        # somma dei minimi (Cardmarket, altrimenti annunci)
    cost_median: float     # somma dei valori di mercato (trend Cardmarket, altrimenti mediana degli annunci)
    unpriced: list[Card] = field(default_factory=list)
    cm_priced: int = 0     # di cui con prezzo Cardmarket (le altre: annunci visti)
    by_rarity: dict[str, tuple[int, int]] = field(default_factory=dict)  # rarità -> (mancanti, totale)

    @property
    def owned(self) -> int:
        return self.total - self.missing

    @property
    def percent(self) -> float:
        return 100.0 * self.owned / self.total if self.total else 0.0


def _cm_ref(cm: dict | None, card_id: str) -> tuple[float | None, float | None]:
    """(minimo, trend) Cardmarket della carta, se noti."""
    p = (cm or {}).get(card_id)
    if not p or not p.ref:
        return None, None
    return (p.low or p.ref), p.ref


def completion(index: CardIndex, wanted_ids: set[str], rows: list[dict], cm: dict | None = None) -> Completion:
    """Quanto manca: prezzi Cardmarket (minimo e trend); per le carte senza, minimo e mediana degli annunci visti."""
    cards = list(index.by_id.values())
    missing = [c for c in cards if c.id in wanted_ids]
    cost_min = cost_med = 0.0
    priced = cm_priced = 0
    unpriced: list[Card] = []
    for c in missing:
        low, trend = _cm_ref(cm, c.id)
        if trend:
            priced += 1
            cm_priced += 1
            cost_min += low
            cost_med += trend
            continue
        st = card_prices(rows, c).overall
        if st.n and st.min is not None and st.median is not None:
            priced += 1
            cost_min += st.min
            cost_med += st.median
        else:
            unpriced.append(c)
    by_rarity: dict[str, tuple[int, int]] = {}
    for c in cards:
        m, t = by_rarity.get(c.rarity, (0, 0))
        by_rarity[c.rarity] = (m + (1 if c.id in wanted_ids else 0), t + 1)
    return Completion(total=len(cards), missing=len(missing), priced=priced, cost_min=cost_min, cost_median=cost_med,
                      unpriced=sorted(unpriced, key=lambda c: c.sort_key), by_rarity=by_rarity, cm_priced=cm_priced)


@dataclass
class Value:
    owned: int = 0
    owned_priced: int = 0
    owned_value: float = 0.0   # valore delle possedute (trend Cardmarket, altrimenti mediana degli annunci)
    missing: int = 0
    missing_priced: int = 0
    missing_cost: float = 0.0  # costo delle mancanti (trend Cardmarket, altrimenti minimo degli annunci)


def collection_value(cards: list[Card], wanted_ids: set[str], rows: list[dict], cm: dict | None = None) -> Value:
    """Valore delle possedute e costo delle mancanti al trend Cardmarket; senza prezzo Cardmarket, gli annunci visti."""
    v = Value()
    for c in cards:
        _, trend = _cm_ref(cm, c.id)
        if trend:
            if c.id in wanted_ids:
                v.missing += 1
                v.missing_priced += 1
                v.missing_cost += trend
            else:
                v.owned += 1
                v.owned_priced += 1
                v.owned_value += trend
            continue
        st = card_prices(rows, c).overall
        if c.id in wanted_ids:
            v.missing += 1
            if st.n and st.min is not None:
                v.missing_priced += 1
                v.missing_cost += st.min
        else:
            v.owned += 1
            if st.n and st.median is not None:
                v.owned_priced += 1
                v.owned_value += st.median
    return v


def fmt_eur(v: float | None) -> str:
    if v is None:
        return "n.d."
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
