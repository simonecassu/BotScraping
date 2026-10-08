"""Un ciclo di ricerca: interroga i marketplace, riconosce le carte, notifica."""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
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
    """Query generiche di ogni collezione dell'indice (o quelle in `generic_queries`, se impostate) più le carte
    mancanti a rotazione."""
    generic = settings.get("generic_queries")
    if generic is None:
        generic = [q for s in index.sets for q in s.queries]
    queries = [q.strip() for q in generic if q.strip()]
    if settings.get("per_card_queries") and wanted_ids:
        batch = int(settings.get("per_card_batch", 20) or 0)
        for cid in db.next_rotation(sorted(wanted_ids), batch):
            card = index.by_id.get(cid)
            if not card:
                continue
            if card.printed_total:
                queries.append(f"{card.name} {card.number}/{card.printed_total}")
            else:
                cs = index.get_set(card.set_id)
                queries.append(f"{card.name} {cs.card_query if cs and cs.card_query else card.set_name}")
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
    # si cercano solo le collezioni attive: indice ridotto a quelle, mancanti solo le loro
    active = set(db.active_sets())
    index = CardIndex([s for s in index.sets if s.id in active], index.aliases)
    wanted = {cid for cid in db.wanted_ids() if cid in index.by_id}
    report.wanted_count = len(wanted)
    run_id = db.start_run()
    if not wanted:
        log.info("Nessuna carta mancante selezionata: ricerca saltata")
        report.finished_at = time.time()
        db.finish_run(run_id, 0, 0, {"info": "nessuna carta selezionata"})
        return report

    matcher = Matcher(index, settings.get("set_keywords"))
    lot_ratio = float(settings.get("lot_min_ratio", 0.5))
    max_price = float(settings.get("max_price", 0) or 0)
    only_italy = bool(settings.get("only_italy", True))
    unverifiable = bool(settings.get("notify_unverifiable_lots", False))
    max_per_card = int(settings.get("max_per_card", 5) or 5)
    language = str(settings.get("language", "ita") or "ita")
    if notifier is None:
        flush_all_queues(db, index.by_id)

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

    generic = {q.strip().lower() for q in (settings.get("generic_queries") or [q for s_ in index.sets for q in s_.queries]) if q.strip()}
    qstats: dict[str, dict] = {}
    # le sorgenti vengono interrogate in parallelo (ognuna con il proprio ritmo), poi gli annunci si valutano in ordine
    for name, listings_by_query, error in scrape_all(scrapers, queries):
        if error:
            report.errors[name] = error
        for q, listings in listings_by_query:
            report.per_source[name] += len(listings)
            log.debug("%s: %d risultati per '%s'", name, len(listings), q)
            new_before, match_before = report.new_listings, report.matches
            for lst in listings:
                report.listings += 1
                if lst.key in seen_this_run or db.is_seen(lst.key):
                    continue
                seen_this_run.add(lst.key)
                report.new_listings += 1
                _handle_listing(lst, matcher, wanted, lot_ratio, max_price, unverifiable, db, report, pending, language)
            if q.lower() in generic:
                st = qstats.setdefault(q.lower(), {"new": 0, "matches": 0})
                st["new"] += report.new_listings - new_before
                st["matches"] += report.matches - match_before
    if qstats and not dry_run:
        record_query_stats(db, qstats)
        if name in report.errors and report.per_source.get(name):
            # qualche query è riuscita: l'errore è parziale, non bloccante
            report.errors[name] = "parziale: " + report.errors[name]

    _notify_pending(pending, db, report, dry_run, notifier=notifier, settings=settings)
    report.finished_at = time.time()
    db.finish_run(run_id, report.listings, report.matches, report.errors, dict(report.per_source))
    log.info("Ciclo completato: %d query, %d annunci (%d nuovi), %d match, %d notifiche, %d affari, %d in coda · per fonte: %s",
             report.queries, report.listings, report.new_listings, report.matches, report.notified, report.deals,
             report.queued, ", ".join(f"{k} {v}" for k, v in report.per_source.items()) or "-")
    if report.skipped:
        log.info("Scartati: " + "; ".join(f"{n} × {why}" for why, n in report.skipped.most_common()))
    return report


