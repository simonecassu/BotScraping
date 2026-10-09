import datetime as dt
import os
import tempfile
import time
from zoneinfo import ZoneInfo

from pokebot import plans
from pokebot.db import Database
from pokebot.telegram_bot import TelegramCommands
from tests.test_telegram import FakeClient


class PayClient(FakeClient):
    def create_invoice_link(self, title, description, payload, amount, period=0):
        self.invoice = (payload, amount, period)
        return f"https://t.me/$inv_{payload}"


def setup(index, monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))
    client = PayClient([])
    tc = TelegramCommands(index, db, client=client)
    monkeypatch.setattr("pokebot.telegram_bot.TelegramClient", lambda *a, **k: client)
    tc.handle_payload({"chat_id": 1, "text": "/start", "name": "Owner"})
    return db, client, tc


def at(hour, days=0):
    d = dt.datetime.now(ZoneInfo("Europe/Rome")).replace(hour=hour, minute=10, second=0) + dt.timedelta(days=days)
    return d.timestamp()


def test_migration_keeps_existing_people_full(index, monkeypatch):
    db, _, _ = setup(index, monkeypatch)
    db.add_chat_id("2")
    plans.migrate(db)
    assert plans.tier(db, "1") == "owner" and plans.tier(db, "2") == "pro" and plans.get(db, "2")["lifetime"]
    db.add_chat_id("3")
    plans.migrate(db)  # una volta sola
    assert plans.tier(db, "3") == "light"


def test_trial_reminder_end_and_light(index, monkeypatch):
    db, client, tc = setup(index, monkeypatch)
    plans.migrate(db)
    tc.handle_payload({"chat_id": 2, "text": "/start", "name": "Anna"})
    tc.handler.handle("/approva 2", "1")
    assert plans.tier(db, "2") == "trial"
    db.set_kv("deals_channel", "@affari")
    now = time.time()
    assert plans.check_trials(db, client, now) == []
    out = plans.check_trials(db, client, now + 4.5 * 86400)
    assert out == ["…2: promemoria fine prova"] and "Domani finisce" in client.sent[-1][1]
    assert plans.check_trials(db, client, now + 4.6 * 86400) == []
    # due inseguimenti durante la prova: alla fine ne resta uno, ogni 2 ore
    tc.handler.handle("/insegui 131 151", "2")
    assert len(db.get_kv("watches:2")) == 2
    out = plans.check_trials(db, client, now + 5.1 * 86400)
    assert out == ["…2: fine prova, passato a Light"]
    assert plans.tier(db, "2", now + 5.1 * 86400) == "light"
    text = client.sent[-1][1]
    assert "prova di 5 giorni è finita" in text and "250" in text
    rows = client.last_buttons
    assert [b[1] for b in rows[0]] == [f"/voto {n}" for n in range(1, 6)]
    assert rows[1][0][1] == "https://t.me/$inv_pro:2" and client.invoice == ("pro:2", 250, 30 * 86400)
    assert rows[2][0][1] == "https://t.me/affari"
    w = db.get_kv("watches:2")
    assert len(w) == 1 and list(w.values())[0]["every"] == 7200
    assert plans.check_trials(db, client, now + 6 * 86400) == []  # una volta sola


def test_light_limits_and_vote(index, monkeypatch):
    db, client, tc = setup(index, monkeypatch)
    db.add_chat_id("2")
    plans.set_light(db, "2")
    h = tc.handler
    r = h.handle("/insegui 131", "2")
    assert "ogni 2 ore" in r.text
    assert "una carta alla volta" in h.handle("/insegui 151", "2").text
    for sid in ("me55", "me55c", "sv1", "sv2", "sv3", "sv4"):
        db.ensure_album("2", sid)
    assert plans.collections_count(db, "2") >= 5
    assert "fino a 5 collezioni" in h.handle("/collezione sv8", "2").text
    r = h.handle("/voto 4", "2")
    assert "Grazie" in r.text and r.sends and r.sends[0][0] == "1" and "4/5" in r.sends[0][1]
    h.handle("/recensione mi piace", "2")
    assert "mi piace" in h.handle("/recensioni", "1").text and "4.0/5" in h.handle("/recensioni", "1").text
    assert "Solo il proprietario" in h.handle("/recensioni", "2").text
    r = h.handle("/abbonati", "2")
    assert r.buttons and r.buttons[0][0][1].startswith("https://t.me/$inv")
    # il proprietario cambia piano
    assert "Pro per sempre" in h.handle("/piano 2 sempre", "1").text and plans.tier(db, "2") == "pro"
    assert "Light" in h.handle("/piano 2 light", "1").text
    assert "Prova" in h.handle("/piano 2 prova 3", "1").text and plans.tier(db, "2") == "trial"
    assert "Prova" in h.handle("/piano", "2").text  # un utente vede solo il suo
    assert "Prova" in h.handle("/piano 1 sempre", "2").text and plans.tier(db, "1") == "owner"


