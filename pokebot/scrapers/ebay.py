"""eBay.it: API Browse ufficiale se configurata, altrimenti scraping della pagina di ricerca.

In entrambi i casi vengono richiesti solo annunci "Compralo Subito" (niente aste),
così la carta è acquistabile immediatamente.
"""
from __future__ import annotations

import base64
import time
from typing import Any
from urllib.parse import urlencode

import requests

from .. import config
from .base import BaseScraper, Listing, ScraperError, parse_price

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


class EbayScraper(BaseScraper):
    name = "ebay"
    label = "eBay.it"
    min_delay = 2.0
    max_delay = 4.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._token: str | None = None
        self._token_exp = 0.0

    @property
    def use_api(self) -> bool:
        return bool(config.EBAY_CLIENT_ID and config.EBAY_CLIENT_SECRET)

    def search(self, query: str, limit: int = 60) -> list[Listing]:
        if not self.use_api:
            raise ScraperError("eBay: servono EBAY_CLIENT_ID e EBAY_CLIENT_SECRET (API Browse); la pagina HTML è bloccata dai server cloud")
        return self._search_api(query, limit)

    # ---- API Browse -----------------------------------------------------
    def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        creds = base64.b64encode(f"{config.EBAY_CLIENT_ID}:{config.EBAY_CLIENT_SECRET}".encode()).decode()
        try:
            resp = self.session.post(
                TOKEN_URL,
                headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
                timeout=config.HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ScraperError(f"eBay API: errore di rete sul token: {exc}") from exc
        if resp.status_code != 200:
            raise ScraperError(f"eBay API: token rifiutato (HTTP {resp.status_code})")
        data = resp.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + float(data.get("expires_in", 7200))
        return self._token

    def _search_api(self, query: str, limit: int) -> list[Listing]:
        filters = ["buyingOptions:{FIXED_PRICE}"]
        if self.only_italy:
            filters.append("itemLocationCountry:IT")
        params = {"q": query, "limit": min(limit, 200), "sort": "newlyListed", "filter": ",".join(filters)}
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_IT",
            "Accept": "application/json",
        }
        resp = self._get(BROWSE_URL, params=params, headers=headers)
        return self.parse_api(resp.json())

    @staticmethod
    def parse_api(data: dict[str, Any]) -> list[Listing]:
        out: list[Listing] = []
        for it in data.get("itemSummaries", []) or []:
            item_id = str(it.get("itemId") or "")
            title = (it.get("title") or "").strip()
            url = it.get("itemWebUrl") or ""
            if not item_id or not title or not url:
                continue
            options = it.get("buyingOptions") or []
            is_auction = "AUCTION" in options and "FIXED_PRICE" not in options
            price = it.get("price") or {}
            loc = it.get("itemLocation") or {}
            out.append(Listing(
                source="ebay",
                listing_id=item_id,
                title=title,
                url=url,
                description=(it.get("shortDescription") or "").strip(),
                price=parse_price(price.get("value")),
                price_text=f"{price.get('value', '')} {price.get('currency', '')}".strip(),
                location=", ".join(p for p in (loc.get("city"), loc.get("country")) if p),
                image=(it.get("image") or {}).get("imageUrl", ""),
                is_auction=is_auction,
                extra={"condition": it.get("condition", "")},
            ))
        return out

    # ---- scraping HTML ----------------------------------------------------
