"""Ciclo completo con scraper finti e notifier finto."""
import os
import tempfile

from pokebot.db import Database
from pokebot.scrapers.base import BaseScraper, Listing
from pokebot.search import run_search


class FakeScraper(BaseScraper):
    name = "fake"
    label = "Fake"

    def __init__(self, listings):
        super().__init__()
        self.listings = listings
        self.queries = []

    def search(self, query, limit=60):
        self.queries.append(query)
        return list(self.listings)


class FakeNotifier:
    configured = True

    def __init__(self):
        self.sent = []

    def notify_listing(self, listing, result):
        self.sent.append((listing, result))
        return True

    def notify_many(self, items, max_per_card=5, images=True):
        return [self.notify_listing(l, r) for l, r in items]

    def notify_deal(self, listing, result, median, images=True):
        self.sent.append((listing, result))
        self.deals = getattr(self, "deals", []) + [(listing, median)]
        return True

    def send(self, text, disable_preview=False):
        self.texts = getattr(self, "texts", []) + [text]
        return True


def make_db():
    return Database(os.path.join(tempfile.mkdtemp(), "t.db"))


def test_run_search_notifies_once(index):
    db = make_db()
    db.set_wanted_bulk(["me55-131", "me55-130"], True)
    db.save_settings({"generic_queries": ["pokemon 30th"], "per_card_queries": True, "per_card_batch": 2})
    listings = [
        Listing("fake", "1", "Lapras 131/128 Pokemon 30th", "https://x/1", price=20, price_text="20 €"),
        Listing("fake", "2", "Lotto 30th: Lapras 131/128, Moltres 130/128, Articuno 132/128", "https://x/2"),
        Listing("fake", "3", "Lotto 30th: Lapras 131/128, Articuno 132/128, Zapdos 133/128", "https://x/3"),
        Listing("fake", "4", "Cerco Lapras 131/128 30th", "https://x/4"),
        Listing("fake", "5", "Lapras 131/128 Pokemon 30th asta", "https://x/5", is_auction=True),
    ]
    scraper = FakeScraper(listings)
    notifier = FakeNotifier()
    rep = run_search(index, db, notifier=notifier, scrapers={"fake": scraper})
    assert rep.queries == 3  # 1 generica + 2 per carta
    assert any("131/128" in q for q in scraper.queries)
    assert rep.matches == 2 and rep.notified == 2
    assert {l.listing_id for l, _ in notifier.sent} == {"1", "2"}
    found = db.list_found()
    assert len(found) == 2 and {f["kind"] for f in found} == {"single", "lot"}

    # secondo ciclo: niente di nuovo
    rep2 = run_search(index, db, notifier=notifier, scrapers={"fake": scraper})
    assert rep2.matches == 0 and rep2.new_listings == 0


def test_max_price_filter(index):
    db = make_db()
    db.set_wanted_bulk(["me55-131"], True)
    db.save_settings({"generic_queries": ["x"], "per_card_queries": False, "max_price": 10})
    scraper = FakeScraper([Listing("fake", "1", "Lapras 131/128 30th", "https://x/1", price=50)])
    notifier = FakeNotifier()
    rep = run_search(index, db, notifier=notifier, scrapers={"fake": scraper})
    assert rep.matches == 0 and not notifier.sent


def test_no_wanted_skips(index):
    db = make_db()
    scraper = FakeScraper([])
    rep = run_search(index, db, notifier=FakeNotifier(), scrapers={"fake": scraper})
    assert rep.queries == 0 and scraper.queries == []


