"""Strumenti da riga di comando.

  python cli.py cerca            esegue un ciclo di ricerca e invia le notifiche
  python cli.py cerca --dry-run  come sopra ma senza inviare su Telegram
  python cli.py test-telegram    invia un messaggio di prova
  python cli.py mancanti         elenca le carte selezionate come mancanti
  python cli.py analizza "titolo annuncio" ["descrizione"]   mostra come il bot classifica un testo
  python cli.py actions          un passaggio completo per GitHub Actions / cron: legge i comandi
                                 Telegram arrivati, esegue la ricerca, compatta il database
                                 (--fase comandi | --fase ricerca: mezzo giro per volta, così bot.yml salva lo
                                 stato subito dopo i comandi e la Mini App si aggiorna in mezzo minuto)
  python cli.py export-json DIR  scrive lo stato per la Mini App e il ponte (cifrato; --chiaro per guardarlo)
  python cli.py seal SRC DST     cifra un file per il branch pubblico bot-state (unseal: il contrario)
  python cli.py queue-ack        cancella dalla coda i comandi eseguiti (dopo aver salvato lo stato)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from pokebot import config
from pokebot.db import Database
from pokebot.matcher import Matcher
from pokebot.notifier import TelegramNotifier
from pokebot.search import run_search
from pokebot.telegram_bot import TelegramCommands


QUEUE_DONE = config.DATA_DIR / "queue-done.json"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pescacarte")
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
    ej = sub.add_parser("export-json", help="scrive lo stato per la Mini App e il ponte nella cartella indicata")
    ej.add_argument("folder")
    ej.add_argument("--chiaro", action="store_true", help="non cifrare (solo per guardarlo in locale)")
    for name, what in (("seal", "cifra"), ("unseal", "decifra")):
        sp = sub.add_parser(name, help=f"{what} un file (stato del branch bot-state)")
        sp.add_argument("src")
        sp.add_argument("dst")
    sub.add_parser("db-hash", help="impronta del contenuto del database (per salvare lo stato solo se cambiato)")
    sub.add_parser("queue-ack", help="cancella dalla coda i comandi eseguiti nell'ultimo giro")
    ac = sub.add_parser("actions", help="comandi Telegram + ricerca + pulizia (per GitHub Actions / cron)")
    ac.add_argument("--force", action="store_true", help="cerca anche se l'intervallo non è ancora passato")
    ac.add_argument("--fase", choices=("comandi", "ricerca"), help="solo i comandi Telegram, oppure solo inseguimenti e ricerca")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd in ("seal", "unseal"):
        from pokebot import vault
        with open(args.src, "rb") as f:
            data = f.read()
        with open(args.dst, "wb") as f:
            f.write(vault.seal(data) if args.cmd == "seal" else vault.unseal(data))
        return 0
    if args.cmd == "queue-ack":
        from pokebot.queue import GitHubQueue
        if QUEUE_DONE.exists():
            GitHubQueue().ack(json.loads(QUEUE_DONE.read_text(encoding="utf-8")))
            QUEUE_DONE.unlink()
        return 0
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
        return _actions(args, db, index)
    if args.cmd == "export-json":
        from pokebot.webapp_export import write_state
        files = write_state(index, db, args.folder, seal=not args.chiaro)
        print(f"Stato scritto in {args.folder} ({len(files)} file)")
        return 0
    if args.cmd == "test-telegram":
        ok = TelegramNotifier(chat_ids=db.chat_ids()[:1] or None).test_message()
        print("Inviato." if ok else "Invio fallito: controlla TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.")
        return 0 if ok else 1
    if args.cmd == "mancanti":
        wanted = db.wanted_of_members()
        for c in index.all_cards():
            if c.id in wanted:
                print(f"{c.label:40s} {c.rarity}")
        print(f"\n{len(wanted)} carte mancanti su {len(index.by_id)}")
        return 0
    if args.cmd == "analizza":
        settings = db.get_settings()
        m = Matcher(index)
        res = m.analyze(args.titolo, args.descrizione, db.wanted_of_members(), float(settings["lot_min_ratio"]),
                        bool(settings["notify_unverifiable_lots"]), args.asta)
        print(f"tipo: {res.kind}  notifica: {res.notify}  motivo: {res.reason}")
        for r in res.refs:
            print(f"  riconosciuta: {r.card.label} ({r.via})")
        for g in res.ambiguous:
            print(f"  ambigua '{g.name}': {', '.join(c.label for c in g.candidates)}")
        return 0
    return 1


def _actions(args, db: Database, index) -> int:
    """Il giro del bot su GitHub Actions, in due fasi: `--fase comandi` (i comandi arrivati dal ponte) e `--fase ricerca`
    (inseguimenti, prezzi, ricerca). Tra le due bot.yml salva lo stato: così la Mini App vede le modifiche in mezzo
    minuto, senza aspettare la ricerca. Senza `--fase` (cron, PC) fa tutto in un colpo."""
    from pokebot import collections as coll
    from pokebot import plans
    commands = TelegramCommands(index, db)
    commands.ensure_menu()  # menu comandi e presentazione del bot, solo quando cambiano
    plans.migrate(db)  # chi era dentro prima dei piani tiene tutto
    db.migrate_personal_settings()  # le vecchie impostazioni "di tutti" diventano del proprietario
    # ogni persona collegata ha i suoi album di casa (nati "tutte mancanti"): devono esistere nel database salvato
    for chat in db.chat_ids():
        for sid in config.HOME_SET_IDS:
            coll.album_for(db, chat, sid)
    if args.fase != "ricerca":
        from pokebot.queue import GitHubQueue
        # comandi salvati dal ponte: si cancellano dalla coda (queue-ack) solo dopo aver salvato lo stato
        n_queue, want_search = GitHubQueue().drain(commands.handle_payload, done_file=str(QUEUE_DONE))
        if n_queue:
            print(f"Comandi dalla coda: {n_queue}")
        if not commands.enabled:
            print("TELEGRAM_BOT_TOKEN mancante: nessun comando letto, nessuna notifica possibile.")
        elif not n_queue:  # senza ponte (webhook spento) i messaggi si leggono direttamente da Telegram
            want_search = commands.poll_once(timeout=0)
        if want_search:
            db.set_kv("search_requested", True)  # la fase di ricerca lo ritrova, anche se gira in un altro processo
        if args.fase == "comandi":
            return 0
    want_search = bool(db.get_kv("search_requested"))
    if want_search:
        db.set_kv("search_requested", False)
    from pokebot import cardmarket
    try:  # prezzi Cardmarket (TCGdex): ogni carta una volta al giorno, prima le mancanti
        line = cardmarket.refresh(db, index, wanted_first=db.wanted_of_members())
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
        from pokebot import channel
        try:
            for line in plans.check_trials(db, commands.client):  # promemoria e fine della prova
                print(f"piani · {line}")
        except Exception as exc:  # noqa: BLE001 - un errore qui non deve fermare la ricerca
            print(f"piani · errore: {exc}")
        try:
            line = channel.post_daily(db, index, commands.client)  # canale degli affari: una volta al giorno
            if line:
                print(f"canale · {line}")
        except Exception as exc:  # noqa: BLE001
            print(f"canale · errore: {exc}")
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


def _search_due(db: Database) -> bool:
    interval = float(db.get_settings().get("interval_minutes", 20) or 20)
    last = float(db.get_kv("last_search_ts", 0) or 0)
    return time.time() - last >= interval * 60 - 30  # 30 s di tolleranza sui ritardi del cron


if __name__ == "__main__":
    sys.exit(main())
