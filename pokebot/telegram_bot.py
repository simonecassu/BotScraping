"""Comandi Telegram: ogni persona collegata gestisce dalla chat il suo Pokébot (album, notifiche, inseguimenti).

Il primo /start in assoluto rende quella chat il proprietario; gli altri entrano dalla lista d'attesa (/approva)
o con un codice d'invito. Ogni messaggio può contenere più comandi, uno per riga.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import requests

from . import cardmarket, config, plans, watch
from . import collections as coll
from . import stats as pstats
from .cards import Card, CardIndex, normalize
from .db import Database, masked

log = logging.getLogger(__name__)

MAX_LEN = 3800  # limite Telegram 4096

RARITY_SHORTCUTS = {
    "ir": "Illustration Rare",
    "sir": "Special Illustration Rare",
    "pr": "Pikachu Rare",
    "fr": "Futuristic Rare",
    "dr": "Double Rare",
    "comuni": "Common",
    "common": "Common",
    "rare": "Rare",
}

HELP = """<b>Comandi</b>
/mancanti – le carte che ti mancano · /lista – tutte le carte della collezione con i numeri
/aggiungi 131 132 149-152 c4 – segna come mancanti (anche ir, sir, pr, un nome o tutte) · /ho 131 – l'hai trovata
/collezione – le tue collezioni · /collezione sv8 – passa a quella (o la segue, partendo da "mi mancano tutte")
/collezione sv8 attiva | disattiva – accende o spegne la ricerca e le notifiche di quella collezione · sv8:7 – una sua carta in qualsiasi comando
/cerca 145 – cosa c'è in vendita adesso per quella carta (fino a 3 insieme), dal più economico: una ricerca ogni 5 minuti (Light: ogni 15)
/insegui 151 – per 6 ore la cerca ogni 5 minuti (Light: ogni 2 ore) e ti avvisa appena spunta un annuncio nuovo · /insegui 151 2h ogni 10m · /insegui stop
/storico – gli annunci trovati, divisi per carta e marketplace
/prezzi 145 – valore Cardmarket e annunci visti · /prezzi – quanto costa finire la collezione
/valore – quanto vale ciò che hai · /progresso – avanzamento per rarità
/spesa – il paniere più economico per le mancanti, raggruppato per venditore
/affari 70 – avviso 🔥 se un prezzo è sotto il 70% del valore Cardmarket (e sotto il minimo) · /affari off
/doppioni 131 132 – le tue copie in più · /scambio – messaggio "cerco / offro" pronto da inviare
/amico – il tuo codice amico · /amico CODICE – diventate amici · /amici – elenco
/condividi sv8 NOME – album in comune con un amico · /accetta CODICE · /esci sv8 – torni ad avere un album tuo
/pausa · /riprendi – sospendi o riattiva le notifiche · /notte 23 8 – ore silenziose
/prezzo 100 – prezzo massimo · /lingua ita | tutte · /soglia 50 – % minima di carte mancanti nei lotti
/max 5 – annunci per carta in ogni messaggio · /immagini on | off · /esporta – file Excel
/abbonati – il tuo piano e l'abbonamento · /voto 1-5 · /recensione testo
Puoi scrivere più comandi in un solo messaggio, uno per riga."""

OWNER_HELP = """
<b>Solo per te (proprietario)</b>
/attesa – chi aspetta l'accesso · /approva ID (o tutti) · /rifiuta ID · /invita – codice d'invito
/utenti – le persone collegate e il loro piano · /espelli ID · /piano ID sempre | pro 30 | prova 5 | light
/recensioni · /rimborsa ID (entro {refund_days} giorni; annulla = ferma solo il rinnovo)
/canale @nome – canale degli affari · /canale ora · /canale 20 · /canale off
/fonti wallapop vinted ebay · /intervallo 20 – valgono per tutti
/cerca – ricerca completa adesso · /storico svuota · /resetvisti – rinotifica gli annunci già visti"""


# ricerche mirate dal vivo (/cerca 145) per chi non è il proprietario: completo (abbonati e prova) e Light
CERCA_EVERY_FULL_S = 5 * 60
CERCA_EVERY_LIGHT_S = 15 * 60

# Presentazione del bot: la descrizione compare nella chat vuota prima di "Avvia", la breve nel profilo e nei link
BOT_DESCRIPTION = ("🃏 Pokébot trova le carte Pokémon che mancano alla tua collezione.\n\n"
                   "✅ Segni nell'app le carte che hai: le altre le cerco io su Vinted, Wallapop ed eBay\n"
                   "🔔 Ti avviso appena spunta un annuncio, con prezzo e foto\n"
                   "🔥 Affari sotto il valore Cardmarket, lista della spesa, doppioni da scambiare\n"
                   "👥 Album condivisi con gli amici\n\n"
                   "Premi Avvia per chiedere l'accesso: {trial} giorni di prova con tutto, poi gratis in versione Light.")
BOT_SHORT_DESCRIPTION = "Cerca su Vinted, Wallapop ed eBay le carte Pokémon che ti mancano e ti avvisa appena spuntano."
PROFILE_VERSION = 3

# Menu comandi mostrato da Telegram toccando "/" (registrato automaticamente dal bot); il proprietario ne ha uno suo
MENU_VERSION = 19
MENU_COMMANDS = [
    ("mancanti", "Carte che ti mancano"),
    ("aggiungi", "Segna mancanti: /aggiungi 131 132 149-152 c4 (anche ir, sir, tutte)"),
    ("ho", "L'hai trovata: /ho 131"),
    ("collezione", "Le tue collezioni; /collezione sv8 passa a quella"),
    ("lista", "Tutte le carte della collezione con i numeri"),
    ("cerca", "Cosa c'e' in vendita adesso per una carta: /cerca 145"),
    ("insegui", "Cerca una carta per 6 ore e avvisa sui nuovi annunci: /insegui 151"),
    ("storico", "Annunci trovati, divisi per carta e marketplace"),
    ("prezzi", "Valore Cardmarket di una carta (/prezzi 145) o costo per finire (/prezzi)"),
    ("valore", "Valore delle carte che hai e costo per finire"),
    ("progresso", "Avanzamento della collezione per rarita'"),
    ("spesa", "Lista della spesa: paniere piu' economico, per venditore"),
    ("affari", "Avviso se un prezzo e' sotto il valore Cardmarket: /affari 70, /affari off"),
    ("doppioni", "Copie in piu' da scambiare: /doppioni 131 132"),
    ("scambio", "Messaggio cerco/offro pronto da inviare"),
    ("amico", "Il tuo codice amico, o /amico CODICE per aggiungerne uno"),
    ("condividi", "Album in comune con un amico: /condividi me55 NOME"),
    ("pausa", "Sospende le notifiche"),
    ("riprendi", "Riattiva le notifiche"),
    ("notte", "Ore silenziose: /notte 23 8, /notte off"),
    ("prezzo", "Prezzo massimo in euro: /prezzo 100 (0 = nessun limite)"),
    ("lingua", "Solo carte italiane (/lingua ita) oppure tutte (/lingua tutte)"),
    ("esporta", "File Excel con checklist, annunci e prezzi"),
    ("abbonati", "Il tuo piano e l'abbonamento"),
    ("aiuto", "Elenco dei comandi"),
]
OWNER_MENU_COMMANDS = MENU_COMMANDS[:-1] + [
    ("attesa", "Chi aspetta l'accesso"),
    ("utenti", "Persone collegate e piani"),
    ("canale", "Canale degli affari"),
    ("fonti", "Marketplace da usare (per tutti)"),
    ("intervallo", "Ogni quanti minuti cercare (per tutti)"),
    ("aiuto", "Elenco dei comandi"),
]


@dataclass
class Reply:
    text: str
    run_search: bool = False
    buttons: list[list[tuple[str, str]]] | None = None  # righe di pulsanti (etichetta, comando)
    document: tuple[str, bytes] | None = None  # (nome file, contenuto) da inviare come allegato
    sends: list | None = None  # [(chat_id, testo, pulsanti)] da recapitare ad altre persone o al canale


@dataclass
class TelegramClient:
    token: str = field(default_factory=lambda: config.TELEGRAM_BOT_TOKEN)

    @property
    def base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def get_me(self) -> str:
        """Username del bot a cui appartiene il token (per i log)."""
        resp = requests.get(f"{self.base}/getMe", timeout=config.HTTP_TIMEOUT)
        data = resp.json()
        if not data.get("ok"):
            raise requests.RequestException(f"getMe: {data.get('description', resp.status_code)}")
        return data["result"].get("username", "?")

    def webhook_info(self) -> dict:
        resp = requests.get(f"{self.base}/getWebhookInfo", timeout=config.HTTP_TIMEOUT)
        data = resp.json()
        return data.get("result", {}) if data.get("ok") else {}

    def get_updates(self, offset: int | None, timeout: int = 0) -> list[dict]:
        params = {"timeout": timeout, "allowed_updates": json.dumps(["message", "callback_query"])}
        if offset is not None:
            params["offset"] = offset
        resp = requests.get(f"{self.base}/getUpdates", params=params, timeout=config.HTTP_TIMEOUT + timeout)
        data = resp.json()
        if not data.get("ok"):
            raise requests.RequestException(f"getUpdates: {data.get('description', resp.status_code)}")
        return data.get("result", [])

    def set_my_commands(self, commands: list[tuple[str, str]], chat_id: str = "") -> bool:
        """Menu dei comandi: per tutti, oppure solo per una chat (il proprietario)."""
        body: dict = {"commands": [{"command": c, "description": d[:256]} for c, d in commands]}
        if chat_id:
            body["scope"] = {"type": "chat", "chat_id": int(chat_id)}
        resp = requests.post(f"{self.base}/setMyCommands", json=body, timeout=config.HTTP_TIMEOUT)
        return resp.status_code == 200 and bool(resp.json().get("ok"))

    def cancel_subscription(self, user_id: str, charge_id: str) -> tuple[bool, str]:
        """Ferma il rinnovo di un abbonamento in Stars (resta valido fino alla scadenza già pagata)."""
        resp = requests.post(f"{self.base}/editUserStarSubscription", json={"user_id": int(user_id), "telegram_payment_charge_id": charge_id,
                                                                             "is_canceled": True}, timeout=config.HTTP_TIMEOUT)
        data = resp.json()
        return bool(data.get("ok")), str(data.get("description", ""))

    def refund_stars(self, user_id: str, charge_id: str) -> tuple[bool, str]:
        """Restituisce per intero le Stars di un pagamento (Telegram non fa rimborsi parziali) e ferma il rinnovo."""
        resp = requests.post(f"{self.base}/refundStarPayment", json={"user_id": int(user_id), "telegram_payment_charge_id": charge_id},
                             timeout=config.HTTP_TIMEOUT)
        data = resp.json()
        try:
            self.cancel_subscription(user_id, charge_id)
        except requests.RequestException:
            pass
        return bool(data.get("ok")), str(data.get("description", ""))

    def create_invoice_link(self, title: str, description: str, payload: str, amount: int, period: int = 0) -> str:
        """Link di pagamento in Telegram Stars (XTR); con `period` è un abbonamento che si rinnova da solo."""
        body = {"title": title, "description": description[:255], "payload": payload, "currency": "XTR",
                "prices": [{"label": title, "amount": int(amount)}]}
        if period:
            body["subscription_period"] = int(period)
        resp = requests.post(f"{self.base}/createInvoiceLink", json=body, timeout=config.HTTP_TIMEOUT)
        data = resp.json()
        if not data.get("ok"):
            raise requests.RequestException(f"createInvoiceLink: {data.get('description', resp.status_code)}")
        return str(data["result"])

    def set_profile(self, description: str, short_description: str) -> bool:
        """Testi che vede chi apre il bot prima di premere Avvia (descrizione) e nel profilo (about)."""
        ok = True
        for method, key, text in (("setMyDescription", "description", description[:512]),
                                  ("setMyShortDescription", "short_description", short_description[:120])):
            resp = requests.post(f"{self.base}/{method}", json={key: text}, timeout=config.HTTP_TIMEOUT)
            ok = ok and resp.status_code == 200 and bool(resp.json().get("ok"))
        return ok

    def send(self, chat_id: str | int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> bool:
        """Manda il testo (a pezzi se lungo); False se Telegram ne rifiuta uno (es. il bot non è più nel canale)."""
        chunks = _chunks(text)
        ok = True
        for i, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
            if buttons and i == len(chunks) - 1:
                payload["reply_markup"] = {"inline_keyboard": [[_button(lbl, data) for lbl, data in row] for row in buttons]}
            ok = requests.post(f"{self.base}/sendMessage", json=payload, timeout=config.HTTP_TIMEOUT).ok and ok
        return ok

    def send_document(self, chat_id: str | int, filename: str, content: bytes, caption: str = "") -> None:
        requests.post(f"{self.base}/sendDocument", data={"chat_id": chat_id, "caption": caption[:1024]},
                      files={"document": (filename, content)}, timeout=config.HTTP_TIMEOUT * 2)

    def answer_callback(self, callback_id: str) -> None:
        try:
            requests.post(f"{self.base}/answerCallbackQuery", json={"callback_query_id": callback_id}, timeout=config.HTTP_TIMEOUT)
        except requests.RequestException:
            pass


def _button(label: str, data: str) -> dict:
    """Pulsante inline: un link se il dato è un URL, altrimenti un comando (callback)."""
    if data.startswith("https://") or data.startswith("http://"):
        return {"text": label, "url": data}
    return {"text": label, "callback_data": data[:64]}


def _chunks(text: str) -> list[str]:
    """Spezza i messaggi lunghi tra una riga e l'altra (una riga troppo lunga si taglia a pezzi)."""
    if len(text) <= MAX_LEN:
        return [text]
    lines = []
    for line in text.split("\n"):
        while len(line) > MAX_LEN:
            lines.append(line[:MAX_LEN])
            line = line[MAX_LEN:]
        lines.append(line)
    out, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) + 1 > MAX_LEN:
            out.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    if cur:
        out.append(cur)
    return out


