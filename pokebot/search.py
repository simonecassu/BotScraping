"""Un ciclo di ricerca: interroga i marketplace, riconosce le carte, notifica."""
from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field

from .cards import CardIndex
from .db import Database
from .matcher import Matcher, MatchResult
from .notifier import TelegramNotifier
from .scrapers import SCRAPERS, Listing, ScraperError

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    started_at: float
    finished_at: float = 0.0
    queries: int = 0
    listings: int = 0
    new_listings: int = 0
    matches: int = 0
    notified: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    wanted_count: int = 0
    skipped: Counter = field(default_factory=Counter)


def build_queries(index: CardIndex, wanted_ids: set[str], settings: dict, db: Database) -> list[str]:
    queries = [q.strip() for q in settings.get("generic_queries", []) if q.strip()]
    if settings.get("per_card_queries") and wanted_ids:
        batch = int(settings.get("per_card_batch", 20) or 0)
        for cid in db.next_rotation(sorted(wanted_ids), batch):
            card = index.by_id.get(cid)
            if not card:
                continue
            if card.printed_total:
                queries.append(f"{card.name} {card.number}/{card.printed_total}")
            else:
                queries.append(f"{card.name} classic collection pokemon 30th")
    # dedup mantenendo l'ordine
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def run_search(index: CardIndex, db: Database, notifier: TelegramNotifier | None = None,
               scrapers: dict | None = None, dry_run: bool = False) -> RunReport:
    report = RunReport(started_at=time.time())
    settings = db.get_settings()
    wanted = db.wanted_ids()
    report.wanted_count = len(wanted)
    run_id = db.start_run()
    if not wanted:
        log.info("Nessuna carta mancante selezionata: ricerca saltata")
        report.finished_at = time.time()
        db.finish_run(run_id, 0, 0, {"info": "nessuna carta selezionata"})
        return report

    matcher = Matcher(index, settings.get("set_keywords", []))
    lot_ratio = float(settings.get("lot_min_ratio", 0.5))
    max_price = float(settings.get("max_price", 0) or 0)
    only_italy = bool(settings.get("only_italy", True))
    unverifiable = bool(settings.get("notify_unverifiable_lots", False))
    notifier = notifier or TelegramNotifier.from_db(db)

    if scrapers is None:
        scrapers = {}
        for name in settings.get("sources", []):
            cls = SCRAPERS.get(name)
            if cls:
                scrapers[name] = cls(only_italy=only_italy)

    queries = build_queries(index, wanted, settings, db)
    report.queries = len(queries)
    seen_this_run: set[str] = set()
    pending: list[tuple[Listing, "MatchResult"]] = []  # match da notificare a fine ciclo (raggruppati se tanti)

    for name, scraper in scrapers.items():
        for q in queries:
            try:
                listings = scraper.search(q)
            except ScraperError as exc:
                log.warning("%s", exc)
                report.errors[name] = str(exc)
                break  # un marketplace bloccato: inutile insistere in questo ciclo
            except Exception as exc:  # noqa: BLE001
                log.exception("Errore inatteso in %s", name)
                report.errors[name] = f"{type(exc).__name__}: {exc}"
                break
            for lst in listings:
                report.listings += 1
                if lst.key in seen_this_run or db.is_seen(lst.key):
                    continue
                seen_this_run.add(lst.key)
                report.new_listings += 1
                _handle_listing(lst, matcher, wanted, lot_ratio, max_price, unverifiable, db, report, pending)

    _notify_pending(pending, notifier, db, report, dry_run)
    report.finished_at = time.time()
    db.finish_run(run_id, report.listings, report.matches, report.errors)
    log.info("Ciclo completato: %d query, %d annunci (%d nuovi), %d match, %d notifiche",
             report.queries, report.listings, report.new_listings, report.matches, report.notified)
    if report.skipped:
        log.info("Scartati: " + "; ".join(f"{n} × {why}" for why, n in report.skipped.most_common()))
    return report


def _handle_listing(lst: Listing, matcher: Matcher, wanted: set[str], lot_ratio: float, max_price: float,
                    unverifiable: bool, db: Database, report: RunReport, pending: list) -> None:
    result = matcher.analyze(lst.title, lst.description, wanted, lot_ratio, unverifiable, lst.is_auction)
    if not result.notify:
        db.mark_seen(lst.key)
        report.skipped[result.reason] += 1
        log.log(logging.INFO if result.kind in ("single", "lot") else logging.DEBUG,
                "Scartato [%s] %s -> %s", lst.source, lst.title[:80], result.reason)
        return
    if max_price and lst.price is not None and lst.price > max_price:
        db.mark_seen(lst.key)
        report.skipped["oltre il prezzo massimo"] += 1
        log.info("Oltre il prezzo massimo [%s] %s (%s)", lst.source, lst.title, lst.price_text)
        return
    report.matches += 1
    pending.append((lst, result))
    log.info("MATCH [%s] %s -> %s", lst.source, lst.title, result.reason)


def _notify_pending(pending: list, notifier: TelegramNotifier, db: Database, report: RunReport, dry_run: bool) -> None:
    outcomes = [False] * len(pending)
    if pending and not dry_run:
        outcomes = notifier.notify_many(pending)
    for (lst, result), sent in zip(pending, outcomes):
        report.notified += int(sent)
        db.add_found(lst.key, lst.source, lst.title, lst.url, lst.price_text or (f"{lst.price:.2f} €" if lst.price else None),
                     lst.location, result.kind, result.matched_payload, result.ratio, sent)
        db.mark_seen(lst.key, notified=sent)
