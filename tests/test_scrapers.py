from pokebot.scrapers.base import parse_price
from pokebot.scrapers.ebay import EbayScraper
from pokebot.scrapers.vinted import VintedScraper
from pokebot.scrapers.wallapop import WallapopScraper


def test_parse_price():
    assert parse_price("1.234,50 €") == 1234.5
    assert parse_price("12,00 €") == 12.0
    assert parse_price("EUR 12.50") == 12.5
    assert parse_price("1.200 €") == 1200.0
    assert parse_price("Gratis") is None
    assert parse_price(None) is None


def test_wallapop_parse_new_format():
    data = {"data": {"section": {"type": "item_cards", "payload": {"items": [
        {"id": "abc123", "title": "Lapras 131/128 Pokemon 30th", "description": "near mint", "web_slug": "lapras-131-128-abc123",
         "price": {"amount": 25, "currency": "EUR"}, "images": [{"urls": {"small": "https://i/s.jpg", "medium": "https://i/m.jpg"}}],
         "location": {"city": "Milano", "region": "Lombardia", "country_code": "IT"}, "reserved": {"flag": False},
         "shipping": {"item_is_shippable": True}},
        {"id": "res1", "title": "Riservato", "web_slug": "x", "reserved": {"flag": True}},
        {"id": "es1", "title": "Moltres 130/128", "web_slug": "y", "price": {"amount": 5, "currency": "EUR"},
         "location": {"city": "Madrid", "country_code": "ES"}},
    ]}}}}
    out = WallapopScraper.parse(data)
    assert [l.listing_id for l in out] == ["abc123", "es1"]
    l = out[0]
    assert l.url == "https://it.wallapop.com/item/lapras-131-128-abc123"
    assert l.price == 25.0 and l.location == "Milano, Lombardia" and l.image == "https://i/m.jpg"
    assert l.extra["country"] == "IT" and out[1].extra["country"] == "ES"


def test_wallapop_parse_legacy_format():
    data = {"search_objects": [
        {"id": "1", "title": "Pikachu ex 149/128", "price": 40, "currency": "EUR", "web_slug": "pikachu-1",
         "images": [{"original": "https://i/o.jpg", "small": "https://i/s.jpg"}],
         "location": {"city": "Roma", "country_code": "IT"}, "flags": {"sold": False, "reserved": False}},
        {"id": "2", "title": "Venduto", "web_slug": "v", "flags": {"sold": True}},
    ]}
    out = WallapopScraper.parse(data)
    assert len(out) == 1 and out[0].price == 40.0 and out[0].image == "https://i/s.jpg"


def test_wallapop_only_italy_filter(monkeypatch):
    s = WallapopScraper(only_italy=True)
    s.min_delay = s.max_delay = 0

    class Resp:
        status_code = 200

        def json(self):
            return {"data": {"section": {"payload": {"items": [
                {"id": "it", "title": "a", "web_slug": "a", "location": {"country_code": "IT"}},
                {"id": "es", "title": "b", "web_slug": "b", "location": {"country_code": "ES"}},
            ]}}}}

    monkeypatch.setattr(s.session, "get", lambda url, **kw: Resp())
    assert [l.listing_id for l in s.search("x")] == ["it"]


def test_vinted_parse():
    data = {"items": [
        {"id": 111, "title": "Carta Pokemon Moltres 130/128", "url": "https://www.vinted.it/items/111-carta",
         "price": {"amount": "12.0", "currency_code": "EUR"}, "photo": {"url": "https://p/1.jpg"},
         "user": {"city": "Roma"}, "description": "Illustration rare"},
        {"id": 222, "title": "riservato", "is_reserved": True},
    ]}
    out = VintedScraper.parse(data)
    assert len(out) == 1 and out[0].price == 12.0 and out[0].location == "Roma"


def test_ebay_api_parse():
    data = {"itemSummaries": [
        {"itemId": "v1|123|0", "title": "Pikachu ex 149/128 30th", "itemWebUrl": "https://www.ebay.it/itm/123",
         "price": {"value": "40.00", "currency": "EUR"}, "buyingOptions": ["FIXED_PRICE"],
         "itemLocation": {"city": "Torino", "country": "IT"}, "image": {"imageUrl": "https://i/1.jpg"}},
        {"itemId": "v1|456|0", "title": "Asta", "itemWebUrl": "https://www.ebay.it/itm/456",
         "price": {"value": "1.00", "currency": "EUR"}, "buyingOptions": ["AUCTION"]},
    ]}
    out = EbayScraper.parse_api(data)
    assert len(out) == 2
    assert out[0].price == 40.0 and not out[0].is_auction
    assert out[1].is_auction


