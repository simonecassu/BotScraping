"""Vinted.it tramite l'API del catalogo (richiede i cookie di sessione anonima)."""
from __future__ import annotations

import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from .. import config
from .base import BaseScraper, Listing, ScraperError, parse_price

BASE_URL = "https://www.vinted.it"
API_URL = f"{BASE_URL}/api/v2/catalog/items"
REFRESH_URL = f"{BASE_URL}/web/api/auth/refresh"
CATALOG_URL = f"{BASE_URL}/catalog"

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
                if not self._has_session_cookie():
                    # la home non rilascia più access_token_web direttamente: lo fa l'endpoint di refresh
                    self._throttle()
                    r2 = self.session.post(REFRESH_URL, timeout=config.HTTP_TIMEOUT,
                                           headers={**BROWSER_HEADERS, "Accept": "application/json, text/plain, */*",
                                                    "Referer": BASE_URL + "/", "Origin": BASE_URL,
                                                    "X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Mode": "cors",
                                                    "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Dest": "empty"})
                    self.refresh_status = r2.status_code
            except requests.RequestException as exc:
                raise ScraperError(f"Vinted: errore di rete: {exc}") from exc

    refresh_status: int | None = None

    def _diag(self) -> str:
        names = sorted({c.name for c in self.session.cookies if "vinted" in (c.domain or "")})
        return (f"home HTTP {self.home_status}, refresh HTTP {self.refresh_status}, "
                f"cookie sessione: {'sì' if self._has_session_cookie() else 'no'}, cookie: {', '.join(names) or 'nessuno'}")

    def _has_session_cookie(self) -> bool:
        return any(c.name == "access_token_web" and "vinted" in (c.domain or "") for c in self.session.cookies)

    api_missing = False  # dopo un 404 dell'API JSON si passa direttamente alla pagina HTML

    def search(self, query: str, limit: int = 60) -> list[Listing]:
        self._ensure_cookies()
        if self.api_missing:
            return self._search_html(query, limit, "API non disponibile")
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
            if "404" in str(exc):
                # l'API JSON non risponde più a questo indirizzo: leggi la pagina di ricerca
                self.api_missing = True
                return self._search_html(query, limit, str(exc))
            if any(code in str(exc) for code in ("401", "403")):
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

    def _search_html(self, query: str, limit: int, api_error: str) -> list[Listing]:
        params = {"search_text": query, "order": "newest_first"}
        try:
            resp = self._get(CATALOG_URL, params=params, headers=BROWSER_HEADERS)
        except ScraperError as exc:
            raise ScraperError(f"{exc} (API: {api_error[:80]}; {self._diag()})") from exc
        items = self.parse_html(resp.text)
        if not items and "/items/" not in resp.text:
            raise ScraperError(f"Vinted: pagina di ricerca senza annunci ({self._diag()}; API: {api_error[:80]})")
        return items[:limit]

    # ------------------------------------------------------------------
    _PRICE_IN_TITLE = re.compile(r"(?:prezzo|price)\s*:\s*(€?\s*\d{1,5}(?:[.,]\d{1,2})?\s*€?)", re.IGNORECASE)
    _ANY_PRICE = re.compile(r"\d{1,5}(?:[.,]\d{1,2})?\s*€|€\s*\d{1,5}(?:[.,]\d{1,2})?")
    # il "title" dei link Vinted è "Titolo, Brand: X, Condizioni: Y, 15.00 €, 16.45 €" (il secondo prezzo include la protezione acquisti)
    _TITLE_META = re.compile(r",\s*(brand|marca|condizioni|condizione|condition|taglia|size|prezzo|price)\s*:", re.IGNORECASE)

    @classmethod
    def parse_html(cls, html: str) -> list[Listing]:
        """Estrae gli annunci dalla pagina /catalog (link /items/<id>-slug con attributo title)."""
        soup = BeautifulSoup(html, "html.parser")
        out: list[Listing] = []
        seen: set[str] = set()
        for a in soup.select("a[href*='/items/']"):
            href = a.get("href", "")
            m = re.search(r"/items/(\d+)", href)
            if not m:
                continue
            item_id = m.group(1)
            if item_id in seen:
                continue
            raw_title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
            if not raw_title:
                continue
            seen.add(item_id)
            meta = cls._TITLE_META.search(raw_title)
            title = (raw_title[: meta.start()] if meta else raw_title).strip(" ,")
            title = cls._ANY_PRICE.split(title)[0].strip(" ,-") or title
            price_text = ""
            pm = cls._PRICE_IN_TITLE.search(raw_title) or cls._ANY_PRICE.search(raw_title)
            if pm:
                price_text = (pm.group(1) if pm.lastindex else pm.group(0)).strip()
            else:
                card = a.find_parent(attrs={"data-testid": re.compile("grid-item|item-card|product")}) or a.parent
                txt = card.get_text(" ", strip=True) if card else ""
                pm2 = cls._ANY_PRICE.search(txt)
                price_text = pm2.group(0) if pm2 else ""
            url = href if href.startswith("http") else BASE_URL + href
            img = a.find("img")
            out.append(Listing(
                source="vinted",
                listing_id=item_id,
                title=title,
                url=url.split("?", 1)[0],
                price=parse_price(price_text),
                price_text=price_text,
                image=(img.get("src") or img.get("data-src") or "") if img else "",
                extra={"via": "html"},
            ))
        return out

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
