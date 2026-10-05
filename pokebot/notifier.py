"""Invio delle notifiche su Telegram (Bot API, senza librerie esterne)."""
from __future__ import annotations

import html
import logging

import requests

from . import config
from .matcher import MatchResult
from .scrapers.base import Listing

log = logging.getLogger(__name__)

SOURCE_LABELS = {"wallapop": "Wallapop", "vinted": "Vinted", "ebay": "eBay.it"}


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = (token or config.TELEGRAM_BOT_TOKEN).strip()
        self.chat_id = (chat_id or config.TELEGRAM_CHAT_ID).strip()

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, disable_preview: bool = False) -> bool:
        if not self.configured:
            log.warning("Telegram non configurato: imposta TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID")
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }
        try:
            resp = requests.post(url, json=payload, timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as exc:
            log.error("Telegram: errore di rete: %s", exc)
            return False
        if resp.status_code != 200:
            log.error("Telegram: HTTP %s %s", resp.status_code, resp.text[:300])
            return False
        return True

    def notify_listing(self, listing: Listing, result: MatchResult) -> bool:
        return self.send(format_listing(listing, result))

    def test_message(self) -> bool:
        return self.send("✅ PokéBot 30th collegato: riceverai qui gli annunci delle carte mancanti.", True)


def format_listing(listing: Listing, result: MatchResult) -> str:
    esc = html.escape
    src = SOURCE_LABELS.get(listing.source, listing.source)
    if result.kind == "lot":
        head = f"📦 <b>LOTTO</b> · {result.wanted_count}/{result.total_cards} carte mancanti"
        if result.ratio is not None:
            head += f" ({result.ratio:.0%})"
    else:
        head = "🃏 <b>CARTA SINGOLA</b>"
        if result.possible_wanted and not result.wanted:
            head += " · ⚠️ numero non indicato, da verificare"
    lines = [head, f"<b>{esc(listing.title)}</b>"]
    meta = []
    if listing.price_text or listing.price is not None:
        meta.append(f"💶 {esc(listing.price_text or f'{listing.price:.2f} €')}")
    meta.append(f"🛒 {esc(src)}")
    if listing.location:
        meta.append(f"📍 {esc(listing.location)}")
    lines.append(" · ".join(meta))
    cards = [c.label for c in result.wanted]
    poss = [c.label for c in result.possible_wanted]
    if cards:
        shown = cards[:12]
        more = f" (+{len(cards) - len(shown)} altre)" if len(cards) > len(shown) else ""
        lines.append("✅ " + esc(", ".join(shown)) + more)
    if poss:
        shown = poss[:8]
        more = f" (+{len(poss) - len(shown)})" if len(poss) > len(shown) else ""
        lines.append("❔ Potrebbe essere: " + esc(", ".join(shown)) + more)
    lines.append(f'🔗 <a href="{esc(listing.url, quote=True)}">Apri annuncio</a>')
    return "\n".join(lines)
