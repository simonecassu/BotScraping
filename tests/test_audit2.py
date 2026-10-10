"""Regressioni del secondo audit: inseguimenti, consegne, riepilogo Light, limiti, permessi, privacy dello stato."""
import datetime as dt
import time
from zoneinfo import ZoneInfo

from pokebot import cardmarket, plans, search, watch
from pokebot.scrapers.base import Listing
from pokebot.telegram_bot import CommandHandler, TelegramCommands
from tests.test_audit_search import _two_people
from tests.test_search import FakeNotifier, FakeScraper, make_db
from tests.test_telegram import FakeClient

ROME = ZoneInfo("Europe/Rome")


class FailingNotifier(FakeNotifier):
    def notify_many(self, items, max_per_card=5, images=True):
        return [False] * len(items)


def test_chase_skips_listings_the_search_already_found(index):
    db = make_db()
    db.save_settings({"sources": ["fake"], "quiet_hours": None})
    watch.add_watch(db, "me55-131", 3600, 300)
    db.add_found("fake:X", "fake", "Lapras 131/128", "https://f/x", "10 €", "", "single",
                 [{"id": "me55-131", "label": "Lapras", "sure": True}], None, True)  # consegnato dalla ricerca completa
    n = FakeNotifier()
    scr = FakeScraper([Listing("fake", "X", "Lapras 131/128 30th", "https://f/x", price=10.0, price_text="10 €")])
    watch.run_watches(index, db, n, {"fake": scr})
    assert n.sent == []


def test_chase_retries_listings_whose_send_failed(index):
    db = make_db()
    db.save_settings({"sources": ["fake"], "quiet_hours": None})
    watch.add_watch(db, "me55-131", 3600, 300)
    scr = FakeScraper([Listing("fake", "Y", "Lapras 131/128 30th", "https://f/y", price=10.0, price_text="10 €")])
    watch.run_watches(index, db, FailingNotifier(), {"fake": scr})
    assert watch.list_watches(db)["me55-131"]["sent"] == [] and watch.found_log(db) == []
    w = watch.list_watches(db)
    w["me55-131"]["last"] -= 600
    db.set_kv("watches:me", w)
    n = FakeNotifier()
    watch.run_watches(index, db, n, {"fake": scr})
    assert [l.key for l, _ in n.sent] == ["fake:Y"]


def test_paused_chases_do_not_wake_the_bridge_and_still_expire(index):
    from pokebot.webapp_export import build_summary
    db = make_db()
    db.save_user_prefs("me", {"paused": True})
    watch.add_watch(db, "me55-131", 3600, 300)
    assert build_summary(index, db)["next_watch"] is None
    w = watch.list_watches(db)
    w["me55-131"]["until"] = time.time() - 1
    db.set_kv("watches:me", w)
    watch.run_watches(index, db, FakeNotifier(), {"fake": FakeScraper([])})
    assert watch.list_watches(db) == {}


def test_light_digest_goes_back_to_19_after_a_missed_evening():
    db = make_db()
    day = dt.datetime(2026, 10, 5, tzinfo=ROME)
    at = lambda d, h, m=0: day.replace(day=d, hour=h, minute=m).timestamp()  # noqa: E731
    queued_at = at(5, 15)
    assert not plans.digest_due(db, "2", at(5, 18, 30), oldest=queued_at)
    assert plans.digest_due(db, "2", at(6, 0, 10), oldest=queued_at)  # nessun giro tra le 19 e mezzanotte: recupero
    plans.mark_digest(db, "2", at(6, 0, 10))
    assert not plans.digest_due(db, "2", at(6, 9), oldest=at(6, 8))
    assert plans.digest_due(db, "2", at(6, 19, 5), oldest=at(6, 8))  # la sera dopo di nuovo alle 19
    plans.mark_digest(db, "2", at(6, 19, 5))
    assert not plans.digest_due(db, "2", at(7, 0, 10), oldest=at(6, 20))


def test_failed_queue_sends_stay_queued_for_a_day(index):
    db = _two_people(index)
    fid = db.add_found("k1", "vinted", "Lapras 131", "https://v/1", "10 €", "", "single",
                       [{"id": "me55-131", "label": "Lapras", "sure": True}], None, False)
    db.enqueue("2", [fid])
    assert search.flush_queued(db, FailingNotifier(), {}, index.by_id, "2") == 0
    assert db.queued_count("2") == 1  # Telegram non ha risposto: si riprova
    with db.connect() as c:
        c.execute("UPDATE found SET created_at = ?", (time.time() - 2 * 86400,))
    search.flush_queued(db, FailingNotifier(), {}, index.by_id, "2")
    assert db.queued_count("2") == 0  # dopo un giorno si lascia perdere


