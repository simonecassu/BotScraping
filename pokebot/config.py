"""Configurazione da variabili d'ambiente (file .env supportato)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("POKEBOT_DATA_DIR", BASE_DIR / "data"))
SETS_DIR = DATA_DIR / "sets"
DB_PATH = Path(os.getenv("POKEBOT_DB_PATH", DATA_DIR / "pokebot.db"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# eBay: se presenti usa l'API ufficiale Browse, altrimenti scraping HTML.
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "").strip()
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "").strip()

WEB_HOST = os.getenv("POKEBOT_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("POKEBOT_PORT", "8080"))
WEB_SECRET = os.getenv("POKEBOT_SECRET", "pokebot-dev-secret")

HTTP_TIMEOUT = float(os.getenv("POKEBOT_HTTP_TIMEOUT", "25"))
USER_AGENT = os.getenv(
    "POKEBOT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
)

# Impostazioni modificabili dall'interfaccia web (valori di default).
DEFAULT_SETTINGS: dict[str, object] = {
    "interval_minutes": 20,
    "lot_min_ratio": 0.5,
    "max_price": 0,  # 0 = nessun limite
    "max_per_card": 5,  # per ogni carta, quanti annunci (i più economici) inviare per ciclo
    "sources": ["wallapop", "vinted", "ebay"],
    "set_keywords": [
        "30th celebration",
        "30th",
        "30 anniversario",
        "30° anniversario",
        "30esimo anniversario",
        "trentesimo anniversario",
        "celebrazione del 30",
        "celebration",
        "me55",
    ],
    "generic_queries": [
        "pokemon 30th celebration",
        "pokemon 30 anniversario",
        "carte pokemon 30th",
        "pokemon celebration 2026",
        "pokemon classic collection 30th",
    ],
    "per_card_queries": True,
    "per_card_batch": 20,  # quante carte mancanti cercare singolarmente per ciclo (a rotazione)
    "notify_unverifiable_lots": False,
    "only_italy": True,
}