def record_query_stats(db: Database, per_query: dict[str, dict]) -> None:
    """Accumula, per ogni query generica, quanti annunci nuovi e quanti match ha portato (per potare quelle inutili)."""
    stats = db.get_kv("query_stats", {}) or {}
    for q, st in per_query.items():
        cur = stats.setdefault(q, {"runs": 0, "new": 0, "matches": 0})
        cur["runs"] += 1
        cur["new"] += st["new"]
        cur["matches"] += st["matches"]
    db.set_kv("query_stats", stats)


def _scrape_source(name: str, scraper, queries: list[str]) -> tuple[str, list[tuple[str, list[Listing]]], str | None]:
    """Tutte le query su una sorgente; si ferma dopo 3 errori di fila o a un blocco anti-bot."""
    out: list[tuple[str, list[Listing]]] = []
    error: str | None = None
    consecutive_errors = 0
    for q in queries:
        try:
            listings = scraper.search(q)
            consecutive_errors = 0
        except ScraperError as exc:
            consecutive_errors += 1
            log.warning("%s (query: %s)", exc, q)
            error = str(exc)
            if consecutive_errors >= 3 or "accesso rifiutato" in str(exc):
                break  # marketplace bloccato o giù: inutile insistere in questo ciclo
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("Errore inatteso in %s", name)
            error = f"{type(exc).__name__}: {exc}"
            break
        out.append((q, listings))
    return name, out, error


def scrape_all(scrapers: dict, queries: list[str]) -> list[tuple[str, list[tuple[str, list[Listing]]], str | None]]:
    """Interroga le sorgenti in parallelo (un thread per marketplace); restituisce i risultati nell'ordine delle sorgenti."""
    if not scrapers or not queries:
        return []
    if len(scrapers) == 1:
        name, scraper = next(iter(scrapers.items()))
        return [_scrape_source(name, scraper, queries)]
    with ThreadPoolExecutor(max_workers=len(scrapers)) as pool:
        futures = {name: pool.submit(_scrape_source, name, scraper, queries) for name, scraper in scrapers.items()}
        return [futures[name].result() for name in scrapers]


def _handle_listing(lst: Listing, matcher: Matcher, wanted: set[str], lot_ratio: float, max_price: float,
                    unverifiable: bool, db: Database, report: RunReport, pending: list, language: str = "tutte") -> None:
    result = matcher.analyze(lst.title, lst.description, wanted, lot_ratio, unverifiable, lst.is_auction, language)
    if result.kind == "single" and result.refs and lst.price and lst.price > 0:
        # prezzo di una carta riconosciuta con certezza, mancante o no: serve per il valore della collezione
        db.add_price_point(result.refs[0].card.id, lst.key, lst.source, lst.price, lst.seller)
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


def flush_queued(db: Database, notifier: TelegramNotifier, settings: dict, index_by_id: dict | None = None,
                 chat_id: str | None = None) -> int:
    """Invia gli annunci accumulati (per una persona) durante la sua pausa o le sue ore notturne, raggruppati per carta."""
    rows = db.queued_found(chat_id)
    if not rows:
        return 0
    if index_by_id is None:
        from .cards import load_sets
        index_by_id = load_sets().by_id
    pairs = []
    ids = []
    seen_ids: set[int] = set()
    for r in rows:
        if r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])
        lst, res = _row_to_pair(r, index_by_id)
        if not (res.wanted or res.possible_wanted) and r["kind"] != "lot":
            db.mark_sent([r["id"]], False, chat_id)
            continue
        pairs.append((lst, res))
        ids.append(r["id"])
    if not pairs:
        return 0
    notifier.send(f"🌅 <b>Accumulati durante la pausa: {len(pairs)} annunci</b>", disable_preview=True)
    outcomes = notifier.notify_many(pairs, max_per_card=int(settings.get("max_per_card", 5) or 5),
                                    images=bool(settings.get("images", True)))
    for row_id, sent in zip(ids, outcomes):
        db.mark_sent([row_id], sent, chat_id)
    log.info("Coda inviata a %s: %d annunci", chat_id or "tutti", len(pairs))
    return len(pairs)


