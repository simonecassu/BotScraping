"""Caricamento dei set e indice delle carte."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import config


def normalize(text: str) -> str:
    """Minuscolo, senza accenti, punteggiatura ridotta a spazi (mantiene ' / # .)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("’", "'").replace("`", "'")
    text = re.sub(r"[\-_–—|,;:()\[\]{}!?\"*+<>=~^%$€£@&]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@dataclass(frozen=True)
class Card:
    id: str
    set_id: str
    set_name: str
    number: str
    name: str
    rarity: str
    supertype: str
    image: str
    image_large: str
    printed_total: int | None

    @property
    def label(self) -> str:
        if self.printed_total:
            return f"{self.name} {self.number}/{self.printed_total}"
        return f"{self.name} #{self.number} ({self.set_name})"

    @property
    def sort_key(self) -> tuple:
        num = self.number
        return (self.set_id, 0 if num.isdigit() else 1, int(num) if num.isdigit() else 0, num)


@dataclass
class CardSet:
    id: str
    name: str
    name_it: str
    printed_total: int | None
    total: int
    release: str
    cards: list[Card] = field(default_factory=list)
    # richiede che il testo dell'annuncio contenga una di queste parole per considerare i nomi del set
    context_keywords: list[str] = field(default_factory=list)
    series: str = ""
    logo: str = ""
    symbol: str = ""
    queries: list[str] = field(default_factory=list)  # ricerche generiche sul set (ogni giro completo)
    card_suffixes: list[str] = field(default_factory=list)  # "{nome} {suffisso}" nelle ricerche mirate
    card_query: str = ""  # per i set senza numerazione propria: "{nome} {card_query}" nella ricerca a rotazione
    primary: bool = False  # collezione principale: codici brevi nei comandi (numero, c1..cN); le altre usano set:numero

    @property
    def keyword(self) -> str:
        return self.context_keywords[0] if self.context_keywords else self.name


class CardIndex:
    def __init__(self, sets: list[CardSet], aliases: dict[str, list[str]] | None = None):
        self.sets = sets
        self.aliases = aliases or {}
        self.by_id: dict[str, Card] = {}
        self.by_set_number: dict[tuple[str, str], Card] = {}
        self.names: dict[str, list[Card]] = {}  # nome normalizzato -> carte
        # codice breve per i comandi Telegram: numero per la collezione principale, c1..cN per la sua Classic,
        # "set:numero" per tutte le altre collezioni (così i numeri non si confondono tra set)
        self.code_of: dict[str, str] = {}
        self.by_code: dict[str, Card] = {}
        for s in sets:
            self._index_set(s)

    def add_set(self, cs: CardSet) -> None:
        """Aggiunge una collezione all'indice già costruito (es. appena scaricata con /collezione)."""
        if any(s.id == cs.id for s in self.sets):
            return
        self.sets.append(cs)
        self._index_set(cs)

    def _index_set(self, s: CardSet) -> None:
        aliases = self.aliases
        for i, c in enumerate(s.cards, start=1):
            self.by_id[c.id] = c
            self.by_set_number[(s.id, c.number)] = c
            if s.primary:
                code = c.number.lower() if s.printed_total else f"c{i}"
            else:
                code = f"{s.id}:{c.number.lower()}"
            self.code_of[c.id] = code
            self.by_code.setdefault(code, c)
            for n in self._name_variants(c.name) + [normalize(a) for a in aliases.get(c.id, [])]:
                if len(n) < 3:
                    continue  # nomi di 1-2 lettere (es. "N") darebbero falsi positivi
                self.names.setdefault(n, [])
                if c not in self.names[n]:
                    self.names[n].append(c)

    @staticmethod
    def _name_variants(name: str) -> list[str]:
        base = normalize(name)
        variants = {base}
        # "Rayquaza-EX" -> "rayquaza ex" già gestito da normalize; aggiungi forme senza spazio
        variants.add(base.replace(" ex", "ex") if base.endswith(" ex") else base)
        if "'s " in base:  # "erika's jigglypuff" -> "jigglypuff di erika"
            owner, _, rest = base.partition("'s ")
            variants.add(f"{rest} di {owner}")
        return sorted(variants)

    def all_cards(self) -> list[Card]:
        return sorted(self.by_id.values(), key=lambda c: c.sort_key)

    def get_set(self, set_id: str) -> CardSet | None:
        return next((s for s in self.sets if s.id == set_id), None)


def set_from_raw(raw: dict) -> CardSet:
    """Costruisce un set dal nostro JSON (data/sets/*.json o quello scaricato dal catalogo pubblico)."""
    cs = CardSet(
        id=raw["id"],
        name=raw["name"],
        name_it=raw.get("name_it", raw["name"]),
        printed_total=raw.get("printed_total"),
        total=raw.get("total", len(raw["cards"])),
        release=raw.get("release", ""),
        context_keywords=raw.get("context_keywords", []),
        series=raw.get("series", ""),
        logo=raw.get("logo", ""),
        symbol=raw.get("symbol", ""),
        queries=raw.get("queries", []),
        card_suffixes=raw.get("card_suffixes", []),
        card_query=raw.get("card_query", ""),
        primary=bool(raw.get("primary", False)),
    )
    for c in raw["cards"]:
        cs.cards.append(
            Card(
                id=c["id"],
                set_id=cs.id,
                set_name=cs.name,
                number=str(c["number"]),
                name=c["name"],
                rarity=c.get("rarity", ""),
                supertype=c.get("supertype", ""),
                image=c.get("image", ""),
                image_large=c.get("image_large", ""),
                printed_total=cs.printed_total,
            )
        )
    return cs


def load_sets(sets_dir: Path | None = None, aliases_path: Path | None = None) -> CardIndex:
    sets_dir = sets_dir or config.SETS_DIR
    aliases_path = aliases_path or (config.DATA_DIR / "aliases.json")
    sets: list[CardSet] = []
    for path in sorted(sets_dir.glob("*.json")):
        cs = set_from_raw(json.loads(path.read_text(encoding="utf-8")))
        cs.primary = True  # le collezioni in data/sets sono quelle "di casa": codici brevi nei comandi
        sets.append(cs)
    aliases: dict[str, list[str]] = {}
    if aliases_path.exists():
        aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
    return CardIndex(sets, aliases)
