"""Stato per la Mini App e per il ponte, pubblicato (cifrato, vedi vault.py) nel branch bot-state a ogni giro.

- `state.json.enc`: il riepilogo che serve al ponte (chi è collegato, quando svegliare il bot);
- `state-<impronta>.json.enc`: lo stato di una persona per la sua Mini App (solo i suoi dati); il nome del file è
  un'impronta del chat id (`vault.file_id`), così nel branch pubblico non si vede chi usa il bot.
"""
from __future__ import annotations

import json
import logging
import os
import time

from . import cardmarket, channel, config, plans, shopping, vault, watch
from . import collections as coll
from . import stats as pstats
from .cards import CardIndex
from .db import Database, masked
from .scrapers.base import parse_price

log = logging.getLogger(__name__)

SETTING_KEYS = ["interval_minutes", "lot_min_ratio", "max_price", "max_per_card", "sources", "language",
                "deal_pct", "images", "paused", "quiet_hours", "home_active"]


class Shared:
    """Ciò che è uguale per tutti, calcolato una volta per giro: annunci, prezzi Cardmarket, statistiche per carta."""

    def __init__(self, index: CardIndex, db: Database):
        self.rows = pstats.PriceRows(db.list_found())
        found_keys = {r["listing_key"] for r in self.rows}
        self.all_rows = pstats.PriceRows(self.rows + [r for r in db.price_rows() if r["listing_key"] not in found_keys])
        self.cm = cardmarket.all_prices(db)
        runs = db.last_runs(1)
        self.last_run = runs[0] if runs else None
        self._prices: dict[str, dict | None] = {}

    def price(self, card) -> dict | None:
        if card.id not in self._prices:
            cp = pstats.card_prices(self.all_rows, card)
            p = self.cm.get(card.id)
            cm = {"low": p.low, "trend": p.ref, "avg30": p.avg30, "updated": p.updated} if p and p.ref else None
            self._prices[card.id] = ({"n": cp.overall.n, "min": cp.overall.min, "median": cp.overall.median,
                                      "max": cp.overall.max, "recent_median": cp.recent_median,
                                      "older_median": cp.older_median, "cm": cm}
                                     if cp.overall.n or cm else None)
        return self._prices[card.id]


