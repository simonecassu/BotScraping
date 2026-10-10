"""Banco di prova di «Valuta carta» (deploy/app/scan.html) su carte vere.

Prende gli scan ufficiali del catalogo pubblico (pokemon-tcg-data: diverse epoche, bordi gialli, argento, pieni),
misura su ognuno la cornice "vera" (lo scan è pulito: la larghezza del bordo si legge direttamente), poi li mette su
uno sfondo con ombra e prospettiva come in una foto e li fa analizzare alla pagina in un browser senza finestra.
Stampa, per ogni carta: tipo di bordo, carta trovata (errore degli angoli), cornice trovata per lato contro quella
vera. Gira in GitHub Actions (scanner-test.yml): da lì le immagini si scaricano senza blocchi.

  python scripts/scanner_test.py                 scan veri (serve Internet)
  python scripts/scanner_test.py --offline       carte disegnate al volo, per provare il banco stesso
"""
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import random
import sys
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master"
# epoche diverse = bordi diversi: gialli (dal 1999 a Spada e Scudo), argento (Scarlatto e Violetto), 30th
SETS = ["base1", "neo1", "ex1", "dp1", "bw1", "xy1", "sm1", "swsh1", "swsh12", "sv1", "sv8", "me55"]
PER_SET = 4  # una carta per rarità, al massimo queste
MM_W, MM_H = 63.0, 88.0


def fetch(url: str, binary: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": "Pescacarte scanner-test"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if binary else json.loads(data)


def sample_cards() -> list[dict]:
    """Per ogni set, la prima carta di ogni rarità (così entrano comuni, holo, ex/V, full art, oro, allenatori)."""
    out = []
    for sid in SETS:
        try:
            cards = fetch(f"{RAW}/cards/en/{sid}.json")
        except Exception as exc:  # noqa: BLE001
            print(f"  {sid}: {exc}", file=sys.stderr)
            continue
        seen = set()
        for c in cards:
            r = c.get("rarity") or "?"
            if r in seen:
                continue
            seen.add(r)
            url = (c.get("images") or {}).get("large") or (c.get("images") or {}).get("small")
            if url:
                out.append({"id": c["id"], "name": c["name"], "set": sid, "rarity": r, "supertype": c.get("supertype"), "url": url})
            if len(seen) >= PER_SET:
                break
    return out


# ---------------------------------------------------------------- cornice vera sullo scan pulito
def border_truth(im: Image.Image) -> dict:
    """Larghezza del bordo per lato (mm) letta sullo scan: colore del bordo a 0,4-0,8 mm dal taglio, prima distanza entro
    6 mm in cui il colore cambia nettamente lungo tutta la fascia centrale. 'none' = nessun bordo (disegno fino al taglio)."""
    im = im.convert("RGB")
    W, H = im.size
    ppm = W / MM_W  # pixel per mm
    px = im.load()
    out = {"ppm": round(ppm, 2)}
    colors = []
    for side in "LRTB":
        length = H if side in "LR" else W
        t0, t1 = int(length * 0.2), int(length * 0.8)
        prof = []
        for o in range(int(6.5 * ppm)):
            s = [0, 0, 0]
            for t in range(t0, t1, 2):
                p = px[o, t] if side == "L" else px[W - 1 - o, t] if side == "R" else px[t, o] if side == "T" else px[t, H - 1 - o]
                s[0] += p[0]; s[1] += p[1]; s[2] += p[2]
            n = len(range(t0, t1, 2))
            prof.append((s[0] / n, s[1] / n, s[2] / n))
        a, b = int(0.4 * ppm), int(0.8 * ppm) + 1
        ref = tuple(sum(p[c] for p in prof[a:b]) / (b - a) for c in range(3))
        colors.append(ref)
        width = None
        for o in range(b, len(prof)):
            if sum(abs(prof[o][c] - ref[c]) for c in range(3)) > 60:
                width = o / ppm
                break
        out[side] = round(width, 2) if width else None
    r, g, bl = (sum(c[i] for c in colors) / 4 for i in range(3))
    lum = 0.299 * r + 0.587 * g + 0.114 * bl
    chroma = max(r, g, bl) - min(r, g, bl)
    kind = "nero" if lum < 70 else "bianco" if lum > 215 and chroma < 25 else "argento" if chroma < 30 else "giallo" if r > 150 and g > 120 and bl < 110 else "colorato"
    if all(out[s] is None for s in "LRTB"):
        kind += " (senza cornice)"
    out["kind"] = kind
    out["color"] = tuple(int(v) for v in (r, g, bl))
    return out


# ---------------------------------------------------------------- "foto" costruite: sfondo, ombra, prospettiva
def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c] / M[c][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def _persp_coeffs(src, dst):
    A, bvec = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); bvec.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); bvec.append(v)
    return _solve(A, bvec)


