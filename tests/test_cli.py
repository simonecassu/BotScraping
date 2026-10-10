import os
import tempfile
import time

import cli
from pokebot.db import Database


def test_seal_and_unseal_files(monkeypatch):
    monkeypatch.setattr("pokebot.config.TELEGRAM_BOT_TOKEN", "123:abc")
    d = tempfile.mkdtemp()
    src, enc, out = (os.path.join(d, n) for n in ("db", "db.enc", "db.out"))
    open(src, "wb").write(b"SQLite format 3\x00 dati")
    assert cli.main(["seal", src, enc]) == 0 and b"dati" not in open(enc, "rb").read()
    assert cli.main(["unseal", enc, out]) == 0 and open(out, "rb").read() == open(src, "rb").read()
    assert cli.main(["unseal", src, out]) == 0 and open(out, "rb").read() == open(src, "rb").read()  # in chiaro: passa così


def test_search_due():
    db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))
    assert cli._search_due(db)  # mai cercato
    db.set_kv("last_search_ts", time.time())
    assert not cli._search_due(db)
    db.save_settings({"interval_minutes": 10})
    db.set_kv("last_search_ts", time.time() - 11 * 60)
    assert cli._search_due(db)


class _Report:
    queries = listings = new_listings = matches = notified = 0
    errors: dict = {}


def _quiet_actions(monkeypatch, db):
    """Un giro `actions` senza rete: niente Telegram, coda, prezzi, inseguimenti, canale."""
    monkeypatch.setattr("pokebot.config.TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(cli, "Database", lambda: db)
    monkeypatch.setattr("pokebot.cardmarket.refresh", lambda *a, **k: "")
    monkeypatch.setattr("pokebot.watch.run_watches", lambda *a, **k: [])
    monkeypatch.setattr("pokebot.telegram_bot.TelegramCommands.ensure_menu", lambda self: None)
    searches = []
    monkeypatch.setattr(cli, "run_search", lambda index, db, **kw: searches.append(1) or _Report())
    return searches


def test_actions_phases_commands_then_search(monkeypatch):
    db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))
    db.set_kv("last_search_ts", time.time())  # ricerca non dovuta: parte solo se qualcuno l'ha chiesta
    searches = _quiet_actions(monkeypatch, db)
    monkeypatch.setattr("pokebot.queue.GitHubQueue.drain", lambda self, handle, done_file=None: (1, True))  # un /cerca in coda
    assert cli.main(["actions", "--fase", "comandi"]) == 0
    assert searches == [] and db.get_kv("search_requested") is True  # la fase comandi non cerca: lo annota
    assert cli.main(["actions", "--fase", "ricerca"]) == 0
    assert searches == [1] and not db.get_kv("search_requested")  # la fase ricerca lo ritrova e lo consuma
    assert cli.main(["actions", "--fase", "ricerca"]) == 0
    assert searches == [1]  # senza richiesta e senza intervallo scaduto, niente ricerca


def test_actions_without_phase_does_everything(monkeypatch):
    db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))
    db.set_kv("last_search_ts", time.time())
    searches = _quiet_actions(monkeypatch, db)
    monkeypatch.setattr("pokebot.queue.GitHubQueue.drain", lambda self, handle, done_file=None: (0, False))
    assert cli.main(["actions"]) == 0 and searches == []
    assert cli.main(["actions", "--force"]) == 0 and searches == [1]


def test_search_summary_for_cerca():
    from collections import Counter
    from pokebot.search import RunReport
    from cli import search_summary
    rep = RunReport(started_at=0, wanted_count=0)
    assert "nessuna carta mancante" in search_summary(rep, "1")
    rep = RunReport(started_at=0, wanted_count=3, listings=120, new_listings=7, per_source=Counter(vinted=100, ebay=20),
                    delivered_to=Counter({"1": 2}), errors={"wallapop": "timeout"})
    text = search_summary(rep, "1", suppressed=True)
    assert "120 annunci letti (ebay 20 · vinted 100), 7 mai visti" in text and "per te 2 annunci" in text
    assert "modalità notte" in text and "wallapop" in text
    text = search_summary(rep, "2", suppressed=True)
    assert "niente di nuovo" in text and "notte" not in text
