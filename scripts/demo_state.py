"""Pokébot dimostrativo per il video promozionale: database temporaneo con una collezione a metà,
un inseguimento, annunci trovati e prezzi. Scrive state.json + state-1.json nella cartella indicata.

  python scripts/demo_state.py /tmp/demo
"""
from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

os.environ["POKEBOT_DB_PATH"] = str(Path(sys.argv[1]) / "demo.db")
os.environ["TELEGRAM_CHAT_ID"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pokebot import collections as coll  # noqa: E402
from pokebot import config, watch  # noqa: E402
from pokebot.db import Database  # noqa: E402
from pokebot.webapp_export import write_state  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    db = Database()
    db.set_kv("telegram_chat_id", "1")
    db.set_user_name("1", "Simone")
    index = coll.load_index(db)
    rnd = random.Random(30)
    for sid in config.HOME_SET_IDS:
        coll.album_for(db, "1", sid)
    home = [c for c in index.all_cards() if c.set_id == "me55"]
    classic = [c for c in index.all_cards() if c.set_id == "me55c"]
    # il 70% delle carte c'è (accese), le rare mancano più spesso
    missing = [c for c in home if rnd.random() < (0.55 if "Rare" in c.rarity and c.rarity != "Rare" else 0.18)]
    missing += [c for c in classic if rnd.random() < 0.3]
    for sid in config.HOME_SET_IDS:
        album = coll.album_for(db, "1", sid)
        db.set_wanted_bulk([c.id for c in home + classic if c.set_id == sid and c not in missing], False, album)
        db.set_wanted_bulk([c.id for c in missing if c.set_id == sid], True, album)
    # doppioni
    for c in rnd.sample([c for c in home if c not in missing], 6):
        db.set_copies(c.id, 1, "1")
    # inseguimento su una carta mancante famosa
    chase = next((c for c in home if c.number == "158"), missing[0])
    if chase not in missing:
        db.set_wanted_bulk([chase.id], True, coll.album_for(db, "1", "me55"))
    watch.add_watch(db, chase.id, chat_id="1")
    # annunci trovati e prezzi per le mancanti (immagine: quella della carta)
    sources = ["vinted", "wallapop", "ebay"]
    sellers = ["luca_tcg", "pokecollezione", "martina88", "cardhunter", "mew_shop"]
    now = time.time()
    found_for_chase = []
    for i, c in enumerate(missing[:40]):
        base = {"Common": 1.2, "Uncommon": 2, "Rare": 3.5, "Double Rare": 9, "Illustration Rare": 18,
                "Special Illustration Rare": 45, "Pikachu Rare": 12, "Futuristic Rare": 22}.get(c.rarity, 6)
        for j in range(rnd.randint(2, 5)):
            price = round(base * rnd.uniform(0.6, 1.5), 2)
            src = rnd.choice(sources)
            key = f"{src}:{c.id}:{j}"
            title = f"{c.name} {c.number}/{c.printed_total or 159} {c.rarity} 30th Celebration ITA"
            fid = db.add_found(key, src, title, f"https://www.{src}.it/items/{1000 + i * 10 + j}", f"{price:.2f} €",
                               rnd.choice(["Milano", "Roma", "Torino", "Bologna", ""]), "single",
                               [{"id": c.id, "label": c.label, "sure": True}], None, True, image=c.image,
                               seller=rnd.choice(sellers))
            with db.connect() as con:
                con.execute("UPDATE found SET created_at = ? WHERE id = ?", (now - rnd.uniform(600, 10 * 86400), fid))
            db.add_price_point(c.id, key, src, price, rnd.choice(sellers))
            if c is chase:
                found_for_chase.append((key, title, price, src, c.image))
    # un affare 🔥 fresco
    if found_for_chase:
        with db.connect() as con:
            con.execute("UPDATE found SET deal = 1, created_at = ? WHERE listing_key = ?", (now - 900, found_for_chase[0][0]))

    class L:  # annunci scovati dall'inseguimento
        def __init__(self, key, title, price, src, image):
            self.key, self.title, self.price, self.source, self.image = key, title, price, src, image
            self.url, self.price_text, self.location = f"https://www.{src}.it/items/{abs(hash(key)) % 9999}", f"{price:.2f} €", "Milano"
    watch.record_found(db, chase.id, [L(*f) for f in found_for_chase] or
                       [L(f"vinted:{chase.id}:x{k}", f"{chase.name} {chase.number} 30th", 19.9 + 3 * k, "vinted", chase.image) for k in range(3)], "1")
    db.set_kv("last_search_ts", now - 240)
    run = db.start_run()
    db.finish_run(run, 412, 23, {}, {"vinted": 180, "wallapop": 120, "ebay": 112})
    write_state(index, db, str(out / "state.json"))
    print(f"demo pronta in {out}: {len(missing)} mancanti, inseguimento su {chase.label}")


if __name__ == "__main__":
    main()
