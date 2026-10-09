"""Regressioni dell'audit: matcher, prezzi, filtri personali, coda, inseguimenti, notifiche."""
import datetime as dt
from zoneinfo import ZoneInfo

from pokebot import plans, search
from pokebot.notifier import TelegramNotifier
from pokebot.scrapers.base import Listing, parse_price
from tests.test_search import FakeScraper, make_db


def test_italian_listing_is_not_french(matcher):
    r = matcher.analyze("Carte pokemon Mewtwo ex 151/128 30th", "", {"me55-151"}, language="ita")
    assert r.notify and r.kind == "single"
    assert matcher.analyze("Dracaufeu cartes 30 ans", "", {"me55-151"}, language="ita").reason == "lingua diversa (francese)"


def test_euro_amount_counts_as_sale_and_description_words_dont_make_lots(matcher):
    assert matcher.analyze("Lapras 131/128 scambio", "10€", {"me55-131"}).notify
    assert matcher.analyze("Lapras 131/128 scambio", "solo scambi", {"me55-131"}).reason == "solo scambio"
    r = matcher.analyze("Mewtwo ex 151/128 30th", "Carta da collezione, protezione completa", {"me55-151"})
    assert r.kind == "single"


def test_prices_with_thousands():
    assert parse_price("1,250.00 €") == 1250.0 and parse_price("1.250,00 €") == 1250.0 and parse_price("15,00 €") == 15.0


def test_notifier_does_not_copy_the_owner(monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "1")
    assert TelegramNotifier(chat_ids=["2"]).chat_ids == ["2"]
    assert TelegramNotifier().chat_ids == ["1"]


def _two_people(index):
    db = make_db()
    db.set_kv("telegram_chat_id", "1")
    db.add_chat_id("2")
    for chat in ("1", "2"):
        album, _ = db.ensure_album(chat, "me55")
        db.set_wanted_bulk(["me55-131", "me55-130"], True, album)
        db.set_kv(f"active_sets:{chat}", ["me55"])
        plans.set_pro(db, chat, lifetime=True)
    db.save_settings({"generic_queries": ["x"], "per_card_queries": False})
    return db


def _deliveries(monkeypatch):
    got = {}

    def fake_deliver(pending, ntf, st, *a, **k):
        got.setdefault(ntf.chat_ids[0], []).extend(lst.listing_id for lst, _ in pending)
        return {lst.key for lst, _ in pending}, set(), set()
    monkeypatch.setattr(search, "_deliver", fake_deliver)
    return got


def test_personal_price_language_and_lot_filters(index, monkeypatch):
    db = _two_people(index)
    db.save_user_prefs("2", {"max_price": 15, "language": "tutte", "lot_min_ratio": 0.9})
    got = _deliveries(monkeypatch)
    search.run_search(index, db, scrapers={"fake": FakeScraper([
        Listing("fake", "cheap", "Lapras 131/128 30th", "u1", price=10, price_text="10 €"),
        Listing("fake", "dear", "Lapras 131/128 30th", "u2", price=40, price_text="40 €"),
        Listing("fake", "eng", "Lapras 131/128 30th english", "u3", price=9, price_text="9 €"),
        Listing("fake", "lot", "Lotto 30th Lapras 131/128 Moltres 130/128 Zapdos 133/128", "u4", price=12, price_text="12 €"),
    ])})
    assert sorted(got["1"]) == ["cheap", "dear", "lot"]           # italiano, nessun limite, lotto 2/3 ≥ 50%
    assert sorted(got["2"]) == ["cheap", "eng"]                   # max 15 €, ogni lingua, lotto 2/3 < 90%


def test_collection_off_and_owned_card_are_not_delivered(index, monkeypatch):
    db = _two_people(index)
    db.set_kv("active_sets:2", [])                                 # 2 ha spento la 30th
    got = _deliveries(monkeypatch)
    search.run_search(index, db, scrapers={"fake": FakeScraper([
        Listing("fake", "a", "Lapras 131/128 30th", "u1", price=10, price_text="10 €")])})
    assert got == {"1": ["a"]}


def test_watch_hit_is_still_delivered_to_others(index, monkeypatch):
    from pokebot import watch
    db = _two_people(index)
    watch.add_watch(db, "me55-131", chat_id="1")
    sent = []

    class N:
        def notify_many(self, items, max_per_card=5, images=True):
            sent.extend(lst.listing_id for lst, _ in items)
            return [True] * len(items)

        def send(self, *a, **k):
            return True

    scr = {"fake": FakeScraper([Listing("fake", "w1", "Lapras 131/128 30th", "u1", price=10, price_text="10 €")])}
    watch.run_watches(index, db, notifier=N(), scrapers=scr)
    assert sent == ["w1"] and not db.is_seen("fake:w1")           # la ricerca mirata non nasconde l'annuncio agli altri
    watch.run_watches(index, db, notifier=N(), scrapers=scr)        # non rimandato dallo stesso inseguimento
    got = _deliveries(monkeypatch)
    search.run_search(index, db, scrapers=scr)
    assert got == {"2": ["w1"]}                                      # 1 l'ha già avuto dall'inseguimento


def test_queue_drops_cards_found_meanwhile(index):
    db = _two_people(index)
    album = db.albums_of("2")["me55"]
    fid = db.add_found("fake:q", "fake", "Lapras", "u", "5 €", "", "single", [{"id": "me55-131", "sure": True}], None, False)
    db.enqueue("2", [fid])
    db.set_wanted_bulk(["me55-131"], False, album)                 # nel frattempo l'ha trovata
    sent = []

    class N:
        def send(self, *a, **k):
            sent.append("header")
            return True

        def notify_many(self, items, **k):
            sent.extend(items)
            return [True] * len(items)

    assert search.flush_queued(db, N(), db.settings_for("2"), index.by_id, "2") == 0 and sent == []
    assert db.queued_found("2") == []


def test_kicked_person_queue_is_dropped(index):
    db = _two_people(index)
    fid = db.add_found("fake:k", "fake", "Lapras", "u", "5 €", "", "single", [{"id": "me55-131", "sure": True}], None, False)
    db.enqueue("2", [fid])
    db.remove_chat_id("2")
    assert search.flush_all_queues(db, index.by_id) == 0 and db.queued_found("2") == []


def test_light_digest_after_quiet_evening():
    db = make_db()
    rome = ZoneInfo("Europe/Rome")
    today = dt.datetime.now(rome).replace(hour=9, minute=0, second=0, microsecond=0)
    morning = today.timestamp()
    assert not plans.digest_due(db, "2", morning)                                    # di mattina no
    old = (today - dt.timedelta(days=1)).replace(hour=15).timestamp()                # doveva uscire ieri alle 19
    assert plans.digest_due(db, "2", morning, oldest=old)
    assert not plans.digest_due(db, "2", morning, oldest=morning - 600)              # appena arrivato: aspetta le 19
    assert plans.digest_due(db, "2", today.replace(hour=19, minute=5).timestamp())
    plans.mark_digest(db, "2", today.replace(hour=19, minute=5).timestamp())
    assert not plans.digest_due(db, "2", today.replace(hour=20).timestamp(), oldest=old)
