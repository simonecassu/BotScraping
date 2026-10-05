"""Esecuzione periodica in background."""
from __future__ import annotations

import logging
import threading
import time

from .cards import CardIndex
from .db import Database
from .search import RunReport, run_search
from .telegram_bot import TelegramCommands

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, index: CardIndex, db: Database):
        self.index = index
        self.db = db
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.running = False
        self.last_report: RunReport | None = None
        self.next_run_at: float | None = None
        self._lock = threading.Lock()
        self.commands = TelegramCommands(index, db)
        self._tg_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pokebot-scheduler", daemon=True)
        self._thread.start()
        if self.commands.enabled:
            self._tg_thread = threading.Thread(target=self._telegram_loop, name="pokebot-telegram", daemon=True)
            self._tg_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger(self) -> None:
        """Esegui subito un ciclo (chiamato dall'interfaccia web)."""
        self._wake.set()

    def run_once(self) -> RunReport:
        with self._lock:
            self.running = True
            try:
                self.last_report = run_search(self.index, self.db)
            finally:
                self.running = False
        return self.last_report

    def _telegram_loop(self) -> None:
        """Ascolta i comandi Telegram (/aggiungi, /mancanti, /cerca...) con long polling."""
        while not self._stop.is_set():
            try:
                if self.commands.poll_once(timeout=25):
                    self.trigger()
            except Exception:  # noqa: BLE001
                log.exception("Errore nel polling Telegram")
                time.sleep(10)

    def _loop(self) -> None:
        # primo ciclo poco dopo l'avvio
        self.next_run_at = time.time() + 5
        while not self._stop.is_set():
            wait = max(0.0, (self.next_run_at or time.time()) - time.time())
            self._wake.wait(timeout=wait)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("Errore nel ciclo di ricerca")
            interval = float(self.db.get_settings().get("interval_minutes", 15) or 15)
            self.next_run_at = time.time() + max(1.0, interval) * 60
