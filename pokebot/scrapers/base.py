"""Interfaccia comune degli scraper."""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field

import requests

from .. import config

log = logging.getLogger(__name__)


class ScraperError(Exception):
    pass


@dataclass
class Listing:
    source: str
    listing_id: str
    title: str
    url: str
    description: str = ""
    price: float | None = None
    price_text: str = ""
    location: str = ""
    image: str = ""
    is_auction: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.listing_id}"


_PRICE_RE = re.compile(r"(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)")


def parse_price(text: str | None) -> float | None:
    """'1.234,50 €' -> 1234.5 ; 'EUR 12,00' -> 12.0 ; 'Gratis' -> None."""
    if not text:
        return None
    m = _PRICE_RE.search(str(text))
    if not m:
        return None
    raw = m.group(1)
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif raw.count(".") > 1 or (raw.count(".") == 1 and len(raw.split(".")[1]) == 3):
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


class BaseScraper:
    name = "base"
    label = "Base"
    min_delay = 1.0
    max_delay = 2.5

    def __init__(self, session: requests.Session | None = None, only_italy: bool = True):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
        })
        self.only_italy = only_italy
        self._last_request = 0.0

    def _throttle(self) -> None:
        wait = random.uniform(self.min_delay, self.max_delay) - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()

    def _get(self, url: str, **kwargs) -> requests.Response:
        self._throttle()
        kwargs.setdefault("timeout", config.HTTP_TIMEOUT)
        try:
            resp = self.session.get(url, **kwargs)
        except requests.RequestException as exc:
            raise ScraperError(f"{self.label}: errore di rete: {exc}") from exc
        if resp.status_code in (401, 403, 429):
            raise ScraperError(f"{self.label}: accesso rifiutato (HTTP {resp.status_code}), probabile blocco anti-bot")
        if resp.status_code >= 400:
            raise ScraperError(f"{self.label}: HTTP {resp.status_code}")
        return resp

    def search(self, query: str, limit: int = 60) -> list[Listing]:
        raise NotImplementedError
