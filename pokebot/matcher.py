"""Riconoscimento delle carte in un annuncio e decisione singola / lotto / scarto."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cards import Card, CardIndex, normalize

# Annunci da scartare: non sono carte singole pronte e disponibili.
EXCLUDE_PATTERNS: list[tuple[str, str]] = [
    (r"\b(cerco|cercasi|compro|acquisto|in cerca di|wtb|looking for)\b", "annuncio di ricerca/acquisto"),
    (r"\b(vendut[oaei]|sold|riservat[oaei]|reserved|non disponibile|esaurit[oaei])\b", "venduto o riservato"),
    (r"\b(preorder|pre order|preordine|prenotazion[ei]|prevendita|in arrivo)\b", "preordine / non disponibile subito"),
    (r"\b(asta|aste|auction)\b", "asta, non acquisto immediato"),
    (r"\b(proxy|custom|fake|fals[oaei]|replica|repliche|riproduzion[ei]|stampat[oaei] in casa|orica)\b", "carta non originale"),
    (r"\b(codice|codici|code card|online code|digitale|digital|tcg live|pocket)\b", "codice / digitale"),
    (r"\b(bust[ae]|bustin[ae]|booster|box|etb|elite trainer|display|blister|tin|pacchett[oi]|sigillat[oaei]|sealed|collection box|premium collection)\b",
     "prodotto sigillato"),
    (r"\b(sleeve|sleeves|toploader|raccoglitor[ei]|album|binder|playmat|deck box|portamazzo|portacarte)\b", "accessorio"),
    (r"\b(no singola|non singola|mystery|mistery|misterios[oaie]|random|a sorpresa|pacchetto sorpresa)\b", "mystery box / non è la carta singola"),
]
_EXCLUDE_RE = [(re.compile(p), why) for p, why in EXCLUDE_PATTERNS]

# "scambio" va bene solo se si vende anche.
_TRADE_RE = re.compile(r"\b(scambio|scambi|scambiare|trade|swap)\b")
_SELL_RE = re.compile(r"\b(vendo|vendita|vendesi|in vendita|prezzo|euro|eur)\b")
_EURO_AMOUNT_RE = re.compile(r"\d\s*€|€\s*\d")  # sul testo originale: normalize toglie il simbolo

# parole che indicano un lotto vero e proprio
_LOT_WORDS_RE = re.compile(r"\b(lotto|lotti|lot|bundle|collezione|stock|blocco)\b")
# quantità ("x2", "2 carte"): da sole, con una sola carta riconosciuta, indicano più copie della stessa carta
_LOT_RE = re.compile(r"\b(lotto|lotti|lot|bundle|collezione|stock|blocco|x\s?\d{1,3}|\d{1,3}\s?x|\d{1,3}\s+carte)\b")
_FULL_SET_RE = re.compile(r"\b(set completo|master set|masterset|full set|complete set|completo|completa|tutte le carte|intero set)\b")
_STATED_COUNT_RE = re.compile(r"\b(\d{1,3})\s+(carte|cards|pezzi|pz)\b|\b(carte|cards)\s*x\s?(\d{1,3})\b|\bx\s?(\d{1,3})\b")

# Annunci in altre lingue (usato con l'impostazione lingua = "ita")
_LANG_PATTERNS: list[tuple[str, str]] = [
    (r"\b(fr|vf|francese|french|francais|francaise)\b|"
     r"\b(cartes|neuve|neuf|etat|tres bon|jamais jouee|envoi|30 ans|\d+ ans|celebrations 30|lot de|pieces)\b|"
     r"\b(lokhlass|sulfura|artikodin|electhor|noadkoko|dracaufeu|ectoplasma|miaouss|nymphali|drattak|amphinobi|chochodile|"
     r"lugulabre|lougaroc|ekaiser|gromago|baggiguane|mentali|evoli|carapuce|salameche)\b", "francese"),
    (r"\b(eng|en|english|inglese|english version|usa)\b", "inglese"),
    (r"\b(de|deutsch|german|tedesco|deutsche|neuwertig|karte|karten)\b", "tedesco"),
    (r"\b(es|espanol|spanish|spagnolo|castellano|nueva|tarjeta|cartas)\b", "spagnolo"),
    (r"\b(jp|jpn|jap|japanese|giapponese|japan|kor|korean|coreano|chinese|cinese|zh)\b", "giapponese/asiatica"),
]
_LANG_RE = [(re.compile(p), lang) for p, lang in _LANG_PATTERNS]
_ITALIAN_HINT_RE = re.compile(r"\b(ita|italiano|italiana|italiane|it)\b")

# Indizi di set diversi (25° anniversario "Celebrations")
_OTHER_SET_RE = re.compile(r"\b(25th|25 anniversario|25° anniversario|celebrations|venticinquesimo)\b")


@dataclass
class CardRef:
    card: Card
    via: str  # "numero" | "nome" | "nome+numero"


@dataclass
class AmbiguousRef:
    name: str
    candidates: list[Card]


@dataclass
class MatchResult:
    kind: str  # "single" | "lot" | "none" | "excluded"
    notify: bool
    reason: str
    refs: list[CardRef] = field(default_factory=list)
    ambiguous: list[AmbiguousRef] = field(default_factory=list)
    wanted: list[Card] = field(default_factory=list)
    possible_wanted: list[Card] = field(default_factory=list)  # da gruppi ambigui
    total_cards: int = 0
    wanted_count: int = 0
    ratio: float | None = None

    @property
    def matched_payload(self) -> list[dict]:
        out = [{"id": c.id, "label": c.label, "sure": True} for c in self.wanted]
        out += [{"id": c.id, "label": c.label, "sure": False} for c in self.possible_wanted]
        return out


class Matcher:
    def __init__(self, index: CardIndex, set_keywords: list[str] | None = None):
        self.index = index
        if set_keywords is None:  # parole chiave di tutte le collezioni dell'indice
            set_keywords = [k for s in index.sets for k in s.context_keywords]
        self.set_keywords = [normalize(k) for k in set_keywords if k.strip()]
        self._set_kw_re = self._build_kw_re(self.set_keywords)
        # Per i set senza numerazione propria (Classic Collection) servono parole di contesto
        self._set_context_re: dict[str, re.Pattern | None] = {}
        for s in index.sets:
            kws = [normalize(k) for k in s.context_keywords]
            self._set_context_re[s.id] = self._build_kw_re(kws) if kws else None
        # regex dei nomi: dal più lungo al più corto
        names = sorted(index.names.keys(), key=len, reverse=True)
        self._name_res = [(n, re.compile(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])")) for n in names]
        # numeri "n/128" per i set con printed_total
        self._number_res: list[tuple[str, re.Pattern]] = []
        totals = [s.printed_total for s in index.sets if s.printed_total]
        self._shared_total: set[str] = {s.id for s in index.sets if s.printed_total and totals.count(s.printed_total) > 1}
        for s in index.sets:
            if s.printed_total:
                self._number_res.append((s.id, re.compile(r"(?<!\d)(\d{1,3})\s*/\s*0?" + str(s.printed_total) + r"(?!\d)")))

    @staticmethod
    def _build_kw_re(keywords: list[str]) -> re.Pattern | None:
        if not keywords:
            return None
        parts = [r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])" for k in keywords]
        return re.compile("|".join(parts))

    # ------------------------------------------------------------------
    def analyze(self, title: str, description: str, wanted_ids: set[str], lot_min_ratio: float = 0.5,
                notify_unverifiable_lots: bool = False, is_auction: bool = False, language: str = "tutte") -> MatchResult:
        text = normalize(f"{title} . {description}")
        title_n = normalize(title)

        if is_auction:
            return MatchResult("excluded", False, "asta, non acquisto immediato")
        if language == "ita":
            other = self.detect_other_language(title_n, text)
            if other:
                return MatchResult("excluded", False, f"lingua diversa ({other})")
        for rx, why in _EXCLUDE_RE:
            if rx.search(title_n) or (rx.search(text) and why in ("carta non originale", "codice / digitale")):
                return MatchResult("excluded", False, why)
        if _TRADE_RE.search(title_n) and not (_SELL_RE.search(text) or _EURO_AMOUNT_RE.search(f"{title} {description}")):
            return MatchResult("excluded", False, "solo scambio")

        has_set_kw = bool(self._set_kw_re and self._set_kw_re.search(text))
        if _OTHER_SET_RE.search(text) and not has_set_kw:
            return MatchResult("none", False, "sembra un altro set (25° anniversario)")

        refs, ambiguous, in_set = self._extract_refs(text, has_set_kw)
        if not in_set:
            return MatchResult("none", False, "nessun riferimento alle collezioni seguite")
        if not refs and not ambiguous:
            if _FULL_SET_RE.search(text) or _LOT_RE.search(text) or _STATED_COUNT_RE.search(text):
                return self._evaluate_lot(text, refs, ambiguous, wanted_ids, lot_min_ratio, notify_unverifiable_lots)
            return MatchResult("none", False, "nessuna carta riconosciuta")

        # una sola carta riconosciuta con "x2" o "2 pezzi" = più copie della stessa carta, non un lotto.
        # Se c'è almeno una carta identificata con certezza, i nomi senza numero ("... Pikachu Nintendo")
        # sono quasi sempre rumore del titolo: non bastano da soli a fare un lotto.
        # "collezione", "completa" e simili contano solo nel titolo: in descrizione sono quasi sempre frasi
        # come "carta da collezione" o "spedizione con protezione completa"
        distinct = len(refs) if refs else len(ambiguous)
        is_lot = (
            bool(_LOT_WORDS_RE.search(title_n))
            or bool(_FULL_SET_RE.search(title_n))
            or distinct >= 2
        )
        if is_lot:
            return self._evaluate_lot(text, refs, ambiguous, wanted_ids, lot_min_ratio, notify_unverifiable_lots)
        return self._evaluate_single(refs, ambiguous if not refs else [], wanted_ids)

    @staticmethod
    def detect_other_language(title_n: str, text: str) -> str | None:
        """Lingua diversa dall'italiano dichiarata o evidente nel titolo (la descrizione conta solo se esplicita)."""
        for rx, lang in _LANG_RE:
            if rx.search(title_n):
                # "ITA / ENG" (bilingue o lotto misto): se l'italiano è citato esplicitamente, lascia passare
                if _ITALIAN_HINT_RE.search(title_n) and lang != "francese":
                    continue
                return lang
        # nella descrizione accetta solo dichiarazioni esplicite di lingua
        explicit = re.search(r"\b(lingua|language|version|versione)\s*:?\s*(fr|francese|french|eng|english|inglese|de|tedesco|german|es|spagnolo|spanish|jp|jpn|giapponese|japanese)\b", text)
        if explicit and not _ITALIAN_HINT_RE.search(text):
            return explicit.group(2)
        return None

    # ------------------------------------------------------------------
    def _extract_refs(self, text: str, has_set_kw: bool) -> tuple[list[CardRef], list[AmbiguousRef], bool]:
        refs: dict[str, CardRef] = {}
        in_set = has_set_kw

        # 1) numeri espliciti "150/128"
        for set_id, rx in self._number_res:
            if set_id in self._shared_total and not self._set_allowed(set_id, text, has_set_kw):
                continue  # più collezioni con la stessa numerazione: serve il nome del set nel testo
            for m in rx.finditer(text):
                card = self.index.by_set_number.get((set_id, m.group(1).lstrip("0") or "0"))
                if card:
                    in_set = True
                    refs.setdefault(card.id, CardRef(card, "numero"))

        # 2) nomi (dal più lungo), senza sovrapposizioni
        covered: list[tuple[int, int]] = []
        ambiguous: list[AmbiguousRef] = []
        for name, rx in self._name_res:
            for m in rx.finditer(text):
                span = (m.start(), m.end())
                if any(a <= span[0] < b or a < span[1] <= b for a, b in covered):
                    continue
                candidates = [c for c in self.index.names[name] if self._set_allowed(c.set_id, text, has_set_kw)]
                if not candidates:
                    continue
                covered.append(span)
                # numero subito dopo il nome ("pikachu ex 149/128" oppure "pikachu ex 149")
                tail = text[m.end(): m.end() + 24]
                # il numero non deve essere seguito da lettere ("30th", "30esimo", "30°" non sono numeri di carta)
                mnum = re.match(r"\s*(?:n\.?|#|numero|num\.?|no\.?)?\s*(\d{1,3})(?![\da-z°])", tail)
                if mnum:
                    num = mnum.group(1).lstrip("0") or "0"
                    hit = [c for c in candidates if c.number == num]
                    if hit:
                        refs.setdefault(hit[0].id, CardRef(hit[0], "nome+numero"))
                        continue
                if len(candidates) == 1:
                    refs.setdefault(candidates[0].id, CardRef(candidates[0], "nome"))
                elif any(c.id in refs for c in candidates):
                    continue  # già risolto tramite numero
                else:
                    ambiguous.append(AmbiguousRef(name, candidates))

        # "Mewtwo" accanto a "Mewtwo ex 151/128" è la stessa carta, non una seconda: scarta i nomi "prefisso"
        # di una carta già identificata dal numero
        sure_names = [normalize(r.card.name) for r in refs.values() if r.via != "nome"]
        for cid, r in list(refs.items()):
            if r.via == "nome" and any(n.startswith(normalize(r.card.name) + " ") for n in sure_names):
                del refs[cid]
        # un gruppo ambiguo risolto da un numero trovato dopo va rimosso
        ambiguous = [a for a in ambiguous if not any(c.id in refs for c in a.candidates)]
        # dedup gruppi ambigui per nome
        seen_names: set[str] = set()
        uniq: list[AmbiguousRef] = []
        for a in ambiguous:
            if a.name not in seen_names:
                seen_names.add(a.name)
                uniq.append(a)
        # senza parola chiave del set, solo un numero "n/128" prova che si parla di questo set
        in_set = in_set or any(r.via == "numero" for r in refs.values())
        return list(refs.values()), uniq, in_set

    def _set_allowed(self, set_id: str, text: str, has_set_kw: bool) -> bool:
        """Un nome vale per una collezione se nel testo ci sono le sue parole chiave; se il testo non nomina nessuna
        collezione, valgono le collezioni con numerazione propria (poi decide `in_set`, cioè un numero "n/128").
        Le collezioni senza numerazione propria (Classic) richiedono sempre le loro parole di contesto."""
        rx = self._set_context_re.get(set_id)
        if rx is not None and rx.search(text):
            return True
        cs = self.index.get_set(set_id)
        if not cs or not cs.printed_total:
            return False
        return not has_set_kw

    # ------------------------------------------------------------------
    def _evaluate_single(self, refs: list[CardRef], ambiguous: list[AmbiguousRef], wanted_ids: set[str]) -> MatchResult:
        if refs:
            card = refs[0].card
            if card.id in wanted_ids:
                return MatchResult("single", True, f"carta singola mancante ({refs[0].via})", refs=refs,
                                   wanted=[card], total_cards=1, wanted_count=1, ratio=1.0)
            return MatchResult("single", False, "carta singola ma già posseduta", refs=refs, total_cards=1)
        grp = ambiguous[0]
        poss = [c for c in grp.candidates if c.id in wanted_ids]
        if poss:
            return MatchResult("single", True, f"carta singola '{grp.name}', numero non indicato: da verificare",
                               ambiguous=ambiguous, possible_wanted=poss, total_cards=1, wanted_count=1, ratio=None)
        return MatchResult("single", False, "carta singola, nessuna versione mancante", ambiguous=ambiguous, total_cards=1)

    def _evaluate_lot(self, text: str, refs: list[CardRef], ambiguous: list[AmbiguousRef], wanted_ids: set[str],
                      lot_min_ratio: float, notify_unverifiable: bool) -> MatchResult:
        wanted = [r.card for r in refs if r.card.id in wanted_ids]
        possible = [c for a in ambiguous for c in a.candidates if c.id in wanted_ids]
        full_set = _FULL_SET_RE.search(text)

        if full_set and not refs:
            # set completo: tutte le carte del set (escludendo i set di contesto non menzionati)
            all_cards = [c for s in self.index.sets for c in s.cards if self._set_allowed(s.id, text, True)]
            total = len(all_cards)
            wanted = [c for c in all_cards if c.id in wanted_ids]
            ratio = len(wanted) / total if total else 0.0
            ok = ratio >= lot_min_ratio
            return MatchResult("lot", ok, f"set completo: {len(wanted)}/{total} carte mancanti ({ratio:.0%})",
                               wanted=wanted, total_cards=total, wanted_count=len(wanted), ratio=ratio)

        identified = len(refs) + len(ambiguous)
        stated = self._stated_count(text)
        total = max(identified, stated)
        if total == 0:
            return MatchResult("lot", notify_unverifiable, "lotto senza carte identificabili", total_cards=0)
        wanted_count = len(wanted) + sum(1 for a in ambiguous if any(c.id in wanted_ids for c in a.candidates))
        ratio = wanted_count / total
        ok = ratio >= lot_min_ratio and wanted_count > 0
        reason = f"lotto: {wanted_count}/{total} carte mancanti ({ratio:.0%})"
        if stated > identified:
            reason += f", {identified} identificate su {stated} dichiarate"
        return MatchResult("lot", ok, reason, refs=refs, ambiguous=ambiguous, wanted=wanted, possible_wanted=possible,
                           total_cards=total, wanted_count=wanted_count, ratio=ratio)

    @staticmethod
    def _stated_count(text: str) -> int:
        best = 0
        for m in _STATED_COUNT_RE.finditer(text):
            for g in (m.group(1), m.group(4), m.group(5)):
                if g and g.isdigit():
                    best = max(best, int(g))
        return best
