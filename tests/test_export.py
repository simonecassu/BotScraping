import json
import os
import tempfile

from pokebot import collections as coll
from pokebot import vault, watch
from pokebot.db import Database
from pokebot.webapp_export import build_state, build_summary, write_state

TOKEN = "123:abc"


def _two_people(monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_CHAT_ID", "")
    db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))
    db.set_kv("telegram_chat_id", "1")
    db.add_chat_id("2")
    return db


def test_each_person_sees_only_own_listings(index, monkeypatch):
    db = _two_people(monkeypatch)
    db.set_wanted_bulk(["me55-131"], True, coll.album_for(db, "1", "me55"))
    db.set_wanted_bulk(["me55-131"], False, coll.album_for(db, "2", "me55"))
    db.add_found("k1", "vinted", "Lapras 131", "https://v/1", "10 €", "", "single",
                 [{"id": "me55-131", "label": "Lapras", "sure": True}], None, True)
    db.add_found("k2", "vinted", "Pikachu ex 7/191", "https://v/2", "20 €", "", "single",
                 [{"id": "sv8-7", "label": "Pikachu ex", "sure": True}], None, True)  # collezione che nessuno dei due segue
    one, two = build_state(index, db, chat_id="1"), build_state(index, db, chat_id="2")
    # nessun dato degli altri: né chi è collegato, né i loro annunci, né il dettaglio del piano
    for st in (one, two):
        assert not {"owner_chat_id", "chat_ids", "me", "queued", "query_stats"} & set(st)
        assert set(st["plan"]) == {"tier", "label", "price", "onboarding", "max_active", "invoice"}
    assert "me55-131" in one["wanted"] and "me55-131" not in two["wanted"]
    assert [r["key"] for r in one["found"]] == ["k1"] and "sv8-7" not in one["prices"]


def test_summary_for_bridge(index, monkeypatch):
    db = _two_people(monkeypatch)
    watch.add_watch(db, "me55-131", 3600, 300, chat_id="1")
    s = build_summary(index, db)
    assert s["chat_ids"] == ["1", "2"] and s["owner_chat_id"] == "1"
    assert s["next_watch"] and s["queued"] == 0 and s["interval_minutes"] == 20
    assert set(s) == {"generated_at", "owner_chat_id", "chat_ids", "last_search_ts", "interval_minutes", "next_watch",
                      "queued", "channel"}


def test_write_state_is_sealed(index, monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_BOT_TOKEN", TOKEN)
    db = _two_people(monkeypatch)
    folder = tempfile.mkdtemp()
    files = write_state(index, db, folder)
    assert sorted(os.path.basename(f) for f in files) == ["state-1.json.enc", "state-2.json.enc", "state.json.enc"]
    for f in files:
        blob = open(f, "rb").read()
        assert vault.is_sealed(blob) and b"chat_ids" not in blob
        assert isinstance(json.loads(vault.unseal(blob)), dict)
    plain = write_state(index, db, tempfile.mkdtemp(), seal=False)
    assert json.load(open(plain[0]))["chat_ids"] == ["1", "2"]


def test_search_ignores_albums_of_people_who_left(index, monkeypatch):
    db = _two_people(monkeypatch)
    db.set_wanted_bulk(["me55-131"], True, coll.album_for(db, "2", "me55"))
    assert "me55-131" in db.wanted_of_members()
    db.remove_chat_id("2")
    assert "me55-131" not in db.wanted_of_members()
