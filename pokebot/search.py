"""Un ciclo di ricerca: interroga i marketplace, riconosce le carte, notifica."""
from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config
from . import stats as pstats

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
    deals: int = 0
    queued: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    wanted_count: int = 0
    skipped: Counter = field(default_factory=Counter)
    per_source: Counter = field(default_factory=Counter)


def build_queries(index: CardIndex, wanted_ids: set[str], settings: dict, db: Database) -> list[str]:
    queries = [q.strip() for q in settings.get("generic_queries", []) if q.strip()]
    if settings.get("per_card_queries") and wanted_ids:
        batch = int(settings.get("per_card_batch", 20) or 0)
        for cid in db.next_rotation(sorted(wanted_ids), batch):
            card = index.by_id.get(cid)
            if not card:
                continue
            if card.set_id not in ("me55", "me55c"):
                queries.append(f"pokemon {card.name} {card.number}/{card.printed_total or ''} {card.set_name}".replace("/ ", " "))
            elif card.printed_total:
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
    wanted = db.wanted_ids() if settings.get("home_active", True) else set()
    # altre collezioni attivate con /collezione <id> attiva: si aggiungono all'indice e alle carte cercate
    from . import collections as coll
    extra_sets, extra_ids = coll.active_search_sets(db)
    if extra_sets:
        index = CardIndex(list(index.sets) + extra_sets)
        wanted = wanted | extra_ids
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
    max_per_card = int(settings.get("max_per_card", 5) or 5)
    language = str(settings.get("language", "ita") or "ita")
    notifier = notifier or TelegramNotifier.from_db(db)
    suppressed = notifications_suppressed(settings)
    if not suppressed:
        flush_queued(db, notifier, settings)

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
        consecutive_errors = 0
        for q in queries:
            try:
                listings = scraper.search(q)
                consecutive_errors = 0
            except ScraperError as exc:
                consecutive_errors += 1
                log.warning("%s (query: %s)", exc, q)
                report.errors[name] = str(exc)
                if consecutive_errors >= 3 or "accesso rifiutato" in str(exc):
                    break  # marketplace bloccato o giù: inutile insistere in questo ciclo
                continue
            except Exception as exc:  # noqa: BLE001
                log.exception("Errore inatteso in %s", name)
                report.errors[name] = f"{type(exc).__name__}: {exc}"
                break
            report.per_source[name] += len(listings)
            log.debug("%s: %d risultati per '%s'", name, len(listings), q)
            for lst in listings:
                report.listings += 1
                if lst.key in seen_this_run or db.is_seen(lst.key):
                    continue
                seen_this_run.add(lst.key)
                report.new_listings += 1
                _handle_listing(lst, matcher, wanted, lot_ratio, max_price, unverifiable, db, report, pending, language)
        if name in report.errors and report.per_source.get(name):
            # qualche query è riuscita: l'errore è parziale, non bloccante
            report.errors[name] = "parziale: " + report.errors[name]

    _notify_pending(pending, notifier, db, report, dry_run, max_per_card, settings, suppressed)
    report.finished_at = time.time()
    db.finish_run(run_id, report.listings, report.matches, report.errors, dict(report.per_source))
    log.info("Ciclo completato: %d query, %d annunci (%d nuovi), %d match, %d notifiche, %d affari, %d in coda · per fonte: %s",
             report.queries, report.listings, report.new_listings, report.matches, report.notified, report.deals,
             report.queued, ", ".join(f"{k} {v}" for k, v in report.per_source.items()) or "-")
    if report.skipped:
        log.info("Scartati: " + "; ".join(f"{n} × {why}" for why, n in report.skipped.most_common()))
    return report


def _handle_listing(lst: Listing, matcher: Matcher, wanted: set[str], lot_ratio: float, max_price: float,
                    unverifiable: bool, db: Database, report: RunReport, pending: list, language: str = "tutte") -> None:
    result = matcher.analyze(lst.title, lst.description, wanted, lot_ratio, unverifiable, lst.is_auction, language)
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


def notifications_suppressed(settings: dict) -> bool:
    """True se siamo in pausa o nelle ore notturne: si accumula e si invia dopo."""
    if settings.get("paused"):
        return True
    qh = settings.get("quiet_hours")
    if qh and len(qh) == 2:
        start, end = int(qh[0]), int(qh[1])
        hour = datetime.now(ZoneInfo(config.TIMEZONE)).hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end
    return False