def _apply(coeffs, x, y):
    """Inverso della trasformazione di Pillow (che mappa destinazione → sorgente): dove finisce un punto della sorgente."""
    a, b, c, d, e, f, g, h = coeffs
    # risolvo (x,y) = T(u,v) per (u,v): due equazioni lineari in u,v dopo moltiplicazione per il denominatore
    A = [[a - g * x, b - h * x], [d - g * y, e - h * y]]
    B = [x - c, y - f]
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    return ((B[0] * A[1][1] - A[0][1] * B[1]) / det, (A[0][0] * B[1] - B[0] * A[1][0]) / det)


VARIANTS = {
    "carta": {"bg": (205, 203, 196), "shadow": 0.45, "tilt": 0.0},      # foglio chiaro, ombra leggera
    "panno": {"bg": (48, 50, 56), "shadow": 0.7, "tilt": 0.025},         # sfondo scuro, ombra forte, un po' di prospettiva
}


def compose(scan: Image.Image, variant: str, seed: int) -> tuple[Image.Image, list]:
    """Lo scan su uno sfondo, con ombra dello spessore e prospettiva. Restituisce la foto e i 4 angoli veri della carta."""
    v = VARIANTS[variant]
    rnd = random.Random(seed)
    scan = scan.convert("RGB")
    target_w = 760
    scan = scan.resize((target_w, round(target_w * scan.height / scan.width)), Image.LANCZOS)
    cw, ch = 1200, 1500
    page = Image.new("RGB", (cw, ch), v["bg"])
    ox, oy = (cw - scan.width) // 2 + rnd.randint(-30, 30), (ch - scan.height) // 2 + rnd.randint(-30, 30)
    mask = Image.new("L", scan.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, scan.width - 1, scan.height - 1], radius=int(scan.width * 0.035), fill=255)
    sm = Image.new("L", (cw, ch), 0)
    sm.paste(mask, (ox + 9, oy + 13))
    sm = sm.filter(ImageFilter.GaussianBlur(10)).point(lambda p: int(p * v["shadow"]))
    page.paste(Image.new("RGB", (cw, ch), (20, 20, 24)), (0, 0), sm)
    page.paste(scan, (ox, oy), mask)
    corners = [(ox, oy), (ox + scan.width, oy), (ox + scan.width, oy + scan.height), (ox, oy + scan.height)]
    if v["tilt"]:
        k = v["tilt"]
        src = [(0, 0), (cw, 0), (cw, ch), (0, ch)]
        dst = [(k * cw, 0), (cw - k * cw, 0), (cw, ch), (0, ch)]
        coeffs = _persp_coeffs(src, dst)  # Pillow: dal punto di destinazione al punto sorgente
        page = page.transform((cw, ch), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=v["bg"])
        corners = [_apply(coeffs, x, y) for x, y in corners]
    px = page.load()
    for y in range(0, ch, 3):
        for x in range(0, cw, 3):
            n = rnd.randint(-6, 6)
            r, g, b = px[x, y]
            px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
    return page, corners


