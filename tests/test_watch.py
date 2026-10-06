import os
import tempfile
import time

from pokebot.db import Database
from pokebot.scrapers.base import Listing
from pokebot import watch
from tests.test_search import FakeNotifier, FakeScraper


def _db():
    return Database(os.path.join(tempfile.mkdtemp(), "t.db"))


def test_insegui_command(index):
    from pokebot.telegram_bot import CommandHandler
    db = _db()
    h = CommandHandler(index, db)
    assert "Nessun inseguimento" in h.handle("/insegui").text
    r = h.handle("/insegui 151")
    assert "Inseguo" in r.text and "ogni 5 min per 6 ore" in r.text and r.run_search is False
    w = watch.list_watches(db)
    assert set(w) == {"me55-151"} and w["me55-151"]["every"] == 300
    assert 6 * 3600 - 5 <= w["me55-151"]["until"] - w["me55-151"]["started"] <= 6 * 3600
    assert "/cerca 151" not in r.text
    assert "in vendita adesso" in h.handle("/cerca 151").text or "Carta" in h.handle("/cerca 151").text  # /cerca resta la ricerca mirata
    r = h.handle("/insegui c4 2h ogni 10m")
    assert "ogni 10 min per 2 ore" in r.text
    w = watch.list_watches(db)[index.by_code["c4"].id]
    assert w["every"] == 600 and 7200 - 5 <= w["until"] - w["started"] <= 7200
    assert "Inseguimenti attivi</b> (2)" in h.handle("/insegui").text
    assert watch.parse_watch_args("151 2h ogni 10m") == ("151", 7200, 600)
    assert watch.parse_watch_args("c4 3g") == ("c4", 48 * 3600, 300)  # tetto 48 ore
    assert watch.parse_watch_args("c4 10m ogni 1h") == ("c4", 600, 600)  # la frequenza non supera la durata
    assert watch.parse_watch_args("131 151") == ("131 151", 6 * 3600, 300)
    assert "inseguimenti 2" in h.handle("/stato").text
    assert "Fermato" in h.handle("/insegui stop c4").text and set(watch.list_watches(db)) == {"me55-151"}
    assert "Fermati 1" in h.handle("/insegui stop").text and not watch.list_watches(db)
    assert "non riconosciuta" in h.handle("/insegui pippo").text
    assert watch.fmt_duration(6 * 3600) == "6 ore" and watch.fmt_duration(90 * 60) == "90 min"


def test_run_watches_notifies_only_new_and_expires(index, monkeypatch):
    db = _db()
    db.save_settings({"sources": ["fake"], "quiet_hours": None, "paused": False})
    lapras = Listing("fake", "L1", "Lapras 131/128 30th celebration", "https://f/1", price=12.0, price_text="12 €")
    scr = FakeScraper([lapras])
    n = FakeNotifier()
    watch.add_watch(db, "me55-131", 3600, 300)

    assert watch.run_watches(index, db, n, {"fake": scr}) == ["Lapras 131/128: 1 nuovi"]
    assert [l.key for l, _ in n.sent] == ["fake:L1"] and watch.list_watches(db)["me55-131"]["found"] == 1
    assert scr.queries  # ha interrogato la fonte

    # troppo presto: non ricontrolla
    q = len(scr.queries)
    assert watch.run_watches(index, db, n, {"fake": scr}) == [] and len(scr.queries) == q

    # 5 minuti dopo: ricontrolla, ma l'annuncio è già visto → nessuna notifica
    w = watch.list_watches(db)
    w["me55-131"]["last"] -= 600
    db.set_kv("watches", w)
    assert watch.run_watches(index, db, n, {"fake": scr}) == ["Lapras 131/128: 0 nuovi"]
    assert len(n.sent) == 1

    # nuovo annuncio → notifica solo quello
    scr.listings.append(Listing("fake", "L2", "Lapras 131/128 celebration nuova", "https://f/2", price=9.0, price_text="9 €"))
    w = watch.list_watches(db)
    w["me55-131"]["last"] -= 600
    db.set_kv("watches", w)
    watch.run_watches(index, db, n, {"fake": scr})
    assert [l.key for l, _ in n.sent] == ["fake:L1", "fake:L2"]

    # scaduta: riepilogo e rimozione
    w = watch.list_watches(db)
    w["me55-131"]["until"] = time.time() - 1
    db.set_kv("watches", w)
    sent_texts = []
    n.send = lambda text, disable_preview=False: sent_texts.append(text) or True
    assert watch.run_watches(index, db, n, {"fake": scr}) == ["Lapras 131/128: terminata"]
    assert not watch.list_watches(db) and "finito" in sent_texts[0] and "2 annunci nuovi" in sent_texts[0]


def test_run_watches_postponed_when_paused(index):
    db = _db()
    db.save_settings({"sources": ["fake"], "paused": True})
    watch.add_watch(db, "me55-131", 3600, 300)
    scr = FakeScraper([])
    assert watch.run_watches(index, db, FakeNotifier(), {"fake": scr}) == ["rimandate (pausa/notte)"]
    assert not scr.queries and watch.list_watches(db)["me55-131"]["last"] == 0


def test_found_log_and_export(index):
    from pokebot.webapp_export import build_state
    db = _db()
    db.save_settings({"sources": ["fake"], "quiet_hours": None})
    watch.add_watch(db, "me55-131")
    scr = FakeScraper([Listing("fake", "L1", "Lapras 131/128 30th", "https://f/1", price=12.0, price_text="12 €", image="https://img/1.jpg")])
    watch.run_watches(index, db, FakeNotifier(), {"fake": scr})
    st = build_state(index, db)
    assert st["watches"][0]["code"] == "131" and st["watches"][0]["found"] == 1 and st["watches"][0]["checks"] == 1
    assert st["watch_found"][0]["key"] == "fake:L1" and st["watch_found"][0]["card"] == "me55-131" and st["watch_found"][0]["image"] == "https://img/1.jpg"
