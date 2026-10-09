"""Piani: prova di 5 giorni con tutto, poi Light gratis per sempre oppure abbonamento in Telegram Stars.

  owner  – il proprietario: tutto, sempre
  pro    – abbonato (fino alla scadenza pagata) o regalato dal proprietario ("sempre")
  trial  – i primi TRIAL_DAYS giorni dopo l'approvazione
  light  – gratis: un riepilogo al giorno, 1 inseguimento con controlli ogni 2 ore, massimo 5 collezioni
           di cui 3 con le notifiche accese

Stato per persona nel kv `plan:<chat>`: {trial_until, pro_until, lifetime, reminded, ended, rating, review, payments}.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import time
from zoneinfo import ZoneInfo

from . import config
from .db import Database, masked

log = logging.getLogger(__name__)

TRIAL_DAYS = 5
PRICE_STARS = 250
SUB_PERIOD_S = 30 * 86400  # Telegram accetta solo abbonamenti di 30 giorni
DIGEST_HOUR = 19
REFUND_DAYS = 3  # rimborso intero solo nei primi giorni: Telegram non fa rimborsi parziali
LIGHT_MAX_WATCHES = 1
LIGHT_WATCH_EVERY_S = 2 * 3600
LIGHT_MAX_COLLECTIONS = 5
LIGHT_MAX_ACTIVE = 3  # di cui con la ricerca (e le notifiche) accesa

FULL = ("owner", "pro", "trial")


def get(db: Database, chat: str) -> dict:
    v = db.get_kv(f"plan:{chat}")
    return dict(v) if isinstance(v, dict) else {}


def save(db: Database, chat: str, p: dict) -> None:
    db.set_kv(f"plan:{chat}", p)




def tier(db: Database, chat: str, now: float | None = None) -> str:
    now = now or time.time()
    chat = str(chat)
    if not chat or chat == "me" or chat == db.owner_chat_id():
        return "owner"
    p = get(db, chat)
    if p.get("lifetime") or float(p.get("pro_until") or 0) > now:
        return "pro"
    if float(p.get("trial_until") or 0) > now:
        return "trial"
    return "light"


def is_full(db: Database, chat: str, now: float | None = None) -> bool:
    return tier(db, chat, now) in FULL


def migrate(db: Database) -> None:
    """Chi era già dentro prima dei piani tiene tutto, gratis e per sempre."""
    if db.get_kv("plans_v1"):
        return
    owner = db.owner_chat_id()
    for chat in db.chat_ids():
        if chat != owner and not get(db, chat):
            save(db, chat, {"lifetime": True, "since": time.time(), "note": "dentro prima dei piani"})
    db.set_kv("plans_v1", True)


def start_trial(db: Database, chat: str, days: int = TRIAL_DAYS, now: float | None = None) -> dict:
    now = now or time.time()
    p = get(db, chat)
    p.update({"trial_until": now + days * 86400, "reminded": False, "ended": False})
    save(db, chat, p)
    return p


def set_pro(db: Database, chat: str, until: float | None = None, lifetime: bool = False) -> dict:
    p = get(db, chat)
    if lifetime:
        p["lifetime"] = True
    else:
        p["lifetime"] = False
        p["pro_until"] = float(until or 0)
    p["ended"] = True  # niente messaggio di fine prova dopo un abbonamento
    p["pro_ended"] = False
    save(db, chat, p)
    return p


def set_light(db: Database, chat: str) -> dict:
    p = get(db, chat)
    p.update({"lifetime": False, "pro_until": 0, "trial_until": 0, "ended": True, "reminded": True})
    save(db, chat, p)
    apply_light_limits(db, chat)
    return p


def record_payment(db: Database, chat: str, payment: dict, now: float | None = None) -> dict | None:
    """Pagamento Stars riuscito (anche i rinnovi mensili): Pro fino alla scadenza dell'abbonamento.
    None se quel pagamento era già registrato (lo stesso comando arrivato due volte)."""
    now = now or time.time()
    p = get(db, chat)
    charge = str(payment.get("telegram_payment_charge_id") or "")[:80]
    if charge and any(x.get("charge") == charge for x in (p.get("payments") or [])):
        return None
    until = float(payment.get("subscription_expiration_date") or 0) or max(now, float(p.get("pro_until") or 0)) + SUB_PERIOD_S
    p["pro_until"] = max(until, float(p.get("pro_until") or 0))
    p["ended"] = True
    p["pro_ended"] = False
    pays = list(p.get("payments") or [])
    pays.append({"ts": now, "stars": int(payment.get("total_amount") or 0),
                 "charge": str(payment.get("telegram_payment_charge_id") or "")[:80],
                 "recurring": bool(payment.get("is_recurring")), "first": bool(payment.get("is_first_recurring"))})
    p["payments"] = pays[-24:]
    save(db, chat, p)
    return p


def describe(db: Database, chat: str, now: float | None = None) -> str:
    now = now or time.time()
    t = tier(db, chat, now)
    p = get(db, chat)
    if t == "owner":
        return "👑 Proprietario: tutto incluso"
    if t == "pro":
        if p.get("lifetime"):
            return "⭐ Pro per sempre"
        return f"⭐ Pro fino al {_date(p.get('pro_until'))}"
    if t == "trial":
        left = float(p.get("trial_until") or 0) - now
        days = max(1, int(left // 86400) + (1 if left % 86400 else 0))
        return f"🎁 Prova gratuita: ancora {days} giorn{'o' if days == 1 else 'i'} con tutto"
    return "🌱 Light gratis: un riepilogo al giorno, 1 inseguimento, 3 collezioni con notifiche"


def _date(ts) -> str:
    try:
        return dt.datetime.fromtimestamp(float(ts), ZoneInfo(config.TIMEZONE)).strftime("%d/%m")
    except (TypeError, ValueError):
        return "?"


def _today(now: float) -> str:
    return dt.datetime.fromtimestamp(now, ZoneInfo(config.TIMEZONE)).date().isoformat()


# ---- riepilogo giornaliero della Light ---------------------------------------------
def digest_due(db: Database, chat: str, now: float | None = None, oldest: float | None = None) -> bool:
    """Dalle 19, una volta al giorno. Se il riepilogo di ieri è saltato (ore silenziose sulle 19), parte al primo
    momento utile: lo si capisce dall'annuncio più vecchio in coda (`oldest`), che sarebbe dovuto uscire ieri sera."""
    now = now or time.time()
    local = dt.datetime.fromtimestamp(now, ZoneInfo(config.TIMEZONE))
    if db.get_kv(f"digest_last:{chat}") == local.date().isoformat():
        return False
    if local.hour >= DIGEST_HOUR:
        return True
    yesterday_digest = (local - dt.timedelta(days=1)).replace(hour=DIGEST_HOUR, minute=0, second=0, microsecond=0)
    return oldest is not None and oldest < yesterday_digest.timestamp()


