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
        sets.append({
            "id": s.id, "name": s.name, "name_it": s.name_it, "printed_total": s.printed_total,
            "cards": [{"id": c.id, "code": index.code_of[c.id], "number": c.number, "name": c.name,
                       "rarity": c.rarity, "image": c.image} for c in s.cards],
        })
    prices = {}
    for c in index.by_id.values():
        cp = pstats.card_prices(rows, c)
        if cp.overall.n:
            prices[c.id] = {
                "n": cp.overall.n, "min": cp.overall.min, "median": cp.overall.median, "max": cp.overall.max,
                "recent_median": cp.recent_median, "older_median": cp.older_median,
                "by_source": {src: {"n": st.n, "min": st.min, "median": st.median, "max": st.max}
                              for src, st in cp.by_source.items()},
            }
    comp = pstats.completion(index, wanted, rows)
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
        "collections": coll.export(db),
    }


def write_state(index: CardIndex, db: Database, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(build_state(index, db), fh, ensure_ascii=False, separators=(",", ":"))
