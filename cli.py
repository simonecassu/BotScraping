"""Strumenti da riga di comando.

  python cli.py cerca            esegue un ciclo di ricerca e invia le notifiche
  python cli.py cerca --dry-run  come sopra ma senza inviare su Telegram
  python cli.py test-telegram    invia un messaggio di prova
  python cli.py mancanti         elenca le carte selezionate come mancanti
  python cli.py analizza "titolo annuncio" ["descrizione"]   mostra come il bot classifica un testo
  python cli.py actions          un passaggio completo per GitHub Actions / cron: legge i comandi
                                 Telegram arrivati, esegue la ricerca, compatta il database
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from pokebot import config
from pokebot.cards import load_sets
from pokebot.db import Database
from pokebot.matcher import Matcher
from pokebot.notifier import TelegramNotifier
from pokebot.search import run_search
from pokebot.telegram_bot import TelegramCommands


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PokéBot")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("cerca", help="esegui un ciclo di ricerca")
    s.add_argument("--dry-run", action="store_true", help="non inviare notifiche")
    s.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("test-telegram", help="invia un messaggio di prova")
    sub.add_parser("mancanti", help="elenca le carte mancanti")
    a = sub.add_parser("analizza", help="classifica un testo di annuncio")
    a.add_argument("titolo")
    a.add_argument("descrizione", nargs="?", default="")
    a.add_argument("--asta", action="store_true")
    ej = sub.add_parser("export-json", help="scrive il riepilogo JSON per la Mini App")
    ej.add_argument("path")
    sub.add_parser("db-hash", help="impronta del contenuto del database (per salvare lo stato solo se cambiato)")
    ac = sub.add_parser("actions", help="comandi Telegram + ricerca + pulizia (per GitHub Actions / cron)")
    ac.add_argument("--force", action="store_true", help="cerca anche se l'intervallo non è ancora passato")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "db-hash":
        import hashlib
        import sqlite3
        if not config.DB_PATH.exists():
            print("nessun-database")
            return 0
        con = sqlite3.connect(str(config.DB_PATH))
        h = hashlib.sha256()
        for line in con.iterdump():
            h.update(line.encode("utf-8", "replace"))
        print(h.hexdigest())
        return 0

    db = Database()
    from pokebot import collections as coll
    index = coll.load_index(db)  # collezioni di casa + quelle seguite

    if args.cmd == "cerca":
        rep = run_search(index, db, dry_run=args.dry_run)
        print(f"Query: {rep.queries} · annunci: {rep.listings} (nuovi {rep.new_listings}) · match: {rep.matches} · notifiche: {rep.notified}")
        for k, v in rep.errors.items():
            print(f"  errore {k}: {v}")
        return 0
    if args.cmd == "actions":
        commands = TelegramCommands(index, db)
        commands.ensure_menu()  # menu comandi e presentazione del bot, solo quando cambiano
        from pokebot import plans
        plans.migrate(db)  # chi era dentro prima dei piani tiene tutto
        # ogni persona collegata ha i suoi album di casa (nati "tutte mancanti"): devono esistere nel database salvato
        for chat in db.chat_ids():
            for sid in config.HOME_SET_IDS:
                coll.album_for(db, chat, sid)
        want_search = False
        from pokebot.queue import GitHubQueue
        n_queue, want_search = GitHubQueue().drain(commands.handle_payload)  # comandi salvati dal ponte
        if n_queue:
            print(f"Comandi dalla coda: {n_queue}")
        payload = _dispatch_payload()
        if payload is not None:  # ponte senza coda (fallback): il comando viaggia nel payload
            want_search = commands.handle_payload(payload) or want_search
        elif commands.enabled and not n_queue:
            want_search = commands.poll_once(timeout=0)
        else:
            print("TELEGRAM_BOT_TOKEN mancante: nessun comando letto, nessuna notifica possibile.")
        from pokebot import cardmarket
        try:  # prezzi Cardmarket (TCGdex): ogni carta una volta al giorno, prima le mancanti
            all_wanted = set().union(*(db.wanted_for(c) for c in db.chat_ids())) if db.chat_ids() else set()
            line = cardmarket.refresh(db, index, wanted_first=all_wanted)
            if line:
                print(f"cardmarket · {line}")
        except Exception as exc:  # noqa: BLE001 - i prezzi non devono mai fermare il bot
            print(f"cardmarket · errore: {exc}")
        from pokebot.watch import run_watches
        for line in run_watches(index, db):  # inseguimenti (/insegui): a ogni sveglia, 5 minuti
            print(f"inseguimento · {line}")
        if commands.enabled and not db.get_kv("bot_username"):
            try:  # serve all'app per il link "aggiungimi" e al canale per il pulsante
                db.set_kv("bot_username", commands.client.get_me())
            except Exception as exc:  # noqa: BLE001
                print(f"getMe: {exc}")
        if commands.enabled:
            for line in plans.check_trials(db, commands.client):  # promemoria e fine della prova
                print(f"piani · {line}")
            from pokebot import channel
            line = channel.post_daily(db, index, commands.client)  # canale degli affari: una volta al giorno
            if line:
                print(f"canale · {line}")
        if not (args.force or want_search or _search_due(db)):
            from pokebot.search import flush_all_queues
            flush_all_queues(db, index.by_id)  # chi ha finito pausa o notte riceve quello che si è accumulato
            print("Ricerca completa non ancora dovuta (vedi /intervallo): solo comandi Telegram e inseguimenti.")
            db.prune()
            return 0
        rep = run_search(index, db)
        db.set_kv("last_search_ts", time.time())
        db.prune()
        print(f"Query: {rep.queries} · annunci: {rep.listings} (nuovi {rep.new_listings}) · match: {rep.matches} · notifiche: {rep.notified}")
        for k, v in rep.errors.items():
            print(f"  errore {k}: {v}")
        return 0
    if args.cmd == "export-json":
        from pokebot.webapp_export import write_state
        write_state(index, db, args.path)
        print(f"Stato scritto in {args.path}")
        return 0
    if args.cmd == "test-telegram":
        ok = TelegramNotifier(chat_ids=db.chat_ids()[:1] or None).test_message()
        print("Inviato." if ok else "Invio fallito: controlla TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.")
        return 0 if ok else 1
    if args.cmd == "mancanti":
        wanted = db.wanted_ids()
        for c in index.all_cards():
            if c.id in wanted:
                print(f"{c.label:40s} {c.rarity}")
        print(f"\n{len(wanted)} carte mancanti su {len(index.by_id)}")
        return 0
    if args.cmd == "analizza":
        settings = db.get_settings()
        m = Matcher(index)
        res = m.analyze(args.titolo, args.descrizione, db.wanted_ids(), float(settings["lot_min_ratio"]),
                        bool(settings["notify_unverifiable_lots"]), args.asta)
        print(f"tipo: {res.kind}  notifica: {res.notify}  motivo: {res.reason}")
        for r in res.refs:
            print(f"  riconosciuta: {r.card.label} ({r.via})")
        for g in res.ambiguous:
            print(f"  ambigua '{g.name}': {', '.join(c.label for c in g.candidates)}")
        return 0
    return 1


def _dispatch_payload() -> dict | None:
    """Payload del repository_dispatch (ponte Telegram → GitHub), se presente."""
    raw = os.getenv("POKEBOT_DISPATCH_PAYLOAD", "").strip()
    if not raw or raw == "null":
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and data.get("text") else None


def _search_due(db: Database) -> bool:
    interval = float(db.get_settings().get("interval_minutes", 20) or 20)
    last = float(db.get_kv("last_search_ts", 0) or 0)
    return time.time() - last >= interval * 60 - 30  # 30 s di tolleranza sui ritardi del cron


if __name__ == "__main__":
    sys.exit(main())
