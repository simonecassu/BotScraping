"""Comandi Telegram: il bot si configura e si comanda direttamente dalla chat.

Al primo /start salva il chat id (niente getUpdates a mano). Poi:
  /mancanti, /aggiungi 131 132 149-152 c4, /rimuovi ..., /lista, /stato, /soglia 50, /prezzo 100, /cerca ...
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from dataclasses import dataclass, field

import requests

from . import config
from .cards import Card, CardIndex, normalize
from .db import Database

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
/mancanti – elenco carte che ti mancano
/aggiungi 131 132 149-152 c4 – segna come mancanti (numeri, intervalli, codici c1..c30 per la Classic)
/aggiungi ir | sir | pr | pikachu | tutte | classic – per rarità, per nome o tutto il set
/rimuovi 131 ... – l'hai trovata: toglila (anche /ho 131)
/lista – tutte le carte del set con i numeri  ·  /lista classic – la Classic Collection
/stato – ultimo ciclo, errori, impostazioni
/storico – cartelle per carta: tutti gli annunci trovati finora, divisi per eBay / Vinted / Wallapop
/prezzi 145 – min / mediana / max visti per la carta, per marketplace · /prezzi – quanto costa finire il set
/progresso – avanzamento del set, mancanti per rarità, stima di spesa
/affari 60 – avviso 🔥 immediato se un prezzo è sotto il 60% della mediana storica · /affari off
/pausa · /riprendi – sospendi/riattiva le notifiche (il bot accumula) · /notte 23 8 – ore silenziose
/esporta – file Excel con checklist, storico e prezzi · /immagini on|off – foto della carta nei messaggi
/soglia 50 – % minima di carte mancanti perché un lotto venga segnalato
/prezzo 100 – prezzo massimo in € (0 = nessun limite)
/fonti wallapop vinted ebay – quali marketplace usare
/lingua ita – scarta annunci in francese/inglese/altre lingue · /lingua tutte – accetta tutto
/intervallo 20 – ogni quanti minuti fare la ricerca
/max 5 – per ogni carta, quanti annunci (i più economici) ricevere a ogni giro
/cerca – ricerca immediata · /cerca 145 – una carta: tutto ciò che è in vendita adesso, dal più economico
/insegui 151 – per 6 ore cerca la 151 ogni 5 minuti e ti avvisa appena spunta un annuncio nuovo
/insegui 151 2h · /insegui 151 2h ogni 10m · /insegui 131 151 – durata, frequenza, più carte · /insegui – attivi · /insegui stop
/invita – codice per collegare un'altra persona (stesse notifiche, stessa checklist, stessa app) · /utenti · /espelli ID
/collezione – altre collezioni (partono da "mi mancano tutte") · /collezione sv8 ho 4 7 – segna le possedute · /collezione sv8 attiva – ⚠️ la cerca anche · /collezione me55 disattiva – spegne la 30th
/resetvisti – rinotifica anche gli annunci già visti
Puoi scrivere più comandi in un solo messaggio, uno per riga."""


# Menu comandi mostrato da Telegram toccando "/" (registrato automaticamente dal bot)
MENU_VERSION = 13
MENU_COMMANDS = [
    ("mancanti", "Carte che ti mancano"),
    ("aggiungi", "Segna mancanti: /aggiungi 131 132 149-152 c4 (anche ir, sir, tutte)"),
    ("rimuovi", "Trovata! Toglila: /rimuovi 131"),
    ("lista", "Tutte le carte con i numeri (/lista classic per la Classic)"),
    ("stato", "Ultimo giro, errori, impostazioni"),
    ("storico", "Cartelle per carta con tutti gli annunci trovati, divisi per marketplace"),
    ("prezzi", "Prezzi visti per una carta (/prezzi 145) o costo per finire il set (/prezzi)"),
    ("progresso", "Avanzamento del set, mancanti per rarita', stima di spesa"),
    ("affari", "Avviso immediato se un prezzo e' sotto la % della mediana: /affari 60, /affari off"),
    ("pausa", "Sospende le notifiche (continua a cercare e accumula)"),
    ("riprendi", "Riattiva le notifiche e invia quanto accumulato"),
    ("notte", "Ore silenziose: /notte 23 8 (accumula e invia al mattino), /notte off"),
    ("esporta", "File Excel con checklist, storico annunci e prezzi"),
    ("immagini", "Immagine della carta nei messaggi: /immagini on | off"),
    ("cerca", "Cerca subito tutto, oppure una carta mirata: /cerca 145"),
    ("insegui", "Cerca una carta ogni 5 minuti per 6 ore: /insegui 151 (o /insegui 151 2h ogni 10m)"),
    ("invita", "Codice per collegare un'altra persona alle stesse notifiche"),
    ("utenti", "Chi e' collegato al bot"),
    ("collezione", "Altre collezioni: /collezione sv8 ho 4 7 (possedute), /collezione sv8 attiva"),
    ("intervallo", "Ogni quanti minuti cercare: /intervallo 20"),
    ("max", "Quanti annunci (i piu' economici) per carta in ogni giro: /max 5"),
    ("soglia", "Percentuale minima di carte mancanti nei lotti: /soglia 50"),
    ("prezzo", "Prezzo massimo in euro: /prezzo 100 (0 = nessun limite)"),
    ("fonti", "Marketplace da usare: /fonti wallapop vinted ebay"),
    ("lingua", "Solo carte italiane (/lingua ita) oppure tutte le lingue (/lingua tutte)"),
    ("resetvisti", "ATTENZIONE: rinotifica tutti gli annunci gia' visti (chiede conferma)"),
    ("aiuto", "Elenco dei comandi"),
]


