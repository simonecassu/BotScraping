import os
import tempfile
import time

from pokebot import shopping, stats
from pokebot.db import Database


def _db():
    return Database(os.path.join(tempfile.mkdtemp(), "t.db"))


def _row(key, price, ids, kind="single", seller=None, age=0):
    return {"listing_key": key, "source": key.split(":")[0], "title": key, "url": "https://x/" + key, "price": f"{price} €",
            "kind": kind, "matched": [{"id": i, "sure": True} for i in ids], "created_at": time.time() - age, "seller": seller}


def test_shopping_list_prefers_cheap_singles_and_good_lots(index):
    cards = [c for s in index.sets for c in s.cards]
    wanted = {"me55-131", "me55-145", "me55-146", "me55-151"}
    rows = [
        _row("ebay:a", 10, ["me55-131"], seller="mario"),
        _row("ebay:b", 12, ["me55-131"], seller="luigi"),
        _row("ebay:c", 8, ["me55-145"], seller="mario"),
        _row("vinted:d", 15, ["me55-146"]),
        _row("ebay:lot", 20, ["me55-145", "me55-146"], kind="lot", seller="peach"),  # 20 < 8 + 15: conviene
        _row("ebay:old", 1, ["me55-151"], age=30 * 86400),  # troppo vecchio
    ]
    sl = shopping.build(cards, wanted, rows)
    keys = {p.row["listing_key"] for p in sl.picks}
    assert keys == {"ebay:a", "ebay:lot"} and sl.total == 30 and sl.covered == 3
    assert [c.id for c in sl.uncovered] == ["me55-151"]
    groups = dict(sl.by_seller())
    assert set(groups) == {"ebay:mario", "ebay:peach"} and sl.seller_label("ebay:mario") == "eBay.it · mario"
    # lotto troppo caro: restano le singole
    rows[4] = _row("ebay:lot", 30, ["me55-145", "me55-146"], kind="lot")
    sl2 = shopping.build(cards, wanted, rows)
    assert {p.row["listing_key"] for p in sl2.picks} == {"ebay:a", "ebay:c", "vinted:d"}


def test_collection_value_and_price_points(index):
    db = _db()
    db.add_price_point("me55-1", "vinted:x", "vinted", 2.0)
    db.add_price_point("me55-1", "vinted:y", "vinted", 4.0)
    db.add_price_point("me55-1", "vinted:y", "vinted", 99.0)  # stesso annuncio: ignorato
    db.add_price_point("me55-131", "ebay:z", "ebay", 10.0)
    db.set_wanted("me55-131", True)
    cards = index.get_set("me55").cards
    v = stats.collection_value(cards, db.wanted_ids(), db.price_rows())
    assert v.owned == 160 and v.owned_priced == 1 and v.owned_value == 3.0
    assert v.missing == 1 and v.missing_priced == 1 and v.missing_cost == 10.0


def test_copies_commands_and_swap(index):
    from pokebot.telegram_bot import CommandHandler
    db = _db()
    h = CommandHandler(index, db)
    db.set_wanted("me55-131", True)
    assert "Nessun doppione" in h.handle("/doppioni").text
    assert "➕" in h.handle("/doppioni 145 146").text and db.copies() == {"me55-145": 1, "me55-146": 1}
    h.handle("/doppioni 145")
    assert db.copies()["me55-145"] == 2
    assert "➖" in h.handle("/doppioni togli 146").text and "me55-146" not in db.copies()
    assert "×2" in h.handle("/doppioni").text
    sw = h.handle("/scambio").text
    assert "CERCO: Lapras 131/128" in sw and "OFFRO (doppioni): Hisuian Zorua 145/128 ×2" in sw
    assert "Valore" in h.handle("/valore").text or "valore" in h.handle("/valore").text
    assert "nessun annuncio recente" in h.handle("/spesa").text.lower()


def test_search_records_price_points_for_owned_cards(index):
    from pokebot.search import run_search
    from pokebot.scrapers.base import Listing
    from tests.test_search import FakeNotifier, FakeScraper
    db = _db()
    db.save_settings({"sources": ["fake"], "generic_queries": ["x"], "per_card_queries": False, "quiet_hours": None, "deal_pct": 0})
    db.set_wanted("me55-131", True)
    scr = FakeScraper([Listing("fake", "o", "Pikachu ex 149/128 30th", "https://f/1", price=30.0, price_text="30 €", seller="ash"),
                       Listing("fake", "w", "Lapras 131/128 30th", "https://f/2", price=12.0, price_text="12 €")])
    run_search(index, db, FakeNotifier(), {"fake": scr})
    pts = {r["matched"][0]["id"]: r for r in db.price_rows()}
    assert set(pts) == {"me55-149", "me55-131"} and pts["me55-149"]["seller"] == "ash"
