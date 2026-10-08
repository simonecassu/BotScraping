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
    cs, album, created = coll.follow(db, "me", "sv8")
    assert created and album == "me:sv8" and cs.name == "Surging Sparks" and cs.printed_total == 191 and len(cs.cards) == 3
    assert cs.context_keywords == ["surging sparks", "ssp"] and cs.queries[0] == "pokemon Surging Sparks" and not cs.primary
    assert coll.loaded_ids(db) == ["sv8"] and coll.get_set(db, "sv8", download=False).id == "sv8"
    assert coll.wanted_numbers(db, cs, album) == {"1", "4", "7"}  # punto di partenza: mi mancano tutte
    coll.mark(db, cs, ["1", "7"], False, album)  # possedute
    coll.mark(db, cs, ["7"], True, album)  # ripensamento: manca
    assert coll.owned_numbers(db, cs, album) == {"1"} and coll.wanted_numbers(db, cs, album) == {"4", "7"}
    assert coll.follow(db, "me", "sv8")[2] is False  # già seguita: niente reset
    idx = coll.load_index(db)
    assert [s.id for s in idx.sets] == ["me55", "me55c", "sv8"] and idx.code_of["sv8-7"] == "sv8:7" and idx.code_of["me55-131"] == "131"
    assert db.active_sets("me") == ["me55", "me55c"]
    db.set_active("sv8", True, "me")
    ex = coll.export(db, idx, "me")[0]
    assert ex["active"] and ex["wanted"] == ["4", "7"] and ex["owned"] == ["1"] and ex["cards"][0]["name"] == "Exeggcute" and ex["shared_with"] == []
    assert coll.export(db, idx, "altro") == []  # un'altra persona non la vede


def test_shared_album(index, monkeypatch):
    _fake_net(monkeypatch)
    db = _db()
    cs, album, _ = coll.follow(db, "anna", "sv8")
    coll.mark(db, cs, ["1"], False, album)  # anna ha la 1
    cs2, album2, _ = coll.follow(db, "bea", "sv8")
    coll.mark(db, cs2, ["4"], False, album2)  # bea ha la 4
    db.join_album("bea", album)  # bea entra nell'album di anna: le carte di una delle due contano come prese
    assert db.albums_of("bea") == {"sv8": album} and db.album_members(album) == ["anna", "bea"]
    assert db.wanted_ids([album]) == {"sv8-7"}
    assert db.wanted_for("anna") == db.wanted_for("bea") == {"sv8-7"}
    db.leave_album("bea", album)
    assert db.albums_of("bea")["sv8"] == "bea:sv8" and db.wanted_for("bea") == {"sv8-7"} and db.album_members(album) == ["anna"]
    db.set_wanted("sv8-7", False, album)
    assert db.wanted_for("anna") == set() and db.wanted_for("bea") == {"sv8-7"}  # ora separati


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
    assert db.current_set("me") == "sv8" and index.get_set("sv8") is not None and db.wanted_for("me") >= {"sv8-1", "sv8-4", "sv8-7"}
    # i comandi normali lavorano sulla collezione corrente
    assert "Pikachu ex" in h.handle("/mancanti").text and "Altre collezioni" in h.handle("/mancanti").text
    r = h.handle("/ho 1 7")
    assert "Exeggcute" in r.text and "Surging Sparks: mancanti <b>1</b>/3" in r.text
    assert "Surging Sparks" in h.handle("/progresso").text and "2/3" in h.handle("/progresso").text
    assert "Pikachu ex" in h.handle("/aggiungi 7").text and db.wanted_ids() >= {"sv8-7"}
    r = h.handle("/collezione sv8 ho 1 7 99")
    assert "Exeggcute 1" in r.text and "Non capiti: 99" in r.text and coll.wanted_numbers(db, index.get_set("sv8")) == {"4"}
    assert "⚠️" in h.handle("/collezione sv8 attiva").text and "sv8" in db.active_sets("me")
    assert "spenta" in h.handle("/collezione sv8 disattiva").text and "sv8" not in db.active_sets("me")
    # si torna alla 30th; una carta di un'altra collezione si indica come set:numero
    assert "corrente" in h.handle("/collezione 30th").text and db.current_set("me") == "me55"
    assert "Lapras" in h.handle("/aggiungi 131").text and "30th Celebration: mancanti" in h.handle("/aggiungi 131").text
    assert "Durant ex" in h.handle("/ho sv8:4").text and coll.wanted_numbers(db, index.get_set("sv8")) == set()
    assert "spenta" in h.handle("/collezione me55 disattiva").text and "me55" not in db.active_sets("me")
    assert "attiva" in h.handle("/collezione me55 attiva").text and "me55" in db.active_sets("me")
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
    cs, album, _ = coll.follow(db, "me", "sv8")
    coll.mark(db, cs, ["1", "4"], False, album)  # resta mancante solo la 7
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


def test_friend_and_share_commands(index, monkeypatch):
    _fake_net(monkeypatch)
    from pokebot.telegram_bot import CommandHandler
    db = _db()
    db.set_user_name("7", "Simone")
    db.set_user_name("8", "Giulia")
    h = CommandHandler(index, db)
    assert "Nessun amico" in h.handle("/amici", "7").text
    code = h.handle("/amico", "7").text.split("/amico ")[1].split("<")[0].strip()
    assert "non valido" in h.handle("/amico ZZZZZZ", "8").text
    assert "amici" in h.handle(f"/amico {code}", "8").text and db.friends("7") == ["8"] and db.friends("8") == ["7"]
    assert "Giulia" in h.handle("/amici", "7").text
    # ognuno ha il suo album della 30th: Simone ha tutto tranne la 131, Giulia ha solo la 145
    h.handle("/ho tutte\n/aggiungi 131", "7")
    h.handle("/ho 145", "8")
    assert db.wanted_for("7") >= {"me55-131"} and "me55-145" not in db.wanted_for("8") and "me55-1" in db.wanted_for("8")
    r = h.handle("/condividi 30th Giulia", "7")
    assert "Proposta inviata" in r.text and r.sends and r.sends[0][0] == "8" and "/accetta " in r.sends[0][2][0][0][1]
    code2 = r.sends[0][2][0][0][1].split()[1]
    assert "Ok, album non condiviso" in h.handle("/accetta no", "8").text
    r = h.handle(f"/accetta {code2}", "8")
    assert "album unico" in r.text and r.sends and r.sends[0][0] == "7"
    album = db.albums_of("7")["me55"]
    assert db.albums_of("8")["me55"] == album and db.album_members(album) == ["7", "8"]
    # carte di uno dei due = prese: resta mancante solo la 131 (Simone non l'ha, Giulia nemmeno)
    assert {i for i in db.wanted_for("8") if i.startswith("me55-")} == {"me55-131"}
    assert "con Giulia" in h.handle("/collezione", "7").text
    h.handle("/ho 131", "8")  # la spunta di Giulia vale per entrambi
    assert "me55-131" not in db.wanted_for("7")
    assert "copia tutta tua" in h.handle("/esci 30th", "8").text and db.albums_of("8")["me55"] == "8:me55"
    # impostazioni personali: la pausa di Giulia non ferma Simone
    h.handle("/pausa", "8")
    assert db.settings_for("8")["paused"] is True and db.settings_for("7").get("paused", False) is False