def _row_to_pair(row: dict, index_by_id: dict | None = None) -> tuple[Listing, MatchResult]:
    """Ricostruisce annuncio e risultato da una riga della tabella found (per inviare la coda)."""
    from .cards import Card  # noqa: F401 - solo per i type hint
    from .scrapers.base import parse_price
    lst = Listing(row["source"], row["listing_key"].split(":", 1)[-1], row["title"], row["url"],
                  price=parse_price(row.get("price")), price_text=row.get("price") or "", location=row.get("location") or "")
    wanted = []
    possible = []
    for m in row["matched"]:
        card = (index_by_id or {}).get(m["id"])
        if card is None:
            continue
        (wanted if m.get("sure", True) else possible).append(card)
    res = MatchResult(row["kind"], True, "dalla coda", wanted=wanted, possible_wanted=possible,
                      total_cards=len(wanted) + len(possible), wanted_count=len(wanted), ratio=row.get("ratio"))
    return lst, res


def flush_queued(db: Database, notifier: TelegramNotifier, settings: dict, index_by_id: dict | None = None) -> int:
    """Invia gli annunci accumulati durante la pausa o la notte, raggruppati per carta."""
    rows = db.queued_found()
    if not rows:
        return 0
    if index_by_id is None:
        from .cards import load_sets
        index_by_id = load_sets().by_id
    pairs = []
    ids = []
    for r in rows:
        lst, res = _row_to_pair(r, index_by_id)
        if not (res.wanted or res.possible_wanted) and r["kind"] != "lot":
            db.mark_sent([r["id"]], False)
            continue
        pairs.append((lst, res))
        ids.append(r["id"])
    if not pairs:
        return 0
    notifier.send(f"🌅 <b>Accumulati durante la pausa: {len(pairs)} annunci</b>", disable_preview=True)
    outcomes = notifier.notify_many(pairs, max_per_card=int(settings.get("max_per_card", 5) or 5),
                                    images=bool(settings.get("images", True)))
    for row_id, sent in zip(ids, outcomes):
        db.mark_sent([row_id], sent)
    log.info("Coda inviata: %d annunci", len(pairs))
    return len(pairs)


def _notify_pending(pending: list, notifier: TelegramNotifier, db: Database, report: RunReport, dry_run: bool,
                    max_per_card: int = 5, settings: dict | None = None, suppressed: bool = False) -> None:
    settings = settings or {}
    images = bool(settings.get("images", True))
    deal_pct = float(settings.get("deal_pct", 60) or 0)

    if suppressed:
        for lst, result in pending:
            report.queued += 1
            db.add_found(lst.key, lst.source, lst.title, lst.url, lst.price_text or (f"{lst.price:.2f} €" if lst.price else None),
                         lst.location, result.kind, result.matched_payload, result.ratio, False, queued=True, image=lst.image)
            db.mark_seen(lst.key, notified=False)
        if pending:
            log.info("Notifiche sospese (pausa/notte): %d annunci in coda", len(pending))
        return

    # 🔥 affari: prezzo molto sotto la mediana storica della carta -> avviso immediato, fuori dai gruppi
    deals: set[str] = set()
    max_deals = int(settings.get("max_deals_per_run", 3) or 0)
    if deal_pct > 0 and pending and max_deals > 0:
        history = db.list_found()
        candidates = []
        for lst, result in pending:
            if result.kind != "single" or not result.wanted or lst.price is None or lst.price < 1:
                continue
            median = pstats.median_for_deal(history, result.wanted[0].id)
            # sotto il 25% della mediana è quasi sempre un errore di riconoscimento o un'inserzione sospetta
            if median and median * 0.25 <= lst.price <= median * deal_pct / 100.0:
                candidates.append((lst.price / median, lst, result, median))
        candidates.sort(key=lambda t: t[0])
        if len(candidates) > max_deals:
            log.info("Affari trovati: %d, inviati solo i %d migliori", len(candidates), max_deals)
        for _, lst, result, median in candidates[:max_deals]:
            if True:
                sent = False if dry_run else notifier.notify_deal(lst, result, median, images)
                deals.add(lst.key)
                report.deals += int(sent)
                report.notified += int(sent)
                db.add_found(lst.key, lst.source, lst.title, lst.url, lst.price_text or f"{lst.price:.2f} €", lst.location,
                             result.kind, result.matched_payload, result.ratio, sent, image=lst.image, deal=True)
                db.mark_seen(lst.key, notified=sent)
                log.info("AFFARE [%s] %s %.2f € (mediana %.2f)", lst.source, lst.title[:60], lst.price, median)

    rest = [(lst, res) for lst, res in pending if lst.key not in deals]
    # tetto ai messaggi per giro: oltre il limite si registra nello storico senza inviare (anti-valanga)
    max_msgs = int(settings.get("max_messages_per_run", 10) or 0)
    overflow_note = None
    if max_msgs > 0 and rest:
        from .notifier import group_matches
        groups = group_matches(rest)
        if len(groups) > max_msgs:
            keep_keys = {lst.key for _, _, members in groups[:max_msgs] for lst, _ in members}
            skipped_groups = groups[max_msgs:]
            overflow = [(lst, res) for lst, res in rest if lst.key not in keep_keys]
            rest = [(lst, res) for lst, res in rest if lst.key in keep_keys]
            for lst, result in overflow:
                db.add_found(lst.key, lst.source, lst.title, lst.url, lst.price_text or (f"{lst.price:.2f} €" if lst.price else None),
                             lst.location, result.kind, result.matched_payload, result.ratio, False, image=lst.image)
                db.mark_seen(lst.key, notified=False)
            names = ", ".join(t.replace("🃏 ", "").replace("📦 ", "").replace("❔ ", "") for _, t, _ in skipped_groups[:12])
            overflow_note = (f"⚠️ Giro insolitamente ricco: {len(groups)} carte con novità, inviate le prime {max_msgs}. "
                             f"Le altre ({len(overflow)} annunci) sono nello storico: {names}" + (" …" if len(skipped_groups) > 12 else "") +
                             "\nUsa /storico o /cerca <carta>.")
            log.info("Tetto messaggi: %d gruppi su %d inviati, %d annunci solo nello storico", max_msgs, len(groups), len(overflow))
    outcomes = [False] * len(rest)
    if rest and not dry_run:
        outcomes = notifier.notify_many(rest, max_per_card=max_per_card, images=images)
    if overflow_note and not dry_run:
        notifier.send(overflow_note, disable_preview=True)
    for (lst, result), sent in zip(rest, outcomes):
        report.notified += int(sent)
        db.add_found(lst.key, lst.source, lst.title, lst.url, lst.price_text or (f"{lst.price:.2f} €" if lst.price else None),
                     lst.location, result.kind, result.matched_payload, result.ratio, sent, image=lst.image)
        db.mark_seen(lst.key, notified=sent)


