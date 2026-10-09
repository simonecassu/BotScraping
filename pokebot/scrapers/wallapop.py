"""Wallapop (it.wallapop.com) tramite l'API JSON usata dal sito.

Prova prima l'endpoint di ricerca attuale (/api/v3/search) e, se non risponde,
quello storico (/api/v3/general/search). Entrambi i formati sono gestiti.
"""
from __future__ import annotations

from typing import Any

from .base import BaseScraper, Listing, ScraperError, parse_price

SEARCH_URL = "https://api.wallapop.com/api/v3/search"
LEGACY_SEARCH_URL = "https://api.wallapop.com/api/v3/general/search"
ITEM_URL = "https://it.wallapop.com/item/{slug}"

# Centro Italia: Wallapop ordina anche per distanza, con questo centro copre tutta la penisola
DEFAULT_LAT = 42.5
DEFAULT_LON = 12.5

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "X-DeviceOS": "0",
    "Origin": "https://it.wallapop.com",
    "Referer": "https://it.wallapop.com/",
}


class WallapopScraper(BaseScraper):
    name = "wallapop"
    label = "Wallapop"

    def search(self, query: str, limit: int = 60) -> list[Listing]:
        params = {
            "source": "search_box",
            "keywords": query,
            "latitude": DEFAULT_LAT,
            "longitude": DEFAULT_LON,
            "order_by": "newest",
        }
        try:
            resp = self._get(SEARCH_URL, params=params, headers=HEADERS)
            data = resp.json()
        except (ScraperError, ValueError) as first_exc:
            legacy = {
                "keywords": query,
                "latitude": DEFAULT_LAT,
                "longitude": DEFAULT_LON,
                "order_by": "newest",
                "country_code": "IT",
                "filters_source": "search_box",
            }
            try:
                resp = self._get(LEGACY_SEARCH_URL, params=legacy, headers=HEADERS)
                data = resp.json()
            except ValueError as exc:
                raise ScraperError("Wallapop: risposta non JSON") from exc
            except ScraperError as second_exc:
                raise ScraperError(f"{first_exc} · seconda via: {second_exc}") from first_exc
        items = self.parse(data)
        if self.only_italy:
            items = [i for i in items if (i.extra.get("country") or "IT") == "IT"]
        return items[:limit]

    # ------------------------------------------------------------------
    @classmethod
    def parse(cls, data: dict[str, Any]) -> list[Listing]:
        raw_items = cls._extract_items(data)
        out: list[Listing] = []
        for it in raw_items:
            try:
                lst = cls._parse_item(it)
            except Exception:  # noqa: BLE001
                continue
            if lst:
                out.append(lst)
        return out

    @staticmethod
    def _extract_items(data: dict[str, Any]) -> list[dict]:
        if not isinstance(data, dict):
            return []
        if "search_objects" in data:  # formato storico
            return data.get("search_objects") or []
        section = (data.get("data") or {}).get("section") or {}
        payload = section.get("payload") or {}
        if "items" in payload:
            return payload.get("items") or []
        # alcune risposte raggruppano più sezioni
        items: list[dict] = []
        for sec in (data.get("data") or {}).get("sections") or []:
            items.extend(((sec or {}).get("payload") or {}).get("items") or [])
        return items

    @staticmethod
    def _parse_item(it: dict[str, Any]) -> Listing | None:
        item_id = str(it.get("id") or "")
        title = (it.get("title") or "").strip()
        slug = it.get("web_slug") or ""
        if not item_id or not title:
            return None
        flags = it.get("flags") or {}
        if flags.get("sold") or flags.get("reserved") or flags.get("expired") or flags.get("banned") or flags.get("onhold"):
            return None
        if (it.get("reserved") or {}).get("flag") or (it.get("sold") or {}).get("flag"):
            return None
        price = it.get("price")
        currency = it.get("currency") or "EUR"
        if isinstance(price, dict):
            currency = price.get("currency") or currency
            price = price.get("amount")
        price_val = parse_price(str(price)) if price is not None else None
        price_text = f"{price_val:g} {currency}" if price_val is not None else ""
        loc = it.get("location") or {}
        location = ", ".join(p for p in (loc.get("city"), loc.get("region")) if p)
        image = ""
        imgs = it.get("images") or []
        if imgs and isinstance(imgs[0], dict):
            first = imgs[0]
            urls = first.get("urls") or {}
            image = urls.get("medium") or urls.get("small") or first.get("medium") or first.get("small") or first.get("original") or ""
        url = ITEM_URL.format(slug=slug) if slug else f"https://it.wallapop.com/item/{item_id}"
        return Listing(
            source="wallapop",
            listing_id=item_id,
            title=title,
            url=url,
            description=(it.get("description") or "").strip(),
            price=price_val,
            price_text=price_text,
            location=location,
            image=image,
            seller=str(it.get("user_id") or (it.get("user") or {}).get("id") or ""),
            extra={"country": loc.get("country_code") or "", "shippable": bool((it.get("shipping") or {}).get("item_is_shippable"))},
        )