def mark_digest(db: Database, chat: str, now: float | None = None) -> None:
    db.set_kv(f"digest_last:{chat}", _today(now or time.time()))


# ---- limiti della Light ------------------------------------------------------------
def collections_count(db: Database, chat: str) -> int:
    """La 30th (con la sua Classic Collection) conta come una collezione sola."""
    return len({sid for sid in db.albums_of(chat) if sid not in config.HOME_SET_IDS[1:]})


def _home_key(sid: str) -> str:
    return config.HOME_SET_IDS[0] if sid in config.HOME_SET_IDS else sid


def active_count(db: Database, chat: str) -> int:
    """Collezioni con la ricerca accesa (la 30th con la Classic conta una volta)."""
    return len({_home_key(s) for s in db.active_sets(chat)})


def apply_light_limits(db: Database, chat: str) -> None:
    """Al passaggio a Light: restano accese le prime 3 collezioni e un solo inseguimento (il primo), ogni 2 ore."""
    keep_sets, seen = [], set()
    for sid in db.active_sets(chat):
        k = _home_key(sid)
        if k in seen or len(seen) < LIGHT_MAX_ACTIVE:
            seen.add(k)
            keep_sets.append(sid)
    if keep_sets != db.active_sets(chat):
        db.set_kv(f"active_sets:{chat}", keep_sets)
    w = db.get_kv(f"watches:{chat}", {}) or {}
    if not isinstance(w, dict) or not w:
        return
    now = time.time()
    alive = [kv for kv in w.items() if float(kv[1].get("until") or 0) > now]  # quelli già finiti non contano
    keep = sorted(alive, key=lambda kv: float(kv[1].get("started") or 0))[:LIGHT_MAX_WATCHES]
    for _, v in keep:
        v["every"] = max(float(v.get("every") or 0), LIGHT_WATCH_EVERY_S)
    db.set_kv(f"watches:{chat}", dict(keep))