class CommandHandler:
    """Interpreta i comandi (indipendente dalla rete, così è testabile)."""

    def __init__(self, index: CardIndex, db: Database):
        self.index = index
        self.db = db
        self.chat = ""  # persona che sta parlando (impostata a ogni handle)

    # ---- la persona ---------------------------------------------------------
    def _settings(self) -> dict:
        return self.db.settings_for(self.chat)

    def _save(self, values: dict) -> None:
        """Le impostazioni personali vanno nelle preferenze della persona, le altre sono condivise."""
        personal = {k: v for k, v in values.items() if k in config.PERSONAL_SETTINGS}
        shared = {k: v for k, v in values.items() if k not in config.PERSONAL_SETTINGS}
        if personal:
            self.db.save_user_prefs(self.chat, personal)
        if shared:
            self.db.save_settings(shared)

    def _album(self, set_id: str) -> str:
        return coll.album_for(self.db, self.chat, set_id)

    def _wanted(self) -> set[str]:
        for s_ in self.scope():
            self._album(s_.id)  # gli album di casa nascono al primo uso
        return self.db.wanted_for(self.chat)

    def _set_wanted(self, cards: list, wanted: bool) -> list[str]:
        """Segna le carte nei propri album; restituisce le collezioni saltate per il limite della Light."""
        by_set: dict[str, list[str]] = {}
        for c in cards:
            by_set.setdefault(c.set_id, []).append(c.id)
        skipped = []
        for sid, ids in by_set.items():
            if self._follow_limit(sid):
                skipped.append(sid)
                continue
            self.db.set_wanted_bulk(ids, wanted, self._album(sid))
        return skipped

    def _my_sets(self) -> list:
        """Collezioni della persona: quelle di casa più i suoi album."""
        albums = self.db.albums_of(self.chat)
        return [s_ for s_ in self.index.sets if s_.primary or s_.id in albums]

    @staticmethod
    def _set_alias(set_id: str) -> str:
        set_id = set_id.lower().strip()
        return config.HOME_SET_IDS[0] if set_id in ("30th", "30", "celebration") else set_id

    def _follow_limit(self, set_id: str) -> Reply | None:
        """Light: al massimo LIGHT_MAX_COLLECTIONS collezioni. None se si può seguire (o la segue già)."""
        if (set_id in config.HOME_SET_IDS or set_id in self.db.albums_of(self.chat) or plans.is_full(self.db, self.chat)
                or plans.collections_count(self.db, self.chat) < plans.LIGHT_MAX_COLLECTIONS):
            return None
        return Reply(f"🌱 Con la versione Light segui fino a {plans.LIGHT_MAX_COLLECTIONS} collezioni. "
                     "/abbonati per seguirne quante vuoi.")

    # ------------------------------------------------------------------
    def handle(self, text: str, chat_id: str = "") -> Reply:
        """Un messaggio può contenere più comandi, uno per riga."""
        self.chat = str(chat_id or "") or self.db.owner_chat_id() or "me"
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if len(lines) <= 1:
            return self._safe_one(lines[0] if lines else "", chat_id)
        replies = [self._safe_one(ln, chat_id) for ln in lines]
        return Reply("\n\n".join(r.text for r in replies), run_search=any(r.run_search for r in replies),
                     buttons=next((r.buttons for r in reversed(replies) if r.buttons), None),
                     document=next((r.document for r in reversed(replies) if r.document), None),
                     sends=[x for r in replies for x in (r.sends or [])] or None)

    def _safe_one(self, text: str, chat_id: str = "") -> Reply:
        """Un comando che va in errore risponde con un messaggio, senza fermare gli altri né il giro del bot."""
        try:
            return self._handle_one(text, chat_id)
        except Exception:  # noqa: BLE001
            log.exception("Comando fallito: %s", text.split(" ", 1)[0][:30])
            return Reply("⚠️ Non sono riuscito a eseguire " + html.escape(text.split(" ", 1)[0][:30]) + ": riprova o scrivi /aiuto.")

    def _help(self, chat_id: str = "") -> str:
        return HELP + (OWNER_HELP.format(refund_days=plans.REFUND_DAYS) if self._is_owner(chat_id or self.chat) else "")

    def _owner_only(self, chat_id: str = "") -> Reply | None:
        """Comandi che cambiano qualcosa per tutti: solo il proprietario."""
        if self._is_owner(chat_id or self.chat):
            return None
        return Reply("Questo comando vale per tutti gli utenti: lo può usare solo il proprietario del bot.")

    def _handle_one(self, text: str, chat_id: str = "") -> Reply:
        text = (text or "").strip()
        if not text.startswith("/"):
            return Reply("Scrivimi un comando, ad esempio /mancanti. Con /aiuto vedi l'elenco.")
        cmd, _, args = text.partition(" ")
        cmd = cmd.split("@", 1)[0].lower()
        args = args.strip()
        if cmd == "/start":
            if plans.onboarding(self.db, self.chat):
                text, buttons = plans.welcome_message()
                return Reply(text, buttons=buttons)
            return Reply("✅ Collegato! Da adesso ti mando qui gli annunci delle carte mancanti.\n\n" + self._help(chat_id))
        if cmd in ("/aiuto", "/help"):
            return Reply(self._help(chat_id))
        if cmd == "/mancanti":
            return Reply(self._fmt_missing())
        if cmd in ("/aggiungi", "/manca", "/mancano"):
            return self._change(args, True)
        if cmd in ("/rimuovi", "/ho", "/trovata", "/togli"):
            return self._change(args, False)
        if cmd == "/lista":
            return Reply(self._fmt_list(args))
        if cmd == "/stato":
            return Reply(self._fmt_status())
        if cmd in ("/storico", "/cartelle", "/trovati"):
            return self._history(args, chat_id)
        if cmd in ("/prezzi", "/prezzo_carta"):
            return self._prices(args)
        if cmd in ("/progresso", "/avanzamento"):
            return self._progress()
        if cmd in ("/affari", "/affare"):
            return self._deals(args)
        if cmd in ("/pausa", "/stop"):
            self._save({"paused": True})
            return Reply("⏸ Notifiche in pausa. Continuo a cercare e accumulo: con /riprendi ti mando tutto in un colpo.")
        if cmd in ("/riprendi", "/play", "/riparti", "/notifiche"):
            first = plans.onboarding(self.db, self.chat)
            plans.set_onboarding(self.db, self.chat, False)
            self._save({"paused": False})
            if first:
                n = len(self._wanted())
                return Reply(f"🔔 <b>Notifiche accese!</b> Cerco le tue {n} carte mancanti e ti scrivo appena ne spunta una. "
                             "Al primo giro potrebbero arrivare diversi annunci insieme, poi solo le novità.", run_search=True)
            return Reply("▶️ Notifiche riattivate. Se c'è qualcosa in coda, arriva tra pochi secondi.")
        if cmd in ("/notte", "/silenzio"):
            return self._quiet(args)
        if cmd in ("/valore", "/value"):
            return self._value()
        if cmd in ("/spesa", "/lista_spesa", "/paniere"):
            return self._shopping()
        if cmd in ("/doppioni", "/doppione", "/copie"):
            return self._copies(args)
        if cmd in ("/scambio", "/scambi"):
            return self._swap()
        if cmd in ("/esporta", "/export", "/excel"):
            return self._export()
        if cmd in ("/immagini", "/foto"):
            a = args.strip().lower()
            if a in ("on", "si", "sì", "attiva"):
                self._save({"images": True})
                return Reply("🖼 Immagine della carta attiva nei messaggi.")
            if a in ("off", "no", "disattiva"):
                self._save({"images": False})
                return Reply("🖼 Immagini disattivate: solo testo.")
            return Reply("Usa <code>/immagini on</code> oppure <code>/immagini off</code>.")
        if cmd == "/soglia":
            return self._set_number(args, "lot_min_ratio", lambda v: min(1.0, max(0.0, v / 100)),
                                    lambda v: f"Soglia lotti: {v * 100:.0f}% di carte mancanti.")
        if cmd == "/prezzo":
            return self._set_number(args, "max_price", lambda v: max(0.0, v),
                                    lambda v: f"Prezzo massimo: {'nessun limite' if not v else f'{v:g} €'}.")
        if cmd in ("/max", "/massimo"):
            return self._set_number(args, "max_per_card", lambda v: max(1, min(20, int(v))),
                                    lambda v: f"Per ogni carta ricevi al massimo {v} annunci (i più economici) per giro.")
        if cmd == "/intervallo":
            return self._owner_only(chat_id) or self._set_number(args, "interval_minutes", lambda v: max(5, min(1440, int(v))),
                                                                 lambda v: f"Ricerca ogni {v} minuti.")
        if cmd == "/fonti":
            return self._owner_only(chat_id) or self._set_sources(args)
        if cmd == "/lingua":
            a = args.strip().lower()
            if a in ("ita", "it", "italiano", "italiana"):
                self._save({"language": "ita"})
                return Reply("⚙️ Solo annunci italiani: scarto quelli dichiaratamente in francese, inglese, tedesco, spagnolo o giapponese.")
            if a in ("tutte", "tutto", "any", "all"):
                self._save({"language": "tutte"})
                return Reply("⚙️ Accetto annunci in qualsiasi lingua.")
            return Reply("Usa <code>/lingua ita</code> oppure <code>/lingua tutte</code>.")
        if cmd == "/cerca":
            owner = self._is_owner(chat_id or self.chat)
            if not args.strip():
                if owner:
                    return Reply("🔎 Ok, cerco adesso.", run_search=True)
                return Reply("🔎 La ricerca completa gira da sola per tutti, più volte all'ora. "
                             "Per vedere subito cosa c'è in vendita per una carta: <code>/cerca 145</code>.")
            if not owner:  # le ricerche mirate interrogano i marketplace dal vivo: con un intervallo minimo
                every = CERCA_EVERY_FULL_S if plans.is_full(self.db, self.chat) else CERCA_EVERY_LIGHT_S
                last = float(self.db.get_kv(f"last_cerca:{self.chat}", 0) or 0)
                if time.time() - last < every:
                    wait = int(every - (time.time() - last)) // 60 + 1
                    return Reply(f"🔎 Hai appena fatto una ricerca: la prossima tra {wait} min.")
                self.db.set_kv(f"last_cerca:{self.chat}", time.time())
            return self._search_cards(args)
        if cmd == "/insegui":
            return self._chase(args)
        if cmd in ("/amico", "/amica"):
            return self._friends(args)
        if cmd in ("/amici", "/amiche"):
            return self._friends_list()
        if cmd in ("/condividi", "/share"):
            return self._share(args)
        if cmd in ("/accetta", "/accetto"):
            return self._accept(args)
        if cmd in ("/esci", "/lascia"):
            return self._leave(args)
        if cmd == "/invita":
            return self._invite(chat_id)
        if cmd == "/utenti":
            return self._users(chat_id)
        if cmd in ("/espelli", "/rimuoviutente"):
            return self._kick(chat_id, args)
        if cmd in ("/attesa", "/lista_attesa"):
            return self._waitlist(chat_id)
        if cmd in ("/approva", "/accetta_utente"):
            return self._approve(chat_id, args)
        if cmd == "/rifiuta":
            return self._reject(chat_id, args)
        if cmd == "/canale":
            return self._channel(chat_id, args)
        if cmd in ("/abbonati", "/abbonamento", "/premium", "/pro"):
            return self._subscribe()
        if cmd == "/piano":
            return self._plan(chat_id, args)
        if cmd == "/voto":
            return self._vote(args)
        if cmd in ("/recensione", "/commento"):
            return self._review(args)
        if cmd == "/recensioni":
            return self._reviews(chat_id)
        if cmd in ("/rimborsa", "/rimborso"):
            return self._refund(chat_id, args)
        if cmd in ("/collezione", "/collezioni", "/set"):
            return self._collection(args)
        if cmd == "/resetvisti":
            if self._owner_only(chat_id):
                return self._owner_only(chat_id)
            if args.strip().lower() != "conferma":
                return Reply("⚠️ Questo rinotifica <b>tutti</b> gli annunci già visti (possono essere centinaia).\n"
                             "Se sei sicuro scrivi <code>/resetvisti conferma</code>.")
            self.db.forget_seen()
            return Reply("♻️ Memoria azzerata: al prossimo ciclo rinotifico anche gli annunci già visti "
                         "(al massimo 10 carte per giro, il resto finisce nello storico).")
        return Reply("Comando sconosciuto. /aiuto per l'elenco.")

    # ------------------------------------------------------------------
    # ---- collezione corrente -------------------------------------------------
    def current(self):
        """La collezione su cui lavorano i comandi (cambia con /collezione <id>)."""
        cs = self.index.get_set(self.db.current_set(self.chat))
        return cs or self.index.sets[0]

    def scope(self) -> list:
        """Collezione corrente più le sue "sorelle" (30th → anche la Classic Collection)."""
        cur = self.current()
        if cur.primary:
            return [s for s in self.index.sets if s.primary and (s.id == cur.id or s.id.startswith(cur.id))]
        return [cur]

    def scope_index(self) -> CardIndex:
        return CardIndex(self.scope(), self.index.aliases)

    def _by_number(self, n: str) -> Card | None:
        cur = self.current()
        if cur.primary:
            return self.index.by_code.get(n)
        return self.index.by_set_number.get((cur.id, n))

    def _one_card(self, arg: str) -> Card | None:
        """Una carta: numero della collezione corrente, codice della 30th, id o set:numero."""
        arg = arg.strip().lower()
        if not arg:
            return None
        if ":" in arg:
            return self._card_of_other_set(arg)
        return self._by_number(arg) or self.index.by_code.get(arg) or self.index.by_id.get(arg)

    def resolve(self, args: str) -> tuple[list[Card], list[str]]:
        """Trasforma '131 132 149-152 c4 ir pikachu sv8:7' in carte della collezione corrente (o set:numero di altre).
        Restituisce (carte, token non capiti)."""
        cards: dict[str, Card] = {}
        unknown: list[str] = []
        scope = self.scope()
        scope_cards = [c for s in scope for c in s.cards]
        main_sets = [s for s in scope if s.printed_total]
        tokens = [t for t in re.split(r"[\s,;]+", args.strip().lower()) if t]
        i = 0
        while i < len(tokens):
            t = tokens[i]
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            if t in ("tutte", "tutto", "all"):
                if nxt in ("classic", "classica"):
                    i += 1
                    for s_ in scope:
                        if not s_.printed_total:
                            cards.update({c.id: c for c in s_.cards})
                else:
                    for s_ in main_sets or scope:
                        cards.update({c.id: c for c in s_.cards})
            elif t in ("classic", "classica"):
                for s_ in scope:
                    if not s_.printed_total:
                        cards.update({c.id: c for c in s_.cards})
            elif t in RARITY_SHORTCUTS:
                rar = RARITY_SHORTCUTS[t]
                cards.update({c.id: c for c in scope_cards if c.rarity == rar})
            elif re.fullmatch(r"\d{1,3}-\d{1,3}", t):
                a, b = (int(x) for x in t.split("-"))
                for n in range(min(a, b), max(a, b) + 1):
                    c = self._by_number(str(n))
                    if c:
                        cards[c.id] = c
            elif (c := self._by_number(t)):
                cards[c.id] = c
            elif t in self.index.by_code:
                c = self.index.by_code[t]
                cards[c.id] = c
            elif ":" in t:
                c = self._card_of_other_set(t)
                if c:
                    cards[c.id] = c
                else:
                    unknown.append(t)
            else:
                # nome di carta (anche più parole: "pikachu ex"), prima nella collezione corrente poi nelle altre
                phrase = t
                if nxt and f"{t} {nxt}" in self.index.names:
                    phrase = f"{t} {nxt}"
                    i += 1
                hits = [c for c in scope_cards if normalize(c.name) == phrase]
                if not hits:
                    hits = [c for c in scope_cards if phrase in normalize(c.name)]
                if not hits:  # nelle altre collezioni della persona (mai in quelle che non segue)
                    hits = [c for s_ in self._my_sets() for c in s_.cards if normalize(c.name) == phrase]
                if hits:
                    cards.update({c.id: c for c in hits})
                else:
                    unknown.append(t)
            i += 1
        return sorted(cards.values(), key=lambda c: c.sort_key), unknown

    def _card_of_other_set(self, token: str) -> Card | None:
        """'sv8:7': carta di una collezione anche non ancora seguita (la scarica e la aggiunge all'indice)."""
        sid, _, num = token.partition(":")
        try:
            cs = coll.get_set(self.db, sid.lower())
        except Exception:  # noqa: BLE001
            return None
        if not cs:
            return None
        self.index.add_set(cs)
        return self.index.by_set_number.get((cs.id, num.lstrip("0") or num))

    def _change(self, args: str, wanted: bool) -> Reply:
        if not args:
            return Reply("Indica cosa: es. <code>/aggiungi 131 132 149-152 c4</code>, <code>/aggiungi ir</code>, <code>/aggiungi pikachu ex</code>.")
        cards, unknown = self.resolve(args)
        if not cards:
            return Reply("Non ho riconosciuto nessuna carta in: " + html.escape(args) + "\n/lista per vedere i numeri.")
        skipped = self._set_wanted(cards, wanted)
        if skipped:
            cards = [c for c in cards if c.set_id not in skipped]
            if not cards:
                return self._follow_limit(skipped[0])
        verb = "segnate come mancanti" if wanted else "tolte dalle mancanti"
        lines = [f"{'➕' if wanted else '✅'} {len(cards)} carte {verb}:"]
        lines += [self._card_line(c) for c in cards[:40]]
        if len(cards) > 40:
            lines.append(f"… e altre {len(cards) - 40}")
        if unknown:
            lines.append("❓ Non capiti: " + html.escape(" ".join(unknown)))
        lines.append(self._totals_line())
        return Reply("\n".join(lines))

    def _totals_line(self) -> str:
        wanted = self._wanted()
        scope = self.scope()
        n = sum(1 for s_ in scope for c in s_.cards if c.id in wanted)
        tot = sum(len(s_.cards) for s_ in scope)
        return f"{html.escape(self.current().name)}: mancanti <b>{n}</b>/{tot}"

    def _set_number(self, args: str, key: str, conv, fmt) -> Reply:
        try:
            raw = float(args.replace(",", ".").replace("%", "").replace("€", "").strip())
            if not math.isfinite(raw):
                raise ValueError(raw)
            v = conv(raw)
        except (ValueError, OverflowError):
            return Reply("Serve un numero, es. <code>/soglia 50</code> oppure <code>/prezzo 100</code>.")
        self._save({key: v})
        return Reply("⚙️ " + fmt(v))

    def _set_sources(self, args: str) -> Reply:
        from .scrapers import SCRAPERS
        chosen = [t for t in re.split(r"[\s,]+", args.lower()) if t in SCRAPERS]
        if not chosen:
            return Reply("Fonti disponibili: " + ", ".join(SCRAPERS) + "\nEs. <code>/fonti wallapop vinted ebay</code>")
        self._save({"sources": chosen})
        return Reply("⚙️ Fonti attive: " + ", ".join(chosen))

    # ------------------------------------------------------------------
    def _prices(self, args: str) -> Reply:
        rows = pstats.PriceRows(self.db.list_found())
        arg = args.strip().lower()
        if arg:
            card = self._one_card(arg)
            if not card:
                return Reply("Carta non riconosciuta: usa il numero (es. <code>/prezzi 145</code>), un codice c1..c30 o sv8:7.")
            cp = pstats.card_prices(rows, card)
            cmp = cardmarket.get(self.db, card.id)
            lines = [f"💶 <b>{html.escape(card.label)}</b>"]
            if cmp and cmp.ref:
                lines.append(f"🏷 <b>Cardmarket</b>: trend <b>{pstats.fmt_eur(cmp.trend or cmp.ref)}</b> · minimo {pstats.fmt_eur(cmp.low)}"
                             f" · media 30 giorni {pstats.fmt_eur(cmp.avg30)}" + (f" <i>(al {cmp.updated})</i>" if cmp.updated else ""))
                pct = float(self._settings().get("deal_pct", cardmarket.DEAL_PCT) or 0)
                if pct and cmp.ref >= cardmarket.DEAL_MIN_TREND:  # sotto i 5 € di valore non c'è mai un "affare"
                    lines.append(f"🔥 Affare sotto {pstats.fmt_eur(cmp.ref * pct / 100)}"
                                 + (f" e sotto il minimo {pstats.fmt_eur(cmp.low)}" if cmp.low else ""))
            else:
                lines.append("🏷 Cardmarket: prezzo non disponibile per questa carta.")
            if not cp.overall.n:
                lines.append("Nessun annuncio visto finora dal bot.")
                return Reply("\n".join(lines))
            lines += [f"\n👀 <b>Annunci visti dal bot</b> ({cp.overall.n}): minimo {pstats.fmt_eur(cp.overall.min)} · "
                      f"mediana {pstats.fmt_eur(cp.overall.median)} · massimo {pstats.fmt_eur(cp.overall.max)}"]
            for src in pstats.SOURCE_ORDER:
                st = cp.by_source.get(src)
                if st and st.n:
                    lines.append(f"• {pstats.SOURCE_LABELS[src]}: min {pstats.fmt_eur(st.min)} · mediana {pstats.fmt_eur(st.median)} · {st.n} annunci")
            if cp.recent_median is not None and cp.older_median:
                delta = 100.0 * (cp.recent_median - cp.older_median) / cp.older_median
                arrow = "📈" if delta > 5 else "📉" if delta < -5 else "➡️"
                lines.append(f"{arrow} Ultimi 7 giorni: mediana {pstats.fmt_eur(cp.recent_median)} ({delta:+.0f}% rispetto a prima)")
            return Reply("\n".join(lines), buttons=[[("📂 Storico", f"/storico {card.set_id}:{card.number}")]])
        cm = cardmarket.all_prices(self.db)
        comp = pstats.completion(self.scope_index(), self._wanted(), rows, cm)
        if not comp.missing:
            return Reply(f"🎉 Nessuna carta mancante: {html.escape(self.current().name)} completa!")
        other = comp.priced - comp.cm_priced
        lines = [f"💶 <b>Per finire il set</b> ({comp.missing} carte mancanti)",
                 f"Circa <b>{pstats.fmt_eur(comp.cost_median)}</b> al valore di mercato · {pstats.fmt_eur(comp.cost_min)} ai prezzi minimi",
                 f"Prezzi Cardmarket per {comp.cm_priced} carte" + (f", annunci visti dal bot per {other}" if other else "")
                 + (f"; {len(comp.unpriced)} ancora senza prezzo" if comp.unpriced else "")]
        priced = []
        wanted = self._wanted()
        for c in sorted((c for s_ in self.scope() for c in s_.cards if c.id in wanted), key=lambda c: c.sort_key):
            p = cm.get(c.id)
            if p and p.ref:
                priced.append((p.ref, c))
                continue
            st = pstats.card_prices(rows, c).overall
            if st.n:
                priced.append((st.min, c))
        priced.sort(key=lambda t: -t[0])
        if priced:
            lines.append("\nLe più care:")
            lines += [f"• {html.escape(c.label)}: {pstats.fmt_eur(m)}" for m, c in priced[:8]]
        if comp.unpriced:
            lines.append("\nSenza prezzo: " + html.escape(", ".join(self.index.code_of[c.id] for c in comp.unpriced[:30]))
                         + (" …" if len(comp.unpriced) > 30 else ""))
        return Reply("\n".join(lines))

    # ---- persone collegate ----------------------------------------------------
    def _is_owner(self, chat_id: str) -> bool:
        owner = self.db.owner_chat_id()
        return bool(chat_id) and (not owner or str(chat_id) == owner)

    def _invite(self, chat_id: str) -> Reply:
        if not self._is_owner(chat_id):
            return Reply("Solo il proprietario del bot può invitare altre persone.")
        import secrets
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        self.db.set_kv("invite_code", {"code": code, "expires": time.time() + 48 * 3600})
        return Reply("👥 Codice invito (vale 48 ore, una persona):\n"
                     f"<code>/start {code}</code>\n\n"
                     f"Chi lo riceve apre il bot e incolla quel comando: entra subito, senza lista d'attesa, con il suo "
                     f"Pokébot personale e {plans.TRIAL_DAYS} giorni di prova.")

    def _users(self, chat_id: str) -> Reply:
        if not self._is_owner(chat_id):
            return Reply("Solo il proprietario del bot vede le persone collegate. Per i tuoi amici usa /amici.")
        ids = self.db.chat_ids()
        if not ids:
            return Reply("Nessuno collegato ancora.")
        lines = [f"👥 <b>Persone collegate</b> ({len(ids)})"]
        for cid in ids:
            lines.append(f"• {html.escape(self.db.user_name(cid))} <code>{cid}</code> · {plans.describe(self.db, cid)}"
                         f"{' · tu' if cid == str(chat_id) else ''}")
        lines.append("\n/invita per aggiungere qualcuno · /espelli ID per scollegarlo · /piano ID per cambiare piano · /recensioni")
        return Reply("\n".join(lines))

    def _kick(self, chat_id: str, args: str) -> Reply:
        if not self._is_owner(chat_id):
            return Reply("Solo il proprietario del bot può scollegare qualcuno.")
        target = args.strip()
        if not target.lstrip("-").isdigit():
            return Reply("Usa <code>/espelli ID</code> con l'ID che vedi in /utenti.")
        if target == self.db.owner_chat_id():
            return Reply("Il proprietario non si può scollegare.")
        if not self.db.remove_chat_id(target):
            return Reply("Quell'ID non è tra le persone collegate (vedi /utenti).")
        self.db.ban(target)  # non rientra da solo (un rinnovo dell'abbonamento o un /start)
        note = ""
        pays = [x for x in (plans.get(self.db, target).get("payments") or []) if x.get("charge") and not x.get("refunded")]
        if pays:
            try:
                ok, _ = TelegramClient().cancel_subscription(target, pays[-1]["charge"])
            except (requests.RequestException, ValueError):
                ok = False
            note = ("\nAbbonamento: rinnovo fermato." if ok else "\n⚠️ Non sono riuscito a fermare il rinnovo dell'abbonamento: "
                    f"usa <code>/rimborsa {target}</code>.")
        return Reply(f"👋 Chat <code>{target}</code> scollegata: non riceve più notifiche né comandi.{note}")

    # ---- lista d'attesa e canale degli affari ---------------------------------
    def waitlist_signup(self, chat_id: str, name: str = "", source: str = "") -> Reply:
        """Qualcuno senza invito ha scritto /start: va in lista d'attesa e il proprietario viene avvisato."""
        chat_id = str(chat_id)
        if self.db.is_banned(chat_id):
            return Reply("Pokébot non è disponibile per questo account.")
        if not self.db.add_to_waitlist(chat_id, name, source):
            pos = list(self.db.waitlist()).index(chat_id) + 1 if chat_id in self.db.waitlist() else 0
            return Reply("⏳ Sei già in lista d'attesa" + (f" (posizione {pos})" if pos else "") +
                         ". Ti scrivo io appena il tuo accesso viene attivato.")
        wl = self.db.waitlist()
        pos = len(wl)
        owner = self.db.owner_chat_id()
        sends = []
        if owner:
            label = html.escape(name or chat_id)
            src = f" · da {html.escape(source)}" if source else ""
            sends.append((owner,
                          f"🙋 <b>Nuova richiesta di accesso</b>: {label} (<code>{chat_id}</code>){src}\n"
                          f"In attesa: {pos}\n"
                          f"/approva {chat_id} · /rifiuta {chat_id} · /attesa per la lista",
                          [[("✅ Approva", f"/approva {chat_id}"), ("❌ Rifiuta", f"/rifiuta {chat_id}")]]))
        return Reply("👋 Benvenuto su <b>Pokébot</b>!\n\n"
                     "Pokébot cerca su Wallapop, Vinted ed eBay le carte che mancano alla tua collezione e ti avvisa "
                     "appena spuntano, con prezzi, affari 🔥, lista della spesa e album da condividere con gli amici.\n\n"
                     f"⏳ Per ora l'accesso è su invito: sei in lista d'attesa (posizione {pos}). "
                     f"Ti scrivo io qui appena viene attivato, non devi fare altro: da quel momento hai {plans.TRIAL_DAYS} giorni "
                     "di prova con tutte le funzioni, poi resta gratis in versione Light.", sends=sends)

    def _waitlist(self, chat_id: str) -> Reply:
        if not self._is_owner(chat_id):
            return Reply("Solo il proprietario del bot vede la lista d'attesa.")
        wl = self.db.waitlist()
        users = len(self.db.chat_ids())
        lines = [f"📋 <b>Lista d'attesa</b>: {len(wl)} in attesa · {users} dentro"]
        now = time.time()
        for cid, e in sorted(wl.items(), key=lambda kv: float(kv[1].get("ts") or 0)):
            days = (now - float(e.get("ts") or now)) / 86400
            ago = "oggi" if days < 1 else f"{int(days)} g fa"
            src = f" · da {html.escape(str(e.get('source')))}" if e.get("source") else ""
            lines.append(f"• {html.escape(str(e.get('name') or cid))} <code>{cid}</code> · {ago}{src}")
        if wl:
            lines.append("\n/approva ID · /approva tutti · /rifiuta ID")
        return Reply("\n".join(lines))

    def _approve(self, chat_id: str, args: str) -> Reply:
        if not self._is_owner(chat_id):
            return Reply("Solo il proprietario del bot può approvare le richieste.")
        wl = self.db.waitlist()
        target = args.strip().lower()
        ids = list(wl) if target in ("tutti", "tutte", "all") else [args.strip()]
        done, sends = [], []
        for cid in ids:
            entry = self.db.remove_from_waitlist(cid)
            if entry is None or cid in self.db.chat_ids():  # già dentro (es. con un invito): niente da rifare
                continue
            self.db.unban(cid)
            self.db.add_chat_id(cid)
            plans.start_trial(self.db, cid)
            for sid in config.HOME_SET_IDS:
                coll.album_for(self.db, cid, sid)
            done.append(f"{html.escape(str(entry.get('name') or cid))} (<code>{cid}</code>)")
            plans.set_onboarding(self.db, cid, True)
            text, buttons = plans.welcome_message()
            sends.append((cid, text, buttons))
        if not done:
            return Reply("Nessuno con quell'ID in lista d'attesa (vedi /attesa). Usa <code>/approva ID</code> o <code>/approva tutti</code>.")
        return Reply("✅ Accesso attivato per: " + ", ".join(done) + f"\nIn attesa: {len(self.db.waitlist())}", sends=sends)

    def _reject(self, chat_id: str, args: str) -> Reply:
        if not self._is_owner(chat_id):
            return Reply("Solo il proprietario del bot può rifiutare le richieste.")
        entry = self.db.remove_from_waitlist(args.strip())
        if entry is None:
            return Reply("Nessuno con quell'ID in lista d'attesa (vedi /attesa).")
        return Reply(f"🗑 {html.escape(str(entry.get('name') or args.strip()))} tolto dalla lista d'attesa (non riceve nulla).")

    # ---- piani: prova, Light, abbonamento ---------------------------------------
    def _subscribe(self) -> Reply:
        t = plans.tier(self.db, self.chat)
        head = plans.describe(self.db, self.chat)
        if t == "owner":
            return Reply(head)
        if t == "pro":
            return Reply(head + "\nL'abbonamento si gestisce (e si disdice) dalle impostazioni di Telegram, alla voce Stars.")
        link = plans.invoice_link(self.db, TelegramClient(), self.chat)
        text = (f"{head}\n\n⭐ <b>Pokébot completo · {plans.PRICE_STARS} Stars al mese</b>\n"
                "• avvisi nel momento in cui esce l'annuncio, non una volta al giorno\n"
                "• affari 🔥 sotto il valore Cardmarket\n"
                f"• inseguimenti ogni {watch.fmt_duration(watch.EVERY_S)}, fino a {watch.MAX_WATCHES} carte insieme\n"
                "• collezioni illimitate\n\n"
                "Si paga con le Stars di Telegram (anche con Apple Pay) e si rinnova ogni mese: disdici quando vuoi.")
        if not link:
            return Reply(text + "\n\n⚠️ Il pagamento non è disponibile in questo momento, riprova tra poco.")
        return Reply(text, buttons=[[(f"⭐ Abbonati · {plans.PRICE_STARS} Stars", link)]])

    def _plan(self, chat_id: str, args: str) -> Reply:
        """/piano – il tuo piano · (proprietario) /piano ID [sempre|light|pro 30|prova 5]."""
        a = args.split()
        if not a or not self._is_owner(chat_id):
            return Reply(plans.describe(self.db, self.chat) + "\n/abbonati per i dettagli dell'abbonamento.")
        target = a[0]
        if target not in self.db.chat_ids():
            return Reply("Quell'ID non è tra le persone collegate (vedi /utenti).")
        who = html.escape(self.db.user_name(target))
        if len(a) == 1:
            return Reply(f"{who}: {plans.describe(self.db, target)}")
        what = a[1].lower()
        n = min(int(a[2]), 3650) if len(a) > 2 and a[2].isdigit() and len(a[2]) < 6 else 0
        if what in ("sempre", "gratis", "regala"):
            plans.set_pro(self.db, target, lifetime=True)
        elif what == "light":
            plans.set_light(self.db, target)
        elif what == "pro":
            plans.set_pro(self.db, target, until=time.time() + (n or 30) * 86400)
        elif what == "prova":
            plans.start_trial(self.db, target, n or plans.TRIAL_DAYS)
        else:
            return Reply("Usa <code>/piano ID sempre</code>, <code>/piano ID pro 30</code>, <code>/piano ID prova 5</code> o <code>/piano ID light</code>.")
        return Reply(f"✅ {who}: {plans.describe(self.db, target)}")

    def _refund(self, chat_id: str, args: str) -> Reply:
        """(proprietario) /rimborsa ID – rimborso intero entro REFUND_DAYS dal pagamento (Telegram non fa rimborsi parziali);
        dopo: /rimborsa ID annulla ferma solo il rinnovo; /rimborsa ID forza rimborsa comunque."""
        if not self._is_owner(chat_id):
            return Reply("Solo il proprietario può fare rimborsi.")
        parts = args.split()
        target = parts[0] if parts else ""
        mode = parts[1].lower() if len(parts) > 1 else ""
        p = plans.get(self.db, target)
        pays = [x for x in (p.get("payments") or []) if x.get("charge") and not x.get("refunded")]
        if not pays:
            return Reply("Nessun pagamento da rimborsare per quell'ID (vedi /utenti).")
        last = pays[-1]
        who = html.escape(self.db.user_name(target))
        used = (time.time() - float(last.get("ts") or 0)) / 86400
        used_txt = f"{used:.0f} giorn{'o' if round(used) == 1 else 'i'}" if used >= 1 else "meno di un giorno"
        if mode in ("annulla", "stop", "disdici"):
            try:
                ok, err = TelegramClient().cancel_subscription(target, last["charge"])
            except (requests.RequestException, ValueError) as exc:
                ok, err = False, str(exc)
            if not ok:
                return Reply(f"⚠️ Non riesco a fermare il rinnovo: {html.escape(err or 'errore Telegram')}")
            return Reply(f"⏹ Rinnovo fermato per {who}: resta completo fino al {plans._date(p.get('pro_until'))}, poi passa a Light.",
                         sends=[(target, f"⏹ Il tuo abbonamento non si rinnoverà: hai tutto fino al {plans._date(p.get('pro_until'))}, "
                                         "poi passi alla versione Light.", None)])
        if used > plans.REFUND_DAYS and mode != "forza":
            return Reply(f"⛔ {who} ha pagato {used_txt} fa: il rimborso intero vale solo nei primi {plans.REFUND_DAYS} giorni "
                         "e Telegram non permette rimborsi parziali.\n"
                         f"• <code>/rimborsa {target} annulla</code> – ferma il rinnovo, resta completo fino al {plans._date(p.get('pro_until'))}\n"
                         f"• <code>/rimborsa {target} forza</code> – rimborsa comunque tutte le {last.get('stars', 0)} ⭐")
        try:
            ok, err = TelegramClient().refund_stars(target, last["charge"])
        except (requests.RequestException, ValueError) as exc:
            ok, err = False, str(exc)
        if not ok:
            return Reply(f"⚠️ Rimborso non riuscito: {html.escape(err or 'errore Telegram')}")
        for x in p["payments"]:
            if x.get("charge") == last["charge"]:
                x["refunded"] = True
        plans.save(self.db, target, p)
        plans.set_light(self.db, target)
        return Reply(f"↩️ Rimborsate {last.get('stars', 0)} ⭐ a {who}: rinnovo fermato, ora è su Light.",
                     sends=[(target, f"↩️ Ti ho rimborsato {last.get('stars', 0)} ⭐ e fermato l'abbonamento. Resti sulla versione Light.", None)])

    def _feedback(self, key: str, value, owner_text: str, reply: str) -> Reply:
        """Salva voto o recensione nel piano della persona e lo fa sapere al proprietario."""
        p = plans.get(self.db, self.chat)
        p[key] = value
        p[f"{key}_ts"] = time.time()
        plans.save(self.db, self.chat, p)
        owner = self.db.owner_chat_id()
        who = html.escape(self.db.user_name(self.chat))
        return Reply(reply, sends=[(owner, owner_text.format(who=who), None)] if owner and owner != self.chat else None)

    def _vote(self, args: str) -> Reply:
        a = args.strip()
        if not a.isdigit() or not 1 <= int(a) <= 5:
            return Reply("Vota da 1 a 5, per esempio <code>/voto 4</code>.")
        return self._feedback("rating", int(a), "⭐ {who} ha votato <b>" + a + "/5</b>",
                              "🙏 Grazie! Se vuoi, dimmi in una riga cosa ti è piaciuto o cosa manca: "
                              "<code>/recensione il tuo commento</code>.")

    def _review(self, args: str) -> Reply:
        text = args.strip()[:500]
        if not text:
            return Reply("Scrivi il commento dopo il comando, per esempio <code>/recensione mi ha trovato il Charizard!</code>")
        return self._feedback("review", text, "💬 Recensione di {who}: " + html.escape(text).replace("{", "{{").replace("}", "}}"),
                              "🙏 Grazie, la leggo di sicuro.")

    def _reviews(self, chat_id: str) -> Reply:
        if not self._is_owner(chat_id):
            return Reply("Solo il proprietario vede le recensioni.")
        rows = [(c, plans.get(self.db, c)) for c in self.db.chat_ids()]
        rows = [(c, p) for c, p in rows if p.get("rating") or p.get("review")]
        if not rows:
            return Reply("Nessun voto ancora.")
        votes = [int(p["rating"]) for _, p in rows if p.get("rating")]
        lines = [f"⭐ <b>Recensioni</b>: media {sum(votes) / len(votes):.1f}/5 su {len(votes)} voti" if votes else "⭐ <b>Recensioni</b>"]
        for c, p in rows:
            lines.append(f"• {html.escape(self.db.user_name(c))}: {p.get('rating', '–')}/5" +
                         (f" · {html.escape(str(p.get('review'))[:200])}" if p.get("review") else ""))
        return Reply("\n".join(lines))

    def _channel(self, chat_id: str, args: str) -> Reply:
        """/canale @nome – imposta il canale degli affari · /canale ora – pubblica subito · /canale 20 – ora del post · /canale off."""
        if not self._is_owner(chat_id):
            return Reply("Solo il proprietario del bot gestisce il canale.")
        from . import channel as ch
        a = args.strip()
        cur = self.db.get_kv("deals_channel") or ""
        hour = ch.post_hour(self.db)
        if not a:
            if not cur:
                return Reply("📣 Nessun canale impostato.\nCrea un canale Telegram, aggiungi il bot come amministratore e scrivi "
                             "<code>/canale @nomecanale</code>: ogni giorno pubblico i 3 affari migliori con un pulsante che porta qui.")
            last = self.db.get_kv("deals_channel_last") or "mai"
            return Reply(f"📣 Canale affari: <code>{html.escape(cur)}</code> · post alle {hour}:00 · ultimo: {last}\n"
                         "/canale ora – pubblica adesso · /canale 20 – cambia ora · /canale off – spegni")
        if a.lower() in ("off", "no", "spegni"):
            self.db.set_kv("deals_channel", None)
            return Reply("📣 Canale affari spento.")
        if a.lower() in ("ora", "adesso", "test"):
            if not cur:
                return Reply("Prima imposta il canale con <code>/canale @nomecanale</code>.")
            deals = ch.pick_deals(self.db, self.index, exclude=ch.posted(self.db))
            if not deals:
                return Reply("Negli ultimi 3 giorni nessun annuncio nuovo con un prezzo Cardmarket da confrontare: "
                             "niente da pubblicare per ora.")
            ch.remember(self.db, deals)
            text, buttons = ch.format_post(deals, self.db.get_kv("bot_username") or "")
            return Reply(f"📣 Pubblico sul canale {len(deals)} occasioni.", sends=[(cur, text, buttons)])
        if a.isdigit() and 0 <= int(a) <= 23:
            self.db.set_kv("deals_channel_hour", int(a))
            return Reply(f"📣 Post giornaliero alle {int(a)}:00.")
        if a.startswith("@") or (a.startswith("-100") and a[1:].isdigit()):  # @nome oppure l'id numerico del canale
            self.db.set_kv("deals_channel", a)
            return Reply(f"📣 Canale affari: <code>{html.escape(a)}</code>. Ti mando subito un messaggio di prova lì: "
                         "se non arriva, controlla che il bot sia amministratore del canale.\n"
                         f"Ogni giorno alle {hour}:00 pubblico le 3 migliori occasioni, con il prezzo confrontato con Cardmarket.",
                         sends=[(a, "✅ Pokébot collegato a questo canale: da qui in poi pubblico gli affari del giorno.", None)])
        return Reply("Usa <code>/canale @nomecanale</code>, <code>/canale ora</code>, <code>/canale 20</code> o <code>/canale off</code>.")

    def _collection(self, args: str) -> Reply:
        """/collezione – le mie · /collezione sv8 – seguila (album mio) e rendila corrente · … attiva|disattiva · … ho|manca N… · … tutte|svuota."""
        a = args.strip()
        active = set(self.db.active_sets(self.chat))
        cur = self.current()
        albums = self.db.albums_of(self.chat)
        if not a:
            wanted = self._wanted()
            lines = ["📚 <b>Le tue collezioni</b>"]
            for s_ in self._my_sets():
                n = sum(1 for c in s_.cards if c.id in wanted)
                album = albums.get(s_.id)
                others = [m for m in (self.db.album_members(album) if album else []) if m != self.chat]
                shared = (" · 👥 con " + ", ".join(html.escape(self.db.user_name(m)) for m in others)) if others else ""
                lines.append(f"{'▶️' if s_.id == cur.id else '•'} <code>{s_.id}</code> {html.escape(s_.name)} · {len(s_.cards)} carte · "
                             f"mancanti {n} · " + ("🔎 ricerca attiva" if s_.id in active else "💤 ricerca spenta") + shared)
            lines.append("\n▶️ = corrente: /mancanti, /aggiungi 4 7, /ho 4, /progresso, /prezzi lavorano su di lei. "
                         "<code>/collezione sv8</code> passa a un'altra (scaricata se serve: parte da \"mi mancano tutte\"); "
                         "<code>/collezione sv8 attiva</code> la fa cercare; <code>/condividi sv8</code> la condivide con un amico.")
            return Reply("\n".join(lines))
        set_id, _, rest = a.partition(" ")
        set_id = self._set_alias(set_id)
        rest = rest.strip()
        blocked = self._follow_limit(set_id)
        if blocked:
            return blocked
        try:
            cs, album, created = coll.follow(self.db, self.chat, set_id)
        except KeyError:
            return Reply(f"Collezione <code>{html.escape(set_id)}</code> non trovata nel catalogo. Sfogliale dalla Mini App.")
        except Exception as exc:  # noqa: BLE001
            return Reply(f"⚠️ Non riesco a scaricare la collezione adesso ({html.escape(str(exc)[:80])}). Riprova tra poco.")
        self.index.add_set(cs)
        verb, _, nums = rest.partition(" ")
        verb = verb.lower()
        wanted_album = self.db.wanted_ids([album])
        if not verb:
            self.db.set_current(cs.id, self.chat)
            n = sum(1 for c in cs.cards if c.id in wanted_album)
            on = cs.id in active
            others = [m for m in self.db.album_members(album) if m != self.chat]
            return Reply(f"📚 <b>{html.escape(cs.name)}</b> (<code>{cs.id}</code>) è la collezione corrente · {len(cs.cards)} carte"
                         f"{' · numerazione /' + str(cs.printed_total) if cs.printed_total else ''}\nMancanti: <b>{n}</b> · "
                         + ("🔎 ricerca attiva" if on else "💤 ricerca spenta")
                         + (" · 👥 condivisa con " + ", ".join(html.escape(self.db.user_name(m)) for m in others) if others else "")
                         + f"\n\n/mancanti · /aggiungi 4 7 · /ho 4 · /progresso · /prezzi · "
                         f"<code>/collezione {cs.id} {'disattiva' if on else 'attiva'}</code> · <code>/condividi {cs.id}</code>")
        # la 30th e la sua Classic Collection si accendono e si spengono insieme
        toggle_ids = list(config.HOME_SET_IDS) if cs.id in config.HOME_SET_IDS else [cs.id]
        if verb in ("attiva", "on", "cerca"):
            if (cs.id not in active and not plans.is_full(self.db, self.chat)
                    and plans.active_count(self.db, self.chat) >= plans.LIGHT_MAX_ACTIVE):
                return Reply(f"🌱 Con la versione Light puoi tenere le notifiche accese su {plans.LIGHT_MAX_ACTIVE} collezioni. "
                             "Spegnine una con <code>/collezione ID disattiva</code>, oppure /abbonati per accenderle tutte.")
            for sid in toggle_ids:
                self.db.set_active(sid, True, self.chat)
            return Reply(f"🔎 Ricerca attiva per <b>{html.escape(cs.name)}</b> ({len([c for c in cs.cards if c.id in wanted_album])} mancanti). "
                         f"⚠️ Ogni collezione attiva aggiunge ricerche e notifiche a ogni giro: tienine poche accese. "
                         f"<code>/collezione {cs.id} disattiva</code> per spegnerla.")
        if verb in ("disattiva", "off", "spegni"):
            for sid in toggle_ids:
                self.db.set_active(sid, False, self.chat)
            return Reply(f"💤 Ricerca spenta per <b>{html.escape(cs.name)}</b>: la checklist resta.")
        if verb in ("svuota", "reset", "completa"):
            coll.mark(self.db, cs, [c.number for c in cs.cards], False, album)
            return Reply(f"🧹 {html.escape(cs.name)}: segnata completa, nessuna mancante.")
        if verb in ("tutte", "manca", "mancano", "aggiungi", "ho", "trovata", "rimuovi", "presa"):
            if verb == "tutte":
                numbers, want, bad = [c.number for c in cs.cards], True, []
            else:
                want = verb in ("manca", "mancano", "aggiungi")
                by_num = {c.number.lower(): c for c in cs.cards}
                numbers, bad = [], []
                for t in nums.replace(",", " ").split():
                    if "-" in t and all(x.isdigit() for x in t.split("-", 1)):
                        lo, hi = sorted(int(x) for x in t.split("-", 1))  # solo i numeri che esistono: mai un ciclo enorme
                        numbers += [by_num[k].number for k in sorted((k for k in by_num if k.isdigit()), key=int) if lo <= int(k) <= hi]
                    elif t.lower() in by_num:
                        numbers.append(by_num[t.lower()].number)
                    else:
                        bad.append(t)
                if not numbers:
                    return Reply(f"Nessun numero valido per {html.escape(cs.name)}. Es. <code>/collezione {cs.id} ho 4 7 10-12</code>.")
            coll.mark(self.db, cs, numbers, want, album)
            n = len(coll.wanted_numbers(self.db, cs, album))
            names = ", ".join(html.escape(next(c.name for c in cs.cards if c.number == x) + " " + x) for x in numbers[:6])
            more = f" e altre {len(numbers) - 6}" if len(numbers) > 6 else ""
            msg = f"{'🃏 Mancanti' if want else '✅ Prese'}: {names}{more}\n{html.escape(cs.name)}: ora {n} mancanti"
            if want and cs.id not in active:
                msg += f" · 💤 ricerca spenta (<code>/collezione {cs.id} attiva</code> per cercarle)"
            if bad:
                msg += "\n❓ Non capiti: " + html.escape(" ".join(bad))
            return Reply(msg)
        return Reply(f"Non ho capito. Usa <code>/collezione {cs.id}</code>, <code>… attiva</code>, <code>… disattiva</code>, "
                     f"<code>… ho 4 7</code>, <code>… manca 4</code>.")

    # ---- amici e album condivisi --------------------------------------------------
    def _friends(self, args: str) -> Reply:
        """/amico – il tuo codice amico · /amico CODICE – diventate amici · /amico togli NOME · /amici – elenco."""
        a = args.strip()
        if not a:
            code = self.db.friend_code(self.chat)
            return Reply("🤝 Il tuo codice amico:\n"
                         f"<code>/amico {code}</code>\n\nChi lo scrive al bot diventa tuo amico: potrete condividere album con /condividi. "
                         "Lo trovi sempre anche nella sezione Amici dell'app.")
        verb, _, rest = a.partition(" ")
        if verb.lower() in ("togli", "rimuovi"):
            target = self._friend_by_name(rest)
            if not target:
                return Reply("Amico non trovato: /amici per l'elenco.")
            self.db.remove_friend(self.chat, target)
            return Reply(f"👋 {html.escape(self.db.user_name(target))} non è più tra i tuoi amici.")
        inv = self.db.get_kv(f"friendcode:{verb.upper()}") or {}
        if not inv or float(inv.get("expires") or 0) < time.time():
            if self.db.get_kv(f"share:{verb.upper()}"):
                return self._accept(verb)  # era un codice album
            return Reply("Codice amico non valido o scaduto.")
        other = str(inv["chat"])
        if other == self.chat:
            return Reply("Quello è il tuo codice 🙂")
        if other in self.db.friends(self.chat):
            return Reply(f"Tu e <b>{html.escape(self.db.user_name(other))}</b> siete già amici.")
        self.db.add_friend(self.chat, other)
        return Reply(f"🤝 Ora tu e <b>{html.escape(self.db.user_name(other))}</b> siete amici. "
                     f"Condividi un album con <code>/condividi me55 {html.escape(self.db.user_name(other))}</code>.")

    def _friend_by_name(self, name: str) -> str | None:
        name = name.strip().lower()
        for f in self.db.friends(self.chat):
            if name and (name == f or name == self.db.user_name(f).lower()):
                return f
        return None

    def _friends_list(self) -> Reply:
        fr = self.db.friends(self.chat)
        if not fr:
            return Reply("Nessun amico ancora. /amico ti dà un codice da passare a chi vuoi; chi lo scrive diventa tuo amico.")
        albums = self.db.albums_of(self.chat)
        lines = ["🤝 <b>Amici</b>"]
        for f in fr:
            shared = [sid for sid, alb in albums.items() if f in self.db.album_members(alb)]
            lines.append(f"• {html.escape(self.db.user_name(f))}" + (" · 👥 album in comune: " + ", ".join(shared) if shared else ""))
        lines.append("\n/condividi sv8 NOME per condividere un album · /esci sv8 per uscire da un album condiviso")
        return Reply("\n".join(lines))

    def _share(self, args: str) -> Reply:
        """/condividi sv8 NOME – propone a un amico l'album; lui accetta con /accetta."""
        import secrets
        parts = args.strip().split(maxsplit=1)
        if not parts:
            return Reply("Usa <code>/condividi me55 NOME</code> (un amico, vedi /amici) oppure <code>/condividi me55</code> per un codice da passare.")
        set_id = self._set_alias(parts[0])
        cs = self.index.get_set(set_id)
        if not cs or not (cs.primary or cs.id in self.db.albums_of(self.chat)):
            return Reply("Collezione non tra le tue: prima <code>/collezione " + html.escape(set_id) + "</code>.")
        target = self._friend_by_name(parts[1]) if len(parts) > 1 else None
        if len(parts) > 1 and not target:
            return Reply("Amico non trovato: /amici per l'elenco, oppure <code>/condividi " + cs.id + "</code> per un codice.")
        album = coll.album_for(self.db, self.chat, cs.id)
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        self.db.set_kv(f"share:{code}", {"album": album, "from": self.chat, "expires": time.time() + 7 * 86400})
        if target:
            text = (f"👥 <b>{html.escape(self.db.user_name(self.chat))}</b> vuole condividere con te l'album "
                    f"<b>{html.escape(cs.name)}</b>: diventerebbe unico per tutti e due (le carte che uno dei due ha contano come prese).")
            return Reply(f"📨 Proposta inviata a {html.escape(self.db.user_name(target))}: quando accetta, l'album diventa unico.",
                         sends=[(target, text, [[("✅ Accetta", f"/accetta {code}"), ("✖️ No grazie", f"/accetta no {code}")]])])
        return Reply(f"👥 Codice per condividere <b>{html.escape(cs.name)}</b> (vale 7 giorni):\n<code>/accetta {code}</code>\n"
                     "Chi lo scrive al bot entra nel tuo album: diventa unico per tutti e due.")

    def _accept(self, args: str) -> Reply:
        code = args.strip().upper()
        if not code or code.startswith("NO"):
            refused = code[2:].strip()
            inv = self.db.get_kv(f"share:{refused}") if refused else None
            if inv:
                self.db.set_kv(f"share:{refused}", None)
                return Reply("Ok, album non condiviso.", sends=[(str(inv["from"]), f"✖️ {html.escape(self.db.user_name(self.chat))} "
                                                                                     "ha preferito non condividere l'album.", None)])
            return Reply("Ok, album non condiviso.")
        inv = self.db.get_kv(f"share:{code}") or {}
        if not inv or float(inv.get("expires") or 0) < time.time():
            return Reply("Codice non valido o scaduto.")
        album = str(inv["album"])
        set_id = self.db.album_set(album)
        if not set_id:
            return Reply("Quell'album non esiste più.")
        if str(inv["from"]) == self.chat:
            return Reply("È un tuo album 🙂")
        blocked = self._follow_limit(set_id)
        if blocked:
            return blocked
        cs = coll.get_set(self.db, set_id)
        self.index.add_set(cs)
        self.db.join_album(self.chat, album)
        self.db.add_friend(self.chat, str(inv["from"]))
        self.db.set_kv(f"share:{code}", None)
        members = [m for m in self.db.album_members(album) if m != self.chat]
        n = len(self.db.wanted_ids([album]))
        other = str(inv["from"])
        return Reply(f"👥 <b>{html.escape(cs.name)}</b> ora è un album unico con {', '.join(html.escape(self.db.user_name(m)) for m in members)}: "
                     f"{n} mancanti. Ogni spunta vale per tutti.",
                     sends=[(other, f"✅ {html.escape(self.db.user_name(self.chat))} ha accettato: <b>{html.escape(cs.name)}</b> è un album unico.", None)])

    def _leave(self, args: str) -> Reply:
        set_id = self._set_alias(args)
        album = self.db.albums_of(self.chat).get(set_id)
        if not album or len(self.db.album_members(album)) < 2:
            return Reply("Quell'album non è condiviso.")
        self.db.leave_album(self.chat, album)
        return Reply(f"👋 Sei uscito dall'album condiviso <code>{html.escape(set_id)}</code>: ne hai una copia tutta tua.")

    def _chase(self, args: str) -> Reply:
        """/insegui 151 – per 6 ore cerca quella carta ogni 5 minuti; /insegui – elenco; /insegui stop [carte]."""
        a = args.strip()
        if not a:
            return Reply(watch.describe(self.index, self.db, self.chat))
        first, _, rest = a.partition(" ")
        if first.lower() in ("stop", "basta", "ferma", "off"):
            if not rest.strip():
                n = watch.clear_watches(self.db, self.chat)
                return Reply(f"⏹ Fermati {n} inseguimenti." if n else "Nessun inseguimento attivo.")
            cards, unknown = self.resolve(rest)
            stopped = [c.label for c in cards if watch.remove_watch(self.db, c.id, self.chat)]
            msg = ("⏹ Fermato: " + ", ".join(html.escape(x) for x in stopped)) if stopped else "Quelle carte non erano inseguite."
            if unknown:
                msg += "\n❓ Non capiti: " + html.escape(" ".join(unknown))
            return Reply(msg)
        card_args, duration, every = watch.parse_watch_args(a)
        cards, unknown = self.resolve(card_args)
        if not cards:
            return Reply("Carta non riconosciuta. Es. <code>/insegui 151</code>, <code>/insegui c4 2h</code>, <code>/insegui 151 2h ogni 10m</code>.")
        light = not plans.is_full(self.db, self.chat)
        max_w = plans.LIGHT_MAX_WATCHES if light else watch.MAX_WATCHES
        if light:
            every = max(every, plans.LIGHT_WATCH_EVERY_S)
            duration = max(duration, every)
        active = watch.list_watches(self.db, self.chat)
        renew = [c for c in cards if c.id in active]  # già inseguite: si rinnovano senza occupare posti nuovi
        new = [c for c in cards if c.id not in active]
        room = max(0, max_w - len(active))
        cards, left_out = renew + new[:room], new[room:]
        if not cards:
            if light:
                return Reply(f"🌱 Con la versione Light puoi inseguire una carta alla volta, controllata ogni "
                             f"{watch.fmt_duration(plans.LIGHT_WATCH_EVERY_S)}. Ferma quella attiva con /insegui stop, oppure "
                             f"/abbonati per inseguirne fino a {watch.MAX_WATCHES} ogni {watch.fmt_duration(watch.EVERY_S)}.")
            return Reply(f"Al massimo {watch.MAX_WATCHES} inseguimenti insieme: ferma qualcosa con /insegui stop.")
        for c in cards:
            w = watch.add_watch(self.db, c.id, duration, every, self.chat)
        names = ", ".join(html.escape(c.label) for c in cards)
        lines = [f"🏃 Inseguo <b>{names}</b>: ogni {watch.fmt_duration(every)} per {watch.fmt_duration(duration)} "
                 f"(fino alle {watch.fmt_time(w['until'])}).",
                 "Primo controllo tra pochi secondi, poi ti avviso solo quando spunta un annuncio nuovo. Alla fine, il riepilogo."]
        if left_out:
            lines.append(f"⚠️ Non inseguite (massimo {max_w} insieme): " + html.escape(", ".join(c.label for c in left_out)))
        if unknown:
            lines.append("❓ Non capiti: " + html.escape(" ".join(unknown)))
        stop = f"{cards[0].set_id}:{cards[0].number}"  # vale anche se nel frattempo cambi collezione corrente
        return Reply("\n".join(lines), buttons=[[("⏹ Ferma", f"/insegui stop {stop}"), ("🏃 Attivi", "/insegui")]])

    def _search_cards(self, args: str) -> Reply:
        """Ricerca mirata, subito, di una o più carte (max 3): cosa c'è in vendita adesso."""
        from .search import search_card
        cards, unknown = self.resolve(args)
        if not cards:
            return Reply("Carta non riconosciuta. Es. <code>/cerca 145</code> oppure <code>/cerca c4</code>.")
        settings = self._settings()
        out: list[str] = []
        for card in cards[:3]:
            items, errors = search_card(self.index, self.db, card, settings)
            head = f"🔎 <b>{html.escape(card.label)}</b> · in vendita adesso: {len(items)}"
            if not items:
                out.append(head + "\nNiente al momento" + (" (" + ", ".join(errors) + " non raggiungibile)" if errors else "") + ".")
                continue
            lines = [head]
            for i, (lst, res) in enumerate(items, start=1):
                src = pstats.SOURCE_LABELS.get(lst.source, lst.source)
                price = html.escape(lst.price_text or (f"{lst.price:.2f} €" if lst.price is not None else "prezzo n.d."))
                tag = " 📦 lotto" if res.kind == "lot" else ""
                loc = f" · {html.escape(lst.location)}" if lst.location else ""
                lines.append(f'{i}. <b>{price}</b> · {html.escape(src)}{loc}{tag}\n    <a href="{html.escape(lst.url, quote=True)}">{html.escape(lst.title[:70])}</a>')
            if errors:
                lines.append("⚠️ Fonte non raggiungibile: " + html.escape(", ".join(errors)))
            out.append("\n".join(lines))
        if len(cards) > 3:
            out.append(f"(mostrate 3 carte su {len(cards)}: una ricerca mirata alla volta è più precisa)")
        if unknown:
            out.append("❓ Non capiti: " + html.escape(" ".join(unknown)))
        code = self.index.code_of[cards[0].id]
        return Reply("\n\n".join(out), buttons=[[("💶 Prezzi", f"/prezzi {code}"), ("📂 Storico", f"/storico {code}")]])

    def _all_price_rows(self) -> list[dict]:
        """Annunci trovati più i punti prezzo, senza contare due volte lo stesso annuncio."""
        found = self.db.list_found()
        keys = {r["listing_key"] for r in found}
        return pstats.PriceRows(found + [r for r in self.db.price_rows() if r["listing_key"] not in keys])

    def _value(self) -> Reply:
        cur = self.current()
        cards = [c for s_ in self.scope() for c in s_.cards]
        v = pstats.collection_value(cards, self._wanted(), self._all_price_rows(), cardmarket.all_prices(self.db))
        lines = [f"💎 <b>{html.escape(cur.name)}</b>",
                 f"Possiedi {v.owned} carte: valore <b>{pstats.fmt_eur(v.owned_value)}</b> al trend Cardmarket "
                 f"({v.owned_priced} carte con prezzo" + (f", {v.owned - v.owned_priced} ancora senza" if v.owned > v.owned_priced else "") + ")"]
        if v.missing:
            lines.append(f"Mancano {v.missing}: per finirla circa <b>{pstats.fmt_eur(v.missing_cost)}</b> al trend Cardmarket"
                         + (f" ({v.missing - v.missing_priced} senza prezzo)" if v.missing > v.missing_priced else ""))
        lines.append("\n<i>Prezzi Cardmarket aggiornati ogni giorno; per le carte che Cardmarket non copre, gli annunci visti dal bot.</i>")
        return Reply("\n".join(lines), buttons=[[("🛒 Lista della spesa", "/spesa"), ("💶 Prezzi", "/prezzi")]])

    def _shopping(self) -> Reply:
        from . import shopping
        cur = self.current()
        cards = [c for s_ in self.scope() for c in s_.cards]
        sl = shopping.build(cards, self._wanted(), self.db.list_found())
        if not sl.picks:
            return Reply(f"🛒 {html.escape(cur.name)}: nessun annuncio recente per le carte mancanti. Il bot continua a cercare.")
        lines = [f"🛒 <b>Lista della spesa · {html.escape(cur.name)}</b>",
                 f"{sl.covered} carte su {sl.covered + len(sl.uncovered)} mancanti, totale <b>{pstats.fmt_eur(sl.total)}</b> "
                 f"(annunci degli ultimi 14 giorni, raggruppati per venditore)"]
        cm = cardmarket.all_prices(self.db)
        ref_tot = sum((cm[c.id].ref or 0) for p in sl.picks for c in p.cards if c.id in cm)
        if ref_tot:
            lines.append(f"Al trend Cardmarket le stesse carte valgono {pstats.fmt_eur(ref_tot)}.")
        for key, picks in sl.by_seller():
            tot = sum(p.price for p in picks)
            n = sum(len(p.cards) for p in picks)
            lines.append(f"\n<b>{html.escape(sl.seller_label(key))}</b> · {n} cart{'a' if n == 1 else 'e'} · {pstats.fmt_eur(tot)}")
            for p in picks:
                names = ", ".join(c.label for c in p.cards)
                tag = " 📦" if p.row.get("kind") == "lot" else ""
                ref = sum((cm[c.id].ref or 0) for c in p.cards if c.id in cm and cm[c.id].ref)
                note = ""
                if ref and len(p.cards) == 1:
                    v, _ = cardmarket.verdict(p.price, cm[p.cards[0].id])
                    note = f" · CM {pstats.fmt_eur(ref)}" + (f" {cardmarket.VERDICT_LABEL[v].split()[0]}" if v else (" ⚠️ molto sopra Cardmarket" if p.price > ref * cardmarket.FAIR_MAX_RATIO else ""))
                lines.append(f'• {pstats.fmt_eur(p.price)}{tag}{note} <a href="{html.escape(p.row["url"], quote=True)}">{html.escape(names[:90])}</a>')
        if sl.uncovered:
            lines.append("\n❌ Senza annuncio recente: " + ", ".join(html.escape(c.label) for c in sl.uncovered[:15])
                         + (f" e altre {len(sl.uncovered) - 15}" if len(sl.uncovered) > 15 else ""))
        return Reply("\n".join(lines), buttons=[[("💎 Valore", "/valore"), ("🃏 Mancanti", "/mancanti")]])

    def _copies(self, args: str) -> Reply:
        a = args.strip()
        copies = self.db.copies(self.chat)
        if not a:
            mine = [(self.index.by_id[cid], n) for cid, n in copies.items() if cid in self.index.by_id]
            if not mine:
                return Reply("🔁 Nessun doppione segnato. Es. <code>/doppioni 131 132</code> (una copia in più ciascuna), "
                             "<code>/doppioni togli 131</code>, <code>/doppioni azzera</code>.")
            rows = self._all_price_rows()
            cm = cardmarket.all_prices(self.db)
            lines = [f"🔁 <b>Doppioni</b> ({sum(n for _, n in mine)} copie in più)"]
            tot = 0.0
            for c, n in sorted(mine, key=lambda t: t[0].sort_key):
                p = cm.get(c.id)
                if p and p.ref:
                    unit = p.ref
                else:
                    st = pstats.card_prices(rows, c).overall
                    unit = st.median if st.n and st.median is not None else None
                val = f" · ~{pstats.fmt_eur(unit)} l'una" if unit else ""
                if unit:
                    tot += unit * n
                lines.append(f"• {html.escape(c.label)} ×{n}{val}")
            if tot:
                lines.append(f"\nValore stimato dei doppioni: <b>{pstats.fmt_eur(tot)}</b>")
            lines.append("/scambio per il messaggio di scambio pronto da inviare")
            return Reply("\n".join(lines))
        verb, _, rest = a.partition(" ")
        if verb.lower() in ("azzera", "reset"):
            self.db.set_kv(f"copies:{self.chat}", {})
            return Reply("🔁 Doppioni azzerati.")
        remove = verb.lower() in ("togli", "rimuovi", "meno", "-")
        cards, unknown = self.resolve(rest if remove else a)
        if not cards:
            return Reply("Carta non riconosciuta. Es. <code>/doppioni 131 132</code> oppure <code>/doppioni togli 131</code>.")
        for c in cards:
            self.db.set_copies(c.id, copies.get(c.id, 0) + (-1 if remove else 1), self.chat)
        msg = ("➖ " if remove else "➕ ") + ", ".join(html.escape(c.label) for c in cards[:20])
        if unknown:
            msg += "\n❓ Non capiti: " + html.escape(" ".join(unknown))
        return Reply(msg + "\n/doppioni per l'elenco · /scambio per il messaggio")

    def _swap(self) -> Reply:
        """Messaggio di scambio pronto da copiare: cerco le mancanti della collezione corrente, offro i doppioni."""
        cur = self.current()
        wanted = self._wanted()
        want = [c for s_ in self.scope() for c in s_.cards if c.id in wanted]
        copies = self.db.copies(self.chat)
        offer = [(self.index.by_id[cid], n) for cid, n in copies.items() if cid in self.index.by_id]
        if not want and not offer:
            return Reply("Niente da scambiare: nessuna mancante nella collezione corrente e nessun doppione (/doppioni).")
        lines = [f"🔁 Scambio carte Pokémon · {cur.name}"]
        if want:
            lines.append("CERCO: " + ", ".join(c.label for c in want[:40]) + (f" (+{len(want) - 40})" if len(want) > 40 else ""))
        if offer:
            lines.append("OFFRO (doppioni): " + ", ".join(f"{c.label}" + (f" ×{n}" if n > 1 else "") for c, n in sorted(offer, key=lambda t: t[0].sort_key)))
        lines.append("Scrivetemi in privato, scambio anche più carte insieme.")
        # un <code> per riga: se il messaggio viene spezzato, ogni pezzo resta HTML valido
        return Reply("Copia e incolla dove vuoi:\n\n" + "\n".join(f"<code>{html.escape(ln)}</code>" for ln in lines))

    def _progress(self) -> Reply:
        comp = pstats.completion(self.scope_index(), self._wanted(), pstats.PriceRows(self.db.list_found()), cardmarket.all_prices(self.db))
        filled = round(comp.percent / 10)
        bar = "🟩" * filled + "⬜" * (10 - filled)
        lines = [f"📊 <b>{html.escape(self.current().name)}</b>: {comp.owned}/{comp.total} carte ({comp.percent:.0f}%)", bar]
        if comp.missing:
            lines.append(f"\nMancano {comp.missing}:")
            order = ["Common", "Uncommon", "Rare", "Double Rare", "Pikachu Rare", "Illustration Rare",
                     "Special Illustration Rare", "Futuristic Rare"]
            keys = [k for k in order if k in comp.by_rarity] + sorted(k for k in comp.by_rarity if k not in order)
            for k in keys:
                m, t = comp.by_rarity[k]
                if m:
                    lines.append(f"• {html.escape(k)}: {m}/{t}")
            if comp.priced:
                lines.append(f"\n💶 Stima per finire: <b>{pstats.fmt_eur(comp.cost_median)}</b> al trend Cardmarket"
                             f" ({comp.priced} carte con prezzo" + (f", {len(comp.unpriced)} senza" if comp.unpriced else "") + ")")
        else:
            lines.append("🎉 Collezione completa!")
        return Reply("\n".join(lines), buttons=[[("💶 Prezzi", "/prezzi"), ("🃏 Mancanti", "/mancanti")]])

    def _deals(self, args: str) -> Reply:
        a = args.strip().lower().replace("%", "")
        if a in ("off", "no", "0"):
            self._save({"deal_pct": 0})
            return Reply("🔥 Avvisi affare disattivati.")
        if not a:
            cur = int(self._settings().get("deal_pct", 70) or 0)
            return Reply(f"🔥 Avviso affare: {'spento' if not cur else f'sotto il {cur}% del trend Cardmarket (e sotto il minimo Cardmarket)'}.\n"
                         "Imposta con <code>/affari 70</code> oppure spegni con <code>/affari off</code>.")
        try:
            v = max(10, min(95, int(float(a))))
        except (ValueError, OverflowError):
            return Reply("Serve una percentuale, es. <code>/affari 60</code>, oppure <code>/affari off</code>.")
        self._save({"deal_pct": v})
        return Reply(f"🔥 Avviso affare attivo: ti scrivo subito se una carta mancante esce sotto il {v}% del suo trend Cardmarket "
                     f"e sotto il minimo Cardmarket, per carte da almeno {cardmarket.DEAL_MIN_TREND:.0f} €. "
                     "Per le carte senza prezzo Cardmarket vale la mediana degli annunci visti.")

    def _quiet(self, args: str) -> Reply:
        a = args.strip().lower()
        if a in ("off", "no"):
            self._save({"quiet_hours": None})
            return Reply("🌙 Ore silenziose disattivate.")
        parts = re.findall(r"\d{1,2}", a)
        if len(parts) != 2:
            cur = self._settings().get("quiet_hours")
            desc = f"dalle {cur[0]} alle {cur[1]}" if cur else "nessuna"
            return Reply(f"🌙 Ore silenziose: {desc}.\nImposta con <code>/notte 23 8</code> (accumula e manda al mattino) o <code>/notte off</code>.")
        start, end = int(parts[0]) % 24, int(parts[1]) % 24
        self._save({"quiet_hours": [start, end]})
        return Reply(f"🌙 Dalle {start}:00 alle {end}:00 non ti disturbo: accumulo e ti mando tutto al primo giro dopo le {end}:00.")

    def _export(self) -> Reply:
        from .export import build_workbook
        content = build_workbook(self.index, self.db, self._wanted(), self._my_sets(), self._my_rows())
        name = dt.datetime.now(ZoneInfo(config.TIMEZONE)).strftime("pokebot_%Y%m%d_%H%M.xlsx")
        return Reply("📎 Ecco il file Excel: checklist, storico annunci e prezzi.", document=(name, content))

    # ------------------------------------------------------------------
    def _my_rows(self) -> list[dict]:
        """Annunci dello storico che riguardano le collezioni della persona."""
        mine = {c.id for s_ in self._my_sets() for c in s_.cards}
        return [r for r in self.db.list_found() if any(m["id"] in mine for m in r["matched"])]

    def _history(self, args: str, chat_id: str = "") -> Reply:
        """Cartelle per carta con lo storico degli annunci trovati (solo le collezioni della persona)."""
        arg = args.strip().lower()
        if arg in ("svuota", "cancella", "pulisci"):
            if self._owner_only(chat_id):  # lo storico è uno solo per tutti
                return self._owner_only(chat_id)
            self.db.clear_found()
            return Reply("🗑 Storico svuotato. Gli annunci già visti non verranno comunque rinotificati.")
        rows = self._my_rows()
        if not rows:
            return Reply("Nessun annuncio trovato finora. Lo storico si riempie a ogni giro di ricerca.")
        if not arg:
            return self._history_index(rows)
        if arg in ("lotti", "lotto", "lot"):
            members = [r for r in rows if r["kind"] == "lot"]
            return self._history_detail("📦 Lotti con carte mancanti", members, show_cards=True)
        card = self._one_card(arg)
        if not card:
            return Reply("Carta non riconosciuta. Usa /storico senza argomenti e tocca una cartella.")
        members = [r for r in rows if r["kind"] != "lot" and any(m["id"] == card.id for m in r["matched"])]
        return self._history_detail(f"🃏 {card.label}", members)

    def _history_index(self, rows: list[dict]) -> Reply:
        counts: dict[str, int] = {}
        lots = 0
        for r in rows:
            if r["kind"] == "lot":
                lots += 1
                continue
            for m in r["matched"]:
                counts[m["id"]] = counts.get(m["id"], 0) + 1
        cards = sorted((self.index.by_id[cid] for cid in counts if cid in self.index.by_id), key=lambda c: c.sort_key)
        lines = [f"📂 <b>Storico annunci</b> · {len(rows)} trovati in totale", "Tocca una cartella per vedere gli annunci divisi per marketplace."]
        buttons: list[list[tuple[str, str]]] = []
        row: list[tuple[str, str]] = []
        for c in cards:
            code = self.index.code_of[c.id]
            row.append((f"{code} {c.name} · {counts[c.id]}", f"/storico {code}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons = buttons[:48]  # Telegram accetta un numero limitato di righe di pulsanti
        if lots:
            buttons.append([(f"📦 Lotti · {lots}", "/storico lotti")])
        return Reply("\n".join(lines), buttons=buttons)

    def _history_detail(self, title: str, members: list[dict], show_cards: bool = False, per_source: int = 12) -> Reply:
        from .scrapers.base import parse_price
        if not members:
            return Reply(f"<b>{html.escape(title)}</b>\nNessun annuncio in questa cartella.", buttons=[[("⬅️ Cartelle", "/storico")]])
        lines = [f"<b>{html.escape(title)}</b> · {len(members)} annunci trovati"]
        by_source: dict[str, list[dict]] = {}
        for r in members:
            by_source.setdefault(r["source"], []).append(r)
        order = [s for s in pstats.SOURCE_ORDER if s in by_source] + [s for s in by_source if s not in pstats.SOURCE_ORDER]
        for src in order:
            items = by_source[src]
            items.sort(key=lambda r: (parse_price(r.get("price")) or float("inf"), -r["created_at"]))
            lines.append(f"\n<b>{html.escape(pstats.SOURCE_LABELS.get(src, src))}</b> ({len(items)})")
            for r in items[:per_source]:
                when = dt.datetime.fromtimestamp(r["created_at"], ZoneInfo(config.TIMEZONE)).strftime("%d/%m")
                price = html.escape(r.get("price") or "n.d.")
                extra = ""
                if show_cards:
                    labels = [m["label"] for m in r["matched"][:3]]
                    extra = " · " + html.escape(", ".join(labels)) + (" …" if len(r["matched"]) > 3 else "")
                lines.append(f'• {price} · {when} · <a href="{html.escape(r["url"], quote=True)}">{html.escape(r["title"][:60])}</a>{extra}')
            if len(items) > per_source:
                lines.append(f"  … e altri {len(items) - per_source}")
        return Reply("\n".join(lines), buttons=[[("⬅️ Cartelle", "/storico")]])

    def _card_line(self, c: Card) -> str:
        code = self.index.code_of[c.id]
        label = f"{code} · {c.name}" if c.printed_total else f"{code} · {c.name} #{c.number}"
        return html.escape(f"{label} ({c.rarity})")

    def _fmt_missing(self) -> str:
        wanted = self._wanted()
        scope = self.scope()
        in_scope = [c for s in scope for c in s.cards if c.id in wanted]
        lines = []
        if not in_scope:
            lines.append(f"🃏 <b>{html.escape(self.current().name)}</b>: nessuna carta mancante. "
                         "Usa /aggiungi (es. <code>/aggiungi tutte</code>) o /lista.")
        else:
            lines.append(f"🃏 <b>Carte mancanti: {len(in_scope)}/{sum(len(s.cards) for s in scope)}</b>")
            for s in scope:
                ordered = sorted(s.cards, key=lambda c: c.sort_key) if s.printed_total else s.cards
                cs = [c for c in ordered if c.id in wanted]
                if cs:
                    lines.append(f"\n<b>{html.escape(s.name)}</b> ({len(cs)})")
                    lines += [self._card_line(c) for c in cs]
        others = [(s, sum(1 for c in s.cards if c.id in wanted)) for s in self._my_sets() if s not in scope]
        if others:
            lines.append("\n📚 Altre collezioni: " + " · ".join(f"{html.escape(s.name)} {n}/{len(s.cards)}" for s, n in others)
                         + "\n/collezione &lt;id&gt; per passare a una di loro")
        return "\n".join(lines)

    def _fmt_list(self, args: str) -> str:
        want_classic = "classic" in args.lower()
        wanted = self._wanted()
        lines = []
        scope = self.scope()
        # la divisione "set principale / Classic" serve solo se la collezione ha entrambe le parti
        if not (any(s.printed_total for s in scope) and any(not s.printed_total for s in scope)):
            want_classic = None
        for s in scope:
            if want_classic is not None and bool(s.printed_total) == want_classic:
                continue
            lines.append(f"<b>{html.escape(s.name)}</b> – {len(s.cards)} carte (✗ = ti manca)")
            ordered = sorted(s.cards, key=lambda c: c.sort_key) if s.printed_total else s.cards
            for c in ordered:
                mark = "✗ " if c.id in wanted else ""
                lines.append(mark + self._card_line(c))
        if want_classic is False:
            lines.append("\nPer la Classic Collection: /lista classic")
        return "\n".join(lines)

    def _fmt_status(self) -> str:
        s = self._settings()
        runs = self.db.last_runs(1)
        max_price = s["max_price"]
        price_txt = "nessuno" if not max_price else f"{max_price:g} €"
        qh = s.get("quiet_hours")
        flags = []
        if s.get("paused"):
            flags.append("⏸ in pausa")
        if qh:
            flags.append(f"🌙 notte {qh[0]}-{qh[1]}")
        flags.append(f"🔥 affari {'off' if not s.get('deal_pct') else str(int(s['deal_pct'])) + '%'}")
        flags.append(f"🖼 immagini {'on' if s.get('images', True) else 'off'}")
        from .watch import list_watches
        if list_watches(self.db, self.chat):
            flags.append(f"🏃 inseguimenti {len(list_watches(self.db, self.chat))}")
        active = set(self.db.active_sets(self.chat))
        wanted = self._wanted()
        act_cards = [c for s_ in self._my_sets() if s_.id in active for c in s_.cards]
        flags.insert(0, f"📚 {html.escape(self.current().name)} corrente")
        owner = self._is_owner(self.chat)
        lines = [f"🃏 Mancanti nelle collezioni cercate: <b>{sum(1 for c in act_cards if c.id in wanted)}</b>/{len(act_cards)} · " + " · ".join(flags),
                 f"⚙️ Max {int(s.get('max_per_card', 5))} annunci per carta · soglia lotti {s['lot_min_ratio'] * 100:.0f}% · "
                 f"prezzo max {price_txt} · lingua: {s.get('language', 'ita')}"
                 + (f" · ricerca ogni {int(s['interval_minutes'])} min · fonti: {', '.join(s['sources'])}" if owner else "")]
        if runs:
            r = runs[0]
            when = dt.datetime.fromtimestamp(r["started_at"], ZoneInfo(config.TIMEZONE)).strftime("%d/%m %H:%M")
            lines.append(f"🕒 Ultimo ciclo {when}: {r['listings_seen']} annunci letti, {r['matches']} segnalati")
            per_source = r.get("per_source") or {}
            if per_source:
                lines.append("📊 Letti per fonte: " + " · ".join(
                    f"{pstats.SOURCE_LABELS.get(k, k)} {v}" for k, v in per_source.items()))
            errors = r["errors"] or {}
            if owner:  # i dettagli degli errori (e le ricerche fatte per tutti) solo al proprietario
                for k, v in errors.items():
                    lines.append(f"⚠️ {html.escape(k)}: {html.escape(str(v)[:200])}")
            elif errors:
                lines.append("⚠️ Qualche marketplace non ha risposto all'ultimo giro: riprovo al prossimo.")
        else:
            lines.append("🕒 Nessun ciclo ancora eseguito.")
        qs = (self.db.get_kv("query_stats", {}) or {}) if owner else {}
        if qs:
            lines.append("🔤 Query generiche (giri · nuovi · segnalati): " + " · ".join(
                f"<i>{html.escape(q)}</i> {v['runs']}/{v['new']}/{v['matches']}" for q, v in sorted(qs.items(), key=lambda kv: -kv[1]["matches"])))
        return "\n".join(lines)


def _is_start(text: str) -> bool:
    return text.strip().split()[0].split("@", 1)[0].lower() == "/start" if text.strip() else False


class TelegramCommands:
    """Legge i messaggi in arrivo, li esegue e risponde. Il chat id viene salvato al primo /start."""

    def __init__(self, index: CardIndex, db: Database, client: TelegramClient | None = None):
        self.index = index
        self.db = db
        self.client = client or TelegramClient()
        self.handler = CommandHandler(index, db)

    @property
    def enabled(self) -> bool:
        return bool(self.client.token)

    def ensure_menu(self) -> None:
        """Registra il menu comandi su Telegram (una volta sola per versione del menu)."""
        if not self.enabled:
            return
        if self.db.get_kv("telegram_profile_version") != PROFILE_VERSION and hasattr(self.client, "set_profile"):
            try:
                if self.client.set_profile(BOT_DESCRIPTION.format(trial=plans.TRIAL_DAYS), BOT_SHORT_DESCRIPTION):
                    self.db.set_kv("telegram_profile_version", PROFILE_VERSION)
                    log.info("Presentazione del bot registrata")
            except requests.RequestException as exc:
                log.warning("Telegram setMyDescription: %s", exc)
        owner = self.db.owner_chat_id()
        version = f"{MENU_VERSION}:{owner}"  # il menu del proprietario va rifatto se cambia il proprietario
        if self.db.get_kv("telegram_menu_version") == version:
            return
        try:
            if self.client.set_my_commands(MENU_COMMANDS) and (not owner or self.client.set_my_commands(OWNER_MENU_COMMANDS, owner)):
                self.db.set_kv("telegram_menu_version", version)
                log.info("Menu comandi Telegram registrato")
        except requests.RequestException as exc:
            log.warning("Telegram setMyCommands: %s", exc)

    def poll_once(self, timeout: int = 0) -> bool:
        """Elabora i messaggi nuovi. Restituisce True se qualcuno ha chiesto /cerca."""
        if not self.enabled:
            return False
        self.ensure_menu()
        offset = self.db.get_kv("telegram_offset")
        info: dict = {}
        if timeout == 0:  # esecuzione singola (GitHub Actions): diagnostica nel log
            try:
                info = self.client.webhook_info()
            except (requests.RequestException, ValueError) as exc:
                log.warning("Telegram getWebhookInfo: %s", exc)
            if info.get("url"):
                log.info("Telegram: webhook attivo (%s): i comandi arrivano dal ponte, lettura diretta saltata", info["url"])
                return False
        try:
            updates = self.client.get_updates(offset, timeout=timeout)
        except requests.RequestException as exc:
            log.warning("Telegram getUpdates: %s", exc)
            return False
        if timeout == 0:
            try:
                me = self.client.get_me()
            except (requests.RequestException, ValueError, KeyError) as exc:
                me = f"? ({exc})"
            log.info("Telegram: bot @%s, %d messaggi nuovi (offset %s), in coda su Telegram: %s, ultimo errore webhook: %s",
                     me, len(updates), offset, info.get("pending_update_count", "?"), info.get("last_error_message", "-"))
        want_search = False
        for upd in updates:
            self.db.set_kv("telegram_offset", int(upd["update_id"]) + 1)
            cb = upd.get("callback_query")
            if cb:  # pulsante toccato: il dato del pulsante è un comando
                self.client.answer_callback(str(cb.get("id", "")))
                msg = cb.get("message") or {}
                text = cb.get("data") or ""
            else:
                msg = upd.get("message") or {}
                text = msg.get("text") or ""
            chat_id = str((msg.get("chat") or {}).get("id") or "")
            frm = (cb.get("from") if cb else msg.get("from")) or {}
            want_search = self._process(chat_id, text, str(frm.get("first_name") or "")) or want_search
        return want_search

    def _process(self, chat_id: str, text: str, name: str) -> bool:
        """Un messaggio: chi non è autorizzato può solo chiedere l'accesso con /start. True se chiede la ricerca."""
        if not chat_id or not text:
            return False
        name = name[:40]
        if not self._authorized(chat_id, text):
            if _is_start(text):
                self._deliver(chat_id, self._signup(chat_id, text, name))
            else:
                log.info("Messaggio ignorato da una chat non autorizzata")
            return False
        if name:
            self.db.set_user_name(chat_id, name)
        reply = self.handler.handle(text, chat_id)
        self._deliver(chat_id, reply)
        return reply.run_search

    def _deliver(self, chat_id: str, reply: Reply) -> None:
        try:
            self.client.send(chat_id, reply.text, reply.buttons)
            if reply.document:
                self.client.send_document(chat_id, reply.document[0], reply.document[1])
            for other, text, buttons in (reply.sends or []):
                self.client.send(other, text, buttons)
        except requests.RequestException as exc:
            log.warning("Telegram invio: %s", exc)

    def handle_payload(self, payload: dict | None) -> bool:
        """Comando arrivato tramite repository_dispatch (ponte Telegram → GitHub). True se chiede /cerca."""
        if not isinstance(payload, dict):
            return False
        chat_id = str(payload.get("chat_id") or "")
        text = str(payload.get("text") or "")
        if chat_id and isinstance(payload.get("payment"), dict):  # scritto solo dal ponte, mai dal testo dell'utente
            self._payment(chat_id, payload["payment"], str(payload.get("name") or ""))
            return False
        return self._process(chat_id, text, str(payload.get("name") or ""))

    def _payment(self, chat_id: str, pay: dict, name: str) -> None:
        if pay.get("currency") != "XTR" or not str(pay.get("invoice_payload", "")).startswith("pro:"):
            log.warning("Pagamento ignorato: non è l'abbonamento")
            return
        owner = self.db.owner_chat_id()
        if self.db.is_banned(chat_id):  # espulso: le Stars tornano indietro e l'abbonamento si ferma
            try:
                ok, _ = self.client.refund_stars(chat_id, str(pay.get("telegram_payment_charge_id") or ""))
            except (requests.RequestException, ValueError):
                ok = False
            if owner:
                self._deliver(owner, Reply(f"⛔ Pagamento da un account espulso (<code>{chat_id}</code>): "
                                           + ("rimborsato." if ok else "rimborso non riuscito, controlla in Telegram.")))
            return
        if name:
            self.db.set_user_name(chat_id, name[:40])
        p = plans.record_payment(self.db, chat_id, pay)
        if p is None:
            log.info("Pagamento già registrato: ignorato")
            return
        if chat_id not in self.db.chat_ids():  # ha pagato prima di essere approvato (link girato da altri): entra subito
            self.db.remove_from_waitlist(chat_id)
            self.db.add_chat_id(chat_id)
            plans.set_onboarding(self.db, chat_id, True)  # come chi viene approvato: prima l'album, poi le notifiche
            text, buttons = plans.welcome_message()
            self._deliver(chat_id, Reply(text, buttons=buttons))
        renewal = bool(pay.get("is_recurring")) and not pay.get("is_first_recurring")
        log.info("Pagamento Stars registrato (%s stelle, rinnovo: %s)", pay.get("total_amount"), renewal)
        self._deliver(chat_id, Reply(
            ("🔁 Abbonamento rinnovato, grazie!" if renewal else "🎉 <b>Grazie!</b> Ora hai Pokébot completo: avvisi immediati, affari 🔥, "
             "inseguimenti ogni 5 minuti e collezioni illimitate.") + f"\n{plans.describe(self.db, chat_id)}"))
        if owner and owner != chat_id:
            self._deliver(owner, Reply(f"💰 {'Rinnovo' if renewal else 'Nuovo abbonamento'}: {html.escape(self.db.user_name(chat_id))} "
                                       f"(<code>{chat_id}</code>) · {int(pay.get('total_amount') or 0)} ⭐ · fino al {plans._date(p.get('pro_until'))}"))

    def _signup(self, chat_id: str, text: str, name: str) -> Reply:
        """Sconosciuto con /start (senza codice valido): lista d'attesa. `/start canale` dice da dove arriva."""
        parts = text.strip().split()
        source = parts[1][:20] if len(parts) > 1 else ""
        if name:
            self.db.set_user_name(chat_id, name)
        return self.handler.waitlist_signup(chat_id, name, source)

    def _authorized(self, chat_id: str, text: str) -> bool:
        """Persone collegate (proprietario, approvate dalla lista d'attesa, entrate con un invito). Un `/start CODICE`
        con il codice d'invito valido fa entrare subito; il primo /start in assoluto crea il proprietario."""
        chat_id = str(chat_id)
        allowed = self.db.chat_ids()
        if chat_id in allowed:
            return True
        if not _is_start(text):
            return False
        parts = text.strip().split()
        if not allowed:
            self.db.set_kv("telegram_chat_id", chat_id)  # il primo che scrive /start diventa il proprietario
            log.info("Proprietario collegato: %s", masked(chat_id))
            return True
        if len(parts) < 2 or self.db.is_banned(chat_id):  # chi è stato espulso rientra solo con /approva
            return False
        inv = self.db.get_kv("invite_code") or {}
        if (isinstance(inv, dict) and inv.get("code") and parts[1].upper() == str(inv["code"]).upper()
                and float(inv.get("expires") or 0) > time.time()):
            self.db.remove_from_waitlist(chat_id)
            self.db.add_chat_id(chat_id)
            plans.start_trial(self.db, chat_id)
            plans.set_onboarding(self.db, chat_id, True)
            self.db.set_kv("invite_code", None)  # monouso
            log.info("Nuova persona collegata con invito: %s", masked(chat_id))
            return True
        return False