def build_state(index: CardIndex, db: Database, max_found: int = 300, chat_id: str = "", shared: Shared | None = None) -> dict:
    """Stato per la Mini App di una persona (chat_id); senza chat: il proprietario."""
    shared = shared or Shared(index, db)
    chat_id = chat_id or db.owner_chat_id() or "me"
    for sid in config.HOME_SET_IDS:
        coll.album_for(db, chat_id, sid)
    wanted = db.wanted_for(chat_id)
    albums = db.albums_of(chat_id)
    groups = _groups(index, albums)
    my_ids = {c.id for cards in groups.values() for c in cards}
    home_index = CardIndex([s for s in index.sets if s.primary], index.aliases)
    active = db.active_sets(chat_id)
    settings = dict(db.settings_for(chat_id), home_active=any(s.id in active for s in home_index.sets))
    comp = pstats.completion(home_index, wanted, shared.rows, shared.cm)
    prices = {cid: p for cid in my_ids if (p := shared.price(index.by_id[cid])) is not None}
    # annunci delle carte che le mancano, e di ogni annuncio solo le sue carte (non quelle cercate da altri)
    mine = [dict(r, matched=[m for m in r["matched"] if m["id"] in wanted]) for r in shared.rows
            if any(m["id"] in wanted for m in r["matched"])]
    last = shared.last_run
    watches = []
    for cid, w in watch.list_watches(db, chat_id).items():
        card, _ = watch.resolve_card(index, db, cid)
        watches.append({"card": cid, "label": card.label if card else cid, "code": watch.code_for(index, card) if card else cid,
                        "started": w.get("started"), "until": w.get("until"), "every": w.get("every"),
                        "checks": w.get("checks", 0), "found": w.get("found", 0)})
    return {
        "generated_at": time.time(),
        "friends": [{"id": f, "name": db.user_name(f), "shared": [sid for sid, alb in albums.items() if f in db.album_members(alb)]}
                    for f in db.friends(chat_id)],
        "friend_code": db.friend_code(chat_id),
        "plan": _plan_info(db, chat_id),
        "bot_username": db.get_kv("bot_username") or "",
        "home_album": {sid: {"shared_with": [{"id": m, "name": db.user_name(m)}
                                             for m in db.album_members(albums.get(sid, "")) if m != chat_id]}
                       for sid in config.HOME_SET_IDS},
        "wanted": sorted(wanted),
        "settings": {k: settings.get(k) for k in SETTING_KEYS},
        "sets": [{"id": s.id, "name": s.name, "name_it": s.name_it, "printed_total": s.printed_total,
                  "cards": [{"id": c.id, "code": index.code_of[c.id], "number": c.number, "name": c.name,
                             "rarity": c.rarity, "image": c.image} for c in s.cards]}
                 for s in home_index.sets],
        "prices": prices,
        "completion": {"total": comp.total, "missing": comp.missing, "owned": comp.owned, "percent": comp.percent,
                       "priced": comp.priced, "cm_priced": comp.cm_priced, "cost_min": comp.cost_min,
                       "cost_median": comp.cost_median, "by_rarity": {k: list(v) for k, v in comp.by_rarity.items()}},
        "last_run": ({"started_at": last["started_at"], "listings_seen": last["listings_seen"], "matches": last["matches"],
                      "errors": last["errors"], "per_source": last.get("per_source") or {}} if last else None),
        "found": [_found_row(r) for r in sorted(mine, key=lambda r: -r["created_at"])[:max_found]],
        "watches": watches,
        "watch_found": list(reversed(watch.found_log(db, chat_id))),
        "collections": coll.export(db, index, chat_id),
        "values": _values(groups, wanted, shared),
        "shopping": _shopping(groups, wanted, shared),
        "copies": db.copies(chat_id),
    }


def _found_row(r: dict) -> dict:
    return {"id": r["id"], "key": r["listing_key"], "source": r["source"], "title": r["title"], "url": r["url"],
            "price": r.get("price") or "", "price_num": parse_price(r.get("price")), "location": r.get("location") or "",
            "kind": r["kind"], "cards": [m["id"] for m in r["matched"]],
            "sure": [m["id"] for m in r["matched"] if m.get("sure", True)],
            "notified": bool(r.get("notified")), "deal": bool(r.get("deal")), "image": r.get("image") or "",
            "ts": r["created_at"]}


def _groups(index: CardIndex, albums: dict | None = None) -> dict[str, list]:
    """Collezioni come le vede la Mini App: "home" = quelle di casa insieme, le altre (della persona) una per una."""
    out: dict[str, list] = {"home": [c for s in index.sets if s.primary for c in s.cards]}
    for s in index.sets:
        if not s.primary and (albums is None or s.id in albums):
            out[s.id] = list(s.cards)
    return out


def _values(groups: dict[str, list], wanted: set[str], shared: Shared) -> dict:
    out = {}
    for key, cards in groups.items():
        v = pstats.collection_value(cards, wanted, shared.all_rows, shared.cm)
        out[key] = {"owned": v.owned, "owned_priced": v.owned_priced, "owned_value": round(v.owned_value, 2),
                    "missing": v.missing, "missing_priced": v.missing_priced, "missing_cost": round(v.missing_cost, 2)}
    return out


def _shopping(groups: dict[str, list], wanted: set[str], shared: Shared) -> dict:
    cm = shared.cm
    out = {}
    for key, cards in groups.items():
        sl = shopping.build(cards, wanted, shared.rows)
        out[key] = {"total": round(sl.total, 2), "covered": sl.covered, "uncovered": [c.id for c in sl.uncovered],
                    "sellers": [{"label": sl.seller_label(k), "total": round(sum(p.price for p in picks), 2),
                                 "picks": [{"price": p.price, "lot": p.row.get("kind") == "lot",
                                            "cm": round(sum(cm[c.id].ref for c in p.cards if c.id in cm and cm[c.id].ref), 2) or None,
                                            "url": p.row["url"], "title": p.row["title"], "image": p.row.get("image") or "",
                                            "cards": [c.id for c in p.cards], "key": p.row["listing_key"]} for p in picks]}
                                for k, picks in sl.by_seller()]}
    return out


