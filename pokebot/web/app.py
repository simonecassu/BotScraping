"""Interfaccia web: checklist carte mancanti, impostazioni, annunci trovati."""
from __future__ import annotations

import logging
import time
from datetime import datetime

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from .. import config
from ..cards import CardIndex, load_sets
from ..db import Database
from ..notifier import TelegramNotifier
from ..scheduler import Scheduler

log = logging.getLogger(__name__)


def create_app(index: CardIndex | None = None, db: Database | None = None,
               scheduler: Scheduler | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = config.WEB_SECRET
    index = index or load_sets()
    db = db or Database()
    app.extensions["pokebot"] = {"index": index, "db": db, "scheduler": scheduler}

    @app.template_filter("ts")
    def _fmt_ts(value):
        if not value:
            return "-"
        return datetime.fromtimestamp(float(value)).strftime("%d/%m/%Y %H:%M")

    # ---- pagine --------------------------------------------------------
    @app.get("/")
    def checklist():
        wanted = db.wanted_ids()
        sets = []
        for s in index.sets:
            cards = sorted(s.cards, key=lambda c: c.sort_key)
            sets.append({
                "id": s.id, "name": s.name, "name_it": s.name_it, "total": len(cards),
                "missing": sum(1 for c in cards if c.id in wanted),
                "rarities": sorted({c.rarity for c in cards}),
                "cards": cards,
            })
        total = len(index.by_id)
        return render_template("checklist.html", sets=sets, wanted=wanted, total=total,
                               missing=len(wanted), status=_status(db, scheduler))

    @app.get("/trovati")
    def found():
        return render_template("found.html", found=db.list_found(300), runs=db.last_runs(10),
                               status=_status(db, scheduler), total=len(index.by_id), missing=len(db.wanted_ids()))

    @app.route("/impostazioni", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            f = request.form
            values = {
                "interval_minutes": max(1, int(f.get("interval_minutes", 15) or 15)),
                "lot_min_ratio": min(1.0, max(0.0, float(f.get("lot_min_percent", 50) or 50) / 100)),
                "max_price": max(0.0, float((f.get("max_price") or "0").replace(",", "."))),
                "sources": f.getlist("sources"),
                "set_keywords": _lines(f.get("set_keywords", "")),
                "generic_queries": _lines(f.get("generic_queries", "")),
                "per_card_queries": f.get("per_card_queries") == "on",
                "per_card_batch": max(0, int(f.get("per_card_batch", 20) or 0)),
                "notify_unverifiable_lots": f.get("notify_unverifiable_lots") == "on",
                "only_italy": f.get("only_italy") == "on",
            }
            db.save_settings(values)
            flash("Impostazioni salvate.", "ok")
            return redirect(url_for("settings"))
        notifier = TelegramNotifier()
        return render_template("settings.html", s=db.get_settings(), telegram_ok=notifier.configured,
                               ebay_api=bool(config.EBAY_CLIENT_ID and config.EBAY_CLIENT_SECRET),
                               status=_status(db, scheduler), total=len(index.by_id), missing=len(db.wanted_ids()))

    # ---- azioni ----------------------------------------------------------
    @app.post("/api/wanted")
    def api_wanted():
        data = request.get_json(silent=True) or {}
        ids = data.get("ids") or ([data["id"]] if data.get("id") else [])
        wanted = bool(data.get("wanted", True))
        ids = [i for i in ids if i in index.by_id]
        db.set_wanted_bulk(ids, wanted)
        return jsonify({"ok": True, "missing": len(db.wanted_ids()), "total": len(index.by_id)})

    @app.post("/azioni/cerca-ora")
    def run_now():
        if scheduler:
            scheduler.trigger()
            flash("Ricerca avviata: i risultati compariranno tra poco in «Trovati».", "ok")
        else:
            flash("Scheduler non attivo (avvia con run.py).", "err")
        return redirect(request.referrer or url_for("found"))

    @app.post("/azioni/test-telegram")
    def test_telegram():
        ok = TelegramNotifier().test_message()
        flash("Messaggio di prova inviato su Telegram." if ok else
              "Invio fallito: controlla TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nel file .env.", "ok" if ok else "err")
        return redirect(url_for("settings"))

    @app.post("/azioni/svuota-trovati")
    def clear_found():
        db.clear_found()
        flash("Registro svuotato.", "ok")
        return redirect(url_for("found"))

    @app.post("/azioni/dimentica-visti")
    def forget_seen():
        db.forget_seen()
        flash("Memoria degli annunci già visti azzerata: al prossimo ciclo verranno rinotificati.", "ok")
        return redirect(url_for("found"))

    @app.get("/api/status")
    def api_status():
        return jsonify(_status(db, scheduler))

    return app


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.replace(",", "\n").splitlines() if ln.strip()]


def _status(db: Database, scheduler: Scheduler | None) -> dict:
    runs = db.last_runs(1)
    last = runs[0] if runs else None
    return {
        "scheduler": scheduler is not None,
        "running": bool(scheduler and scheduler.running),
        "next_run_at": scheduler.next_run_at if scheduler else None,
        "next_in_min": (max(0, round((scheduler.next_run_at - time.time()) / 60)) if scheduler and scheduler.next_run_at else None),
        "last_run": last,
        "telegram": TelegramNotifier().configured,
        "now": time.time(),
    }
