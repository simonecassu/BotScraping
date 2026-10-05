import os
import tempfile
import time

import cli
from pokebot.db import Database


def test_dispatch_payload(monkeypatch):
    monkeypatch.delenv("POKEBOT_DISPATCH_PAYLOAD", raising=False)
    assert cli._dispatch_payload() is None
    monkeypatch.setenv("POKEBOT_DISPATCH_PAYLOAD", "null")
    assert cli._dispatch_payload() is None
    monkeypatch.setenv("POKEBOT_DISPATCH_PAYLOAD", '{"chat_id": 5, "text": "/cerca"}')
    assert cli._dispatch_payload() == {"chat_id": 5, "text": "/cerca"}
    monkeypatch.setenv("POKEBOT_DISPATCH_PAYLOAD", "{non json")
    assert cli._dispatch_payload() is None


def test_search_due():
    db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))
    assert cli._search_due(db)  # mai cercato
    db.set_kv("last_search_ts", time.time())
    assert not cli._search_due(db)
    db.save_settings({"interval_minutes": 10})
    db.set_kv("last_search_ts", time.time() - 11 * 60)
    assert cli._search_due(db)
