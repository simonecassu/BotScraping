import os
import tempfile
import time

from pokebot import channel
from pokebot.db import Database
from pokebot.telegram_bot import CommandHandler, TelegramCommands, _button
from tests.test_telegram import FakeClient


def _cm(db, cid, low, trend):
    with db.connect() as c:
        c.execute("INSERT INTO cm_prices(card_id, low, trend, avg7, avg30, updated, fetched) VALUES (?,?,?,?,?,?,?)",
                  (cid, low, trend, None, None, "", time.time()))


def _db():
    return Database(os.path.join(tempfile.mkdtemp(), "t.db"))


def test_stranger_start_goes_to_waitlist_and_owner_approves(index, monkeypatch):
    from pokebot import config
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    db = _db()
    client = FakeClient([])
    tc = TelegramCommands(index, db, client=client)
    assert tc.handle_payload({"chat_id": 1, "text": "/start", "name": "Owner"}) is False  # primo: proprietario
    assert db.owner_chat_id() == "1"
    # sconosciuti: /start li mette in lista, il resto è ignorato
    assert tc.handle_payload({"chat_id": 2, "text": "/mancanti", "name": "Anna"}) is False
    assert db.waitlist() == {}
    tc.handle_payload({"chat_id": 2, "text": "/start canale", "name": "Anna"})
    tc.handle_payload({"chat_id": 3, "text": "/start", "name": "Bea"})
    tc.handle_payload({"chat_id": 2, "text": "/start", "name": "Anna"})  # di nuovo: già in lista
    wl = db.waitlist()
    assert set(wl) == {"2", "3"} and wl["2"]["source"] == "canale" and wl["2"]["name"] == "Anna"
    assert db.chat_ids() == ["1"]
    texts = [t for c, t in client.sent if c == "2"]
    assert "posizione 1" in texts[0] and "già in lista" in texts[1]
    owner_msgs = [t for c, t in client.sent if c == "1"]
    assert any("Nuova richiesta" in t and "Anna" in t and "da canale" in t for t in owner_msgs)
    # lista e approvazione
    r = tc.handler.handle("/attesa", "1")
    assert "2 in attesa" in r.text and "Anna" in r.text
    assert "Solo il proprietario" in tc.handler.handle("/attesa", "2").text
    assert tc.handle_payload({"chat_id": 1, "text": "/approva 2"}) is False
    assert db.chat_ids() == ["1", "2"] and set(db.waitlist()) == {"3"}
    assert db.albums_of("2")  # album di casa creati
    assert any(c == "2" and "attivo" in t for c, t in client.sent)
    # ora Anna può usare il bot
    tc.handle_payload({"chat_id": 2, "text": "/stato"})
    assert any(c == "2" and "Mancanti" in t for c, t in client.sent)  # ora Anna è collegata
    # rifiuto e approva tutti
    tc.handle_payload({"chat_id": 4, "text": "/start"})
    assert "tolto" in tc.handler.handle("/rifiuta 3", "1").text
    assert "Nessuno" in tc.handler.handle("/approva 3", "1").text
    tc.handler.handle("/approva tutti", "1")
    assert db.waitlist() == {} and db.chat_ids() == ["1", "2", "4"]


def test_invite_code_still_works_and_wrong_code_waitlists(index, monkeypatch):
    from pokebot import config
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    db = _db()
    client = FakeClient([])
    tc = TelegramCommands(index, db, client=client)
    tc.handle_payload({"chat_id": 7, "text": "/start"})
    tc.handler.handle("/invita", "7")
    code = db.get_kv("invite_code")["code"]
    tc.handle_payload({"chat_id": 9, "text": "/start XXXXXX"})
    assert "9" in db.waitlist() and db.chat_ids() == ["7"]
    tc.handle_payload({"chat_id": 8, "text": f"/start {code}"})
    assert db.chat_ids() == ["7", "8"] and "8" not in db.waitlist()