def test_payment_makes_pro_and_ignores_fakes(index, monkeypatch):
    db, client, tc = setup(index, monkeypatch)
    db.add_chat_id("2")
    plans.set_light(db, "2")
    exp = time.time() + 30 * 86400
    # un utente che scrive il comando non attiva niente
    tc.handle_payload({"chat_id": 2, "text": "/pagamento"})
    assert plans.tier(db, "2") == "light"
    tc.handle_payload({"chat_id": 2, "text": "/pagamento", "payment": {"currency": "EUR", "invoice_payload": "pro:2"}})
    assert plans.tier(db, "2") == "light"
    tc.handle_payload({"chat_id": 2, "text": "/pagamento", "name": "Anna", "payment": {
        "currency": "XTR", "total_amount": 250, "invoice_payload": "pro:2", "telegram_payment_charge_id": "ch1",
        "subscription_expiration_date": exp, "is_recurring": True, "is_first_recurring": True}})
    assert plans.tier(db, "2") == "pro" and abs(plans.get(db, "2")["pro_until"] - exp) < 1
    assert any(c == "2" and "Grazie" in t for c, t in client.sent)
    assert any(c == "1" and "Nuovo abbonamento" in t and "250" in t for c, t in client.sent)
    assert plans.tier(db, "2", exp + 10) == "light"
    # chi paga dalla lista d'attesa entra subito
    db.add_to_waitlist("5", "Bea")
    tc.handle_payload({"chat_id": 5, "text": "/pagamento", "payment": {
        "currency": "XTR", "total_amount": 250, "invoice_payload": "pro:5", "subscription_expiration_date": exp}})
    assert "5" in db.chat_ids() and "5" not in db.waitlist() and plans.tier(db, "5") == "pro"


def test_light_gets_daily_digest(index, monkeypatch):
    from pokebot import search
    db, _, _ = setup(index, monkeypatch)
    db.add_chat_id("2")
    plans.set_light(db, "2")
    album, _ = db.ensure_album("2", "me55")
    db.set_wanted_bulk(["me55-131"], True, album)
    sent = []

    class N:
        def __init__(self, chat_ids=None):
            self.chat = (chat_ids or [""])[0]

        def send(self, text, disable_preview=False):
            sent.append((self.chat, text))
            return True

        def notify_many(self, items, max_per_card=5, images=True):
            sent.append((self.chat, f"{len(items)} annunci"))
            return [True] * len(items)

    monkeypatch.setattr(search, "TelegramNotifier", N)
    fid = db.add_found("k1", "vinted", "Lapras 131", "https://v/1", "10 €", "", "single",
                       [{"id": "me55-131", "label": "Lapras", "sure": True}], None, False)
    db.enqueue("2", [fid])
    from pokebot.webapp_export import build_summary
    assert build_summary(index, db)["queued"] == 0  # la coda della Light non sveglia il bot ogni 5 minuti
    monkeypatch.setattr(plans.time, "time", lambda: at(10))
    assert search.flush_all_queues(db, index.by_id) == 0 and sent == []
    monkeypatch.setattr(plans.time, "time", lambda: at(19))
    assert search.flush_all_queues(db, index.by_id) == 1
    assert "riepilogo di oggi" in sent[0][1] and sent[0][0] == "2"
    fid2 = db.add_found("k2", "vinted", "Lapras 131 bis", "https://v/2", "11 €", "", "single",
                        [{"id": "me55-131", "label": "Lapras", "sure": True}], None, False)
    db.enqueue("2", [fid2])
    assert search.flush_all_queues(db, index.by_id) == 0  # già mandato oggi
    monkeypatch.setattr(plans.time, "time", lambda: at(19, days=1))
    assert search.flush_all_queues(db, index.by_id) == 1


def test_onboarding_welcome_and_no_notifications_until_ready(index, monkeypatch):
    from pokebot import search
    db, client, tc = setup(index, monkeypatch)
    plans.migrate(db)
    tc.handle_payload({"chat_id": 2, "text": "/start", "name": "Anna"})
    r = tc.handler.handle("/approva 2", "1")
    welcome = r.sends[0]
    assert welcome[0] == "2" and "Prima riempi l'album" in welcome[1] and welcome[2] == [[("🔔 Album pronto, attiva le notifiche", "/riprendi")]]
    assert plans.onboarding(db, "2")
    assert "Prima riempi" in tc.handler.handle("/start", "2").text

    delivered = []
    monkeypatch.setattr(search, "_deliver", lambda pending, ntf, st, *a, **k: (delivered.append(len(pending)) or (set(), set(), set())))

    from pokebot.matcher import MatchResult
    from pokebot.scrapers.base import Listing
    Res = MatchResult("single", True, "x", wanted=[index.by_id["me55-131"]])
    Lst = Listing("vinted", "k", "Lapras", "u", price=5.0, price_text="5 €")

    monkeypatch.setattr(search, "TelegramNotifier", lambda chat_ids=None: None)
    rep = search.RunReport(started_at=0)
    search._notify_pending([(Lst, Res)], db, rep, False)
    # il proprietario non ha la 131 tra le mancanti, Anna sì ma sta ancora segnando: nessuna consegna né coda
    assert delivered == [] and db.queued_found("2") == []
    r = tc.handler.handle("/riprendi", "2")
    assert "Notifiche accese" in r.text and r.run_search and not plans.onboarding(db, "2")
    Lst = Listing("vinted", "k2", "Lapras", "u", price=5.0, price_text="5 €")
    search._notify_pending([(Lst, Res)], db, rep, False)
    assert delivered == [1]


