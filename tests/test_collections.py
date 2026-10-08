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
    assert cs.context_keywords == ["surging sparks", "ssp"] and cs.queries[0] == "pokemon Surging Sparks" and not cs.primary
    assert coll.loaded_ids(db) == ["sv8"] and coll.get_set(db, "sv8", download=False).id == "sv8"
    assert coll.wanted_numbers(db, cs) == {"1", "4", "7"}  # punto di partenza: mi mancano tutte
    coll.mark(db, cs, ["1", "7"], False)  # possedute
    coll.mark(db, cs, ["7"], True)  # ripensamento: manca
    assert coll.owned_numbers(db, cs) == {"1"} and coll.wanted_numbers(db, cs) == {"4", "7"}
    idx = coll.load_index(db)
    assert [s.id for s in idx.sets] == ["me55", "me55c", "sv8"] and idx.code_of["sv8-7"] == "sv8:7" and idx.code_of["me55-131"] == "131"
    assert db.active_sets() == ["me55", "me55c"]
    db.set_active("sv8", True)
    ex = coll.export(db, idx)[0]
    assert ex["active"] and ex["wanted"] == ["4", "7"] and ex["owned"] == ["1"] and ex["cards"][0]["name"] == "Exeggcute"


def test_migration_from_owned_lists(index, monkeypatch):
    _fake_net(monkeypatch)
    db = _db()
    raw = coll.fetch_set_raw(db, "sv8")
    db.set_kv("set_json:sv8", raw)
    db.set_kv("extra_sets", ["sv8"])
    db.set_kv("extra_owned", {"sv8": ["1"]})
    db.set_kv("extra_active", ["sv8"])
    db.save_settings({"home_active": False})
    coll.load_index(db)
    assert db.wanted_ids() == {"sv8-4", "sv8-7"} and db.active_sets() == ["sv8"] and db.get_kv("collections_v2") is True


def test_collection_command(index, monkeypatch):
    _fake_net(monkeypatch)
    from pokebot.telegram_bot import CommandHandler
    db = _db()
    h = CommandHandler(index, db)
    assert "30th Celebration" in h.handle("/collezione").text and "▶️" in h.handle("/collezione").text
    r = h.handle("/collezione sv8")  # scarica, aggiunge all'indice, diventa corrente
    assert "Surging Sparks" in r.text and "corrente" in r.text and "ricerca spenta" in r.text
    assert db.current_set() == "sv8" and index.get_set("sv8") is not None and db.wanted_ids() >= {"sv8-1", "sv8-4", "sv8-7"}
    # i comandi normali lavorano sulla collezione corrente
    assert "Pikachu ex" in h.handle("/mancanti").text and "Altre collezioni" in h.handle("/mancanti").text
    r = h.handle("/ho 1 7")
    assert "Exeggcute" in r.text and "Surging Sparks: mancanti <b>1</b>/3" in r.text
    assert "Surging Sparks" in h.handle("/progresso").text and "2/3" in h.handle("/progresso").text
    assert "Pikachu ex" in h.handle("/aggiungi 7").text and db.wanted_ids() >= {"sv8-7"}
    r = h.handle("/collezione sv8 ho 1 7 99")
    assert "Exeggcute 1" in r.text and "Non capiti: 99" in r.text and coll.wanted_numbers(db, index.get_set("sv8")) == {"4"}
    assert "⚠️" in h.handle("/collezione sv8 attiva").text and "sv8" in db.active_sets()
    assert "spenta" in h.handle("/collezione sv8 disattiva").text and "sv8" not in db.active_sets()
    # si torna alla 30th; una carta di un'altra collezione si indica come set:numero
    assert "corrente" in h.handle("/collezione 30th").text and db.current_set() == "me55"
    assert "Lapras" in h.handle("/aggiungi 131").text and "30th Celebration: mancanti" in h.handle("/aggiungi 131").text
    assert "Durant ex" in h.handle("/ho sv8:4").text and coll.wanted_numbers(db, index.get_set("sv8")) == set()
    assert "spenta" in h.handle("/collezione me55 disattiva").text and "me55" not in db.active_sets()
    assert "attiva" in h.handle("/collezione me55 attiva").text and "me55" in db.active_sets()
    monkeypatch.setattr(coll, "_get_json", lambda urls: CATALOG)
    assert "non trovata" in h.handle("/collezione xyz").text


def test_search_only_active_sets(index, monkeypatch):
    _fake_net(monkeypatch)
    from pokebot.search import run_search
    from pokebot.scrapers.base import Listing
    from tests.test_search import FakeNotifier, FakeScraper
    db = _db()
    db.save_settings({"sources": ["fake"], "generic_queries": [], "per_card_queries": True, "quiet_hours": None, "deal_pct": 0})
    db.set_wanted("me55-131", True)
    cs = coll.get_set(db, "sv8")
    coll.mark(db, cs, ["1", "4"], False)  # resta mancante solo la 7
    idx = coll.load_index(db)
    scr = FakeScraper([Listing("fake", "p", "Pikachu ex 7/191 Surging Sparks", "https://f/1", price=20.0, price_text="20 €")])
    n = FakeNotifier()
    rep = run_search(idx, db, n, {"fake": scr})
    assert rep.wanted_count == 1 and not any("Surging" in q for q in scr.queries) and not n.sent  # sv8 spenta
    db.set_active("sv8", True)
    scr2 = FakeScraper([Listing("fake", "p2", "Pikachu ex 7/191 Surging Sparks", "https://f/2", price=20.0, price_text="20 €")])
    rep = run_search(idx, db, n, {"fake": scr2})
    assert any("Pikachu ex 7/191" in q for q in scr2.queries) and rep.wanted_count == 2
    assert [l.key for l, _ in n.sent] == ["fake:p2"]
    # con la 30th spenta non si cerca nemmeno lei
    db.set_active("me55", False)
    db.set_active("me55c", False)
    scr3 = FakeScraper([Listing("fake", "x", "Lapras 131/128 30th", "https://f/3", price=10.0, price_text="10 €")])
    rep = run_search(idx, db, n, {"fake": scr3})
    assert rep.wanted_count == 1 and not any("Lapras" in q for q in scr3.queries)


def test_generic_queries_come_from_sets(index):
    from pokebot.search import build_queries
    db = _db()
    qs = build_queries(index, set(), db.get_settings(), db)
    assert "pokemon 30th celebration" in qs and "pokemon classic collection 30th" in qs