def test_url_button_and_channel_post(index, monkeypatch):
    assert _button("x", "https://t.me/b?start=canale") == {"text": "x", "url": "https://t.me/b?start=canale"}
    assert _button("x", "/approva 1") == {"text": "x", "callback_data": "/approva 1"}
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    db = _db()
    db.set_kv("telegram_chat_id", "1")
    client = FakeClient([])
    tc = TelegramCommands(index, db, client=client)
    h = tc.handler
    assert "Nessun canale" in h.handle("/canale", "1").text
    r = h.handle("/canale @affaripoke", "1")
    tc._deliver("1", r)
    assert db.get_kv("deals_channel") == "@affaripoke"
    assert any(c == "@affaripoke" and "collegato" in t for c, t in client.sent)
    # storico: 5 annunci della stessa carta a ~30 €, uno a 12 € nelle ultime 24 ore → affare
    now = time.time()
    for i in range(5):
        db.add_found(f"k{i}", "vinted", f"Articuno ex 131 #{i}", f"https://v/{i}", f"{28 + i} €", "", "single",
                     [{"id": "me55-131", "label": "Articuno ex 131", "sure": True}], None, True)
    db.add_found("deal", "wallapop", "Articuno ex 131 cheap", "https://w/deal", "12 €", "", "single",
                 [{"id": "me55-131", "label": "Articuno ex 131", "sure": True}], None, True)
    db.add_found("old", "ebay", "Articuno ex 131 older", "https://e/old", "9 €", "", "single",
                 [{"id": "me55-131", "sure": True}], None, True)
    with db.connect() as c:
        c.execute("UPDATE found SET created_at = ? WHERE listing_key = 'old'", (now - 4 * 86400,))
    _cm(db, "me55-131", low=20, trend=30)
    _cm(db, "me55-132", low=8, trend=10)
    db.add_found("good", "vinted", "Articuno 132", "https://v/good", "9 €", "", "single",
                 [{"id": "me55-132", "sure": True}], None, True)
    _cm(db, "me55-133", low=10, trend=10)
    db.add_found("fair", "wallapop", "Zapdos 133", "https://w/fair", "12 €", "", "single",
                 [{"id": "me55-133", "sure": True}], None, True)
    deals = channel.pick_deals(db, index, now)
    assert [d["verdict"] for d in deals] == ["deal", "good", "fair"]  # anche sopra Cardmarket il post esce, etichettato
    assert deals[0]["url"] == "https://w/deal" and deals[0]["price"] == 12 and deals[1]["url"] == "https://v/good"
    text, buttons = channel.format_post(deals, "fakebot")
    assert "migliori occasioni" in text and "12.00 €" in text and "🔥 Affare" in text and "👍 Sotto il valore" in text
    assert "📌 Vicino al valore" in text and "120% del valore Cardmarket" in text
    assert "40% del valore Cardmarket" in text
    assert "Affari Pokémon" in channel.format_post(deals[:1], "fakebot")[0]
    assert buttons == [[("🎣 Attiva Pescacarte", "https://t.me/fakebot?start=canale")]]
    # /canale ora pubblica subito
    r = h.handle("/canale ora", "1")
    assert r.sends and r.sends[0][0] == "@affaripoke" and "Pokémon del" in r.sends[0][1]
    # post giornaliero: dovuto solo dopo l'ora e una volta al giorno
    import datetime as dt
    from zoneinfo import ZoneInfo
    local = dt.datetime.now(ZoneInfo("Europe/Rome"))
    at_hour = local.replace(hour=19, minute=5).timestamp()
    before = local.replace(hour=8, minute=0).timestamp()
    assert not channel.due(db, before)
    assert channel.due(db, at_hour)
    assert "pubblicato" in channel.post_daily(db, index, client, at_hour)
    assert "https://w/deal" not in client.sent[-1][1]  # già uscito con /canale ora: non si ripubblica
    assert db.get_kv("bot_username") == "fakebot"
    assert any(c == "@affaripoke" and "Pokémon del" in t for c, t in client.sent)
    assert client.last_buttons == [[("🎣 Attiva Pescacarte", "https://t.me/fakebot?start=canale")]]
    assert not channel.due(db, at_hour + 600) and channel.post_daily(db, index, client, at_hour + 600) == ""
    assert "20:00" in h.handle("/canale 20", "1").text
    h.handle("/canale off", "1")
    assert not db.get_kv("deals_channel") and not channel.due(db, at_hour)


def test_friend_code_is_stable_and_reusable(index, monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    db = _db()
    for c in ("1", "2", "3"):
        db.add_chat_id(c) if c != "1" else db.set_kv("telegram_chat_id", "1")
    h = CommandHandler(index, db)
    code = db.friend_code("1")
    assert db.friend_code("1") == code and code in h.handle("/amico", "1").text
    assert "amici" in h.handle(f"/amico {code}", "2").text
    assert "amici" in h.handle(f"/amico {code}", "3").text  # riusabile da più persone
    assert set(db.friends("1")) == {"2", "3"}
    assert "già amici" in h.handle(f"/amico {code}", "2").text
    assert "tuo codice" in h.handle(f"/amico {code}", "1").text
    from pokebot.webapp_export import build_state
    st = build_state(index, db, chat_id="1")
    assert st["friend_code"] == code and {f["id"] for f in st["friends"]} == {"2", "3"}
    assert all("shared" in f for f in st["friends"])
