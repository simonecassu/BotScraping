"""Indice compatto di tutte le carte del catalogo pubblico (pokemon-tcg-data), per la ricerca nella Mini App.

  python scripts/build_card_index.py deploy/app/cards.json

Formato: {"built": <epoch>, "sets": {"sv8": [["7", "Pikachu ex"], ...], ...}}. Lo genera ponte.yml a ogni deploy.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RAW = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master"


def fetch(url: str):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def main() -> None:
    out = sys.argv[1]
    sets = fetch(f"{RAW}/sets/en.json")

    def one(s):
        try:
            cards = fetch(f"{RAW}/cards/en/{s['id']}.json")
        except Exception as exc:  # noqa: BLE001 - un set mancante non ferma l'indice
            print(f"  {s['id']}: {exc}", file=sys.stderr)
            return s["id"], []
        return s["id"], [[str(c["number"]), c["name"]] for c in cards]

    with ThreadPoolExecutor(max_workers=8) as ex:
        index = dict(ex.map(one, sets))
    n = sum(len(v) for v in index.values())
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"built": int(time.time()), "sets": index}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{n} carte in {len(index)} collezioni → {out}")


if __name__ == "__main__":
    main()