def card_queries(card) -> list[str]:
    if card.printed_total:
        return [f"{card.name} {card.number}/{card.printed_total}", f"{card.name} 30th", f"{card.name} 30 anniversario"]
    return [f"{card.name} classic collection", f"{card.name} 30th classic"]


def search_card(index: CardIndex, db: Database, card, settings: dict | None = None, scrapers: dict | None = None,
                limit: int = 10, only_new: bool = False) -> tuple[list[tuple[Listing, "MatchResult"]], dict[str, str]]:
    """Ricerca mirata di una carta su tutte le fonti: restituisce gli annunci in vendita adesso, dal più economico.

    Include anche annunci già visti (con `only_new=True` restituisce solo quelli mai visti né registrati).
    Gli annunci vengono registrati nello storico e segnati come visti.
    """
    settings = settings or db.get_settings()
    matcher = Matcher(index, settings.get("set_keywords", []))
    language = str(settings.get("language", "ita") or "ita")
    lot_ratio = float(settings.get("lot_min_ratio", 0.5))
    max_price = float(settings.get("max_price", 0) or 0)
    if scrapers is None:
        scrapers = {}
        for name in settings.get("sources", []):
            cls = SCRAPERS.get(name)
            if cls:
                scrapers[name] = cls(only_italy=bool(settings.get("only_italy", True)))
    found: dict[str, tuple[Listing, MatchResult]] = {}
    errors: dict[str, str] = {}
    for name, scraper in scrapers.items():
        for q in card_queries(card):
            try:
                listings = scraper.search(q, limit=40)
            except ScraperError as exc:
                errors[name] = str(exc)
                break
            except Exception as exc:  # noqa: BLE001
                errors[name] = f"{type(exc).__name__}: {exc}"
                break
            for lst in listings:
                if lst.key in found:
                    continue
                res = matcher.analyze(lst.title, lst.description, {card.id}, lot_ratio, False, lst.is_auction, language)
                hit = res.notify or any(r.card.id == card.id for r in res.refs)
                if not hit or (max_price and lst.price is not None and lst.price > max_price):
                    continue
                found[lst.key] = (lst, res)
    items = sorted(found.values(), key=lambda pair: (pair[0].price if pair[0].price is not None else float("inf")))
    existing = {r["listing_key"] for r in db.list_found()}
    if only_new:
        items = [pair for pair in items if pair[0].key not in existing and not db.is_seen(pair[0].key)]
    for lst, res in items[:limit]:
        if lst.key not in existing:
            db.add_found(lst.key, lst.source, lst.title, lst.url, lst.price_text or (f"{lst.price:.2f} €" if lst.price else None),
                         lst.location, res.kind, res.matched_payload or [{"id": card.id, "label": card.label, "sure": True}],
                         res.ratio, True, image=lst.image)
        db.mark_seen(lst.key, notified=True)
    return items[:limit], errors