def flush_all_queues(db: Database, index_by_id: dict | None = None) -> int:
    """Per ogni persona con annunci in coda e notifiche attive: invia con le sue impostazioni."""
    total = 0
    for chat in db.queued_chats():
        settings = db.settings_for(chat)
        if notifications_suppressed(settings):
            continue
        total += flush_queued(db, TelegramNotifier(chat_ids=[chat]), settings, index_by_id, chat)
    return total


def _notify_pending(pending: list, db: Database, report: RunReport, dry_run: bool,
                    notifier: TelegramNotifier | None = None, settings: dict | None = None) -> None:
    """Registra gli annunci una volta sola, poi li consegna a ogni persona collegata con le SUE impostazioni
    (pausa, notte, foto, quanti per carta, affari). Chi ha le notifiche sospese li trova in coda al risveglio."""
    if not pending:
        return
    found_ids: dict[str, int] = {}
    for lst, result in pending:
        found_ids[lst.key] = db.add_found(lst.key, lst.source, lst.title, lst.url,
                                          lst.price_text or (f"{lst.price:.2f} €" if lst.price else None), lst.location,
                                          result.kind, result.matched_payload, result.ratio, False, image=lst.image, seller=lst.seller)
        db.mark_seen(lst.key, notified=False)
    if notifier is not None:  # un solo destinatario esplicito (test / prova): le impostazioni passate valgono per lui
        recipients = [("", notifier, dict(settings or db.get_settings()))]
    else:
        recipients = [(chat, TelegramNotifier(chat_ids=[chat]), db.settings_for(chat)) for chat in db.chat_ids()]
    history = db.list_found()
    pending_keys = set(found_ids)
    sent_any: set[str] = set()
    deal_keys: set[str] = set()
    for i, (chat, ntf, st) in enumerate(recipients):
        first = i == 0  # i contatori del giro si riferiscono alla prima persona (il proprietario)
        if notifications_suppressed(st):
            db.enqueue(chat, list(found_ids.values()))
            if first:
                report.queued += len(pending)
            log.info("Notifiche sospese per %s (pausa/notte): %d annunci in coda", chat or "destinatario", len(pending))
            continue
        delivered, deals = _deliver(pending, ntf, st, history, dry_run, report if first else None, pending_keys)
        sent_any |= delivered
        deal_keys |= deals
    db.mark_notified([found_ids[k] for k in sent_any])
    db.mark_deal([found_ids[k] for k in deal_keys])


def _deliver(pending: list, notifier: TelegramNotifier, settings: dict, history: list[dict], dry_run: bool,
             report: RunReport | None, pending_keys: set[str] | None = None) -> tuple[set[str], set[str]]:
    """Consegna a una persona: affari 🔥, poi i gruppi per carta con il tetto per giro.
    Restituisce (chiavi inviate, chiavi segnalate come affare)."""
    images = bool(settings.get("images", True))
    deal_pct = float(settings.get("deal_pct", 60) or 0)
    max_per_card = int(settings.get("max_per_card", 5) or 5)
    sent: set[str] = set()
    deals: set[str] = set()
    max_deals = int(settings.get("max_deals_per_run", 3) or 0)
    if deal_pct > 0 and max_deals > 0:
        candidates = []
        for lst, result in pending:
            if result.kind != "single" or not result.wanted or lst.price is None or lst.price < 1:
                continue
            median = pstats.median_for_deal(history, result.wanted[0].id, exclude_keys=pending_keys or {lst.key})
            # sotto il 25% della mediana è quasi sempre un errore di riconoscimento o un'inserzione sospetta
            if median and median * 0.25 <= lst.price <= median * deal_pct / 100.0:
                candidates.append((lst.price / median, lst, result, median))
        candidates.sort(key=lambda t: t[0])
        if len(candidates) > max_deals:
            log.info("Affari trovati: %d, inviati solo i %d migliori", len(candidates), max_deals)
        for _, lst, result, median in candidates[:max_deals]:
            ok = False if dry_run else notifier.notify_deal(lst, result, median, images)
            deals.add(lst.key)
            if ok:
                sent.add(lst.key)
            if report:
                report.deals += int(ok)
                report.notified += int(ok)
            log.info("AFFARE [%s] %s %.2f € (mediana %.2f)", lst.source, lst.title[:60], lst.price, median)
    rest = [(lst, res) for lst, res in pending if lst.key not in deals]
    # tetto ai messaggi per giro: oltre il limite restano solo nello storico (anti-valanga)
    max_msgs = int(settings.get("max_messages_per_run", 10) or 0)
    overflow_note = None
    if max_msgs > 0 and rest:
        from .notifier import group_matches
        groups = group_matches(rest)
        if len(groups) > max_msgs:
            keep_keys = {lst.key for _, _, members in groups[:max_msgs] for lst, _ in members}
            skipped_groups = groups[max_msgs:]
            n_over = sum(1 for lst, _ in rest if lst.key not in keep_keys)
            rest = [(lst, res) for lst, res in rest if lst.key in keep_keys]
            names = ", ".join(t.replace("🃏 ", "").replace("📦 ", "").replace("❔ ", "") for _, t, _ in skipped_groups[:12])
            overflow_note = (f"⚠️ Giro insolitamente ricco: {len(groups)} carte con novità, inviate le prime {max_msgs}. "
                             f"Le altre ({n_over} annunci) sono nello storico: {names}" + (" …" if len(skipped_groups) > 12 else "") +
                             "\nUsa /storico o /cerca <carta>.")
            log.info("Tetto messaggi: %d gruppi su %d inviati, %d annunci solo nello storico", max_msgs, len(groups), n_over)
    outcomes = [False] * len(rest)
    if rest and not dry_run:
        outcomes = notifier.notify_many(rest, max_per_card=max_per_card, images=images)
    if overflow_note and not dry_run:
        notifier.send(overflow_note, disable_preview=True)
    for (lst, _), ok in zip(rest, outcomes):
        if ok:
            sent.add(lst.key)
        if report:
            report.notified += int(ok)
    return sent, deals


