"""Casi realistici di annunci italiani."""


def ids(cards):
    return sorted(c.id for c in cards)


# ---- carte singole --------------------------------------------------------
def test_single_by_number_wanted(matcher):
    r = matcher.analyze("Lapras 131/128 Illustration Rare Pokemon 30th Celebration", "", {"me55-131"})
    assert r.kind == "single" and r.notify
    assert ids(r.wanted) == ["me55-131"]


def test_single_by_number_without_set_keyword(matcher):
    # il solo "131/128" basta a identificare il set
    r = matcher.analyze("Carta Pokemon Lapras 131/128 near mint", "", {"me55-131"})
    assert r.kind == "single" and r.notify


def test_single_not_wanted(matcher):
    r = matcher.analyze("Lapras 131/128 Pokemon 30th", "", {"me55-1"})
    assert r.kind == "single" and not r.notify


def test_single_name_plus_number_adjacent(matcher):
    r = matcher.analyze("Pikachu ex 149 pokemon 30th celebration", "", {"me55-149"})
    assert r.notify and ids(r.wanted) == ["me55-149"]


def test_single_ambiguous_name_possible(matcher):
    # "Lapras" senza numero: due versioni (17 e 131). Notifica come "da verificare".
    r = matcher.analyze("Lapras pokemon 30th celebration", "", {"me55-131"})
    assert r.kind == "single" and r.notify
    assert not r.wanted and ids(r.possible_wanted) == ["me55-131"]


def test_single_ambiguous_none_wanted(matcher):
    r = matcher.analyze("Lapras pokemon 30th celebration", "", {"me55-1"})
    assert r.kind == "single" and not r.notify


def test_name_only_without_set_context_is_ignored(matcher):
    r = matcher.analyze("Lapras carta pokemon", "", {"me55-131", "me55-17"})
    assert r.kind == "none" and not r.notify


def test_longest_name_wins(matcher):
    # "mew ex" non deve far scattare anche "mew"
    r = matcher.analyze("Mew ex 152/128 30th celebration", "", {"me55-R", "me55-152"})
    assert ids(r.wanted) == ["me55-152"] and r.kind == "single"


def test_number_pattern_with_spaces(matcher):
    r = matcher.analyze("Pokemon Moltres 130 / 128 IR", "", {"me55-130"})
    assert r.notify and ids(r.wanted) == ["me55-130"]


def test_classic_collection_requires_context(matcher):
    r = matcher.analyze("Charizard base set 4/102 holo", "", {"me55c-4"})
    assert not r.notify
    r = matcher.analyze("Charizard 30th Celebration Classic Collection", "", {"me55c-4"})
    assert r.notify and ids(r.wanted) == ["me55c-4"]


# ---- lotti ------------------------------------------------------------------
def test_lot_ratio_ok(matcher):
    title = "Lotto Pokemon 30th Celebration: Lapras 131/128, Moltres 130/128, Articuno 132/128, Zapdos 133/128"
    r = matcher.analyze(title, "", {"me55-131", "me55-130", "me55-1"})
    assert r.kind == "lot" and r.total_cards == 4 and r.wanted_count == 2
    assert r.ratio == 0.5 and r.notify


def test_lot_ratio_too_low(matcher):
    title = "Lotto Pokemon 30th: Lapras 131/128, Moltres 130/128, Articuno 132/128, Zapdos 133/128"
    r = matcher.analyze(title, "", {"me55-131"})
    assert r.kind == "lot" and not r.notify and r.ratio == 0.25


def test_lot_stated_count_lowers_ratio(matcher):
    r = matcher.analyze("Lotto 20 carte pokemon 30th celebration", "Tra cui Lapras 131/128 e Moltres 130/128",
                        {"me55-131", "me55-130"})
    assert r.kind == "lot" and r.total_cards == 20 and not r.notify


def test_lot_unverifiable_default_skip(matcher):
    r = matcher.analyze("Lotto carte pokemon 30th celebration", "varie rarità", {"me55-131"})
    assert r.kind == "lot" and not r.notify
    r2 = matcher.analyze("Lotto carte pokemon 30th celebration", "varie rarità", {"me55-131"},
                         notify_unverifiable_lots=True)
    assert r2.notify


def test_full_set_ratio(matcher, index):
    main_set = [c.id for c in index.get_set("me55").cards]
    half = set(main_set[: len(main_set) // 2 + 1])
    r = matcher.analyze("Set completo Pokemon 30th Celebration 161 carte", "", half)
    assert r.kind == "lot" and r.notify and r.total_cards == 161
    r2 = matcher.analyze("Set completo Pokemon 30th Celebration", "", {"me55-1"})
    assert not r2.notify


def test_two_refs_without_lot_word_is_lot(matcher):
    r = matcher.analyze("Pokemon 30th Lapras 131/128 e Moltres 130/128", "", {"me55-131", "me55-130"})
    assert r.kind == "lot" and r.notify and r.ratio == 1.0


def test_custom_threshold(matcher):
    title = "Lotto 30th: Lapras 131/128, Moltres 130/128, Articuno 132/128, Zapdos 133/128"
    assert not matcher.analyze(title, "", {"me55-131"}, lot_min_ratio=0.5).notify
    assert matcher.analyze(title, "", {"me55-131"}, lot_min_ratio=0.25).notify


# ---- esclusioni -------------------------------------------------------------
def test_excludes(matcher):
    w = {"me55-131"}
    assert matcher.analyze("CERCO Lapras 131/128 30th", "", w).kind == "excluded"
    assert matcher.analyze("Lapras 131/128 30th VENDUTA", "", w).kind == "excluded"
    assert matcher.analyze("Lapras 131/128 30th preordine", "", w).kind == "excluded"
    assert matcher.analyze("Lapras 131/128 30th all'asta", "", w).kind == "excluded"
    assert matcher.analyze("Lapras 131/128 30th proxy", "", w).kind == "excluded"
    assert matcher.analyze("Lapras 131/128 30th codice online", "", w).kind == "excluded"
    assert matcher.analyze("Pokemon 30th Celebration Elite Trainer Box ETB", "", w).kind == "excluded"
    assert matcher.analyze("Raccoglitore Pokemon 30th celebration", "", w).kind == "excluded"
    assert matcher.analyze("Lapras 131/128 30th", "", w, is_auction=True).kind == "excluded"


def test_trade_only_vs_trade_and_sell(matcher):
    w = {"me55-131"}
    assert matcher.analyze("Scambio Lapras 131/128 30th", "", w).kind == "excluded"
    assert matcher.analyze("Vendo o scambio Lapras 131/128 30th", "", w).notify


def test_other_anniversary_set(matcher):
    r = matcher.analyze("Pokemon Celebrations 25th anniversary Mew", "", {"me55-R"})
    assert not r.notify


def test_pokemon_tcg_pocket_excluded(matcher):
    r = matcher.analyze("Account Pokemon TCG Pocket 30th celebration", "", {"me55-131"})
    assert r.kind == "excluded"


def test_anniversary_number_is_not_card_number(matcher):
    # "30th" non deve diventare la carta n. 30
    r = matcher.analyze("Pikachu 30th celebration", "", {"me55-30"})
    assert not r.refs and r.ambiguous and r.kind == "single"
    assert r.notify and any(c.id == "me55-30" for c in r.possible_wanted)
    r2 = matcher.analyze("Pikachu 30 30th celebration", "", {"me55-30"})
    assert [x.card.id for x in r2.refs] == ["me55-30"]