def synthetic_scan(kind: str, seed: int) -> Image.Image:
    """Carta disegnata (per --offline): bordo giallo o argento, cornice stampata e disegno a macchie."""
    rnd = random.Random(seed)
    W, H = 734, 1024
    border = (240, 196, 50) if kind == "giallo" else (190, 190, 192)
    im = Image.new("RGB", (W, H), border)
    d = ImageDraw.Draw(im)
    L, R, T, B = [int(rnd.uniform(1.6, 2.6) * W / MM_W) for _ in range(4)]
    d.rectangle([L, T, W - 1 - R, H - 1 - B], fill=(40, 40, 60))
    d.rectangle([L + 5, T + 5, W - 6 - R, H - 6 - B], fill=(70, 120, 200))
    for _ in range(500):
        x, y = rnd.randint(L + 10, W - R - 40), rnd.randint(T + 10, H - B - 40)
        d.rectangle([x, y, x + rnd.randint(4, 30), y + rnd.randint(4, 30)], fill=(rnd.randint(30, 230), rnd.randint(30, 230), rnd.randint(30, 230)))
    return im


# ---------------------------------------------------------------- la pagina in un browser senza finestra
def run_page(photos: list[dict], out: pathlib.Path) -> list[dict]:
    from playwright.sync_api import sync_playwright
    html = (ROOT / "deploy/app/scan.html").read_bytes()
    results = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROME_PATH") or None)  # in locale: il Chromium già presente
        pg = b.new_page(viewport={"width": 390, "height": 800})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())

        def route(r):
            u = r.request.url
            if "telegram-web-app.js" in u:
                return r.fulfill(content_type="application/javascript", body="")
            if u.rstrip("/").endswith("/app/scan"):
                return r.fulfill(body=html, content_type="text/html")
            return r.fulfill(status=404, body="")

        pg.route("**/*", route)
        for ph in photos:
            pg.goto("https://pescacarte.test/app/scan")
            pg.wait_for_timeout(150)
            t0 = time.time()
            pg.set_input_files("#file-front", str(ph["path"]))
            try:
                pg.wait_for_selector("#crop:not([hidden])", timeout=60000)
            except Exception:  # noqa: BLE001 - foto rifiutata o carta non trovata
                results.append({**ph, "err": pg.evaluate("document.querySelector('#err').textContent")})
                continue
            quad = pg.evaluate("window.__crop.quad.map(p => p.slice())")
            title = pg.evaluate("document.querySelector('#crop-title').textContent")
            pg.click("#crop-ok")
            pg.click("#analyze")
            pg.wait_for_selector("#results:not([hidden])", timeout=60000)
            pg.wait_for_timeout(100)
            res = pg.evaluate("""(() => { const r = window.__cs.front.res; const side = k => r.bw[k] ? { w: +(r.bw[k].w / 10).toFixed(2), jump: Math.round(r.bw[k].jump), n: r.bw[k].n ?? null } : null;
              return { L: side('L'), R: side('R'), T: side('T'), B: side('B'), unsure: !!r.unsure, cent: r.cent ? r.cent.g : null, geo: r.geo ? { rot: +r.geo.rot.toFixed(2), dev: +Math.max(r.geo.dev, r.geo.bend).toFixed(2) } : null }; })()""")
            results.append({**ph, "quad": quad, "title": title, "res": res, "secs": round(time.time() - t0, 1)})
        b.close()
    if errs:
        print("errori JS:", errs[:5])
    return results


