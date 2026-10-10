"""Novità: interruttori /novita, carte mancanti dei mazzi, /insegui mazzo N, avvisi solo a chi li ha accesi."""
from __future__ import annotations

import json

from pokebot import collections as coll
from pokebot import news, plans
from tests.test_plans import setup


NEWS = {"updated": "2026-10-10T20:00:00+00:00", "releases": [], "events": [], "updates": [],
        "decks": {"id": "1", "event": "Regionale di prova", "date": "2026-10-04T00:00:00+00:00", "players": 100, "format": "Standard", "meta": [],
                  "top": [{"place": 1, "player": "A", "country": "IT", "a": "Mazzo", "price": 100, "missing": None,
                           "list": {"Pokémon": [[2, "Lapras", "MEW 131"], [1, "Mew", "MEW 151"]], "Allenatori": [], "Energie": []},
                           "cards": [[2, "MEW 131", "me55-131"], [1, "MEW 151", "me55-151"], [1, "XXX 1", None]]}]}}


def _prime(db, data=NEWS):
    db.set_kv("news_cache:news.json", {"ts": 9e12, "data": data})


def _bot(index, monkeypatch):
    db, client, tc = setup(index, monkeypatch)
    coll.album_for(db, "1", "me55")  # l'album di casa, come fa cli.py a ogni giro
    return db, client, tc


def say(tc, client, text):
    tc.handle_payload({"chat_id": 1, "text": text, "name": "Owner"})
    return str(client.sent[-1])


def test_settings_default_and_toggle(index, monkeypatch):
    db, _, _ = _bot(index, monkeypatch)
    assert news.settings(db, "5") == news.DEFAULT
    news.set_setting(db, "5", "tornei", True)
    assert news.settings(db, "5")["tornei"] is True and news.settings(db, "5")["espansioni"] is True


def test_user_view_missing_cards(index, monkeypatch):
    db, client, tc = _bot(index, monkeypatch)
    _prime(db)
    view = news.user_view(db, "1", news.load(db))
    assert "me55" in view["followed"]
    assert view["missing"]["1"] == 4  # 2 Lapras + 1 Mew (set seguito, tutte mancanti) + 1 di un set sconosciuto
    assert view["missing_codes"]["1"] == ["MEW 131", "MEW 151", "XXX 1"]
    say(tc, client, "/ho 131")
    view = news.user_view(db, "1", news.load(db))
    assert view["missing"]["1"] == 2 and "MEW 131" not in view["missing_codes"]["1"]


def test_chase_deck_command(index, monkeypatch):
    db, client, tc = _bot(index, monkeypatch)
    _prime(db)
    r = say(tc, client, "/insegui mazzo 1")
    assert "Inseguo" in r and "Lapras" in r and "Mew" in r
    r = say(tc, client, "/insegui mazzo 3")
    assert "Non trovo quel mazzo" in r


def test_novita_command(index, monkeypatch):
    db, client, tc = _bot(index, monkeypatch)
    r = say(tc, client, "/novita")
    assert "Novità" in r and "Uscite 🔔" in r
    r = say(tc, client, "/novita tornei si")
    assert "accesi" in r and news.settings(db, "1")["tornei"] is True
    r = say(tc, client, "/novita tornei no")
    assert "spenti" in r


def test_send_alerts_only_enabled_and_full(index, monkeypatch):
    db, _, _ = _bot(index, monkeypatch)
    alerts = {"alerts": [{"id": "a1", "cat": "espansioni", "text": "nuova uscita"}, {"id": "a2", "cat": "tornei", "text": "top 8"}]}
    monkeypatch.setattr(news, "_fetch", lambda name: alerts)
    sent = []

    class Client:
        def send(self, chat, text, buttons=None):
            sent.append((chat, text))
            return True

    n = news.send_alerts(db, Client(), plans.is_full)
    assert n == 1 and sent == [("1", "nuova uscita")]  # «tornei» è spenta di default
    assert news.send_alerts(db, Client(), plans.is_full) == 0  # già mandati
    assert "a1" in db.get_kv("news_sent") and "a2" in db.get_kv("news_sent")


def test_export_has_news(index, monkeypatch):
    from pokebot import webapp_export
    db, _, _ = _bot(index, monkeypatch)
    _prime(db)
    st = webapp_export.build_state(index, db, chat_id="1")
    assert st["plan"]["full"] is True
    assert st["news"]["notif"]["espansioni"] is True and "me55" in st["news"]["followed"]
    json.dumps(st)