@dataclass
class Reply:
    text: str
    run_search: bool = False
    buttons: list[list[tuple[str, str]]] | None = None  # righe di pulsanti (etichetta, comando)
    document: tuple[str, bytes] | None = None  # (nome file, contenuto) da inviare come allegato


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

    def set_my_commands(self, commands: list[tuple[str, str]]) -> bool:
        resp = requests.post(f"{self.base}/setMyCommands",
                             json={"commands": [{"command": c, "description": d[:256]} for c, d in commands]},
                             timeout=config.HTTP_TIMEOUT)
        return resp.status_code == 200 and bool(resp.json().get("ok"))

    def send(self, chat_id: str | int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
        chunks = _chunks(text)
        for i, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
            if buttons and i == len(chunks) - 1:
                payload["reply_markup"] = {"inline_keyboard": [[{"text": lbl, "callback_data": data[:64]} for lbl, data in row]
                                                               for row in buttons]}
            requests.post(f"{self.base}/sendMessage", json=payload, timeout=config.HTTP_TIMEOUT)

    def send_document(self, chat_id: str | int, filename: str, content: bytes, caption: str = "") -> None:
        requests.post(f"{self.base}/sendDocument", data={"chat_id": chat_id, "caption": caption[:1024]},
                      files={"document": (filename, content)}, timeout=config.HTTP_TIMEOUT * 2)

    def answer_callback(self, callback_id: str) -> None:
        try:
            requests.post(f"{self.base}/answerCallbackQuery", json={"callback_query_id": callback_id}, timeout=config.HTTP_TIMEOUT)
        except requests.RequestException:
            pass


def _chunks(text: str) -> list[str]:
    if len(text) <= MAX_LEN:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > MAX_LEN:
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

    # ------------------------------------------------------------------
    def handle(self, text: str, chat_id: str = "") -> Reply:
        """Un messaggio può contenere più comandi, uno per riga."""
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if len(lines) <= 1:
            return self._handle_one(lines[0] if lines else "", chat_id)
        replies = [self._handle_one(ln, chat_id) for ln in lines]
        return Reply("\n\n".join(r.text for r in replies), run_search=any(r.run_search for r in replies))

    def _handle_one(self, text: str, chat_id: str = "") -> Reply:
        text = (text or "").strip()
        if not text.startswith("/"):
            return Reply("Scrivimi un comando, ad esempio /mancanti. Con /aiuto vedi l'elenco.")
        cmd, _, args = text.partition(" ")
        cmd = cmd.split("@", 1)[0].lower()
        args = args.strip()
        if cmd == "/start":
            return Reply("✅ Collegato! Da adesso ti mando qui gli annunci delle carte mancanti.\n\n" + HELP)
        if cmd in ("/aiuto", "/help"):
            return Reply(HELP)
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
            return self._history(args)
        if cmd in ("/prezzi", "/prezzo_carta"):
            return self._prices(args)
        if cmd in ("/progresso", "/avanzamento"):
            return self._progress()
        if cmd in ("/affari", "/affare"):
            return self._deals(args)
        if cmd in ("/pausa", "/stop"):
            self.db.save_settings({"paused": True})
            return Reply("⏸ Notifiche in pausa. Continuo a cercare e accumulo: con /riprendi ti mando tutto in un colpo.")
        if cmd in ("/riprendi", "/play", "/riparti"):
            self.db.save_settings({"paused": False})
            return Reply("▶️ Notifiche riattivate. Se c'è qualcosa in coda, arriva tra pochi secondi.")
        if cmd in ("/notte", "/silenzio"):
            return self._quiet(args)
        if cmd in ("/esporta", "/export", "/excel"):
            return self._export()
        if cmd in ("/immagini", "/foto"):
            a = args.strip().lower()
            if a in ("on", "si", "sì", "attiva"):
                self.db.save_settings({"images": True})
                return Reply("🖼 Immagine della carta attiva nei messaggi.")
            if a in ("off", "no", "disattiva"):
                self.db.save_settings({"images": False})
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
            return self._set_number(args, "interval_minutes", lambda v: max(5, int(v)),
                                    lambda v: f"Ricerca ogni {v} minuti.")
        if cmd == "/fonti":
            return self._set_sources(args)
        if cmd == "/lingua":
            a = args.strip().lower()
            if a in ("ita", "it", "italiano", "italiana"):
                self.db.save_settings({"language": "ita"})
                return Reply("⚙️ Solo annunci italiani: scarto quelli dichiaratamente in francese, inglese, tedesco, spagnolo o giapponese.")
            if a in ("tutte", "tutto", "any", "all"):
                self.db.save_settings({"language": "tutte"})
                return Reply("⚙️ Accetto annunci in qualsiasi lingua.")
            return Reply("Usa <code>/lingua ita</code> oppure <code>/lingua tutte</code>.")
        if cmd == "/cerca":
            if args.strip():
                return self._search_cards(args)
            return Reply("🔎 Ok, cerco adesso.", run_search=True)
        if cmd == "/insegui":
            return self._chase(args)
        if cmd == "/invita":
            return self._invite(chat_id)
        if cmd == "/utenti":
            return self._users(chat_id)
        if cmd in ("/espelli", "/rimuoviutente"):
            return self._kick(chat_id, args)
        if cmd in ("/collezione", "/collezioni", "/set"):
            return self._collection(args)
        if cmd == "/resetvisti":
            if args.strip().lower() != "conferma":
                return Reply("⚠️ Questo rinotifica <b>tutti</b> gli annunci già visti (possono essere centinaia).\n"
                             "Se sei sicuro scrivi <code>/resetvisti conferma</code>.")
            self.db.forget_seen()
            return Reply("♻️ Memoria azzerata: al prossimo ciclo rinotifico anche gli annunci già visti "
                         "(al massimo 10 carte per giro, il resto finisce nello storico).")
        return Reply("Comando sconosciuto. /aiuto per l'elenco.")

    # ------------------------------------------------------------------
    def resolve(self, args: str) -> tuple[list[Card], list[str]]:
        """Trasforma '131 132 149-152 c4 ir pikachu' in carte. Restituisce (carte, token non capiti)."""
        cards: dict[str, Card] = {}
        unknown: list[str] = []
        main_sets = [s for s in self.index.sets if s.printed_total]
        tokens = [t for t in re.split(r"[\s,;]+", args.strip().lower()) if t]
        i = 0
        while i < len(tokens):
            t = tokens[i]
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            if t in ("tutte", "tutto", "all"):
                if nxt in ("classic", "classica"):
                    i += 1
                    for s in self.index.sets:
                        if not s.printed_total:
                            cards.update({c.id: c for c in s.cards})
                else:
                    for s in main_sets:
                        cards.update({c.id: c for c in s.cards})
            elif t in ("classic", "classica"):
                for s in self.index.sets:
                    if not s.printed_total:
                        cards.update({c.id: c for c in s.cards})
            elif t in RARITY_SHORTCUTS:
                rar = RARITY_SHORTCUTS[t]
                cards.update({c.id: c for c in self.index.by_id.values() if c.rarity == rar})
            elif re.fullmatch(r"\d{1,3}-\d{1,3}", t):
                a, b = (int(x) for x in t.split("-"))
                for n in range(min(a, b), max(a, b) + 1):
                    c = self.index.by_code.get(str(n))
                    if c:
                        cards[c.id] = c
            elif t in self.index.by_code:
                c = self.index.by_code[t]
                cards[c.id] = c
            else:
                # nome di carta (anche più parole: "pikachu ex")
                phrase = t
                if nxt and f"{t} {nxt}" in self.index.names:
                    phrase = f"{t} {nxt}"
                    i += 1
                hits = [c for c in self.index.by_id.values() if normalize(c.name) == phrase]
                if not hits:
                    hits = [c for c in self.index.by_id.values() if phrase in normalize(c.name)]
                if hits:
                    cards.update({c.id: c for c in hits})
                else:
                    unknown.append(t)
            i += 1
        return sorted(cards.values(), key=lambda c: c.sort_key), unknown

    def _change(self, args: str, wanted: bool) -> Reply:
        if not args:
            return Reply("Indica cosa: es. <code>/aggiungi 131 132 149-152 c4</code>, <code>/aggiungi ir</code>, <code>/aggiungi pikachu ex</code>.")
        cards, unknown = self.resolve(args)
        if not cards:
            return Reply("Non ho riconosciuto nessuna carta in: " + html.escape(args) + "\n/lista per vedere i numeri.")
        self.db.set_wanted_bulk([c.id for c in cards], wanted)
        verb = "segnate come mancanti" if wanted else "tolte dalle mancanti"
        lines = [f"{'➕' if wanted else '✅'} {len(cards)} carte {verb}:"]
        lines += [self._card_line(c) for c in cards[:40]]
        if len(cards) > 40:
            lines.append(f"… e altre {len(cards) - 40}")
        if unknown:
            lines.append("❓ Non capiti: " + html.escape(" ".join(unknown)))
        lines.append(f"Totale mancanti: <b>{len(self.db.wanted_ids())}</b>/{len(self.index.by_id)}")
        return Reply("\n".join(lines))

    def _set_number(self, args: str, key: str, conv, fmt) -> Reply:
        try:
            v = conv(float(args.replace(",", ".").replace("%", "").replace("€", "").strip()))
        except ValueError:
            return Reply("Serve un numero, es. <code>/soglia 50</code> oppure <code>/prezzo 100</code>.")
        self.db.save_settings({key: v})
        return Reply("⚙️ " + fmt(v))

    def _set_sources(self, args: str) -> Reply:
        from .scrapers import SCRAPERS
        chosen = [t for t in re.split(r"[\s,]+", args.lower()) if t in SCRAPERS]
        if not chosen:
            return Reply("Fonti disponibili: " + ", ".join(SCRAPERS) + "\nEs. <code>/fonti wallapop vinted ebay</code>")
        self.db.save_settings({"sources": chosen})
        return Reply("⚙️ Fonti attive: " + ", ".join(chosen))

    # ------------------------------------------------------------------
    def _prices(self, args: str) -> Reply:
        from . import stats as pstats
        rows = self.db.list_found()
        arg = args.strip().lower()
        if arg:
            card = self.index.by_code.get(arg) or self.index.by_id.get(arg)
            if not card:
                return Reply("Carta non riconosciuta: usa il numero (es. <code>/prezzi 145</code>) o il codice c1..c30.")
            cp = pstats.card_prices(rows, card)
            if not cp.overall.n:
                return Reply(f"<b>{html.escape(card.label)}</b>\nNessun prezzo visto finora per questa carta.")
            lines = [f"💶 <b>{html.escape(card.label)}</b> · {cp.overall.n} annunci visti",
                     f"Minimo <b>{pstats.fmt_eur(cp.overall.min)}</b> · mediana {pstats.fmt_eur(cp.overall.median)} · massimo {pstats.fmt_eur(cp.overall.max)}"]
            for src in pstats.SOURCE_ORDER:
                st = cp.by_source.get(src)
                if st and st.n:
                    lines.append(f"• {pstats.SOURCE_LABELS[src]}: min {pstats.fmt_eur(st.min)} · mediana {pstats.fmt_eur(st.median)} · {st.n} annunci")
            if cp.recent_median is not None and cp.older_median:
                delta = 100.0 * (cp.recent_median - cp.older_median) / cp.older_median
                arrow = "📈" if delta > 5 else "📉" if delta < -5 else "➡️"
                lines.append(f"{arrow} Ultimi 7 giorni: mediana {pstats.fmt_eur(cp.recent_median)} ({delta:+.0f}% rispetto a prima)")
            return Reply("\n".join(lines), buttons=[[("📂 Storico", f"/storico {self.index.code_of[card.id]}")]])
        comp = pstats.completion(self.index, self.db.wanted_ids(), rows)
        if not comp.missing:
            return Reply("🎉 Nessuna carta mancante: set completo!")
        lines = [f"💶 <b>Per finire il set</b> ({comp.missing} carte mancanti)",
                 f"Ai prezzi <b>minimi</b> visti: <b>{pstats.fmt_eur(comp.cost_min)}</b> · ai prezzi mediani: {pstats.fmt_eur(comp.cost_median)}",
                 f"Stima su {comp.priced} carte con prezzi visti" + (f"; {len(comp.unpriced)} ancora senza prezzo" if comp.unpriced else "")]
        priced = []
        for c in sorted((c for c in self.index.by_id.values() if c.id in self.db.wanted_ids()), key=lambda c: c.sort_key):
            st = pstats.card_prices(rows, c).overall
            if st.n:
                priced.append((st.min, c))
        priced.sort(key=lambda t: -t[0])
        if priced:
            lines.append("\nLe più care (minimo visto):")
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
                     "Chi lo riceve apre il bot e incolla quel comando: da lì riceve le stesse notifiche, "
                     "gestisce la stessa checklist e apre la stessa Mini App dal pulsante App.")

    def _users(self, chat_id: str) -> Reply:
        ids = self.db.chat_ids()
        if not ids:
            return Reply("Nessuno collegato ancora.")
        lines = [f"👥 <b>Persone collegate</b> ({len(ids)})"]
        for i, cid in enumerate(ids):
            lines.append(f"• <code>{cid}</code>{' · proprietario' if i == 0 else ''}{' · tu' if cid == str(chat_id) else ''}")
        if self._is_owner(chat_id):
            lines.append("\n/invita per aggiungere qualcuno · /espelli ID per scollegarlo")
        return Reply("\n".join(lines))

    def _kick(self, chat_id: str, args: str) -> Reply:
        if not self._is_owner(chat_id):
            return Reply("Solo il proprietario del bot può scollegare qualcuno.")
        target = args.strip()
        if not target.lstrip("-").isdigit():
            return Reply("Usa <code>/espelli ID</code> con l'ID che vedi in /utenti.")
        if target == self.db.owner_chat_id():
            return Reply("Il proprietario non si può scollegare.")
        if self.db.remove_chat_id(target):
            return Reply(f"👋 Chat <code>{target}</code> scollegata: non riceve più notifiche né comandi.")
        return Reply("Quell'ID non è tra le persone collegate (vedi /utenti).")

    def _collection(self, args: str) -> Reply:
        """/collezione – elenco · /collezione sv8 – scarica/info · … manca|ho N… · … attiva|disattiva · … svuota."""
        from . import collections as coll
        a = args.strip()
        home = {s.id for s in self.index.sets}
        if not a:
            lines = ["📚 <b>Collezioni</b>"]
            home_on = self.db.get_settings().get("home_active", True)
            for s_ in self.index.sets:
                lines.append(f"• <code>{s_.id}</code> {html.escape(s_.name)} · {len(s_.cards)} carte · "
                             + ("🔎 ricerca attiva" if home_on else "💤 ricerca spenta"))
            act = set(coll.active_ids(self.db))
            for cs in coll.loaded_sets(self.db):
                n = len(coll.wanted_numbers(self.db, cs.id))
                lines.append(f"• <code>{cs.id}</code> {html.escape(cs.name)} · {len(cs.cards)} carte · mancanti {n} · "
                             + ("🔎 ricerca attiva" if cs.id in act else "💤 ricerca spenta"))
            lines.append("\nPer le altre collezioni il punto di partenza è \"mi mancano tutte\": segna quelle che hai. "
                         "Dalla Mini App (Collezioni) oppure: <code>/collezione sv8 ho 4 7</code>, <code>/collezione sv8 manca 4</code>, "
                         "<code>/collezione sv8 attiva</code>.")
            return Reply("\n".join(lines))
        set_id, _, rest = a.partition(" ")
        set_id = set_id.lower().strip()
        rest = rest.strip()
        if set_id in home:
            v = rest.split()[0].lower() if rest else ""
            if v in ("attiva", "on", "cerca"):
                self.db.save_settings({"home_active": True})
                return Reply("🔎 Ricerca attiva per la 30th Celebration.")
            if v in ("disattiva", "off", "spegni"):
                self.db.save_settings({"home_active": False})
                return Reply("💤 Ricerca spenta per la 30th Celebration: la checklist resta, il bot non la cerca finché non la riaccendi.")
            return Reply("Per la 30th usa i comandi normali (/mancanti, /aggiungi 131, /rimuovi 131); "
                         "<code>/collezione me55 disattiva</code> spegne la sua ricerca.")
        try:
            cs = coll.get_set(self.db, set_id)
        except KeyError:
            return Reply(f"Collezione <code>{html.escape(set_id)}</code> non trovata nel catalogo. Sfogliale dalla Mini App.")
        except Exception as exc:  # noqa: BLE001
            return Reply(f"⚠️ Non riesco a scaricare la collezione adesso ({html.escape(str(exc)[:80])}). Riprova tra poco.")
        if not rest:
            n = len(coll.wanted_numbers(self.db, cs.id))
            on = cs.id in coll.active_ids(self.db)
            return Reply(f"📚 <b>{html.escape(cs.name)}</b> (<code>{cs.id}</code>) · {len(cs.cards)} carte"
                         f"{' · numerazione /' + str(cs.printed_total) if cs.printed_total else ''}\nMancanti segnate: <b>{n}</b> · "
                         + ("🔎 ricerca attiva" if on else "💤 ricerca spenta") +
                         f"\n\n<code>/collezione {cs.id} manca 4 7</code> · <code>/collezione {cs.id} ho 4</code> · "
                         f"<code>/collezione {cs.id} {'disattiva' if on else 'attiva'}</code>")
        verb, _, nums = rest.partition(" ")
        verb = verb.lower()
        if verb in ("attiva", "on", "cerca"):
            coll.set_active(self.db, cs.id, True)
            n = len(coll.wanted_numbers(self.db, cs.id))
            return Reply(f"🔎 Ricerca attiva per <b>{html.escape(cs.name)}</b> ({n} mancanti). ⚠️ Ogni collezione attiva aggiunge "
                         "ricerche e notifiche a ogni giro: tienine poche accese. <code>/collezione "
                         f"{cs.id} disattiva</code> per spegnerla.")
        if verb in ("disattiva", "off", "spegni"):
            coll.set_active(self.db, cs.id, False)
            return Reply(f"💤 Ricerca spenta per <b>{html.escape(cs.name)}</b>: la checklist resta.")
        if verb in ("svuota", "reset", "lehotutte", "complete"):
            coll.mark(self.db, cs.id, [c.number for c in cs.cards], False)
            return Reply(f"🧹 {html.escape(cs.name)}: segnata completa, nessuna mancante.")
        if verb in ("manca", "mancano", "aggiungi", "ho", "trovata", "rimuovi", "presa", "tutte"):
            if verb == "tutte":  # "mi mancano tutte": è già il punto di partenza, qui azzera le possedute
                numbers = [c.number for c in cs.cards]
                want = True
            else:
                want = verb in ("manca", "mancano", "aggiungi")
                tokens = nums.replace(",", " ").split()
                by_num = {c.number.lower(): c for c in cs.cards}
                numbers, bad = [], []
                for t in tokens:
                    if "-" in t and all(x.isdigit() for x in t.split("-", 1)):
                        lo, hi = (int(x) for x in t.split("-", 1))
                        numbers += [str(i) for i in range(lo, hi + 1) if str(i) in by_num]
                    elif t.lower() in by_num:
                        numbers.append(by_num[t.lower()].number)
                    else:
                        bad.append(t)
                if not numbers:
                    return Reply(f"Nessun numero valido per {html.escape(cs.name)}. Es. <code>/collezione {cs.id} manca 4 7 10-12</code>.")
            coll.mark(self.db, cs.id, numbers, want)
            n = len(coll.wanted_numbers(self.db, cs.id))
            names = ", ".join(html.escape(next(c.name for c in cs.cards if c.number == x) + " " + x) for x in numbers[:6])
            more = f" e altre {len(numbers) - 6}" if len(numbers) > 6 else ""
            msg = f"{'🃏 Mancanti' if want else '✅ Prese'}: {names}{more}\n{html.escape(cs.name)}: ora {n} mancanti"
            if not want or cs.id in coll.active_ids(self.db):
                pass
            else:
                msg += " · 💤 ricerca spenta (<code>/collezione " + cs.id + " attiva</code> per cercarle)"
            if verb != "tutte" and bad:
                msg += "\n❓ Non capiti: " + html.escape(" ".join(bad))
            return Reply(msg)
        return Reply(f"Non ho capito. Usa <code>/collezione {cs.id} manca 4 7</code>, <code>ho 4</code>, <code>attiva</code>, <code>disattiva</code>.")

    def _resolve_any(self, args: str) -> tuple[list, list[str]]:
        """Come resolve(), più i codici set:numero delle altre collezioni (es. sv8:7)."""
        from . import collections as coll
        plain, cards, unknown = [], [], []
        for t in args.replace(",", " ").split():
            if ":" in t:
                sid, _, num = t.partition(":")
                try:
                    cs = coll.get_set(self.db, sid.lower())
                except Exception:  # noqa: BLE001
                    cs = None
                card = next((c for c in cs.cards if c.number.lower() == num.lower()), None) if cs else None
                (cards.append(card) if card else unknown.append(t))
            else:
                plain.append(t)
        if plain:
            c2, u2 = self.resolve(" ".join(plain))
            cards += c2
            unknown += u2
        return cards, unknown

    def _chase(self, args: str) -> Reply:
        """/insegui 151 – per 6 ore cerca quella carta ogni 5 minuti; /insegui – elenco; /insegui stop [carte]."""
        from . import watch
        a = args.strip()
        if not a:
            return Reply(watch.describe(self.index, self.db))
        first, _, rest = a.partition(" ")
        if first.lower() in ("stop", "basta", "ferma", "off"):
            if not rest.strip():
                n = watch.clear_watches(self.db)
                return Reply(f"⏹ Fermati {n} inseguimenti." if n else "Nessun inseguimento attivo.")
            cards, unknown = self._resolve_any(rest)
            stopped = [c.label for c in cards if watch.remove_watch(self.db, c.id)]
            msg = ("⏹ Fermato: " + ", ".join(html.escape(x) for x in stopped)) if stopped else "Quelle carte non erano inseguite."
            if unknown:
                msg += "\n❓ Non capiti: " + html.escape(" ".join(unknown))
            return Reply(msg)
        card_args, duration, every = watch.parse_watch_args(a)
        cards, unknown = self._resolve_any(card_args)
        if not cards:
            return Reply("Carta non riconosciuta. Es. <code>/insegui 151</code>, <code>/insegui c4 2h</code>, <code>/insegui 151 2h ogni 10m</code>.")
        active = watch.list_watches(self.db)
        room = watch.MAX_WATCHES - len({cid for cid in active if cid not in {c.id for c in cards}})
        cards = cards[:max(0, room)]
        if not cards:
            return Reply(f"Al massimo {watch.MAX_WATCHES} inseguimenti insieme: ferma qualcosa con /insegui stop.")
        for c in cards:
            w = watch.add_watch(self.db, c.id, duration, every)
        names = ", ".join(html.escape(c.label) for c in cards)
        lines = [f"🏃 Inseguo <b>{names}</b>: ogni {watch.fmt_duration(every)} per {watch.fmt_duration(duration)} "
                 f"(fino alle {watch.fmt_time(w['until'])}).",
                 "Primo controllo tra pochi secondi, poi ti avviso solo quando spunta un annuncio nuovo. Alla fine, il riepilogo."]
        if unknown:
            lines.append("❓ Non capiti: " + html.escape(" ".join(unknown)))
        code = watch.code_for(self.index, cards[0])
        return Reply("\n".join(lines), buttons=[[("⏹ Ferma", f"/insegui stop {code}"), ("🏃 Attivi", "/insegui")]])

    def _search_cards(self, args: str) -> Reply:
        """Ricerca mirata, subito, di una o più carte (max 3): cosa c'è in vendita adesso."""
        from . import stats as pstats
        from .search import search_card
        cards, unknown = self.resolve(args)
        if not cards:
            return Reply("Carta non riconosciuta. Es. <code>/cerca 145</code> oppure <code>/cerca c4</code>.")
        settings = self.db.get_settings()
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

    def _progress(self) -> Reply:
        from . import stats as pstats
        comp = pstats.completion(self.index, self.db.wanted_ids(), self.db.list_found())
        filled = round(comp.percent / 10)
        bar = "🟩" * filled + "⬜" * (10 - filled)
        lines = [f"📊 <b>Progresso set</b>: {comp.owned}/{comp.total} carte ({comp.percent:.0f}%)", bar]
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
                lines.append(f"\n💶 Stima per finire: <b>{pstats.fmt_eur(comp.cost_min)}</b> ai minimi visti"
                             f" ({comp.priced} carte con prezzo" + (f", {len(comp.unpriced)} senza" if comp.unpriced else "") + ")")
        else:
            lines.append("🎉 Set completo!")
        return Reply("\n".join(lines), buttons=[[("💶 Prezzi", "/prezzi"), ("🃏 Mancanti", "/mancanti")]])

    def _deals(self, args: str) -> Reply:
        a = args.strip().lower().replace("%", "")
        if a in ("off", "no", "0"):
            self.db.save_settings({"deal_pct": 0})
            return Reply("🔥 Avvisi affare disattivati.")
        if not a:
            cur = int(self.db.get_settings().get("deal_pct", 60) or 0)
            return Reply(f"🔥 Avviso affare: {'spento' if not cur else f'sotto il {cur}% della mediana storica'}.\n"
                         "Imposta con <code>/affari 60</code> oppure spegni con <code>/affari off</code>.")
        try:
            v = max(10, min(95, int(float(a))))
        except ValueError:
            return Reply("Serve una percentuale, es. <code>/affari 60</code>, oppure <code>/affari off</code>.")
        self.db.save_settings({"deal_pct": v})
        return Reply(f"🔥 Avviso affare attivo: ti scrivo subito se una carta mancante esce sotto il {v}% della sua mediana storica "
                     "(servono almeno 4 prezzi visti per quella carta).")

    def _quiet(self, args: str) -> Reply:
        a = args.strip().lower()
        if a in ("off", "no"):
            self.db.save_settings({"quiet_hours": None})
            return Reply("🌙 Ore silenziose disattivate.")
        parts = re.findall(r"\d{1,2}", a)
        if len(parts) != 2:
            cur = self.db.get_settings().get("quiet_hours")
            desc = f"dalle {cur[0]} alle {cur[1]}" if cur else "nessuna"
            return Reply(f"🌙 Ore silenziose: {desc}.\nImposta con <code>/notte 23 8</code> (accumula e manda al mattino) o <code>/notte off</code>.")
        start, end = int(parts[0]) % 24, int(parts[1]) % 24
        self.db.save_settings({"quiet_hours": [start, end]})
        return Reply(f"🌙 Dalle {start}:00 alle {end}:00 non ti disturbo: accumulo e ti mando tutto al primo giro dopo le {end}:00.")

    def _export(self) -> Reply:
        from .export import build_workbook
        content = build_workbook(self.index, self.db)
        name = time.strftime("pokebot_%Y%m%d_%H%M.xlsx")
        return Reply("📎 Ecco il file Excel: checklist, storico annunci e prezzi.", document=(name, content))

    # ------------------------------------------------------------------
    SOURCE_ORDER = ["ebay", "vinted", "wallapop"]
    SOURCE_LABELS = {"ebay": "eBay.it", "vinted": "Vinted", "wallapop": "Wallapop"}

    def _history(self, args: str) -> Reply:
        """Cartelle per carta con lo storico degli annunci trovati."""
        arg = args.strip().lower()
        if arg in ("svuota", "cancella", "pulisci"):
            self.db.clear_found()
            return Reply("🗑 Storico svuotato. Gli annunci già visti non verranno comunque rinotificati.")
        rows = self.db.list_found()
        if not rows:
            return Reply("Nessun annuncio trovato finora. Lo storico si riempie a ogni giro di ricerca.")
        if not arg:
            return self._history_index(rows)
        if arg in ("lotti", "lotto", "lot"):
            members = [r for r in rows if r["kind"] == "lot"]
            return self._history_detail("📦 Lotti con carte mancanti", members, show_cards=True)
        card = self.index.by_code.get(arg) or self.index.by_id.get(arg)
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
        if lots:
            buttons.append([(f"📦 Lotti · {lots}", "/storico lotti")])
        buttons.append([("🗑 Svuota storico", "/storico svuota")])
        return Reply("\n".join(lines), buttons=buttons[:50])

    def _history_detail(self, title: str, members: list[dict], show_cards: bool = False, per_source: int = 12) -> Reply:
        from .scrapers.base import parse_price
        if not members:
            return Reply(f"<b>{html.escape(title)}</b>\nNessun annuncio in questa cartella.", buttons=[[("⬅️ Cartelle", "/storico")]])
        lines = [f"<b>{html.escape(title)}</b> · {len(members)} annunci trovati"]
        by_source: dict[str, list[dict]] = {}
        for r in members:
            by_source.setdefault(r["source"], []).append(r)
        order = [s for s in self.SOURCE_ORDER if s in by_source] + [s for s in by_source if s not in self.SOURCE_ORDER]
        for src in order:
            items = by_source[src]
            items.sort(key=lambda r: (parse_price(r.get("price")) if r.get("price") else float("inf"), -r["created_at"]))
            lines.append(f"\n<b>{html.escape(self.SOURCE_LABELS.get(src, src))}</b> ({len(items)})")
            for r in items[:per_source]:
                when = time.strftime("%d/%m", time.localtime(r["created_at"]))
                price = html.escape(r.get("price") or "n.d.")
                sent = "" if r.get("notified") else " · non inviato"
                extra = ""
                if show_cards:
                    labels = [m["label"] for m in r["matched"][:3]]
                    extra = " · " + html.escape(", ".join(labels)) + (" …" if len(r["matched"]) > 3 else "")
                lines.append(f'• {price} · {when}{sent} · <a href="{html.escape(r["url"], quote=True)}">{html.escape(r["title"][:60])}</a>{extra}')
            if len(items) > per_source:
                lines.append(f"  … e altri {len(items) - per_source}")
        return Reply("\n".join(lines), buttons=[[("⬅️ Cartelle", "/storico")]])

    def _card_line(self, c: Card) -> str:
        code = self.index.code_of[c.id]
        label = f"{code} · {c.name}" if c.printed_total else f"{code} · {c.name} #{c.number}"
        return html.escape(f"{label} ({c.rarity})")

    def _fmt_missing(self) -> str:
        wanted = self.db.wanted_ids()
        if not wanted:
            return "Nessuna carta segnata come mancante. Usa /aggiungi (es. <code>/aggiungi tutte</code>) o /lista."
        lines = [f"🃏 <b>Carte mancanti: {len(wanted)}/{len(self.index.by_id)}</b>"]
        for s in self.index.sets:
            ordered = sorted(s.cards, key=lambda c: c.sort_key) if s.printed_total else s.cards
            cs = [c for c in ordered if c.id in wanted]
            if cs:
                lines.append(f"\n<b>{html.escape(s.name)}</b> ({len(cs)})")
                lines += [self._card_line(c) for c in cs]
        return "\n".join(lines)

    def _fmt_list(self, args: str) -> str:
        want_classic = "classic" in args.lower()
        wanted = self.db.wanted_ids()
        lines = []
        for s in self.index.sets:
            if bool(s.printed_total) == want_classic:
                continue
            lines.append(f"<b>{html.escape(s.name)}</b> – {len(s.cards)} carte (✗ = ti manca)")
            ordered = sorted(s.cards, key=lambda c: c.sort_key) if s.printed_total else s.cards
            for c in ordered:
                mark = "✗ " if c.id in wanted else ""
                lines.append(mark + self._card_line(c))
        if not want_classic:
            lines.append("\nPer la Classic Collection: /lista classic")
        return "\n".join(lines)

    def _fmt_status(self) -> str:
        s = self.db.get_settings()
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
        if list_watches(self.db):
            flags.append(f"🏃 inseguimenti {len(list_watches(self.db))}")
        lines = [f"🃏 Mancanti: <b>{len(self.db.wanted_ids())}</b>/{len(self.index.by_id)} · " + " · ".join(flags),
                 f"⚙️ Ricerca ogni {int(s['interval_minutes'])} min · max {int(s.get('max_per_card', 5))} annunci per carta · "
                 f"soglia lotti {s['lot_min_ratio'] * 100:.0f}% · prezzo max {price_txt} · lingua: {s.get('language', 'ita')} · "
                 f"fonti: {', '.join(s['sources'])}"]
        if runs:
            r = runs[0]
            when = time.strftime("%d/%m %H:%M", time.localtime(r["started_at"]))
            lines.append(f"🕒 Ultimo ciclo {when}: {r['listings_seen']} annunci letti, {r['matches']} segnalati")
            per_source = r.get("per_source") or {}
            if per_source:
                lines.append("📊 Letti per fonte: " + " · ".join(
                    f"{self.SOURCE_LABELS.get(k, k)} {v}" for k, v in per_source.items()))
            for k, v in (r["errors"] or {}).items():
                lines.append(f"⚠️ {html.escape(k)}: {html.escape(str(v)[:200])}")
        else:
            lines.append("🕒 Nessun ciclo ancora eseguito.")
        qs = self.db.get_kv("query_stats", {}) or {}
        if qs:
            lines.append("🔤 Query generiche (giri · nuovi · segnalati): " + " · ".join(
                f"<i>{html.escape(q)}</i> {v['runs']}/{v['new']}/{v['matches']}" for q, v in sorted(qs.items(), key=lambda kv: -kv[1]["matches"])))
        return "\n".join(lines)


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
        if not self.enabled or self.db.get_kv("telegram_menu_version") == MENU_VERSION:
            return
        try:
            if self.client.set_my_commands(MENU_COMMANDS):
                self.db.set_kv("telegram_menu_version", MENU_VERSION)
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
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id") or "")
            if not chat_id or not text:
                continue
            if not self._authorized(chat_id, text):
                log.warning("Messaggio ignorato da chat non autorizzata %s", chat_id)
                continue
            reply = self.handler.handle(text, chat_id)
            want_search = want_search or reply.run_search
            self._deliver(chat_id, reply)
        return want_search

    def _deliver(self, chat_id: str, reply: Reply) -> None:
        try:
            self.client.send(chat_id, reply.text, reply.buttons)
            if reply.document:
                self.client.send_document(chat_id, reply.document[0], reply.document[1])
        except requests.RequestException as exc:
            log.warning("Telegram invio: %s", exc)

    def handle_payload(self, payload: dict | None) -> bool:
        """Comando arrivato tramite repository_dispatch (ponte Telegram → GitHub). True se chiede /cerca."""
        if not isinstance(payload, dict):
            return False
        chat_id = str(payload.get("chat_id") or "")
        text = str(payload.get("text") or "")
        if not chat_id or not text:
            return False
        if not self._authorized(chat_id, text):
            log.warning("Comando via ponte ignorato da chat non autorizzata %s", chat_id)
            return False
        reply = self.handler.handle(text, chat_id)
        self._deliver(chat_id, reply)
        return reply.run_search

    def _authorized(self, chat_id: str, text: str) -> bool:
        """Proprietario (dall'ambiente o dal primo /start) e persone invitate (`/start CODICE` entro 48 ore)."""
        chat_id = str(chat_id)
        allowed = self.db.chat_ids()
        if chat_id in allowed:
            return True
        parts = text.strip().split()
        if not parts or not parts[0].lower().startswith("/start"):
            return False
        if not allowed:
            self.db.set_kv("telegram_chat_id", chat_id)  # il primo che scrive /start diventa il proprietario
            log.info("Chat id Telegram salvato: %s", chat_id)
            return True
        if len(parts) < 2:
            return False
        inv = self.db.get_kv("invite_code") or {}
        if (isinstance(inv, dict) and inv.get("code") and parts[1].upper() == str(inv["code"]).upper()
                and float(inv.get("expires") or 0) > time.time()):
            self.db.add_chat_id(chat_id)
            self.db.set_kv("invite_code", None)  # monouso
            log.info("Nuova persona collegata con invito: %s", chat_id)
            return True
        return False
