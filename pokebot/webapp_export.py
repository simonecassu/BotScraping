"""Riepilogo JSON dello stato per la Mini App Telegram (pubblicato nel branch bot-state a ogni giro)."""
from __future__ import annotations

import json
import time

from . import config
from . import stats as pstats
from . import watch
from . import collections as coll
from .cards import CardIndex
from .db import Database
from .scrapers.base import parse_price

SETTING_KEYS = ["interval_minutes", "lot_min_ratio", "max_price", "max_per_card", "sources", "language",
                "deal_pct", "images", "paused", "quiet_hours", "per_card_queries", "only_italy", "home_active"]


def build_state(index: CardIndex, db: Database, max_found: int = 300, chat_id: str = "") -> dict:
    """Stato per la Mini App di una persona (chat_id); senza chat: il proprietario."""
    from . import collections as coll
    chat_id = chat_id or db.owner_chat_id() or "me"
    for sid in config.HOME_SET_IDS:
        coll.album_for(db, chat_id, sid)
    wanted = db.wanted_for(chat_id)
    rows = db.list_found()
    settings = db.settings_for(chat_id)
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
    active = db.active_sets(chat_id)
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
        "me": {"id": chat_id, "name": db.user_name(chat_id)},
        "friends": [{"id": f, "name": db.user_name(f),
                     "shared": [sid for sid, alb in db.albums_of(chat_id).items() if f in db.album_members(alb)]}
                    for f in db.friends(chat_id)],
        "friend_code": db.friend_code(chat_id),
        "bot_username": db.get_kv("bot_username") or "",
        "home_album": {sid: {"album": db.albums_of(chat_id).get(sid),
                             "shared_with": [{"id": m, "name": db.user_name(m)} for m in db.album_members(db.albums_of(chat_id).get(sid, "")) if m != chat_id]}
                       for sid in config.HOME_SET_IDS},
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
                     "chat": chat_id,
                     "code": (lambda c: watch.code_for(index, c) if c else cid)(watch.resolve_card(index, db, cid)[0]),
                     "started": w.get("started"), "until": w.get("until"),
                     "every": w.get("every"), "checks": w.get("checks", 0), "found": w.get("found", 0)}
                    for cid, w in watch.list_watches(db, chat_id).items()],
        "watch_found": list(reversed(watch.found_log(db, chat_id))),
        "collections": coll.export(db, index, chat_id),
        "values": _values(index, wanted, all_rows, db.albums_of(chat_id)),
        "shopping": _shopping(index, wanted, rows, db.albums_of(chat_id)),
        "copies": db.copies(chat_id),
        "active_sets": active,
        "current_set": db.current_set(chat_id),
    }


def _groups(index: CardIndex, albums: dict | None = None) -> dict[str, list]:
    """Collezioni come le vede la Mini App: "home" = quelle di casa insieme, le altre (della persona) una per una."""
    out: dict[str, list] = {"home": [c for s in index.sets if s.primary for c in s.cards]}
    for s in index.sets:
        if not s.primary and (albums is None or s.id in albums):
            out[s.id] = list(s.cards)
    return out


def _values(index: CardIndex, wanted: set[str], all_rows: list[dict], albums: dict | None = None) -> dict:
    out = {}
    for key, cards in _groups(index, albums).items():
        v = pstats.collection_value(cards, wanted, all_rows)
        out[key] = {"owned": v.owned, "owned_priced": v.owned_priced, "owned_value": round(v.owned_value, 2),
                    "missing": v.missing, "missing_priced": v.missing_priced, "missing_cost": round(v.missing_cost, 2)}
    return out


def _shopping(index: CardIndex, wanted: set[str], rows: list[dict], albums: dict | None = None) -> dict:
    from . import shopping
    out = {}
    for key, cards in _groups(index, albums).items():
        sl = shopping.build(cards, wanted, rows)
        out[key] = {"total": round(sl.total, 2), "covered": sl.covered, "uncovered": [c.id for c in sl.uncovered],
                    "sellers": [{"label": sl.seller_label(k), "total": round(sum(p.price for p in picks), 2),
                                 "picks": [{"price": p.price, "lot": p.row.get("kind") == "lot", "url": p.row["url"], "title": p.row["title"],
                                            "image": p.row.get("image") or "", "cards": [c.id for c in p.cards], "key": p.row["listing_key"]} for p in picks]}
                                for k, picks in sl.by_seller()]}
    return out


def build_summary(index: CardIndex, db: Database) -> dict:
    """state.json: solo ciò che serve al ponte (timer, persone collegate), senza dati personali."""
    any_watch = [{"until": w.get("until")} for _, ws in watch.all_watches(db) for w in ws.values()]
    return {
        "generated_at": time.time(),
        "owner_chat_id": db.owner_chat_id(),
        "chat_ids": db.chat_ids(),
        "last_search_ts": float(db.get_kv("last_search_ts", 0) or 0),
        "queued": len(db.queued_found()),
        "watches": any_watch,
        "settings": {k: db.get_settings().get(k) for k in ("interval_minutes", "paused", "quiet_hours")},
        "per_user": True,
        "channel": {"set": bool(db.get_kv("deals_channel")), "hour": int(db.get_kv("deals_channel_hour", 19) or 19),
                    "last": db.get_kv("deals_channel_last") or ""},
        "waitlist": len(db.waitlist()),
    }


def write_state(index: CardIndex, db: Database, path: str) -> None:
    """Scrive state.json (riepilogo) e state-<chat>.json per ogni persona collegata nella stessa cartella."""
    import os
    folder = os.path.dirname(path) or "."
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_summary(index, db), f, ensure_ascii=False)
    for chat in (db.chat_ids() or [db.owner_chat_id() or "me"]):
        with open(os.path.join(folder, f"state-{chat}.json"), "w", encoding="utf-8") as f:
            json.dump(build_state(index, db, chat_id=chat), f, ensure_ascii=False)