def test_queued_lot_without_missing_cards_is_dropped(index):
    db = _two_people(index)
    fid = db.add_found("lot", "vinted", "Lotto 30th", "https://v/l", "30 €", "", "lot",
                       [{"id": "me55-131", "label": "Lapras", "sure": True}], 0.5, False)
    db.enqueue("2", [fid])
    db.set_kv("active_sets:2", [])  # collezione spenta
    n = FakeNotifier()
    assert search.flush_queued(db, n, {}, index.by_id, "2") == 0 and n.sent == [] and db.queued_count("2") == 0


def test_light_cannot_chase_two_cards(index, monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    db = make_db()
    db.set_kv("telegram_chat_id", "1")
    db.add_chat_id("2")
    plans.set_light(db, "2")
    h = CommandHandler(index, db)
    h.handle("/insegui 152", "2")
    h.handle("/insegui 151 152", "2")
    assert list(watch.list_watches(db, "2")) == ["me55-152"]


def test_non_owner_search_limits(index, monkeypatch):
    import pokebot.telegram_bot as tb
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(search, "search_card", lambda *a, **k: ([], {}))
    db = make_db()
    db.set_kv("telegram_chat_id", "1")
    db.add_chat_id("2")
    db.add_chat_id("3")
    plans.set_light(db, "2")
    plans.set_pro(db, "3", lifetime=True)
    h = CommandHandler(index, db)
    for chat in ("2", "3"):
        assert "prossima tra" not in h.handle("/cerca 145", chat).text
        assert "prossima tra" in h.handle("/cerca 146", chat).text
    six_min_later = time.time() + 6 * 60
    monkeypatch.setattr(tb.time, "time", lambda: six_min_later)
    assert "prossima tra" not in h.handle("/cerca 146", "3").text  # abbonato: ogni 5 minuti
    assert "prossima tra 9 min" in h.handle("/cerca 146", "2").text  # Light: ogni 15
    assert all("prossima tra" not in h.handle("/cerca 145", "1").text for _ in range(3))  # il proprietario mai


def test_kicked_user_cannot_rejoin_with_an_old_invite(index, monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    db = make_db()
    client = FakeClient([])
    tc = TelegramCommands(index, db, client=client)
    tc.handle_payload({"chat_id": 1, "text": "/start"})
    code = db.get_kv("invite_code") or {}
    if not code:
        tc.handler.handle("/invita", "1")
        code = db.get_kv("invite_code")
    db.add_chat_id("5")
    tc.handler.handle("/espelli 5", "1")
    tc.handle_payload({"chat_id": 5, "text": f"/start {code['code']}"})
    assert "5" not in db.chat_ids() and db.is_banned("5")


def test_huge_numbers_do_not_crash(index, monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    db = make_db()
    db.set_kv("telegram_chat_id", "1")
    db.add_chat_id("2")
    h = CommandHandler(index, db)
    assert "✅" in h.handle("/piano 2 pro 99999999999999", "1").text
    assert h.handle("/utenti", "1").text
    assert plans._date(8.6e18) == "?"
    assert watch.parse_duration("9" * 400 + "h") == 1000 * 3600
    assert "Inseguo" in h.handle("/insegui 151 " + "9" * 400 + "h", "2").text


def test_paying_stranger_starts_with_the_intro(index, monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    db = make_db()
    db.set_kv("telegram_chat_id", "1")
    client = FakeClient([])
    tc = TelegramCommands(index, db, client=client)
    tc.handle_payload({"chat_id": 9, "text": "/pagamento", "payment": {
        "currency": "XTR", "total_amount": 250, "invoice_payload": "pro:3", "telegram_payment_charge_id": "c9",
        "subscription_expiration_date": time.time() + 30 * 86400}})
    assert "9" in db.chat_ids() and plans.onboarding(db, "9") and plans.tier(db, "9") == "pro"


def test_cardmarket_name_fallback_needs_a_unique_name():
    class C:
        def __init__(self, name, number):
            self.name, self.number = name, number
    tcg = [{"id": "30th-017", "name": cardmarket._norm("Lapras"), "num": "17"}]
    assert cardmarket.match_card(C("Lapras", "17"), tcg, name_unique=False) == "30th-017"
    assert cardmarket.match_card(C("Lapras", "131"), tcg, name_unique=False) is None
    assert cardmarket.match_card(C("Lapras", "4"), tcg) == "30th-017"  # ristampa con nome unico


def test_state_shows_only_own_cards_of_each_listing(index, monkeypatch):
    from pokebot.webapp_export import build_state
    db = _two_people(index)
    db.set_wanted_bulk(["me55-130"], False, db.albums_of("2")["me55"])
    db.add_found("lot", "vinted", "Lotto", "https://v/l", "30 €", "", "lot",
                 [{"id": "me55-131", "label": "Lapras", "sure": True}, {"id": "me55-130", "label": "Moltres", "sure": True}],
                 1.0, True)
    st = build_state(index, db, chat_id="2")
    assert [r["cards"] for r in st["found"]] == [["me55-131"]]


def test_reports_one_a_day_multiline_and_owner_list(index, monkeypatch):
    from pokebot import reports
    from pokebot.webapp_export import build_state
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    db = make_db()
    db.set_kv("telegram_chat_id", "1")
    db.add_chat_id("2")
    db.set_user_name("2", "Anna <b>")
    h = CommandHandler(index, db)
    assert "Scrivi la segnalazione" in h.handle("/segnala", "2").text
    r = h.handle("/segnala oggi niente avviso\n/cerca 151 non mi va\nvorrei i filtri", "2")
    assert "Grazie" in r.text and r.sends[0][0] == "1" and "Anna &lt;b&gt;" in r.sends[0][1]
    assert reports.mine(db, "2")[0]["text"] == "oggi niente avviso\n/cerca 151 non mi va\nvorrei i filtri"  # tutto il messaggio
    assert "già scritto" in h.handle("/segnala ancora", "2").text and len(reports.all_reports(db)) == 1
    assert "Solo il proprietario" in h.handle("/segnalazioni", "2").text
    assert "vorrei i filtri" in h.handle("/segnalazioni", "1").text
    mine, owner = build_state(index, db, chat_id="2")["reports"], build_state(index, db, chat_id="1")["reports"]
    assert mine["sent_today"] and "all" not in mine and owner["all"][0]["name"] == "Anna <b>"
    tomorrow = time.time() + 86400
    assert not reports.sent_today(db, "2", tomorrow)


def test_copies_set_exact_number_with_short_confirmation(index):
    db = make_db()
    h = CommandHandler(index, db)
    r = h.handle("/doppioni imposta me55:131 3")
    assert "ne hai 4 (3 doppioni)" in r.text and db.copies("me") == {"me55-131": 3}
    assert "ne hai 2 (1 doppione)" in h.handle("/doppioni imposta me55:131 1").text
    assert "ne hai 1 (nessun doppione)" in h.handle("/doppioni imposta me55:131 0").text
    assert db.copies("me") == {}
    assert "Usa" in h.handle("/doppioni imposta me55:131").text
    both = h.handle("/doppioni imposta me55:131 2\n/mancanti")  # in un messaggio con altro: nessuna riga vuota
    assert not both.text.startswith("\n") and db.copies("me") == {"me55-131": 2}


def test_variants_and_decks_are_not_the_classic_card(matcher):
    wanted = {"me55c-149"}
    assert not matcher.analyze("Deck Lugia ex - classic collection", "", wanted).notify  # un mazzo con una Lugia ex
    assert not matcher.analyze("Lugia V classic collection", "", wanted).notify
    assert not matcher.analyze("Dark Lugia classic collection", "", wanted).notify
    r = matcher.analyze("Lugia 149 Classic Collection 30th", "", wanted)
    assert r.notify and [x.card.id for x in r.refs] == ["me55c-149"]
    assert matcher.analyze("Pikachu ex 149/128 30th", "", {"me55-149"}).notify  # il nome lungo della collezione vale


def test_chase_ignores_a_deck_of_a_different_lugia(index):
    db = make_db()
    db.save_settings({"sources": ["fake"], "quiet_hours": None})
    watch.add_watch(db, "me55c-149", 3600, 300)
    n = FakeNotifier()
    scr = FakeScraper([Listing("fake", "D", "Deck Lugia ex - classic collection", "https://f/d", price=18.0, price_text="18 €"),
                       Listing("fake", "L", "Lugia 149 Classic Collection 30th", "https://f/l", price=25.0, price_text="25 €")])
    watch.run_watches(index, db, n, {"fake": scr})
    assert [l.key for l, _ in n.sent] == ["fake:L"]
