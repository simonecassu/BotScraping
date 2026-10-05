import os
import tempfile

from pokebot.db import Database
from pokebot.telegram_bot import CommandHandler, TelegramCommands, _chunks


def make(index):
    db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))
    return db, CommandHandler(index, db)


def test_resolve_numbers_ranges_codes_rarity_names(index):
    _, h = make(index)
    cards, unknown = h.resolve("131 149-152 c2 sir pikachu ex xyz")
    ids = {c.id for c in cards}
    assert {"me55-131", "me55-149", "me55-150", "me55-151", "me55-152", "me55c-4"} <= ids
    assert all(c.id in ids for c in index.by_id.values() if c.rarity == "Special Illustration Rare")
    assert {"me55-53", "me55-54"} <= ids  # "pikachu ex" come nome a due parole
    assert unknown == ["xyz"]


def test_add_remove_and_missing(index):
    db, h = make(index)
    r = h.handle("/aggiungi 131 132")
    assert "2 carte" in r.text and db.wanted_ids() == {"me55-131", "me55-132"}
    r = h.handle("/ho 131")
    assert db.wanted_ids() == {"me55-132"}
    assert "Articuno" in h.handle("/mancanti").text
    h.handle("/aggiungi tutte classic")
    assert sum(1 for i in db.wanted_ids() if i.startswith("me55c-")) == 30
    h.handle("/rimuovi tutte")  # solo set principale
    assert db.wanted_ids() == {i for i in db.wanted_ids() if i.startswith("me55c-")}


def test_settings_commands(index):
    db, h = make(index)
    h.handle("/soglia 60")
    h.handle("/prezzo 80")
    h.handle("/fonti vinted ebay")
    s = db.get_settings()
    assert s["lot_min_ratio"] == 0.6 and s["max_price"] == 80 and s["sources"] == ["vinted", "ebay"]
    assert h.handle("/cerca").run_search
    assert "sconosciuto" in h.handle("/boh").text
    assert "/mancanti" in h.handle("/aiuto").text


def test_list_is_chunked(index):
    _, h = make(index)
    text = h.handle("/lista").text
    parts = _chunks(text)
    assert len(parts) >= 2 and all(len(p) <= 3800 for p in parts)
    assert "Exeggcute" in parts[0]


class FakeClient:
    token = "x"

    def __init__(self, updates):
        self.updates = updates
        self.sent = []
        self.menu = None

    def set_my_commands(self, commands):
        self.menu = commands
        return True

    def get_updates(self, offset, timeout=0):
        return [u for u in self.updates if offset is None or u["update_id"] >= offset]

    def send(self, chat_id, text):
        self.sent.append((str(chat_id), text))


def test_poll_saves_chat_id_and_ignores_strangers(index, monkeypatch):
    from pokebot import config
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))
    client = FakeClient([
        {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/mancanti"}},   # prima di /start: ignorato
        {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/start"}},
        {"update_id": 3, "message": {"chat": {"id": 999}, "text": "/aggiungi tutte"}},  # estraneo: ignorato
        {"update_id": 4, "message": {"chat": {"id": 42}, "text": "/cerca"}},
    ])
    tc = TelegramCommands(index, db, client=client)
    assert tc.poll_once() is True
    assert db.get_kv("telegram_chat_id") == "42"
    assert db.get_kv("telegram_offset") == 5
    assert [c for c, _ in client.sent] == ["42", "42"]
    assert db.wanted_ids() == set()
    # secondo giro: nessun nuovo update
    assert tc.poll_once() is False
    # menu comandi registrato una volta sola
    assert client.menu and client.menu[0][0] == "mancanti"
    from pokebot.telegram_bot import MENU_VERSION
    assert db.get_kv("telegram_menu_version") == MENU_VERSION
    client.menu = None
    tc.poll_once()
    assert client.menu is None
