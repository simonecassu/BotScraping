"""Statistiche prezzi dallo storico degli annunci trovati (tabella found)."""
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


def price_points(rows: list[dict], card_id: str) -> dict[str, list[tuple[float, float]]]:
    """Per fonte: lista di (prezzo, timestamp) delle carte singole riconosciute con certezza."""
    out: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        if r.get("kind") == "lot":
            continue
        if not any(m.get("id") == card_id and m.get("sure", True) for m in r.get("matched", [])):
            continue
        price = parse_price(r.get("price"))
        if price is None or price <= 0:
            continue
        out.setdefault(r["source"], []).append((price, float(r["created_at"])))
    return out


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
    priced: int            # carte mancanti con almeno un prezzo visto
    cost_min: float        # somma dei minimi visti
    cost_median: float     # somma delle mediane viste
    unpriced: list[Card] = field(default_factory=list)
    by_rarity: dict[str, tuple[int, int]] = field(default_factory=dict)  # rarità -> (mancanti, totale)

    @property
    def owned(self) -> int:
        return self.total - self.missing

    @property
    def percent(self) -> float:
        return 100.0 * self.owned / self.total if self.total else 0.0


def completion(index: CardIndex, wanted_ids: set[str], rows: list[dict]) -> Completion:
    cards = list(index.by_id.values())
    missing = [c for c in cards if c.id in wanted_ids]
    cost_min = cost_med = 0.0
    priced = 0
    unpriced: list[Card] = []
    for c in missing:
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
                      unpriced=sorted(unpriced, key=lambda c: c.sort_key), by_rarity=by_rarity)


def fmt_eur(v: float | None) -> str:
    if v is None:
        return "n.d."
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
