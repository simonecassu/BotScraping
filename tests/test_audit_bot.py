"""Regressioni dell'audit sui comandi: permessi, multi-riga, espulsioni, pagamenti, limiti Light."""
import os
import tempfile
import time

from pokebot import plans
from pokebot.db import Database
from pokebot.telegram_bot import TelegramCommands, _chunks
from tests.test_plans import PayClient


def setup(index, monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))
    client = PayClient([])
    monkeypatch.setattr("pokebot.telegram_bot.TelegramClient", lambda *a, **k: client)
    tc = TelegramCommands(index, db, client=client)
    tc.handle_payload({"chat_id": 1, "text": "/start", "name": "Owner"})
    db.add_chat_id("2")
    plans.set_pro(db, "2", lifetime=True)
    return db, client, tc


def test_global_commands_are_owner_only(index, monkeypatch):
    db, _, tc = setup(index, monkeypatch)
    h = tc.handler
    for cmd in ("/fonti ebay", "/intervallo 5", "/resetvisti conferma", "/storico svuota"):
        assert "solo il proprietario" in h.handle(cmd, "2").text, cmd
    assert db.get_settings()["sources"] == ["wallapop", "vinted", "ebay"]
    assert "Fonti attive" in h.handle("/fonti vinted", "1").text
    assert "Solo per te" in h.handle("/aiuto", "1").text and "Solo per te" not in h.handle("/aiuto", "2").text
    r = h.handle("/cerca", "2")  # la ricerca completa la lancia solo il proprietario
    assert not r.run_search and "/cerca 145" in r.text
    assert h.handle("/cerca", "1").run_search and h.handle("/cerca", "1").run_search


def test_huge_range_does_not_hang(index, monkeypatch):
    _, _, tc = setup(index, monkeypatch)
    t = time.time()
    r = tc.handler.handle("/collezione me55 ho 1-99999999999", "2")
    assert time.time() - t < 2 and "Prese" in r.text


def test_multi_line_keeps_buttons_and_sends(index, monkeypatch):
    db, _, tc = setup(index, monkeypatch)
    tc.handle_payload({"chat_id": 3, "text": "/start", "name": "Bea"})
    tc.handle_payload({"chat_id": 4, "text": "/start", "name": "Cleo"})
    r = tc.handler.handle("/approva 3\n/approva 4", "1")
    assert {c for c, _, _ in r.sends} == {"3", "4"}


def test_kick_bans_and_refunds_later_payment(index, monkeypatch):
    db, client, tc = setup(index, monkeypatch)
    refunds, cancels = [], []
    client.refund_stars = lambda uid, ch: (refunds.append(ch) or (True, ""))
    client.cancel_subscription = lambda uid, ch: (cancels.append(ch) or (True, ""))
    pay = {"currency": "XTR", "total_amount": 250, "invoice_payload": "pro:2", "telegram_payment_charge_id": "c1",
           "subscription_expiration_date": time.time() + 30 * 86400}
    tc.handle_payload({"chat_id": 2, "text": "/pagamento", "payment": pay})
    tc.handle_payload({"chat_id": 2, "text": "/pagamento", "payment": pay})  # stesso pagamento due volte
    assert len(plans.get(db, "2")["payments"]) == 1
    r = tc.handler.handle("/espelli 2", "1")
    assert "rinnovo fermato" in r.text and cancels == ["c1"] and db.is_banned("2")
    tc.handle_payload({"chat_id": 2, "text": "/pagamento", "payment": dict(pay, telegram_payment_charge_id="c2")})
    assert refunds == ["c2"] and "2" not in db.chat_ids()           # il rinnovo non lo fa rientrare
    tc.handle_payload({"chat_id": 2, "text": "/start"})
    assert "2" not in db.waitlist()                                  # e nemmeno un nuovo /start


def test_approve_skips_people_already_inside(index, monkeypatch):
    db, _, tc = setup(index, monkeypatch)
    db.add_to_waitlist("2", "Anna")
    plans.set_onboarding(db, "2", False)
    r = tc.handler.handle("/approva tutti", "1")
    assert not r.sends and not plans.onboarding(db, "2") and plans.tier(db, "2") == "pro"


def test_light_limit_applies_to_every_way_of_following(index, monkeypatch):
    db, _, tc = setup(index, monkeypatch)
    plans.set_light(db, "2")
    for sid in ("me55", "me55c", "sv1", "sv2", "sv3", "sv4"):
        db.ensure_album("2", sid)
    db.ensure_album("1", "sv9")
    code = tc.handler.handle("/condividi sv9", "1")
    assert "Collezione non tra le tue" in code.text  # il proprietario non segue sv9 come album suo: niente codice
    from pokebot.cards import Card, CardSet
    cs = CardSet(id="sv9", name="Nove", name_it="", total=1, release="", printed_total=None,
                 cards=[Card(id="sv9-1", set_id="sv9", set_name="Nove", number="1", name="Zzzmon", rarity="",
                             supertype="", image="", image_large="", printed_total=None)])
    tc.handler.index.add_set(cs)
    assert "Light" in tc.handler.handle("/ho sv9:1", "2").text and "sv9" not in db.albums_of("2")
    assert "Non ho riconosciuto" in tc.handler.handle("/ho zzzmon", "2").text  # un nome non crea album di collezioni altrui


def test_share_decline_tells_the_proposer(index, monkeypatch):
    db, _, tc = setup(index, monkeypatch)
    db.add_friend("1", "2")
    db.set_user_name("2", "Anna")
    r = tc.handler.handle("/condividi me55 Anna", "1")
    decline = r.sends[0][2][0][1][1]
    assert decline.startswith("/accetta no ")
    r2 = tc.handler.handle(decline, "2")
    assert r2.sends and r2.sends[0][0] == "1" and "non condividere" in r2.sends[0][1]


def test_channel_hour_zero_and_bad_ids(index, monkeypatch):
    from pokebot import channel
    db, _, tc = setup(index, monkeypatch)
    tc.handler.handle("/canale 0", "1")
    assert channel.post_hour(db) == 0
    assert "Usa" in tc.handler.handle("/canale 24", "1").text and not db.get_kv("deals_channel")


def test_long_lines_are_split():
    parts = _chunks("a" * 5000)
    assert [len(p) for p in parts] == [3800, 1200] and all(parts)


def test_overflowing_numbers_do_not_crash(index, monkeypatch):
    _, _, tc = setup(index, monkeypatch)
    db = tc.db
    for cmd in ("/max inf", "/affari inf", "/prezzo inf", "/prezzo nan"):
        assert "Serve" in tc.handler.handle(cmd, "2").text
    assert db.settings_for("2")["max_price"] == 0
