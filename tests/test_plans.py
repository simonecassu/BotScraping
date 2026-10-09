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
    assert out == ["2: promemoria fine prova"] and "Domani finisce" in client.sent[-1][1]
    assert plans.check_trials(db, client, now + 4.6 * 86400) == []
    # due inseguimenti durante la prova: alla fine ne resta uno, ogni 2 ore
    tc.handler.handle("/insegui 131 151", "2")
    assert len(db.get_kv("watches:2")) == 2
    out = plans.check_trials(db, client, now + 5.1 * 86400)
    assert out == ["2: fine prova, passato a Light"]
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