# ---- primi passi: notifiche spente finché l'album non è pronto ----------------------
def welcome_message() -> tuple[str, list]:
    text = ("🎉 <b>Il tuo Pokébot è attivo!</b>\n"
            f"Hai {TRIAL_DAYS} giorni di prova con tutte le funzioni.\n\n"
            "Pokébot cerca per te su Vinted, Wallapop ed eBay le carte che mancano alla tua collezione "
            "e ti scrive appena ne spunta una, con foto e prezzo.\n\n"
            "<b>Due consigli per partire bene:</b>\n"
            "1️⃣ <b>Prima riempi l'album.</b> Apri l'app dal pulsante <b>App</b>, scegli la collezione e tocca "
            "le carte che hai già: all'inizio risultano tutte mancanti.\n"
            "2️⃣ <b>Poi accendi le notifiche</b> con il pulsante qui sotto. Fino ad allora non ti scrivo, "
            "così non ricevi annunci di carte che hai già.\n\n"
            "Con /aiuto trovi tutti i comandi. Buona caccia! 🃏")
    return text, [[("🔔 Album pronto, attiva le notifiche", "/riprendi")]]


def onboarding(db: Database, chat: str) -> bool:
    return bool(db.user_prefs(chat).get("onboarding"))


def set_onboarding(db: Database, chat: str, on: bool) -> None:
    db.save_user_prefs(chat, {"onboarding": bool(on)})


# ---- messaggi di fine prova ---------------------------------------------------------
def cached_invoice(db: Database, chat: str) -> str:
    """Il link di pagamento già creato per quella persona, se vale ancora (stesso prezzo)."""
    cached = db.get_kv(f"invoice:{chat}")
    return str(cached["link"]) if isinstance(cached, dict) and cached.get("price") == PRICE_STARS and cached.get("link") else ""


def invoice_link(db: Database, client, chat: str) -> str:
    """Link di pagamento dell'abbonamento mensile in Stars (uno per persona, si riusa)."""
    key = f"invoice:{chat}"
    if cached_invoice(db, chat):
        return cached_invoice(db, chat)  # un link con un prezzo vecchio si rifà
    try:
        link = client.create_invoice_link(
            title="Pokébot completo",
            description=("Avvisi immediati sulle carte che ti mancano, affari 🔥, inseguimenti ogni 5 minuti "
                         "e collezioni illimitate. Si rinnova ogni mese, disdici quando vuoi da Telegram."),
            payload=f"pro:{chat}", amount=PRICE_STARS, period=SUB_PERIOD_S)
    except Exception as exc:  # noqa: BLE001 - senza link si ripiega sul comando /abbonati
        log.warning("createInvoiceLink: %s", exc)
        return ""
    if link:
        db.set_kv(key, {"link": link, "price": PRICE_STARS})
    return link or ""


def channel_url(db: Database) -> str:
    ch = str(db.get_kv("deals_channel") or "")
    return f"https://t.me/{ch.lstrip('@')}" if ch.startswith("@") else ""