_BARE_NUM_RE = re.compile(r"(?<![\d/])0*(\d{1,3})(?![\d/])")


def _refers_to(card, res, text: str) -> bool:
    """La ricerca mirata vuole proprio quella carta: numero completo (158/128) oppure nome con numero "nudo" (30C 158)
    o nome senza numeri; se il testo porta il numero di un'altra carta con lo stesso nome (Mew ex 152) non vale."""
    if any(r.card.id == card.id for r in res.refs):
        return True
    if not any(card.id in {c.id for c in a.candidates} for a in res.ambiguous):
        return res.notify and card.id in {c.id for c in res.wanted}
    siblings = {c.number for a in res.ambiguous for c in a.candidates if c.id != card.id and card.id in {x.id for x in a.candidates}}
    nums = {m.group(1) for m in _BARE_NUM_RE.finditer(text)}
    if card.number in nums:
        return True
    return not (nums & siblings)




def card_queries(card, index: CardIndex | None = None) -> list[str]:
    """Ricerca mirata: numero/totale più "nome + suffisso" per ogni suffisso della collezione."""
    cs = index.get_set(card.set_id) if index else None
    suffixes = (cs.card_suffixes if cs and cs.card_suffixes else [cs.keyword if cs else card.set_name])
    out = [f"{card.name} {card.number}/{card.printed_total}"] if card.printed_total else []
    out += [f"{card.name} {suf}" for suf in suffixes[:2]]
    return out


def search_card(index: CardIndex, db: Database, card, settings: dict | None = None, scrapers: dict | None = None,
                limit: int = 10, only_new: bool = False) -> tuple[list[tuple[Listing, "MatchResult"]], dict[str, str]]:
    """Ricerca mirata di una carta su tutte le fonti: restituisce gli annunci in vendita adesso, dal più economico.

    Include anche annunci già visti (con `only_new=True` restituisce solo quelli mai visti né registrati).
    Gli annunci vengono registrati nello storico e segnati come visti.
    """
    settings = settings or db.get_settings()
    matcher = Matcher(index, settings.get("set_keywords"))
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
    for name, listings_by_query, error in scrape_all(scrapers, card_queries(card, index)):
        if error:
            errors[name] = error
        for _q, listings in listings_by_query:
            for lst in listings:
                if lst.key in found:
                    continue
                res = matcher.analyze(lst.title, lst.description, {card.id}, lot_ratio, False, lst.is_auction, language)
                hit = _refers_to(card, res, f"{lst.title} {lst.description}")
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
                         res.ratio, True, image=lst.image, seller=lst.seller)
        db.mark_seen(lst.key, notified=True)
    return items[:limit], errors
