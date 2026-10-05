import pytest

from pokebot import config
from pokebot.cards import load_sets
from pokebot.matcher import Matcher


@pytest.fixture(scope="session")
def index():
    return load_sets()


@pytest.fixture(scope="session")
def matcher(index):
    return Matcher(index, config.DEFAULT_SETTINGS["set_keywords"])
