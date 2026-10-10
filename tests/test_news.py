import datetime as dt
import os
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from pokebot import news
from pokebot.db import Database


class FakeClient:
    def __init__(self, fail=()):
        self.sent, self.fail = [], set(fail)

    def send(self, chat, text, buttons=None):
        if chat in self.fail:
            raise requests.ConnectionError("giù")
        self.sent.append((chat, text))
        return True


def _db():
    db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))
    db.set_kv("telegram_chat_id", "1")
    db.add_chat_id("2")
    return db


def _at(hour):
    return dt.datetime(2026, 10, 10, hour, 0, tzinfo=ZoneInfo("Europe/Rome")).timestamp()


def test_news_sent_once_per_text_only_by_day(tmp_path: Path):
    db, f = _db(), tmp_path / "novita.txt"
    assert news.send(db, FakeClient(), now=_at(12), path=f) is None  # nessun file: nulla
    f.write_text("  \n", encoding="utf-8")
    assert news.send(db, FakeClient(), now=_at(12), path=f) is None  # vuoto: nulla
    f.write_text("Ora l'app si aggiorna in mezzo minuto <3", encoding="utf-8")
    c = FakeClient()
    assert news.send(db, c, now=_at(3), path=f) == "novità pronte: si spediscono di giorno" and c.sent == []
    assert news.send(db, c, now=_at(12), path=f) == "novità inviate a 2 persone"
    assert sorted(ch for ch, _ in c.sent) == ["1", "2"]
    assert c.sent[0][1].startswith("📣 <b>Novità di Pokébot</b>\n") and "&lt;3" in c.sent[0][1]  # testo libero: HTML neutralizzato
    assert news.send(db, c, now=_at(13), path=f) is None  # stesso testo: non si ripete
    f.write_text("Altra novità", encoding="utf-8")
    assert news.send(db, c, now=_at(13), path=f) == "novità inviate a 2 persone"


def test_news_one_failed_chat_does_not_stop_the_others(tmp_path: Path):
    db, f = _db(), tmp_path / "novita.txt"
    f.write_text("Novità", encoding="utf-8")
    c = FakeClient(fail={"1"})
    assert news.send(db, c, now=_at(12), path=f) == "novità inviate a 1 persone"
    assert [ch for ch, _ in c.sent] == ["2"]
    assert news.pending(db, f) is None  # segnata come spedita: niente raffiche al giro dopo


def test_repo_news_file_is_valid():
    text = news.NEWS_FILE.read_text(encoding="utf-8")
    assert text.strip() and len(text) < 3000 and "<" not in text  # testo semplice, va in chat così com'è
