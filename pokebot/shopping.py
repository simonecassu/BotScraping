"""Lista della spesa: il paniere più economico, tra gli annunci visti di recente, per chiudere una collezione.

Carte singole al prezzo minimo; un lotto entra se costa meno della somma delle singole che coprirebbe (o se copre
carte altrimenti introvabili). Il risultato è raggruppato per venditore, così si risparmia sulle spedizioni.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .cards import Card
from .scrapers.base import parse_price

SOURCE_LABELS = {"ebay": "eBay.it", "vinted": "Vinted", "wallapop": "Wallapop"}


@dataclass
class Pick:
    row: dict
    cards: list[Card]
    price: float

    @property
    def seller_key(self) -> str:
        seller = (self.row.get("seller") or "").strip()
        return f"{self.row['source']}:{seller}" if seller else f"{self.row['source']}:#{self.row['listing_key']}"


@dataclass
class ShoppingList:
    picks: list[Pick] = field(default_factory=list)
    uncovered: list[Card] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(p.price for p in self.picks)

    @property
    def covered(self) -> int:
        return sum(len(p.cards) for p in self.picks)

    def by_seller(self) -> list[tuple[str, list[Pick]]]:
        groups: dict[str, list[Pick]] = {}
        for p in self.picks:
            groups.setdefault(p.seller_key, []).append(p)
        # prima i venditori con più carte, poi i più economici
        return sorted(groups.items(), key=lambda kv: (-sum(len(p.cards) for p in kv[1]), sum(p.price for p in kv[1])))

    def seller_label(self, key: str) -> str:
        source, _, seller = key.partition(":")
        name = SOURCE_LABELS.get(source, source)
        return f"{name} · {seller}" if seller and not seller.startswith("#") else name


def build(cards: list[Card], wanted_ids: set[str], rows: list[dict], days: int = 14) -> ShoppingList:
    """`cards`: le carte della collezione; `rows`: righe di `found` (annunci visti)."""
    by_id = {c.id: c for c in cards}
    missing = {cid for cid in wanted_ids if cid in by_id}
    cutoff = time.time() - days * 86400
    best_single: dict[str, tuple[float, dict]] = {}
    lots: list[tuple[float, set[str], dict]] = []
    for r in rows:
        if float(r.get("created_at") or 0) < cutoff:
            continue
        price = parse_price(r.get("price"))
        if price is None or price <= 0:
            continue
        ids = {m["id"] for m in r.get("matched", []) if m.get("sure", True) and m["id"] in missing}
        if not ids:
            continue
        if r.get("kind") == "lot":
            lots.append((price, ids, r))
        else:
            for cid in ids:
                if cid not in best_single or price < best_single[cid][0]:
                    best_single[cid] = (price, r)
    out = ShoppingList()
    covered: set[str] = set()
    for price, ids, r in sorted(lots, key=lambda t: t[0] / len(t[1])):
        new = ids - covered
        if not new:
            continue
        alternative = sum(best_single[c][0] if c in best_single else price for c in new)  # carta introvabile: vale il lotto
        if price <= alternative:
            out.picks.append(Pick(r, sorted((by_id[c] for c in new), key=lambda c: c.sort_key), price))
            covered |= new
    for cid in sorted(missing - covered, key=lambda c: by_id[c].sort_key):
        if cid in best_single:
            price, r = best_single[cid]
            out.picks.append(Pick(r, [by_id[cid]], price))
            covered.add(cid)
        else:
            out.uncovered.append(by_id[cid])
    return out
