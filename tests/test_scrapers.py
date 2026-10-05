from pokebot.scrapers.base import parse_price
from pokebot.scrapers.ebay import EbayScraper
from pokebot.scrapers.subito import SubitoScraper
from pokebot.scrapers.vinted import VintedScraper


def test_parse_price():
    assert parse_price("1.234,50 €") == 1234.5
    assert parse_price("12,00 €") == 12.0
    assert parse_price("EUR 12.50") == 12.5
    assert parse_price("1.200 €") == 1200.0
    assert parse_price("Gratis") is None
    assert parse_price(None) is None


def test_subito_parse():
    data = {"ads": [
        {"urn": "id:ad:12345:list:67890", "subject": "Lapras 131/128 Pokemon 30th", "body": "Near mint, spedizione",
         "urls": {"default": "https://www.subito.it/collezionismo/lapras-12345.htm"},
         "features": {"/price": {"uri": "/price", "values": [{"key": "25", "value": "25 €"}]}},
         "geo": {"town": {"value": "Milano"}, "region": {"value": "Lombardia"}},
         "images": [{"scale": [{"uri": "https://img/1-s.jpg"}, {"uri": "https://img/1-l.jpg"}]}]},
        {"urn": "id:ad:2:list:3", "subject": "Venduto", "urls": {"default": "https://x"},
         "features": [{"uri": "/transaction_status", "values": [{"key": "sold", "value": "Venduto"}]}]},
        {"subject": "senza url"},
    ]}
    out = SubitoScraper.parse(data)
    assert len(out) == 1
    l = out[0]
    assert l.listing_id == "67890" and l.price == 25.0 and l.location == "Milano, Lombardia"
    assert l.image == "https://img/1-l.jpg" and l.key == "subito:67890"


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

    s = VintedScraper()
    s.min_delay = s.max_delay = 0
    monkeypatch.setattr(s.session, "get", fake_get)
    out = s.search("lapras")
    assert calls[0] == "https://www.vinted.it" and "/api/v2/catalog/items" in calls[1]
    assert len(out) == 1 and out[0].url == "https://www.vinted.it/items/1"
