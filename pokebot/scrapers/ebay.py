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
from bs4 import BeautifulSoup

from .. import config
from .base import BaseScraper, Listing, ScraperError, parse_price

SEARCH_URL = "https://www.ebay.it/sch/i.html"
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
        if self.use_api:
            return self._search_api(query, limit)
        return self._search_html(query, limit)

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
    def _search_html(self, query: str, limit: int) -> list[Listing]:
        params = {"_nkw": query, "LH_BIN": "1", "_sop": "10", "_ipg": "60"}
        if self.only_italy:
            params["LH_PrefLoc"] = "1"
        resp = self._get(f"{SEARCH_URL}?{urlencode(params)}")
        return self.parse_html(resp.text)[:limit]

    @staticmethod
    def parse_html(html: str) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[Listing] = []
        for li in soup.select("li.s-item, li.s-card, div.s-item"):
            link = li.select_one("a.s-item__link, a.s-card__link, a[href*='/itm/']")
            title_el = li.select_one(".s-item__title, .s-card__title")
            if not link or not title_el:
                continue
            title = title_el.get_text(" ", strip=True)
            title = title.replace("Nuova inserzione", "").replace("Nuovo annuncio", "").strip()
            if not title or title.lower().startswith("shop on ebay"):
                continue
            href = link.get("href", "")
            item_id = ""
            if "/itm/" in href:
                item_id = href.split("/itm/", 1)[1].split("?", 1)[0].strip("/").split("/")[-1]
            if not item_id:
                continue
            price_el = li.select_one(".s-item__price, .s-card__price")
            price_text = price_el.get_text(" ", strip=True) if price_el else ""
            subtitle = li.select_one(".s-item__subtitle, .s-card__subtitle")
            purchase = li.select_one(".s-item__purchase-options, .s-item__purchaseOptions, .s-item__bids")
            purchase_text = purchase.get_text(" ", strip=True).lower() if purchase else ""
            bids = li.select_one(".s-item__bids, .s-item__bidCount")
            is_auction = bool(bids) or ("offert" in purchase_text and "compralo" not in purchase_text)
            loc_el = li.select_one(".s-item__location, .s-item__itemLocation")
            img = li.select_one("img")
            out.append(Listing(
                source="ebay",
                listing_id=item_id,
                title=title,
                url=href.split("?", 1)[0],
                description=subtitle.get_text(" ", strip=True) if subtitle else "",
                price=parse_price(price_text),
                price_text=price_text,
                location=(loc_el.get_text(" ", strip=True).replace("da ", "", 1) if loc_el else ""),
                image=(img.get("src") or img.get("data-src") or "") if img else "",
                is_auction=is_auction,
            ))
        return out
