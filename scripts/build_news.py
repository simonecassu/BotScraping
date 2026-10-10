"""Novità per la Mini App (deploy/app/news.html): uscite, mazzi dei tornei, eventi con classifica, annunci.

Scrive <out>/news.json (stessa forma dei dati SAMPLE della pagina), <out>/alerts.json (cose nuove da annunciare in chat,
le manda il bot) e le immagini ridotte in <out>/img/. Gira nel workflow «Novità» (novita.yml) senza database né segreti.

Fonti: TCGdex (api.tcgdex.net: set internazionali e giapponesi, carte già inserite = rivelate, prezzi Cardmarket),
limitlesstcg.com (tornei ufficiali conclusi: top 8 e liste), rk9.gg (calendario e classifiche in diretta degli eventi
Play! Pokémon), pokemon.com/it (annunci e prodotti speciali). Le pagine HTML non hanno un'API: ogni lettore è
tollerante e, se una fonte non risponde, resta l'ultimo risultato buono di quella sezione.

Uso: python scripts/build_news.py --out news [--prev news/news.json] [--completo] [--offline]
Esce con 3 quando non c'è niente da fare (aggiornamento completo una volta l'ora, ogni giro solo con un torneo in corso).
"""
from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

UA = "Pescacarte/1.0 (+https://github.com/simonecassu/BotScraping)"
TCGDEX = "https://api.tcgdex.net/v2"
CATALOG = "https://cdn.jsdelivr.net/gh/PokemonTCG/pokemon-tcg-data@master/sets/en.json"
LIMITLESS = "https://limitlesstcg.com"
RK9 = "https://rk9.gg"
POKEMON_IT = "https://www.pokemon.com/it/novita-pokemon/"
FULL_EVERY_S = 55 * 60
IMG_DIR = "img"
MAX_IMG_PER_SET = 16
TYPES = {"Psychic": "psy", "Fire": "fire", "Water": "water", "Grass": "grass", "Darkness": "dark", "Lightning": "light",
         "Fighting": "fight", "Metal": "col", "Dragon": "col", "Colorless": "col", "Fairy": "psy"}
COLORS = {"psy": ("#7b4bb8", "#2a1145"), "fire": ("#e2572b", "#5a1405"), "water": ("#3b7dd8", "#0c2350"), "grass": ("#4c9a3b", "#12320c"),
          "dark": ("#3c3f55", "#0d0e18"), "light": ("#e8c33b", "#6a4a00"), "fight": ("#b0683a", "#3d1d08"), "col": ("#2f4f86", "#0a1328")}
MESI = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9,
        "ottobre": 10, "novembre": 11, "dicembre": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "oct": 10, "nov": 11, "dec": 12}

LOG: list[str] = []
ERRORS: dict[str, str] = {}


