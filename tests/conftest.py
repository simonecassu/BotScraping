import pytest

from pokebot.cards import load_sets
from pokebot.matcher import Matcher


@pytest.fixture
def index():
    return load_sets()


@pytest.fixture
def matcher(index):
    return Matcher(index)
