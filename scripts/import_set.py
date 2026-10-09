"""Scarica (o aggiorna) la lista carte di un set dal dataset open pokemon-tcg-data.

Uso:  python scripts/import_set.py me55 "30th Celebration" --name-it "Celebrazione del 30° anniversario" --printed-total 128
Se il file esiste già, le parti scritte a mano (queries, card_suffixes, card_query, contesto, nome italiano) restano.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

RAW = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("set_id", help="id del set nel dataset (es. me55, me55c)")
    p.add_argument("name", nargs="?", help="nome del set (default: dal dataset)")
    p.add_argument("--name-it", default="")
    p.add_argument("--printed-total", type=int, default=None, help="numero stampato sulle carte (es. 128)")
    p.add_argument("--context", default="", help="parole di contesto obbligatorie, separate da virgola")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data" / "sets"))
    a = p.parse_args()

    sets = requests.get(f"{RAW}/sets/en.json", timeout=60).json()
    meta = next((s for s in sets if s["id"] == a.set_id), {})
    cards = requests.get(f"{RAW}/cards/en/{a.set_id}.json", timeout=60).json()
    out = {
        "id": a.set_id,
        "name": a.name or meta.get("name", a.set_id),
        "name_it": a.name_it or a.name or meta.get("name", a.set_id),
        "printed_total": a.printed_total if a.printed_total is not None else meta.get("printedTotal"),
        "total": meta.get("total", len(cards)),
        "release": meta.get("releaseDate", "").replace("/", "-"),
        "context_keywords": [k.strip() for k in a.context.split(",") if k.strip()],
        "cards": [
            {
                "id": c["id"], "number": c["number"], "name": c["name"], "rarity": c.get("rarity", ""),
                "supertype": c.get("supertype", ""),
                "image": c.get("images", {}).get("small", ""), "image_large": c.get("images", {}).get("large", ""),
            }
            for c in cards
        ],
    }
    path = Path(a.out) / f"{a.set_id}.json"
    if path.exists():  # aggiornando, restano le parti scritte a mano (ricerche, parole di contesto, nome italiano)
        old = json.loads(path.read_text(encoding="utf-8"))
        given = {"name_it": a.name_it, "context_keywords": a.context, "printed_total": a.printed_total}
        for k, v in old.items():
            if k not in out or (k in given and not given[k]):
                out[k] = v
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Scritte {len(cards)} carte in {path}")


if __name__ == "__main__":
    main()