EBAY_HTML = """
<ul class="srp-results">
<li class="s-item"><a class="s-item__link" href="https://www.ebay.it/itm/999?hash=abc"><div class="s-item__title"><span>Shop on eBay</span></div></a></li>
<li class="s-item">
  <a class="s-item__link" href="https://www.ebay.it/itm/123456789?hash=item1">
    <div class="s-item__title"><span class="LIGHT_HIGHLIGHT">Nuova inserzione</span>Lapras 131/128 Pokemon 30th Celebration IR</div>
  </a>
  <img src="https://i.ebayimg.com/1.jpg">
  <div class="s-item__subtitle">Near Mint</div>
  <span class="s-item__price">EUR 19,90</span>
  <span class="s-item__purchase-options">Compralo Subito</span>
  <span class="s-item__location">da Italia</span>
</li>
<li class="s-item">
  <a class="s-item__link" href="https://www.ebay.it/itm/555?x=1"><div class="s-item__title">Moltres 130/128</div></a>
  <span class="s-item__price">EUR 1,00</span><span class="s-item__bids">3 offerte</span>
</li>
</ul>
"""


def test_ebay_html_parse():
    out = EbayScraper.parse_html(EBAY_HTML)
    assert len(out) == 2
    a, b = out
    assert a.listing_id == "123456789" and a.title == "Lapras 131/128 Pokemon 30th Celebration IR"
    assert a.price == 19.9 and a.location == "Italia" and not a.is_auction
    assert a.url == "https://www.ebay.it/itm/123456789"
    assert b.is_auction


def test_vinted_fetches_cookies_then_searches(monkeypatch):
    """Senza cookie: prima GET della home, poi GET dell'API (nessun KeyError sul cookie jar vuoto)."""
    import requests

    calls = []

    class Resp:
        status_code = 200

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    def fake_get(url, **kw):
        calls.append(url)
        if "/api/v2/catalog/items" in url:
            return Resp({"items": [{"id": 1, "title": "Lapras 131/128", "url": "/items/1", "price": {"amount": "5"}}]})
        return Resp({})

    def fake_post(url, **kw):
        calls.append(url)
        return Resp({})

    s = VintedScraper()
    s.min_delay = s.max_delay = 0
    monkeypatch.setattr(s.session, "get", fake_get)
    monkeypatch.setattr(s.session, "post", fake_post)
    out = s.search("lapras")
    assert calls[0].rstrip("/") == "https://www.vinted.it"
    assert "/web/api/auth/refresh" in calls[1] and "/api/v2/catalog/items" in calls[2]
    assert len(out) == 1 and out[0].url == "https://www.vinted.it/items/1"


VINTED_HTML = """
<div data-testid="grid-item"><a href="/items/5551234-lapras-131-128-pokemon" title="Lapras 131/128 Pokemon 30th, prezzo: 7,50 €, marca: Pokémon"><img src="https://i/1.jpg"></a></div>
<div data-testid="grid-item"><a href="https://www.vinted.it/items/5559999-moltres?referrer=catalog">Moltres 130/128</a><p>12,00 €</p></div>
<div><a href="/items/5551234-lapras-131-128-pokemon">duplicato</a></div>
<a href="/member/123">non un annuncio</a>
"""


def test_vinted_html_parse():
    out = VintedScraper.parse_html(VINTED_HTML)
    assert [l.listing_id for l in out] == ["5551234", "5559999"]
    a, b = out
    assert a.title == "Lapras 131/128 Pokemon 30th" and a.price == 7.5 and a.image == "https://i/1.jpg"
    assert a.url == "https://www.vinted.it/items/5551234-lapras-131-128-pokemon"
    assert b.title == "Moltres 130/128" and b.price == 12.0 and b.url == "https://www.vinted.it/items/5559999-moltres"


def test_vinted_falls_back_to_html_on_404(monkeypatch):
    class Resp:
        def __init__(self, status, text="", payload=None):
            self.status_code, self.text, self._p = status, text, payload or {}
            self.url = "https://www.vinted.it/x"

        def json(self):
            return self._p

    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "/api/v2/catalog/items" in url:
            return Resp(404, "<html>non trovata</html>")
        if "/catalog" in url:
            return Resp(200, VINTED_HTML)
        return Resp(200, "")

    s = VintedScraper()
    s.min_delay = s.max_delay = 0
    monkeypatch.setattr(s.session, "get", fake_get)
    monkeypatch.setattr(s.session, "post", lambda url, **kw: Resp(200))
    out = s.search("lapras")
    assert len(out) == 2 and out[0].extra["via"] == "html"
    assert any("/catalog?" in c or c.endswith("/catalog") for c in calls)
