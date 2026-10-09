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
