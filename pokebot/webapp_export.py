"""Riepilogo JSON dello stato per la Mini App Telegram (pubblicato nel branch bot-state a ogni giro)."""
from __future__ import annotations

import json
import time

from . import stats as pstats
from . import watch
from . import collections as coll
from .cards import CardIndex
from .db import Database
from .scrapers.base import parse_price

SETTING_KEYS = ["interval_minutes", "lot_min_ratio", "max_price", "max_per_card", "sources", "language",
                "deal_pct", "images", "paused", "quiet_hours", "per_card_queries", "only_italy", "home_active"]


def build_state(index: CardIndex, db: Database, max_found: int = 300) -> dict:
    wanted = db.wanted_ids()
    rows = db.list_found()
    settings = db.get_settings()
    sets = []
    for s in index.sets:
        if not s.primary:
            continue  # le altre collezioni seguite viaggiano in "collections"
        sets.append({
            "id": s.id, "name": s.name, "name_it": s.name_it, "printed_total": s.printed_total,
            "cards": [{"id": c.id, "code": index.code_of[c.id], "number": c.number, "name": c.name,
                       "rarity": c.rarity, "image": c.image} for c in s.cards],
        })
    all_rows = rows + db.price_rows()
    prices = {}
    for c in index.by_id.values():
        cp = pstats.card_prices(all_rows, c)
        if cp.overall.n:
            prices[c.id] = {
                "n": cp.overall.n, "min": cp.overall.min, "median": cp.overall.median, "max": cp.overall.max,
                "recent_median": cp.recent_median, "older_median": cp.older_median,
                "by_source": {src: {"n": st.n, "min": st.min, "median": st.median, "max": st.max}
                              for src, st in cp.by_source.items()},
            }
    from .cards import CardIndex
    home_index = CardIndex([s for s in index.sets if s.primary], index.aliases)
    comp = pstats.completion(home_index, wanted, rows)
    active = db.active_sets()
    settings = dict(settings, home_active=any(s.id in active for s in home_index.sets))
    runs = db.last_runs(1)
    last = runs[0] if runs else None
    found = []
    for r in sorted(rows, key=lambda r: -r["created_at"])[:max_found]:
        found.append({
            "id": r["id"], "key": r["listing_key"], "source": r["source"], "title": r["title"], "url": r["url"],
            "price": r.get("price") or "", "price_num": parse_price(r.get("price")), "location": r.get("location") or "",
            "kind": r["kind"], "cards": [m["id"] for m in r["matched"]], "sure": [m["id"] for m in r["matched"] if m.get("sure", True)],
            "notified": bool(r.get("notified")), "deal": bool(r.get("deal")), "image": r.get("image") or "",
            "ts": r["created_at"],
        })
    return {
        "generated_at": time.time(),
        "owner_chat_id": db.owner_chat_id(),
        "chat_ids": db.chat_ids(),
        # usati dal ponte per decidere se svegliare GitHub a ogni tick di 5 minuti
        "last_search_ts": float(db.get_kv("last_search_ts", 0) or 0),
        "queued": len(db.queued_found()),
        "query_stats": db.get_kv("query_stats", {}) or {},
        "wanted": sorted(wanted),
        "settings": {k: settings.get(k) for k in SETTING_KEYS},
        "sets": sets,
        "prices": prices,
        "completion": {"total": comp.total, "missing": comp.missing, "owned": comp.owned, "percent": comp.percent,
                       "priced": comp.priced, "cost_min": comp.cost_min, "cost_median": comp.cost_median,
                       "by_rarity": {k: list(v) for k, v in comp.by_rarity.items()}},
        "last_run": ({"started_at": last["started_at"], "listings_seen": last["listings_seen"], "matches": last["matches"],
                      "errors": last["errors"], "per_source": last.get("per_source") or {}} if last else None),
        "found": found,
        "watches": [{"card": cid, "label": (lambda c: c.label if c else cid)(watch.resolve_card(index, db, cid)[0]),
                     "code": (lambda c: watch.code_for(index, c) if c else cid)(watch.resolve_card(index, db, cid)[0]),
                     "started": w.get("started"), "until": w.get("until"),
                     "every": w.get("every"), "checks": w.get("checks", 0), "found": w.get("found", 0)}
                    for cid, w in watch.list_watches(db).items()],
        "watch_found": list(reversed(watch.found_log(db))),
        "collections": coll.export(db, index),
        "values": _values(index, wanted, all_rows),
        "shopping": _shopping(index, wanted, rows),
        "copies": db.copies(),
        "active_sets": active,
        "current_set": db.current_set(),
    }


def _groups(index: CardIndex) -> dict[str, list]:
    """Collezioni come le vede la Mini App: "home" = quelle di casa insieme, le altre una per una."""
    out: dict[str, list] = {"home": [c for s in index.sets if s.primary for c in s.cards]}
    for s in index.sets:
        if not s.primary:
            out[s.id] = list(s.cards)
    return out


def _values(index: CardIndex, wanted: set[str], all_rows: list[dict]) -> dict:
    out = {}
    for key, cards in _groups(index).items():
        v = pstats.collection_value(cards, wanted, all_rows)
        out[key] = {"owned": v.owned, "owned_priced": v.owned_priced, "owned_value": round(v.owned_value, 2),
                    "missing": v.missing, "missing_priced": v.missing_priced, "missing_cost": round(v.missing_cost, 2)}
    return out


def _shopping(index: CardIndex, wanted: set[str], rows: list[dict]) -> dict:
    from . import shopping
    out = {}
    for key, cards in _groups(index).items():
        sl = shopping.build(cards, wanted, rows)
        out[key] = {"total": round(sl.total, 2), "covered": sl.covered, "uncovered": [c.id for c in sl.uncovered],
                    "sellers": [{"label": sl.seller_label(k), "total": round(sum(p.price for p in picks), 2),
                                 "picks": [{"price": p.price, "lot": p.row.get("kind") == "lot", "url": p.row["url"], "title": p.row["title"],
                                            "image": p.row.get("image") or "", "cards": [c.id for c in p.cards], "key": p.row["listing_key"]} for p in picks]}
                                for k, picks in sl.by_seller()]}
    return out


def write_state(index: CardIndex, db: Database, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(build_state(index, db), fh, ensure_ascii=False, separators=(",", ":"))
