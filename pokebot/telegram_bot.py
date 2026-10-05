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
/soglia 50 – % minima di carte mancanti perché un lotto venga segnalato
/prezzo 100 – prezzo massimo in € (0 = nessun limite)
/fonti wallapop vinted ebay – quali marketplace usare
/cerca – ricerca immediata  ·  /resetvisti – rinotifica anche gli annunci già visti"""


# Menu comandi mostrato da Telegram toccando "/" (registrato automaticamente dal bot)
MENU_VERSION = 1
MENU_COMMANDS = [
    ("mancanti", "Carte che ti mancano"),
    ("aggiungi", "Segna mancanti: /aggiungi 131 132 149-152 c4 (anche ir, sir, tutte)"),
    ("rimuovi", "Trovata! Toglila: /rimuovi 131"),
    ("lista", "Tutte le carte con i numeri (/lista classic per la Classic)"),
    ("stato", "Ultimo giro, errori, impostazioni"),
    ("cerca", "Cerca subito"),
    ("soglia", "Percentuale minima di carte mancanti nei lotti: /soglia 50"),
    ("prezzo", "Prezzo massimo in euro: /prezzo 100 (0 = nessun limite)"),
    ("fonti", "Marketplace da usare: /fonti wallapop vinted ebay"),
    ("resetvisti", "Rinotifica anche gli annunci gia' visti"),
    ("aiuto", "Elenco dei comandi"),
]


@dataclass
class Reply:
    text: str
    run_search: bool = False


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

    def delete_webhook(self) -> bool:
        """Se un webhook è attivo, getUpdates non funziona: lo rimuove senza perdere i messaggi in coda."""
        resp = requests.post(f"{self.base}/deleteWebhook", json={"drop_pending_updates": False}, timeout=config.HTTP_TIMEOUT)
        return resp.status_code == 200 and bool(resp.json().get("ok"))

    def get_updates(self, offset: int | None, timeout: int = 0) -> list[dict]:
        params = {"timeout": timeout, "allowed_updates": json.dumps(["message"])}
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

    def send(self, chat_id: str | int, text: str) -> None:
        for chunk in _chunks(text):
            requests.post(f"{self.base}/sendMessage",
                          json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True},
                          timeout=config.HTTP_TIMEOUT)


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
    def handle(self, text: str) -> Reply:
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
        if cmd == "/soglia":
            return self._set_number(args, "lot_min_ratio", lambda v: min(1.0, max(0.0, v / 100)),
                                    lambda v: f"Soglia lotti: {v * 100:.0f}% di carte mancanti.")
        if cmd == "/prezzo":
            return self._set_number(args, "max_price", lambda v: max(0.0, v),
                                    lambda v: f"Prezzo massimo: {'nessun limite' if not v else f'{v:g} €'}.")
        if cmd == "/fonti":
            return self._set_sources(args)
        if cmd == "/cerca":
            return Reply("🔎 Ok, cerco adesso.", run_search=True)
        if cmd == "/resetvisti":
            self.db.forget_seen()
            return Reply("♻️ Memoria azzerata: al prossimo ciclo rinotifico anche gli annunci già visti.")
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
        lines = [f"🃏 Mancanti: <b>{len(self.db.wanted_ids())}</b>/{len(self.index.by_id)}",
                 f"⚙️ Soglia lotti {s['lot_min_ratio'] * 100:.0f}% · prezzo max {price_txt} · fonti: {', '.join(s['sources'])}"]
        if runs:
            r = runs[0]
            when = time.strftime("%d/%m %H:%M", time.localtime(r["started_at"]))
            lines.append(f"🕒 Ultimo ciclo {when}: {r['listings_seen']} annunci letti, {r['matches']} segnalati")
            for k, v in (r["errors"] or {}).items():
                lines.append(f"⚠️ {html.escape(k)}: {html.escape(str(v)[:200])}")
        else:
            lines.append("🕒 Nessun ciclo ancora eseguito.")
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
                if info.get("url"):
                    log.warning("Telegram: webhook attivo su %s, lo rimuovo per poter leggere i messaggi", info["url"])
                    self.client.delete_webhook()
            except (requests.RequestException, ValueError) as exc:
                log.warning("Telegram getWebhookInfo: %s", exc)
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
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id") or "")
            text = msg.get("text") or ""
            if not chat_id or not text:
                continue
            if not self._authorized(chat_id, text):
                log.warning("Messaggio ignorato da chat non autorizzata %s", chat_id)
                continue
            reply = self.handler.handle(text)
            want_search = want_search or reply.run_search
            try:
                self.client.send(chat_id, reply.text)
            except requests.RequestException as exc:
                log.warning("Telegram sendMessage: %s", exc)
        return want_search

    def _authorized(self, chat_id: str, text: str) -> bool:
        pinned = config.TELEGRAM_CHAT_ID or str(self.db.get_kv("telegram_chat_id", "") or "")
        if pinned:
            return chat_id == pinned
        if text.strip().lower().startswith("/start"):
            self.db.set_kv("telegram_chat_id", chat_id)  # il primo che scrive /start diventa il proprietario
            log.info("Chat id Telegram salvato: %s", chat_id)
            return True
        return False