def test_source_error_tolerance(index):
    """Un errore isolato non interrompe la fonte; tre consecutivi sì."""
    from pokebot.scrapers.base import ScraperError

    class Flaky(FakeScraper):
        def __init__(self, fail_on):
            super().__init__([Listing("fake", "ok", "Lapras 131/128 30th", "https://x/ok")])
            self.fail_on = fail_on
            self.n = 0

        def search(self, query, limit=60):
            self.n += 1
            self.queries.append(query)
            if self.n in self.fail_on:
                raise ScraperError("Fake: pagina senza annunci")
            return list(self.listings)

    db = make_db()
    db.set_wanted_bulk(["me55-131"], True)
    db.save_settings({"generic_queries": ["a", "b", "c", "d", "e"], "per_card_queries": False})
    s1 = Flaky(fail_on={2})
    rep = run_search(index, db, notifier=FakeNotifier(), scrapers={"fake": s1})
    assert len(s1.queries) == 5 and rep.errors["fake"].startswith("parziale") and rep.per_source["fake"] == 4
    db2 = make_db()
    db2.set_wanted_bulk(["me55-131"], True)
    db2.save_settings({"generic_queries": ["a", "b", "c", "d", "e"], "per_card_queries": False})
    s2 = Flaky(fail_on={1, 2, 3})
    rep2 = run_search(index, db2, notifier=FakeNotifier(), scrapers={"fake": s2})
    assert len(s2.queries) == 3 and not rep2.errors["fake"].startswith("parziale")


def _found_history(db, card_id, label, prices, source="vinted"):
    for i, p in enumerate(prices):
        db.add_found(f"{source}:h{i}", source, f"{label} storico {i}", f"https://h/{i}", f"{p:.2f} €", "", "single",
                     [{"id": card_id, "label": label, "sure": True}], 1.0, True)


def test_deal_alert_and_grouping(index):
    db = make_db()
    db.set_wanted_bulk(["me55-131"], True)
    db.save_settings({"generic_queries": ["x"], "per_card_queries": False, "deal_pct": 60})
    _found_history(db, "me55-131", "Lapras 131/128", [20, 22, 18, 25, 21])  # mediana 21
    listings = [
        Listing("fake", "cheap", "Lapras 131/128 30th", "https://x/1", price=9, price_text="9 €"),      # 43% -> affare
        Listing("fake", "normal", "Lapras 131/128 30th ITA", "https://x/2", price=19, price_text="19 €"),
    ]
    notifier = FakeNotifier()
    rep = run_search(index, db, notifier=notifier, scrapers={"fake": FakeScraper(listings)})
    assert rep.deals == 1 and rep.matches == 2 and rep.notified == 2
    assert [l.listing_id for l, _ in notifier.deals] == ["cheap"]
    found = {f["listing_key"]: f for f in db.list_found()}
    assert found["fake:cheap"]["deal"] == 1 and found["fake:normal"]["deal"] == 0


def test_pause_queues_and_resume_flushes(index):
    from pokebot.search import flush_queued
    db = make_db()
    db.set_wanted_bulk(["me55-131"], True)
    db.save_settings({"generic_queries": ["x"], "per_card_queries": False, "paused": True})
    notifier = FakeNotifier()
    rep = run_search(index, db, notifier=notifier, scrapers={"fake": FakeScraper([
        Listing("fake", "1", "Lapras 131/128 30th", "https://x/1", price=10, price_text="10 €")])})
    assert rep.queued == 1 and rep.notified == 0 and notifier.sent == []
    assert len(db.queued_found()) == 1
    db.save_settings({"paused": False})
    n = flush_queued(db, notifier, db.get_settings(), index.by_id)
    assert n == 1 and len(notifier.sent) == 1 and db.queued_found() == []
    assert db.list_found()[0]["notified"] == 1 and any("Accumulati" in t for t in notifier.texts)


def test_quiet_hours(monkeypatch):
    from pokebot import search
    from datetime import datetime

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 5, 23, 30, tzinfo=tz)

    monkeypatch.setattr(search, "datetime", FakeDT)
    assert search.notifications_suppressed({"quiet_hours": [23, 8]})
    assert not search.notifications_suppressed({"quiet_hours": [8, 20]})
    assert search.notifications_suppressed({"paused": True})
    assert not search.notifications_suppressed({})
