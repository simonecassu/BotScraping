"""Subito.it tramite l'API JSON usata dal sito (hades.subito.it)."""
from __future__ import annotations

from typing import Any

from .base import BaseScraper, Listing, ScraperError, parse_price

API_URL = "https://hades.subito.it/v1/search/items"


class SubitoScraper(BaseScraper):
    name = "subito"
    label = "Subito.it"

    def search(self, query: str, limit: int = 60) -> list[Listing]:
        params = {
            "q": query,
            "t": "s",        # solo annunci in vendita
            "lim": min(limit, 100),
            "start": 0,
            "sort": "datedesc",
        }
        resp = self._get(API_URL, params=params, headers={"Accept": "application/json"})
        try:
            data = resp.json()
        except ValueError as exc:
            raise ScraperError("Subito.it: risposta non JSON") from exc
        return self.parse(data)

    # ------------------------------------------------------------------
    @classmethod
    def parse(cls, data: dict[str, Any]) -> list[Listing]:
        out: list[Listing] = []
        for ad in data.get("ads", []) or []:
            try:
                lst = cls._parse_ad(ad)
            except Exception:  # noqa: BLE001 - un annuncio malformato non deve fermare gli altri
                continue
            if lst:
                out.append(lst)
        return out

    @staticmethod
    def _features(ad: dict[str, Any]) -> dict[str, Any]:
        feats = ad.get("features") or {}
        if isinstance(feats, list):
            feats = {f.get("uri"): f for f in feats if isinstance(f, dict)}
        return feats

    @classmethod
    def _feature_value(cls, ad: dict[str, Any], uri: str) -> str:
        f = cls._features(ad).get(uri)
        if not f:
            return ""
        vals = f.get("values") or []
        if vals and isinstance(vals[0], dict):
            return str(vals[0].get("value") or vals[0].get("key") or "")
        return ""

    @classmethod
    def _parse_ad(cls, ad: dict[str, Any]) -> Listing | None:
        urn = str(ad.get("urn") or "")
        listing_id = urn.rsplit(":", 1)[-1] if urn else str(ad.get("id") or "")
        urls = ad.get("urls") or {}
        url = urls.get("default") or urls.get("mobile") or ""
        title = (ad.get("subject") or "").strip()
        if not listing_id or not url or not title:
            return None
        status = cls._feature_value(ad, "/transaction_status").lower()
        if status in ("sold", "venduto", "reserved", "riservato"):
            return None
        price_text = cls._feature_value(ad, "/price")
        geo = ad.get("geo") or {}
        town = (geo.get("town") or {}).get("value") or (geo.get("city") or {}).get("value") or ""
        region = (geo.get("region") or {}).get("value") or ""
        location = ", ".join(p for p in (town, region) if p)
        image = ""
        imgs = ad.get("images") or []
        if imgs and isinstance(imgs[0], dict):
            scales = imgs[0].get("scale") or []
            if scales and isinstance(scales[0], dict):
                image = scales[-1].get("uri") or scales[0].get("uri") or ""
            image = image or imgs[0].get("cdn_base_url") or ""
        return Listing(
            source="subito",
            listing_id=listing_id,
            title=title,
            url=url,
            description=(ad.get("body") or "").strip(),
            price=parse_price(price_text),
            price_text=price_text,
            location=location,
            image=image,
            extra={"date": ad.get("date", ""), "shipping": cls._feature_value(ad, "/item_shipping_type")},
        )
