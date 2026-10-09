"""Indice compatto di tutte le carte del catalogo pubblico (pokemon-tcg-data), per la ricerca nella Mini App.

  python scripts/build_card_index.py deploy/app/cards.json

Formato: {"built": <epoch>, "sets": {"sv8": [["7", "Pikachu ex"], ...], ...}, "names_it": {"sv8": "Scintille Folgoranti", ...}}.
I nomi italiani vengono da TCGdex (abbinamento per nome inglese, come cardmarket.set_map). Lo genera ponte.yml a ogni deploy.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RAW = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master"
TCGDEX = "https://api.tcgdex.net/v2"
OVERRIDES = {"me55": "30th", "me55c": "30th-c"}  # come pokebot.cardmarket

norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())  # noqa: E731


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
    names_it = italian_names(sets)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"built": int(time.time()), "sets": index, "names_it": names_it}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{n} carte in {len(index)} collezioni, {len(names_it)} nomi italiani → {out}")


def italian_names(sets: list[dict]) -> dict[str, str]:
    """Nome italiano di ogni collezione: la stessa collezione su TCGdex (per nome inglese, poi per id), in italiano."""
    try:
        en, it = fetch(f"{TCGDEX}/en/sets"), fetch(f"{TCGDEX}/it/sets")
    except Exception as exc:  # noqa: BLE001 - senza TCGdex restano i nomi inglesi
        print(f"  TCGdex non raggiungibile: {exc}", file=sys.stderr)
        return {}
    it_by_id = {x["id"]: x.get("name") for x in it}
    by_name: dict[str, list[dict]] = {}
    for x in en:
        by_name.setdefault(norm(x.get("name")), []).append(x)
    out = {}
    for s in sets:
        tid = OVERRIDES.get(s["id"])
        if not tid:
            cands = by_name.get(norm(s["name"]), [])
            if len(cands) > 1:  # stesso nome: decide il numero di carte
                cands.sort(key=lambda x: abs(int((x.get("cardCount") or {}).get("total") or 0) - int(s.get("total") or 0)))
            tid = cands[0]["id"] if cands else (s["id"] if s["id"] in it_by_id else None)
        name = it_by_id.get(tid) if tid else None
        if name and norm(name) != norm(s["name"]):
            out[s["id"]] = name
    return out


if __name__ == "__main__":
    main()
