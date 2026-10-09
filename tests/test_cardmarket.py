import os
import tempfile
import time

from pokebot import cardmarket as cmk
from pokebot import stats as pstats
from pokebot.cardmarket import CMPrice
from pokebot.db import Database


def _db():
    return Database(os.path.join(tempfile.mkdtemp(), "t.db"))


def test_verdict_rules():
    p = CMPrice(low=6.0, trend=10.0, avg7=None, avg30=None)
    assert cmk.verdict(5.0, p)[0] == "deal"          # 50% del trend e sotto il minimo
    assert cmk.verdict(6.5, p)[0] == "great"         # 65% ma sopra il minimo 6 → non è un affare pieno
    assert cmk.verdict(8.5, p)[0] == "great"         # 85%
    assert cmk.verdict(9.5, p)[0] == "good"          # sotto il valore
    assert cmk.verdict(10.5, p)[0] is None           # sopra il valore
    assert cmk.verdict(1.5, p)[0] is None            # 15%: sospetto
    cheap = CMPrice(low=1.0, trend=4.0, avg7=None, avg30=None)
    assert cmk.verdict(1.0, cheap)[0] == "great"     # carta da 4 €: mai "affare" (soglia 5 €)
    assert cmk.verdict(5.0, None) == (None, None)
    assert cmk.verdict(5.0, p, pct=40)[0] == "great"  # percentuale personale


def test_refresh_maps_sets_and_cards(index):
    db = _db()
    calls = []

    def fake_get(url):
        calls.append(url)
        if url.endswith("/sets"):
            return [{"id": "30th", "name": "30th Celebration", "cardCount": {"total": 161}},
                    {"id": "30th-c", "name": "Classic Collection", "cardCount": {"total": 30}}]
        if url.endswith("/sets/30th"):
            return {"cards": [{"id": "30th-131", "localId": "131"}, {"id": "30th-001", "localId": "001"}]}
        if url.endswith("/sets/30th-c"):
            return {"cards": []}
        if url.endswith("/cards/30th-131"):
            return {"pricing": {"cardmarket": {"low": 12.0, "trend": 20.5, "avg7": 19.0, "avg30": 21.0, "updated": "2026-10-08T22:00:00Z"}}}
        if url.endswith("/cards/30th-001"):
            return {"pricing": {"cardmarket": {"low": None, "trend": 0, "trend-holo": 0.4, "low-holo": 0.1}}}
        raise AssertionError(url)

    line = cmk.refresh(db, index, budget=500, wanted_first={"me55-131"}, get=fake_get)
    assert "con prezzo Cardmarket" in line
    p = cmk.get(db, "me55-131")
    assert p.trend == 20.5 and p.low == 12.0 and p.updated == "2026-10-08"
    assert cmk.get(db, "me55-1").trend == 0.4  # "001" ↔ "1", prezzo holo come ripiego
    assert cmk.get(db, "me55-2").ref is None   # carta non presente su TCGdex: salvata senza prezzo
    n = len(calls)
    assert cmk.refresh(db, index, get=fake_get) == ""  # tutto fresco: nessuna richiesta
    assert len(calls) == n
    later = time.time() + 21 * 3600
    cmk.refresh(db, index, budget=1, wanted_first={"me55-131"}, get=fake_get, now=later)
    assert calls[-1].endswith("/cards/30th-131")  # prima le mancanti


def test_value_and_completion_prefer_cardmarket(index):
    cards = [index.by_id["me55-131"], index.by_id["me55-132"]]
    rows = [{"kind": "single", "price": "50", "matched": [{"id": "me55-131", "sure": True}], "created_at": time.time(), "source": "vinted"}]
    cm = {"me55-131": CMPrice(low=8.0, trend=10.0, avg7=None, avg30=None)}
    v = pstats.collection_value(cards, {"me55-131"}, rows, cm)
    assert v.missing_cost == 10.0 and v.missing_priced == 1  # Cardmarket, non la mediana 50 di Vinted
    from pokebot.cards import CardIndex
    comp = pstats.completion(CardIndex([index.sets[0]]), {"me55-131", "me55-132"}, rows, cm)
    assert comp.cm_priced == 1 and comp.cost_median >= 10.0 and comp.cost_min >= 8.0


def test_personal_deal_uses_cardmarket(index, monkeypatch):
    from pokebot import search
    from pokebot.scrapers.base import Listing

    sent = []

    class N:
        def notify_deal(self, lst, res, ref, images=True, ref_label="", extra=""):
            sent.append((lst.price, ref, ref_label))
            return True

        def notify_many(self, items, max_per_card=5, images=True):
            return [True] * len(items)

        def send(self, *a, **k):
            return True

    class Res:
        kind = "single"
        wanted = [index.by_id["me55-131"]]
        possible_wanted = []

    cm = {"me55-131": CMPrice(low=9.0, trend=20.0, avg7=None, avg30=None)}
    mk = lambda key, price: Listing(source="vinted", listing_id=key, title="Lapras", url="u", price=price, price_text=f"{price} €")
    settings = {"deal_pct": 70, "max_deals_per_run": 3}
    # 13 € è il 65% del trend ma sopra il minimo Cardmarket (9 €): non è un affare
    search._deliver([(mk("a", 13.0), Res)], N(), settings, [], False, None, None, cm)
    assert sent == []
    search._deliver([(mk("b", 8.0), Res)], N(), settings, [], False, None, None, cm)
    assert sent == [(8.0, 20.0, "del trend Cardmarket")]