def end_of_trial_message(db: Database, client, chat: str) -> tuple[str, list]:
    name = html.escape(db.user_name(chat)) if db.user_name(chat) != chat else ""
    text = (f"⏰ <b>{'Ciao ' + name + ', l' if name else 'L'}a tua prova di {TRIAL_DAYS} giorni è finita.</b>\n\n"
            "Pokébot è un progetto appena nato, fatto da un collezionista per i collezionisti: "
            "ogni giorno cerca su Vinted, Wallapop ed eBay le carte che ti mancano, così non devi farlo tu.\n\n"
            "Da adesso sei sulla versione <b>Light</b>, gratis per sempre:\n"
            "• un riepilogo al giorno, alle 19, con gli annunci delle tue carte\n"
            "• 1 inseguimento alla volta, controllato ogni 2 ore\n"
            "• fino a 5 collezioni, di cui 3 con le notifiche accese\n\n"
            f"Con l'abbonamento (<b>{PRICE_STARS} ⭐ al mese</b>) torni ad avere tutto: avvisi nel momento in cui "
            "esce l'annuncio, prima degli altri, affari 🔥, inseguimenti ogni 5 minuti e collezioni illimitate. "
            "Sostieni anche lo sviluppo delle prossime funzioni.\n\n"
            "Ti chiedo un favore: quanto ti è stato utile in questi giorni? Tocca un voto qui sotto 🙏")
    buttons = [[(f"{n}⭐", f"/voto {n}") for n in range(1, 6)]]
    link = invoice_link(db, client, chat)
    buttons.append([(f"⭐ Abbonati · {PRICE_STARS} Stars/mese", link or "/abbonati")])
    ch = channel_url(db)
    if ch:
        buttons.append([("🔥 Canale con gli affari del giorno", ch)])
    return text, buttons


def reminder_message(db: Database, client, chat: str) -> tuple[str, list]:
    text = ("⏳ <b>Domani finisce la tua prova gratuita.</b>\n"
            "Poi passi alla versione Light (un riepilogo al giorno, 1 inseguimento, 3 collezioni con notifiche). "
            f"Se vuoi continuare ad avere gli avvisi subito, l'abbonamento costa {PRICE_STARS} ⭐ al mese.")
    link = invoice_link(db, client, chat)
    return text, [[(f"⭐ Abbonati · {PRICE_STARS} Stars/mese", link or "/abbonati")]]


def check_trials(db: Database, client, now: float | None = None) -> list[str]:
    """A ogni giro: promemoria il giorno prima della fine della prova, messaggio di fine prova, passaggio a Light;
    anche quando un abbonamento scade senza rinnovo."""
    now = now or time.time()
    out = []
    for chat in db.chat_ids():
        p = get(db, chat)
        if not p or p.get("lifetime") or tier(db, chat, now) == "owner":
            continue
        pro_until = float(p.get("pro_until") or 0)
        if pro_until and pro_until <= now and not p.get("pro_ended") and tier(db, chat, now) == "light":
            apply_light_limits(db, chat)
            link = invoice_link(db, client, chat)
            client.send(chat, "⏰ <b>Il tuo abbonamento è scaduto</b>: sei tornato alla versione Light (un riepilogo al giorno, "
                              "1 inseguimento, 3 collezioni con notifiche). Quando vuoi, riattivalo da qui.",
                        [[(f"⭐ Abbonati · {PRICE_STARS} Stars/mese", link or "/abbonati")]])
            p = get(db, chat)
            p["pro_ended"] = True
            save(db, chat, p)
            out.append(f"{masked(chat)}: abbonamento scaduto, passato a Light")
            continue
        trial_until = float(p.get("trial_until") or 0)
        if not trial_until:
            continue
        if tier(db, chat, now) == "trial" and not p.get("reminded") and trial_until - now < 86400:
            text, buttons = reminder_message(db, client, chat)
            client.send(chat, text, buttons)
            p = get(db, chat)
            p["reminded"] = True
            save(db, chat, p)
            out.append(f"{masked(chat)}: promemoria fine prova")
        elif tier(db, chat, now) == "light" and not p.get("ended"):
            apply_light_limits(db, chat)
            text, buttons = end_of_trial_message(db, client, chat)
            client.send(chat, text, buttons)
            p = get(db, chat)
            p["ended"] = True
            save(db, chat, p)
            out.append(f"{masked(chat)}: fine prova, passato a Light")
    return out
