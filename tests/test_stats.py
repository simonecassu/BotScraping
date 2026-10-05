import time

from pokebot import stats


def rows_for(card_id, prices, source="vinted", kind="single", age_days=0):
    ts = time.time() - age_days * 86400
    return [{"listing_key": f"{source}:{i}", "source": source, "kind": kind, "price": f"{p} €", "created_at": ts,
             "matched": [{"id": card_id, "label": "x", "sure": True}]} for i, p in enumerate(prices)]


def test_card_prices_and_trend(index):
    card = index.by_id["me55-131"]
    rows = rows_for("me55-131", [20, 30, 40], age_days=10) + rows_for("me55-131", [10, 12], "ebay")
    cp = stats.card_prices(rows, card)
    assert cp.overall.n == 5 and cp.overall.min == 10 and cp.overall.max == 40 and cp.overall.median == 20
    assert cp.by_source["ebay"].median == 11 and cp.by_source["vinted"].n == 3
    assert cp.recent_median == 11 and cp.older_median == 30
    # lotti e prezzi mancanti ignorati
    rows2 = rows + rows_for("me55-131", [1], kind="lot") + [{"source": "vinted", "kind": "single", "price": None,
                                                              "created_at": time.time(), "matched": [{"id": "me55-131"}]}]
    assert stats.card_prices(rows2, card).overall.n == 5


def test_median_for_deal_needs_points():
    assert stats.median_for_deal(rows_for("c", [10, 20, 30]), "c") is None
    assert stats.median_for_deal(rows_for("c", [10, 20, 30, 40]), "c") == 25


def test_completion(index):
    wanted = {"me55-131", "me55-145"}
    rows = rows_for("me55-131", [10, 20])
    comp = stats.completion(index, wanted, rows)
    assert comp.total == 191 and comp.missing == 2 and comp.owned == 189 and comp.priced == 1
    assert comp.cost_min == 10 and comp.cost_median == 15 and [c.id for c in comp.unpriced] == ["me55-145"]
    assert comp.by_rarity["Illustration Rare"] == (2, 19)  # 18 nel set principale + Magikarp della Classic
    assert stats.fmt_eur(1234.5) == "1.234,50 €" and stats.fmt_eur(None) == "n.d."