def quad_error(found: list, truth: list) -> float:
    """Errore medio degli angoli in px: ogni angolo vero contro il più vicino tra quelli trovati."""
    return sum(min(((fx - tx) ** 2 + (fy - ty) ** 2) ** 0.5 for fx, fy in found) for tx, ty in truth) / 4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="carte disegnate al volo invece degli scan veri")
    ap.add_argument("--out", default="/tmp/scanner-test")
    ap.add_argument("--limit", type=int, default=0, help="al massimo tante carte")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    (out / "foto").mkdir(parents=True, exist_ok=True)
    if args.offline:
        cards = [{"id": f"synth-{k}-{i}", "name": f"{k} {i}", "set": "synth", "rarity": k, "scan": synthetic_scan(k, i)} for k in ("giallo", "argento") for i in range(2)]
    else:
        cards = sample_cards()
        if args.limit:
            cards = cards[:args.limit]
        kept = []
        for c in cards:
            try:
                c["scan"] = Image.open(io.BytesIO(fetch(c["url"], binary=True)))
                kept.append(c)
            except Exception as exc:  # noqa: BLE001
                print(f"  {c['id']}: immagine non scaricata ({exc})", file=sys.stderr)
        cards = kept
    photos = []
    for i, c in enumerate(cards):
        c["truth"] = border_truth(c["scan"])
        for variant in VARIANTS:
            img, corners = compose(c["scan"], variant, seed=i)
            path = out / "foto" / f"{c['id']}-{variant}.jpg"
            img.save(path, quality=90)
            photos.append({"id": c["id"], "name": c["name"], "set": c["set"], "rarity": c["rarity"], "variant": variant, "path": path,
                           "corners": corners, "truth": c["truth"]})
    results = run_page(photos, out)
    # ---- tabella
    print(f"\n{'carta':<14} {'rarità':<24} {'bordo':<22} {'var':<6} {'angoli px':>9}  {'cornice vera S/D/A/B mm':<26} {'trovata S/D/A/B mm (salto)':<40} note")
    stats = {}
    for r in results:
        t = r["truth"]
        truth_s = "/".join("-" if t[k] is None else f"{t[k]:.1f}" for k in "LRTB")
        if "err" in r:
            print(f"{r['id']:<14} {r['rarity'][:24]:<24} {t['kind']:<22} {r['variant']:<6} {'-':>9}  {truth_s:<26} {'-':<40} {r['err'][:60]}")
            stats.setdefault(t["kind"], []).append(("fail", None))
            continue
        qe = quad_error(r["quad"], r["corners"])
        f = r["res"]
        found = "/".join("-" if not f[k] else f"{f[k]['w']:.1f}({f[k]['jump']})" for k in "LRTB")
        errs = [abs(f[k]["w"] - t[k]) for k in "LRTB" if f[k] and t[k] is not None]
        note = []
        if f["unsure"]:
            note.append("verifica")
        if f["geo"]:
            note.append(f"rot {f['geo']['rot']}° dist {f['geo']['dev']}mm")
        if errs:
            note.append(f"err max {max(errs):.2f} mm")
        print(f"{r['id']:<14} {r['rarity'][:24]:<24} {t['kind']:<22} {r['variant']:<6} {qe:>9.1f}  {truth_s:<26} {found:<40} {' · '.join(note)}")
        stats.setdefault(t["kind"], []).append(("ok", qe, errs, sum(1 for k in 'LRTB' if f[k] is None and t[k] is not None)))
    print("\nriepilogo per tipo di bordo:")
    for kind, rows in stats.items():
        ok = [x for x in rows if x[0] == "ok"]
        if not ok:
            print(f"  {kind:<22} carta mai trovata ({len(rows)} foto)")
            continue
        q = sum(x[1] for x in ok) / len(ok)
        e = [v for x in ok for v in x[2]]
        miss = sum(x[3] for x in ok)
        print(f"  {kind:<22} foto {len(rows)}, carta trovata {len(ok)}, angoli ±{q:.1f} px, cornice: errore medio {sum(e) / len(e) if e else 0:.2f} mm, max {max(e) if e else 0:.2f} mm, lati non trovati {miss}")
    (out / "risultati.json").write_text(json.dumps([{k: v for k, v in r.items() if k not in ("path",)} for r in results], default=str, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
