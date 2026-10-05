"""Vinted.it tramite l'API del catalogo (richiede i cookie di sessione anonima)."""
from __future__ import annotations

from typing import Any

import requests

from .. import config
from .base import BaseScraper, Listing, ScraperError, parse_price

BASE_URL = "https://www.vinted.it"
API_URL = f"{BASE_URL}/api/v2/catalog/items"

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Dest": "document",
}


class VintedScraper(BaseScraper):
    name = "vinted"
    label = "Vinted"

    home_status: int | None = None

    def _ensure_cookies(self, force: bool = False) -> None:
        if force or not self._has_session_cookie():
            try:
                self.session.cookies.clear(domain=".vinted.it")
            except KeyError:
                pass  # nessun cookie ancora presente
            self._throttle()
            try:
                resp = self.session.get(BASE_URL + "/", timeout=config.HTTP_TIMEOUT, headers=BROWSER_HEADERS)
                self.home_status = resp.status_code
            except requests.RequestException as exc:
                raise ScraperError(f"Vinted: errore di rete: {exc}") from exc

    def _diag(self) -> str:
        return f"home HTTP {self.home_status}, cookie sessione: {'sì' if self._has_session_cookie() else 'no'}"

    def _has_session_cookie(self) -> bool:
        return any(c.name == "access_token_web" and "vinted" in (c.domain or "") for c in self.session.cookies)

    def search(self, query: str, limit: int = 60) -> list[Listing]:
        self._ensure_cookies()
        params = {
            "search_text": query,
            "order": "newest_first",
            "per_page": min(limit, 96),
            "page": 1,
        }
        headers = {**BROWSER_HEADERS, "Accept": "application/json, text/plain, */*", "Referer": BASE_URL + "/catalog",
                   "X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
                   "Sec-Fetch-Dest": "empty"}
        try:
            resp = self._get(API_URL, params=params, headers=headers)
        except ScraperError as exc:
            if any(code in str(exc) for code in ("401", "403", "404")):
                self._ensure_cookies(force=True)
                try:
                    resp = self._get(API_URL, params=params, headers=headers)
                except ScraperError as exc2:
                    raise ScraperError(f"{exc2} ({self._diag()})") from exc2
            else:
                raise
        try:
            data = resp.json()
        except ValueError as exc:
            raise ScraperError("Vinted: risposta non JSON") from exc
        return self.parse(data)

    # ------------------------------------------------------------------
    @classmethod
    def parse(cls, data: dict[str, Any]) -> list[Listing]:
        out: list[Listing] = []
        for item in data.get("items", []) or []:
            try:
                lst = cls._parse_item(item)
            except Exception:  # noqa: BLE001
                continue
            if lst:
                out.append(lst)
        return out

    @staticmethod
    def _parse_item(item: dict[str, Any]) -> Listing | None:
        item_id = str(item.get("id") or "")
        title = (item.get("title") or "").strip()
        if not item_id or not title:
            return None
        if item.get("is_closed") or item.get("is_reserved") or item.get("is_hidden"):
            return None
        url = item.get("url") or f"{BASE_URL}/items/{item_id}"
        if url.startswith("/"):
            url = BASE_URL + url
        price = item.get("price")
        if isinstance(price, dict):
            amount = price.get("amount")
            currency = price.get("currency_code", "EUR")
            price_text = f"{amount} {currency}" if amount is not None else ""
            price_val = parse_price(str(amount)) if amount is not None else None
        else:
            price_text = str(price or "")
            price_val = parse_price(price_text)
        photo = item.get("photo") or {}
        image = photo.get("url") or ""
        user = item.get("user") or {}
        return Listing(
            source="vinted",
            listing_id=item_id,
            title=title,
            url=url,
            description=(item.get("description") or "").strip(),
            price=price_val,
            price_text=price_text,
            location=user.get("city") or user.get("country_title") or "",
            image=image,
            extra={"brand": item.get("brand_title", ""), "status": item.get("status", "")},
        )