def log(msg: str) -> None:
    LOG.append(msg)
    print(msg, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def fetch(url: str, binary: bool = False, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Language": "it,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def get_json(url: str):
    return json.loads(fetch(url))


def text_of(fragment: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", fragment, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    return re.sub(r"[ \t\r\f\v]+", " ", t)


def diag(name: str, page: str, pattern: str = "") -> None:
    """Quando una pagina non dà risultati: i primi link che somigliano a quello che cerchiamo e le prime righe di tabella."""
    links = [m[1] for m in re.finditer(r'href="([^"]+)"', page) if not pattern or re.search(pattern, m[1])]
    uniq = list(dict.fromkeys(links))[:12]
    log(f"  [{name}] {len(page)} byte, link: {uniq}")
    rows = rows_of(page)[:4]
    for _tr, cells in rows:
        log(f"  [{name}] riga: {[c[:30] for c in cells][:8]}")
    if not rows:
        t = text_of(page)
        log(f"  [{name}] testo: {' '.join(t.split())[:400]}")


def rows_of(page: str) -> list[tuple[str, list[str]]]:
    """Le righe <tr> di una pagina: (html della riga, celle come testo)."""
    out = []
    for tr in re.findall(r"<tr\b.*?</tr>", page, flags=re.S | re.I):
        cells = [text_of(c).strip() for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
        if cells:
            out.append((tr, cells))
    return out


def parse_date(s: str) -> datetime | None:
    """«2026-10-10», «10 ottobre 2026», «Oct 10, 2026», «10/10/2026»."""
    s = s.strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
    m = re.search(r"(\d{1,2})\s+([a-zà]+)\.?\s+(\d{4})", s, flags=re.I)
    if m and m[2].lower()[:3] in {k[:3] for k in MESI}:
        mon = MESI.get(m[2].lower()) or MESI.get(m[2].lower()[:3])
        if mon:
            return datetime(int(m[3]), mon, int(m[1]), tzinfo=timezone.utc)
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        mon = MESI.get(m[1].lower()[:3])
        if mon:
            return datetime(int(m[3]), mon, int(m[2]), tzinfo=timezone.utc)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return datetime(int(m[3]), int(m[2]), int(m[1]), tzinfo=timezone.utc)
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2})\b", s)  # «03 Oct 26» (Limitless)
    if m and MESI.get(m[2].lower()):
        return datetime(2000 + int(m[3]), MESI[m[2].lower()], int(m[1]), tzinfo=timezone.utc)
    return None


def parse_range(s: str) -> tuple[datetime, datetime] | None:
    """«October 9-11, 2026», «October 31 - November 2, 2026», «October 9, 2026» (rk9)."""
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:\s*[-–]\s*(?:([A-Za-z]{3,9})\.?\s+)?(\d{1,2}))?,?\s+(\d{4})", s)
    if not m:
        return None
    m1 = MESI.get(m[1].lower()[:3])
    m2 = MESI.get((m[3] or m[1]).lower()[:3])
    if not m1 or not m2:
        return None
    y = int(m[5])
    start = datetime(y, m1, int(m[2]), tzinfo=timezone.utc)
    end = datetime(y, m2, int(m[4] or m[2]), tzinfo=timezone.utc)
    return start, end


def save_image(url: str, out: Path, name: str) -> str | None:
    """Scarica un'immagine, la riduce a 300 px di lato e la salva come webp: la serve il ponte da /app/news/img/."""
    try:
        from PIL import Image
        raw = fetch(url, binary=True, timeout=30)
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((300, 420))
        (out / IMG_DIR).mkdir(parents=True, exist_ok=True)
        im.save(out / IMG_DIR / f"{name}.webp", "WEBP", quality=72)
        return f"/app/news/{IMG_DIR}/{name}.webp"
    except Exception as exc:  # noqa: BLE001
        log(f"  immagine non salvata ({name}): {exc}")
        return None


# ---------------------------------------------------------------- uscite (TCGdex)
def tcgdex_releases(prev: dict, out: Path, today: datetime) -> list[dict]:
    """Set usciti da poco o in arrivo, internazionali (it, nome italiano) e giapponesi; le carte già inserite valgono come rivelate."""
    prev_rel = {r["id"]: r for r in prev.get("releases", []) if r.get("src") == "tcgdex"}
    releases: list[dict] = []
    for lang, region in (("en", "int"), ("ja", "jp")):
        sets = get_json(f"{TCGDEX}/{lang}/sets")
        recent = sets[-14:]  # l'elenco segue l'ordine delle serie: in coda i set più nuovi
        log(f"  tcgdex {lang}: {len(sets)} set, ultimi {[x['id'] for x in recent[-5:]]}")
        for s in recent:
            try:
                d = get_json(f"{TCGDEX}/{lang}/sets/{s['id']}")
            except Exception as exc:  # noqa: BLE001
                log(f"  set {s['id']}: {exc}")
                continue
            rd = parse_date(d.get("releaseDate") or "")
            if not rd or rd < today - timedelta(days=45) or rd > today + timedelta(days=400):
                continue
            name = d.get("name") or s.get("name")
            if lang == "en":
                try:  # nome italiano quando c'è
                    name = get_json(f"{TCGDEX}/it/sets/{s['id']}").get("name") or name
                except Exception:  # noqa: BLE001
                    pass
            cc = (d.get("cardCount") or {})
            cards = d.get("cards") or []
            old = prev_rel.get(f"tcg-{s['id']}") or {}
            old_rev = {str(c["n"]): c for c in old.get("revealed", [])}
            revealed = []
            fresh = 0
            for c in cards:
                key = str(c.get("localId") or c.get("id"))
                if key in old_rev:
                    revealed.append(old_rev[key])
                    continue
                if fresh >= 40:  # al massimo 40 carte nuove per giro: le altre al prossimo
                    break
                fresh += 1
                detail = {}
                for ln in ((lang, "it") if lang == "en" else (lang,)):
                    try:
                        detail = get_json(f"{TCGDEX}/{ln}/cards/{c['id']}")
                        if detail.get("name"):
                            break
                    except Exception:  # noqa: BLE001
                        continue
                typ = TYPES.get((detail.get("types") or ["Colorless"])[0], "col")
                img = None
                if (c.get("image") or detail.get("image")) and len([r for r in revealed if r.get("img")]) < MAX_IMG_PER_SET:
                    img = save_image(f"{c.get('image') or detail.get('image')}/low.webp", out, f"{s['id']}-{key}")
                n = int(key) if key.isdigit() else key
                revealed.append({"n": n, "name": detail.get("name") or c.get("name") or key, "rarity": detail.get("rarity") or "–",
                                 "type": typ, "when": now_iso(), "img": img})
            status = "uscita" if rd <= today else "preordini" if rd <= today + timedelta(days=45) else "annunciata"
            total = cc.get("official") or cc.get("total") or None
            c1, c2 = COLORS.get(max((r["type"] for r in revealed), key=lambda t: sum(1 for r in revealed if r["type"] == t), default="col"), COLORS["col"])
            releases.append({"id": f"tcg-{s['id']}", "src": "tcgdex", "tcg_set": s["id"], "name": name, "region": region, "kind": "set",
                             "date": iso(rd), "status": status, "cards": total, "highlights": old.get("highlights") or [],
                             "products": old.get("products") or [], "c1": c1, "c2": c2, "revealed": revealed})
    log(f"uscite TCGdex: {len(releases)} set (int {sum(1 for r in releases if r['region'] == 'int')}, jp {sum(1 for r in releases if r['region'] == 'jp')}), "
        f"carte rivelate {sum(len(r['revealed']) for r in releases)}")
    return releases


# ---------------------------------------------------------------- annunci (pokemon.com/it)
KINDS = [("Banlist", r"vietat|banlist|non più (giocabil|ammess)"), ("Rotazione", r"rotazion|marchio di regolament"), ("Regole", r"regol|errat|compendium"),
         ("Evento", r"torneo|campionat|regional|internazional|mondial"), ("Prodotto", r"collezion|box|busta|espansion|set\b|élite|elite|premium")]


def pokemon_news(prev: dict, out: Path) -> tuple[list[dict], list[dict]]:
    """Annunci dalla pagina Novità di pokemon.com/it: gli ultimi articoli, letti dai meta tag della pagina di ogni articolo."""
    old = {u["url"]: u for u in prev.get("updates", []) if u.get("url")}
    old_sp = {r["id"]: r for r in prev.get("releases", []) if r.get("src") == "pokemon.com"}
    page = fetch(POKEMON_IT)
    links = []
    for m in re.finditer(r'href="(/it/novita-pokemon/[^"#?]+)"', page):
        u = "https://www.pokemon.com" + m[1]
        if u not in links and re.search(r"/\d{4}/", u):
            links.append(u)
    links = links[:12]
    log(f"pokemon.com/it: {len(links)} articoli nell'elenco")
    if not links:
        diag("pokemon.com", page, r"novita|news")
    updates, specials = [], []
    seen_new = 0
    for u in links:
        if u in old:
            item = old[u]
        else:
            if seen_new >= 6:
                continue
            seen_new += 1
            try:
                art = fetch(u)
            except Exception as exc:  # noqa: BLE001
                log(f"  articolo non letto: {u} ({exc})")
                continue
            meta = {m[1]: html.unescape(m[2]) for m in re.finditer(r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"', art)}
            title = meta.get("og:title") or (re.search(r"<title>(.*?)</title>", art, flags=re.S) or [None, ""])[1]
            title = re.sub(r"\s*\|.*$", "", text_of(title or "")).strip()
            body = (meta.get("og:description") or meta.get("description") or "").strip()
            dt = parse_date(meta.get("article:published_time") or meta.get("date") or "") or parse_date(text_of(art[:20000])) or datetime.now(timezone.utc)
            kind = "Annuncio"
            for k, rx in KINDS:
                if re.search(rx, title + " " + body, flags=re.I):
                    kind = k
                    break
            img = meta.get("og:image")
            item = {"date": iso(dt), "kind": kind, "title": title or u.rsplit("/", 1)[-1], "body": body[:400], "src": "pokemon.com/it", "url": u, "img": img}
        updates.append(item)
    # prodotti speciali: gli annunci di collezioni e box diventano uscite «prodotto speciale»
    for item in updates:
        if item.get("kind") != "Prodotto" or re.search(r"espansion", item["title"], flags=re.I):
            continue
        sid = "pk-" + re.sub(r"[^a-z0-9]+", "-", item["url"].rsplit("/", 1)[-1].lower())[:40]
        o = old_sp.get(sid)
        img = o.get("img") if o else (save_image(item["img"], out, sid) if item.get("img") else None)
        specials.append({"id": sid, "src": "pokemon.com", "name": item["title"], "region": "int", "kind": "special", "date": item["date"],
                         "status": "annunciata", "cards": None, "highlights": [item["body"][:160]] if item["body"] else [], "products": [],
                         "c1": "#1f5fa8", "c2": "#0b1d3a", "img": img, "revealed": (o or {}).get("revealed", [])})
    log(f"annunci: {len(updates)} ({seen_new} nuovi letti), prodotti speciali {len(specials)}")
    return updates, specials


# ---------------------------------------------------------------- tornei (Limitless) e prezzi (TCGdex)
def limitless_latest_major() -> dict | None:
    """L'ultimo grande torneo concluso nell'elenco di limitlesstcg.com (Regionali, Internazionali, Mondiali)."""
    page = fetch(f"{LIMITLESS}/tournaments")
    best = None
    for tr, cells in rows_of(page):
        m = re.search(r'href="(/tournaments/(\d+))"', tr)
        if not m:
            continue
        name = next((c for c in cells if re.search(r"regional|international|world|championship|special event", c, flags=re.I)), None)
        if not name:
            continue
        d = next((parse_date(c) for c in cells if parse_date(c)), None)
        players = next((int(c.replace(",", "")) for c in cells if re.fullmatch(r"[\d,]{2,6}", c)), None)
        if d and d <= datetime.now(timezone.utc) and (best is None or d > best["date"]):
            best = {"id": m[2], "url": LIMITLESS + m[1], "name": name.strip(), "date": d, "players": players}
    if not best:
        diag("limitless", page, r"/tournaments/")
    return best


def limitless_standings(t: dict) -> list[dict]:
    page = fetch(t["url"])
    top = []
    for tr, cells in rows_of(page):
        if not cells or not re.fullmatch(r"\d{1,3}", cells[0]):
            continue
        place = int(cells[0])
        if place > 32:
            continue
        deck = (re.search(r'href="/decks/([^"/?]+)[^"]*"[^>]*>(.*?)</a>', tr, flags=re.S) or [None, "", ""])
        country = (re.search(r'(?:flag|country)[^>]*?(?:alt|title|data-[a-z]+)="([A-Za-z]{2})"', tr) or [None, ""])[1].upper()
        lst = re.search(r'href="(/tournaments/\d+/decks?/[^"]+|/decks/list/[^"]+)"', tr)
        player = next((c for c in cells[1:] if re.search(r"[A-Za-z]", c) and not re.fullmatch(r"\d+-\d+(-\d+)?", c)), "")
        arche = text_of(deck[2]).strip() if deck and deck[2] else (deck[1].replace("-", " ").title() if deck and deck[1] else "")
        top.append({"place": place, "player": player.strip()[:40], "country": country, "a": arche or "–", "price": None, "missing": None,
                    "list": None, "cards": [], "list_url": LIMITLESS + lst[1] if lst else None})
    if not top or not any(d.get("list_url") for d in top[:8]):
        diag("limitless classifica", page, r"/decks/|/tournaments/\d+/")
    return top


def limitless_decklist(url: str) -> tuple[dict, list]:
    """Una lista: ogni carta è un link /cards/SET/NUMERO con la quantità accanto; i gruppi (Pokémon, Trainer, Energy) dalle
    intestazioni che precedono. Torna i gruppi per la pagina ([quantità, nome, codice]) e le carte ([quantità, 'SET numero'])."""
    page = fetch(url)
    heads = [(m.start(), {"p": "Pokémon", "t": "Allenatori", "e": "Energie"}[m[1][0].lower()])
             for m in re.finditer(r">\s*(Pok[eé]mon|Trainer|Energy)\s*(?:\(\d+\)|\d+)?\s*<", page)]
    groups: dict[str, list] = {"Pokémon": [], "Allenatori": [], "Energie": []}
    cards = []
    prev = 0
    for m in re.finditer(r'<a[^>]+href="/cards/([A-Z0-9]{2,5})/(\d{1,3}[a-zA-Z]?)"[^>]*>(.*?)</a>', page, flags=re.S):
        name = " ".join(text_of(m[3]).split())
        seg = " ".join(text_of(page[max(prev, m.start() - 400):m.start()]).split())
        q = None
        mm = re.match(r"^(\d{1,2})\s+(.*)$", name)
        if mm:
            q, name = int(mm[1]), mm[2]
        else:
            nums = re.findall(r"(?:^|\s)(\d{1,2})(?=\s*$)", seg)
            q = int(nums[-1]) if nums else None
        prev = m.end()
        if not q or q > 60 or not name:
            continue
        grp = "Pokémon"
        for pos, g in heads:
            if pos < m.start():
                grp = g
        code = f"{m[1]} {m[2]}"
        groups[grp].append([q, name, code])
        cards.append([q, code])
    if not cards:
        diag("limitless lista", page, r"cards")
    return groups, cards


def set_maps() -> tuple[dict, dict]:
    """Codice PTCGO (TWM) → id pokemon-tcg-data (sv6), e id pokemon-tcg-data → id TCGdex (per i prezzi)."""
    cat = get_json(CATALOG)
    by_code = {s["ptcgoCode"]: s for s in cat if s.get("ptcgoCode")}
    tcg = get_json(f"{TCGDEX}/en/sets")
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())  # noqa: E731
    by_name = {norm(s["name"]): s["id"] for s in tcg}
    to_tcgdex = {}
    for s in cat:
        k = norm(s["name"])
        if k in by_name:
            to_tcgdex[s["id"]] = by_name[k]
    return by_code, to_tcgdex


def price_cards(top: list[dict], prev: dict, by_code: dict, to_tcgdex: dict) -> dict:
    """Prezzo di ogni lista: somma dei trend Cardmarket (TCGdex). I prezzi restano in cache per un giorno."""
    cache = {k: v for k, v in (prev.get("prices") or {}).items() if time.time() - v.get("ts", 0) < 86400}
    for d in top:
        if not d.get("cards"):
            continue
        total, missing_price = 0.0, 0
        resolved = []
        for q, code in d["cards"]:
            sc, num = code.split(" ", 1)
            s = by_code.get(sc)
            tid = f"{s['id']}-{num}" if s else None
            resolved.append([q, code, tid])
            if not tid:
                missing_price += 1
                continue
            if tid not in cache:
                tx = to_tcgdex.get(s["id"])
                price = None
                if tx:
                    try:
                        cm = (get_json(f"{TCGDEX}/en/cards/{tx}-{num}").get("pricing") or {}).get("cardmarket") or {}
                        price = cm.get("trend") or cm.get("avg7") or cm.get("avg30") or cm.get("low")
                    except Exception:  # noqa: BLE001
                        price = None
                cache[tid] = {"p": price, "ts": time.time()}
            p = cache[tid].get("p")
            if p is None:
                missing_price += 1
            else:
                total += p * q
        d["cards"] = resolved
        d["price"] = round(total) if total else None
        d["price_note"] = f"{missing_price} carte senza prezzo" if missing_price else ""
    return cache


def tournaments(prev: dict) -> tuple[dict | None, dict]:
    t = limitless_latest_major()
    if not t:
        log("tornei: nessun grande torneo trovato nell'elenco")
        return None, prev.get("prices") or {}
    old = prev.get("decks") or {}
    if old.get("id") == t["id"] and old.get("top") and all(d.get("list") or not d.get("list_url") for d in old["top"][:8]):
        log(f"tornei: {t['name']} già letto")
        return old, prev.get("prices") or {}
    top = limitless_standings(t)
    for d in top[:8]:
        if d.get("list_url"):
            try:
                d["list"], d["cards"] = limitless_decklist(d["list_url"])
                if not d["cards"]:
                    d["list"] = None
            except Exception as exc:  # noqa: BLE001
                log(f"  lista {d['place']}°: {exc}")
    prices = {}
    try:
        by_code, to_tcgdex = set_maps()
        prices = price_cards(top[:8], prev, by_code, to_tcgdex)
    except Exception as exc:  # noqa: BLE001
        log(f"  prezzi: {exc}")
    counts: dict[str, int] = {}
    for d in top:
        counts[d["a"]] = counts.get(d["a"], 0) + 1
    n = max(1, len(top))
    meta = sorted(({"a": a, "s": round(100 * c / n, 1)} for a, c in counts.items() if a != "–"), key=lambda x: -x["s"])[:6]
    decks = {"id": t["id"], "event": t["name"], "date": iso(t["date"]), "players": t["players"], "format": "Standard", "url": t["url"],
             "meta_note": f"quota tra i primi {len(top)} classificati", "meta": meta, "top": top[:8]}
    log(f"tornei: {t['name']} ({t['date'].date()}), classificati letti {len(top)}, liste {sum(1 for d in top[:8] if d.get('list'))}/8")
    return decks, prices


# ---------------------------------------------------------------- eventi e dirette (rk9.gg)
def rk9_events(prev: dict) -> list[dict]:
    page = fetch(f"{RK9}/events/pokemon")
    evs = []
    today = datetime.now(timezone.utc)
    for tr, cells in rows_of(page):
        ids = re.findall(r'href="/tournament/([A-Za-z0-9]+-[A-Za-z0-9]+)"', tr)
        m = ids or re.findall(r'href="/event/([A-Za-z0-9-]+)"', tr)
        name = next((c for c in cells if re.search(r"regional|international|world|championship|special", c, flags=re.I)), None)
        if not m or not name or not re.search(r"TCG|Trading Card|Pok[eé]mon", " ".join(cells), flags=re.I):
            continue
        rng = parse_range(" ".join(cells))
        if not rng:
            continue
        start, end = rng
        if end < today - timedelta(days=2) or start > today + timedelta(days=400):
            continue
        loc = next((c for c in cells if c and c != name and not parse_date(c) and re.search(r"[A-Za-z]", c) and len(c) < 60), "")
        kind = "Mondiale" if re.search(r"world", name, flags=re.I) else "Internazionale" if re.search(r"international", name, flags=re.I) else "Regionale"
        city, _, country = loc.partition(",")
        evs.append({"id": m[0], "ids": ids, "name": re.sub(r"\s+", " ", name.strip()), "kind": kind, "city": city.strip(), "country": country.strip(),
                    "start": iso(start.replace(hour=8)), "end": iso(end.replace(hour=20)), "home": bool(re.search(r"ital", loc, flags=re.I))})
    log(f"eventi rk9: {len(evs)} in calendario")
    if not evs:
        diag("rk9", page, r"tournament|event|pairings")
    return evs


def rk9_live(ev: dict) -> dict | None:
    """Classifica in diretta di un evento in corso: prime 8 posizioni della tabella standings (masters TCG)."""
    tried = []
    for tid in (ev.get("ids") or [ev["id"]]):
        for url in (f"{RK9}/standings/{tid}", f"{RK9}/pairings/{tid}?pod=2&rnd=99"):
            try:
                page = fetch(url)
            except Exception as exc:  # noqa: BLE001
                tried.append(f"{url} ({str(exc)[:40]})")
                continue
            tried.append(url)
            if not re.search(r"TCG|Trading Card|Masters", page, flags=re.I):
                continue
            rows = []
            for tr, cells in rows_of(page):
                if cells and re.fullmatch(r"\d{1,4}", cells[0]) and len(cells) >= 3:
                    rec = next((c for c in cells if re.fullmatch(r"\d+-\d+(-\d+)?", c)), "")
                    player = next((c for c in cells[1:] if re.search(r"[A-Za-z]{2}", c) and c != rec), "")
                    country = (re.search(r'\b([A-Z]{2})\b', " ".join(cells[1:4])) or [None, ""])[1]
                    rows.append({"pos": int(cells[0]), "player": player[:40], "country": country, "a": "", "rec": rec})
                if len(rows) >= 8:
                    break
            if rows:
                rnd = re.search(r"[Rr]ound\s+(\d+)", page)
                return {"round": int(rnd[1]) if rnd else 0, "of": 0, "day": 1, "updated": now_iso(), "standings": rows}
            diag("rk9 diretta", page, r"standings|pairings|pod")
    log(f"  diretta {ev['name']}: niente classifica; provati {tried}")
    return None


# ---------------------------------------------------------------- avvisi
def alerts(prev: dict, cur: dict) -> list[dict]:
    out = []
    pr = {r["id"]: r for r in prev.get("releases", [])}
    for r in cur["releases"]:
        o = pr.get(r["id"])
        d = datetime.fromisoformat(r["date"])
        when = f"{d.day} {list(MESI)[d.month - 1]} {d.year}"
        if not o:
            dove = "in Giappone" if r["region"] == "jp" else ""
            out.append({"id": f"rel-{r['id']}", "cat": "espansioni", "text": f"📦 Nuova uscita: <b>{html.escape(r['name'])}</b> {dove}· {when}" + (f" · {r['cards']} carte" if r.get("cards") else "")})
        elif o.get("status") != r["status"] and r["status"] in ("preordini", "uscita"):
            out.append({"id": f"st-{r['id']}-{r['status']}", "cat": "espansioni", "text": (f"🛒 Preordini aperti per <b>{html.escape(r['name'])}</b> (esce il {when})" if r["status"] == "preordini" else f"🎉 Oggi esce <b>{html.escape(r['name'])}</b>")})
        else:
            new_cards = len(r.get("revealed", [])) - len(o.get("revealed", []))
            if new_cards >= 3:
                out.append({"id": f"rev-{r['id']}-{len(r['revealed'])}", "cat": "espansioni", "text": f"🃏 {new_cards} carte nuove rivelate per <b>{html.escape(r['name'])}</b>: guardale in Novità → Uscite"})
    d = cur.get("decks")
    if d and d.get("id") != (prev.get("decks") or {}).get("id") and d.get("top"):
        w = d["top"][0]
        out.append({"id": f"deck-{d['id']}", "cat": "tornei", "text": f"🏆 <b>{html.escape(d['event'])}</b>: top 8 pubblicata. 1° {html.escape(w['player'])} con {html.escape(w['a'])}" + (f" ({w['price']} € su Cardmarket)" if w.get("price") else "") + ". Liste e prezzi in Novità → Mazzi."})
    pe = {e["id"]: e for e in prev.get("events", [])}
    for e in cur["events"]:
        o = pe.get(e["id"])
        if e.get("live") and not (o or {}).get("live"):
            out.append({"id": f"live-{e['id']}", "cat": "live", "text": f"🔴 È iniziato <b>{html.escape(e['name'])}</b> ({html.escape(e.get('city') or '')}): classifica in diretta in Novità → Eventi."})
    pu = {u["url"]: u for u in prev.get("updates", []) if u.get("url")}
    for u in cur["updates"]:
        if u.get("url") and u["url"] not in pu and prev.get("updates"):
            out.append({"id": f"upd-{u['url'].rsplit('/', 1)[-1][:60]}", "cat": "aggiornamenti", "text": f"📣 {html.escape(u['kind'])}: <b>{html.escape(u['title'])}</b>\n{html.escape(u['body'][:200])}"})
    return out


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news")
    ap.add_argument("--prev", default="")
    ap.add_argument("--completo", action="store_true", help="aggiornamento completo anche se l'ultimo è recente")
    ap.add_argument("--offline", action="store_true", help="niente rete: solo la struttura (per le prove)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prev = {}
    if args.prev and Path(args.prev).exists():
        try:
            prev = json.loads(Path(args.prev).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = {}
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now(timezone.utc)
    last = datetime.fromisoformat(prev["updated"]) if prev.get("updated") else None
    live_prev = [e for e in prev.get("events", []) if e.get("start") and e.get("end") and e["start"] <= now_iso() <= e["end"]]
    full = args.completo or not last or (now - last).total_seconds() >= FULL_EVERY_S
    if not full and not live_prev:
        print("niente da fare: ultimo aggiornamento recente e nessun torneo in corso")
        return 3
    cur = {"updated": now_iso(), "releases": prev.get("releases", []), "decks": prev.get("decks"), "events": prev.get("events", []),
           "updates": prev.get("updates", []), "prices": prev.get("prices") or {}, "sources": dict(prev.get("sources") or {})}
    if args.offline:
        full = False
    if full:
        for name, fn in (("tcgdex", lambda: tcgdex_releases(prev, out, today)), ("pokemon.com", lambda: pokemon_news(prev, out)),
                         ("limitless", lambda: tournaments(prev)), ("rk9", lambda: rk9_events(prev))):
            try:
                res = fn()
                if name == "tcgdex":
                    cur["releases"] = [r for r in cur["releases"] if r.get("src") != "tcgdex"] + res
                elif name == "pokemon.com":
                    updates, specials = res
                    cur["updates"] = updates
                    cur["releases"] = [r for r in cur["releases"] if r.get("src") != "pokemon.com"] + specials
                elif name == "limitless":
                    decks, prices = res
                    if decks:
                        cur["decks"] = decks
                    cur["prices"] = prices
                else:
                    if res:
                        cur["events"] = res
                cur["sources"][name] = {"ok": now_iso()}
            except Exception as exc:  # noqa: BLE001
                ERRORS[name] = str(exc)[:200]
                cur["sources"][name] = {**(cur["sources"].get(name) or {}), "error": str(exc)[:200], "error_at": now_iso()}
                log(f"{name}: ERRORE {exc}")
    # dirette: ogni giro, solo per gli eventi in corso
    for e in cur["events"]:
        if e["start"] <= now_iso() <= e["end"] and not args.offline:
            try:
                live = rk9_live(e)
                if live:
                    e["live"] = live
                    log(f"diretta {e['name']}: {len(live['standings'])} in classifica, turno {live['round']}")
            except Exception as exc:  # noqa: BLE001
                log(f"diretta {e['name']}: {exc}")
        elif e.get("live") and e["end"] < now_iso():
            e.pop("live", None)
    if full and not args.offline:
        try:  # id del catalogo pokemon-tcg-data (quello del bot) per i set TCGdex, per «Segui» e «Avvisami»
            norm = lambda x: re.sub(r"[^a-z0-9]+", "", x.lower())  # noqa: E731
            cat = {norm(c["name"]): c["id"] for c in get_json(CATALOG)}
            for r in cur["releases"]:
                if r.get("src") == "tcgdex" and r.get("region") == "int" and not r.get("ptcg"):
                    r["ptcg"] = cat.get(norm(r["name"])) or cat.get(norm(get_json(f"{TCGDEX}/en/sets/{r['tcg_set']}").get("name") or ""))
        except Exception as exc:  # noqa: BLE001
            log(f"catalogo: {exc}")
    cur["releases"].sort(key=lambda r: r["date"])
    cur["alerts_n"] = 0
    new_alerts = alerts(prev, cur) if prev else []
    (out / "news.json").write_text(json.dumps(cur, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "alerts.json").write_text(json.dumps({"generated": now_iso(), "alerts": new_alerts}, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"scritto {out / 'news.json'}: uscite {len(cur['releases'])}, mazzi {'sì' if cur.get('decks') else 'no'}, eventi {len(cur['events'])}, "
        f"annunci {len(cur['updates'])}, avvisi nuovi {len(new_alerts)}, errori {ERRORS or 'nessuno'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
