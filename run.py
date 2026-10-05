"""Avvia interfaccia web + ricerca periodica in background."""
from __future__ import annotations

import logging

from pokebot import config
from pokebot.cards import load_sets
from pokebot.db import Database
from pokebot.scheduler import Scheduler
from pokebot.web.app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    index = load_sets()
    db = Database()
    scheduler = Scheduler(index, db)
    scheduler.start()
    app = create_app(index, db, scheduler)
    logging.getLogger(__name__).info("Interfaccia web su http://%s:%s", config.WEB_HOST, config.WEB_PORT)
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
