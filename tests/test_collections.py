import os
import tempfile

from pokebot import collections as coll
from pokebot.db import Database

CATALOG = [{"id": "sv8", "name": "Surging Sparks", "series": "Scarlet & Violet", "total": 252, "printedTotal": 191,
            "releaseDate": "2024/11/08", "ptcgoCode": "SSP", "images": {"logo": "https://l/sv8.png", "symbol": "https://s/sv8.png"}}]
CARDS = [{"id": "sv8-1", "number": "1", "name": "Exeggcute", "rarity": "Common", "supertype": "Pokémon",
          "images": {"small": "https://i/sv8/1.png", "large": "https://i/sv8/1_hires.png"}},
         {"id": "sv8-4", "number": "4", "name": "Durant ex", "rarity": "Double Rare", "supertype": "Pokémon", "images": {}},
         {"id": "sv8-7", "number": "7", "name": "Pikachu ex", "rarity": "Rare", "supertype": "Pokémon", "images": {}}]


def _db():
    return Database(os.path.join(tempfile.mkdtemp(), "t.db"))


def _fake_net(monkeypatch):
    monkeypatch.setattr(coll, "_get_json", lambda urls: CATALOG if "sets/en.json" in urls[0] else CARDS)


def test_download_mark_activate(index, monkeypatch):
    _fake_net(monkeypatch)
    db = _db()
    cs = coll.get_set(db, "sv8")
    assert cs.name == "Surging Sparks" and cs.printed_total == 191 and len(cs.cards) == 3 and cs.logo == "https://l/sv8.png"
    assert coll.loaded_ids(db) == ["sv8"] and coll.get_set(db, "sv8", download=False).id == "sv8"
    coll.mark(db, "sv8", ["4", "7"], True)
    coll.mark(db, "sv8", ["7"], False)
    assert coll.wanted_numbers(db, "sv8") == {"4"}
    assert coll.active_search_sets(db) == ([], set())  # spenta: non si cerca
    coll.set_active(db, "sv8", True)
    sets, ids = coll.active_search_sets(db)
    assert [s.id for s in sets] == ["sv8"] and ids == {"sv8-4"}
    ex = coll.export(db)[0]
    assert ex["active"] and ex["wanted"] == ["4"] and ex["cards"][0]["name"] == "Exeggcute"


def test_collection_command(index, monkeypatch):
    _fake_net(monkeypatch)
    from pokebot.telegram_bot import CommandHandler
    db = _db()
    h = CommandHandler(index, db)
    assert "30th Celebration" in h.handle("/collezione").text
    assert "Surging Sparks" in h.handle("/collezione sv8").text and "ricerca spenta" in h.handle("/collezione sv8").text
    r = h.handle("/collezione sv8 manca 4 7 99")
    assert "Durant ex 4" in r.text and "Non capiti: 99" in r.text and "ricerca spenta" in r.text
    assert coll.wanted_numbers(db, "sv8") == {"4", "7"}
    assert "Prese" in h.handle("/collezione sv8 ho 7").text and coll.wanted_numbers(db, "sv8") == {"4"}
    assert "⚠️" in h.handle("/collezione sv8 attiva").text and coll.active_ids(db) == ["sv8"]
    assert "spenta" in h.handle("/collezione sv8 disattiva").text and coll.active_ids(db) == []
    assert "di casa" in h.handle("/collezione me55 manca 1").text
    monkeypatch.setattr(coll, "_get_json", lambda urls: CATALOG)
    assert "non trovata" in h.handle("/collezione xyz").text


def test_search_includes_active_extra_sets(index, monkeypatch):
    _fake_net(monkeypatch)
    from pokebot.search import run_search
    from pokebot.scrapers.base import Listing
    from tests.test_search import FakeNotifier, FakeScraper
    db = _db()
    db.save_settings({"sources": ["fake"], "generic_queries": [], "per_card_queries": True, "quiet_hours": None, "deal_pct": 0})
    db.set_wanted("me55-131", True)
    coll.get_set(db, "sv8")
    coll.mark(db, "sv8", ["7"], True)
    scr = FakeScraper([Listing("fake", "p", "Pikachu ex 7/191 Surging Sparks", "https://f/1", price=20.0, price_text="20 €")])
    n = FakeNotifier()
    run_search(index, db, n, {"fake": scr})
    assert not any("Surging" in q for q in scr.queries) and not n.sent  # spenta: né query né notifiche
    coll.set_active(db, "sv8", True)
    scr2 = FakeScraper([Listing("fake", "p2", "Pikachu ex 7/191 Surging Sparks", "https://f/2", price=20.0, price_text="20 €")])
    rep = run_search(index, db, n, {"fake": scr2})
    assert any("Pikachu ex 7/191 Surging Sparks" in q for q in scr2.queries) and rep.wanted_count == 2
    assert [l.key for l, _ in n.sent] == ["fake:p2"]