def test_light_max_three_active_collections(index, monkeypatch):
    db, client, tc = setup(index, monkeypatch)
    db.add_chat_id("2")
    db.set_kv("active_sets:2", ["me55", "me55c", "sv1", "sv2", "sv3"])
    plans.set_light(db, "2")  # al passaggio restano accese le prime 3 (la 30th conta una volta)
    assert db.active_sets("2") == ["me55", "me55c", "sv1", "sv2"] and plans.active_count(db, "2") == 3
    from pokebot.cards import Card, CardSet
    cs = CardSet(id="sv3", name="Test 3", name_it="", total=1, release="", printed_total=1, cards=[Card(id="sv3-1", set_id="sv3", number="1", name="X", rarity="",
                                                                       set_name="Test 3", supertype="", image="", image_large="",
                                                                       printed_total=1)])
    monkeypatch.setattr("pokebot.collections.follow", lambda db_, chat, sid: (cs, db_.ensure_album(chat, sid)[0], False))
    r = tc.handler.handle("/collezione sv3 attiva", "2")
    assert "3 collezioni" in r.text and "sv3" not in db.active_sets("2")
    tc.handler.handle("/collezione sv3 attiva", "1")  # il proprietario non ha limiti
    assert "sv3" in db.active_sets("1")


def test_owner_refund(index, monkeypatch):
    db, client, tc = setup(index, monkeypatch)
    db.add_chat_id("2")
    calls = []
    client.refund_stars = lambda uid, charge: (calls.append((uid, charge)) or (True, ""))
    tc.handle_payload({"chat_id": 2, "text": "/pagamento", "payment": {
        "currency": "XTR", "total_amount": 250, "invoice_payload": "pro:2", "telegram_payment_charge_id": "ch9",
        "subscription_expiration_date": time.time() + 86400 * 30}})
    assert plans.tier(db, "2") == "pro"
    assert "Solo il proprietario" in tc.handler.handle("/rimborsa 2", "2").text
    r = tc.handler.handle("/rimborsa 2", "1")
    assert "250" in r.text and calls == [("2", "ch9")] and plans.tier(db, "2") == "light"
    assert "Nessun pagamento" in tc.handler.handle("/rimborsa 2", "1").text


def test_refund_only_in_first_days(index, monkeypatch):
    db, client, tc = setup(index, monkeypatch)
    db.add_chat_id("2")
    calls = []
    client.refund_stars = lambda uid, charge: (calls.append(("refund", uid, charge)) or (True, ""))
    client.cancel_subscription = lambda uid, charge: (calls.append(("cancel", uid, charge)) or (True, ""))
    tc.handle_payload({"chat_id": 2, "text": "/pagamento", "payment": {
        "currency": "XTR", "total_amount": 250, "invoice_payload": "pro:2", "telegram_payment_charge_id": "ch5",
        "subscription_expiration_date": time.time() + 86400 * 30}})
    p = plans.get(db, "2")
    p["payments"][-1]["ts"] = time.time() - 29 * 86400  # pagato 29 giorni fa
    db.set_kv("plan:2", p)
    r = tc.handler.handle("/rimborsa 2", "1")
    assert "29 giorni" in r.text and "parziali" in r.text and calls == [] and plans.tier(db, "2") == "pro"
    r = tc.handler.handle("/rimborsa 2 annulla", "1")
    assert calls == [("cancel", "2", "ch5")] and plans.tier(db, "2") == "pro" and r.sends[0][0] == "2"
    r = tc.handler.handle("/rimborsa 2 forza", "1")
    assert calls[-1] == ("refund", "2", "ch5") and plans.tier(db, "2") == "light"


def test_expired_subscription_goes_back_to_light_limits(index, monkeypatch):
    db, client, tc = setup(index, monkeypatch)
    db.add_chat_id("2")
    now = time.time()
    tc.handle_payload({"chat_id": 2, "text": "/pagamento", "payment": {
        "currency": "XTR", "total_amount": 250, "invoice_payload": "pro:2", "telegram_payment_charge_id": "x",
        "subscription_expiration_date": now + 86400}})
    db.set_kv("active_sets:2", ["me55", "me55c", "sv1", "sv2", "sv3"])
    assert plans.check_trials(db, client, now) == []
    assert plans.check_trials(db, client, now + 2 * 86400) == ["…2: abbonamento scaduto, passato a Light"]
    assert plans.active_count(db, "2") == 3 and "scaduto" in client.sent[-1][1]
    assert plans.check_trials(db, client, now + 3 * 86400) == []
