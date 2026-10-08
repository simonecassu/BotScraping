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

TIMEZONE = os.getenv("POKEBOT_TIMEZONE", "Europe/Rome")

HTTP_TIMEOUT = float(os.getenv("POKEBOT_HTTP_TIMEOUT", "25"))
USER_AGENT = os.getenv(
    "POKEBOT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
)

# Impostazioni modificabili da Telegram / Mini App (valori di default).
DEFAULT_SETTINGS: dict[str, object] = {
    "interval_minutes": 20,
    "lot_min_ratio": 0.5,
    "max_price": 0,  # 0 = nessun limite
    "max_per_card": 5,  # per ogni carta, quanti annunci (i più economici) inviare per ciclo
    "language": "ita",  # "ita" scarta gli annunci dichiaratamente in altre lingue (FR/EN/DE/ES/JP); "tutte" accetta tutto
    "deal_pct": 60,  # avviso 🔥 se il prezzo è sotto questa % della mediana storica della carta (0 = spento)
    "max_messages_per_run": 10,  # tetto ai messaggi raggruppati per giro: il resto finisce nello storico senza invio
    "max_deals_per_run": 3,  # tetto agli avvisi affare per giro
    "images": True,  # immagine della carta nei messaggi raggruppati
    "paused": False,  # in pausa: cerca e accumula, non notifica
    "quiet_hours": None,  # es. [23, 8]: dalle 23 alle 8 accumula e manda tutto al mattino
    "sources": ["wallapop", "vinted", "ebay"],
    "per_card_queries": True,
    "per_card_batch": 20,  # quante carte mancanti cercare singolarmente per ciclo (a rotazione)
    "notify_unverifiable_lots": False,
    "only_italy": True,
}

# Impostazioni personali: ogni persona collegata ha le sue (le altre sono condivise, come l'album).
PERSONAL_SETTINGS = {"paused", "quiet_hours", "images", "max_per_card", "deal_pct", "max_messages_per_run", "max_deals_per_run",
                     "language", "lot_min_ratio", "max_price", "notify_unverifiable_lots"}

# Collezioni "di casa" (data/sets): comandi con codici brevi, cercate di default. La prima è quella corrente all'inizio.
HOME_SET_IDS = ("me55", "me55c")