def _plan_info(db: Database, chat_id: str) -> dict:
    full = plans.is_full(db, chat_id)
    return {"tier": plans.tier(db, chat_id), "label": plans.describe(db, chat_id), "price": plans.PRICE_STARS,
            "onboarding": plans.onboarding(db, chat_id), "invoice": plans.cached_invoice(db, chat_id),
            # limiti della versione Light (None = nessun limite)
            "max_active": None if full else plans.LIGHT_MAX_ACTIVE,
            "max_collections": None if full else plans.LIGHT_MAX_COLLECTIONS}


def build_summary(index: CardIndex, db: Database) -> dict:
    """Il riepilogo per il ponte: chi può usare il bot e quando serve svegliarlo (nient'altro)."""
    from .search import notifications_suppressed
    now = time.time()
    members = db.chat_ids()
    # inseguimenti: il prossimo controllo dovuto (per la Light ogni 2 ore, non ogni 5 minuti); chi è in pausa o di
    # notte non conta: i suoi inseguimenti ripartono con la ricerca normale
    next_watch = None
    for chat, ws in watch.all_watches(db):
        if notifications_suppressed(db.settings_for(chat)):
            continue
        every_min = 0 if plans.is_full(db, chat) else plans.LIGHT_WATCH_EVERY_S
        for w in ws.values():
            due = float(w.get("last") or 0) + max(float(w.get("every") or 0), every_min)
            due = min(due, float(w.get("until") or due))  # alla scadenza parte il riepilogo finale
            next_watch = due if next_watch is None else min(next_watch, due)
    # annunci in coda da mandare adesso: chi è in pausa o di notte, e la Light fino alle 19, aspettano
    deliverable = sum(db.queued_count(c) for c in db.queued_chats()
                      if c in members and plans.is_full(db, c) and not notifications_suppressed(db.settings_for(c)))
    return {
        "generated_at": now,
        "owner_chat_id": db.owner_chat_id(),
        "chat_ids": members,
        "last_search_ts": float(db.get_kv("last_search_ts", 0) or 0),
        "interval_minutes": int(db.get_settings().get("interval_minutes", 20) or 20),
        "next_watch": next_watch,
        "queued": deliverable,
        "channel": {"set": bool(db.get_kv("deals_channel")), "hour": channel.post_hour(db),
                    "last": db.get_kv("deals_channel_last") or ""},
    }


def write_state(index: CardIndex, db: Database, folder: str, seal: bool = True) -> list[str]:
    """Scrive il riepilogo e lo stato di ogni persona collegata in `folder` (cifrati e con i nomi a impronta, se
    `seal`). Il riepilogo per primo: senza, il ponte non riconosce nessuno. Restituisce i file scritti."""
    os.makedirs(folder, exist_ok=True)

    def write(name: str, data: dict) -> str:
        raw = json.dumps(data, ensure_ascii=False).encode()
        path = os.path.join(folder, name + (".enc" if seal else ""))
        with open(path, "wb") as f:
            f.write(vault.seal(raw) if seal else raw)
        return path

    out = [write("state.json", build_summary(index, db))]
    shared = Shared(index, db)
    for chat in (db.chat_ids() or [db.owner_chat_id() or "me"]):
        try:  # lo stato di una persona che non si riesce a costruire non blocca quello degli altri
            out.append(write(f"state-{vault.file_id(chat) if seal else chat}.json", build_state(index, db, chat_id=chat, shared=shared)))
        except Exception:  # noqa: BLE001
            log.exception("Stato della Mini App non scritto per %s", masked(chat))
    return out
