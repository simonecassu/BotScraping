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
