"""Immagine riepilogativa: una riga per annuncio con la miniatura del venditore accanto a prezzo, fonte e titolo."""
from __future__ import annotations

import io
import logging
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image, ImageDraw, ImageFont

from . import config

log = logging.getLogger(__name__)

W = 720
ROW = 132
PAD = 12
THUMB = 108
SOURCE_LABELS = {"ebay": "eBay.it", "vinted": "Vinted", "wallapop": "Wallapop"}
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _font(size: int, bold: bool = False):
    for path in (FONT_BOLD_CANDIDATES if bold else FONT_CANDIDATES):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fetch(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": config.USER_AGENT})
        if r.status_code != 200:
            return None
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:  # noqa: BLE001 - immagine non disponibile: si lascia il riquadro vuoto
        return None


def _cover(img: Image.Image, size: int) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    return img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def build_collage(title: str, rows: list[dict], dark: bool = True) -> bytes | None:
    """rows: [{image, price, source, title, location, lot}] (max 8). Restituisce PNG o None se nessuna immagine."""
    rows = rows[:8]
    if not rows:
        return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        thumbs = list(pool.map(lambda r: _fetch(r.get("image", "")), rows))
    if not any(thumbs):
        return None
    bg, card, fg, mut, acc = ((18, 20, 26), (28, 31, 40), (235, 235, 238), (150, 156, 170), (255, 203, 5)) if dark else \
        ((245, 245, 247), (255, 255, 255), (20, 20, 24), (110, 115, 125), (190, 140, 0))
    head_h = 56
    img = Image.new("RGB", (W, head_h + ROW * len(rows) + PAD), bg)
    d = ImageDraw.Draw(img)
    d.text((PAD, 14), _ellipsize(d, title, _font(26, True), W - 2 * PAD), fill=acc, font=_font(26, True))
    f_price, f_meta, f_title, f_num = _font(24, True), _font(17), _font(18), _font(22, True)
    for i, (r, th) in enumerate(zip(rows, thumbs)):
        y = head_h + i * ROW
        d.rounded_rectangle((PAD, y, W - PAD, y + ROW - 8), radius=14, fill=card)
        tx = PAD + 10
        d.text((tx, y + 10), f"{i + 1}", fill=mut, font=f_num)
        ix = tx + 30
        if th is not None:
            img.paste(_cover(th, THUMB), (ix, y + (ROW - 8 - THUMB) // 2))
        else:
            d.rounded_rectangle((ix, y + 8, ix + THUMB, y + 8 + THUMB), radius=10, outline=mut)
            d.text((ix + 22, y + 54), "n.d.", fill=mut, font=f_meta)
        x = ix + THUMB + 14
        maxw = W - PAD - 10 - x
        price = r.get("price") or "prezzo n.d."
        d.text((x, y + 12), _ellipsize(d, price, f_price, maxw), fill=fg, font=f_price)
        meta = SOURCE_LABELS.get(r.get("source", ""), r.get("source", ""))
        if r.get("location"):
            meta += f" · {r['location']}"
        if r.get("lot"):
            meta += " · lotto"
        d.text((x, y + 46), _ellipsize(d, meta, f_meta, maxw), fill=mut, font=f_meta)
        d.text((x, y + 72), _ellipsize(d, r.get("title", ""), f_title, maxw), fill=fg, font=f_title)
        if len(r.get("title", "")) > 40:
            rest = r["title"][len(_ellipsize(d, r["title"], f_title, maxw).rstrip("…")):].strip()
            if rest:
                d.text((x, y + 96), _ellipsize(d, rest, f_title, maxw), fill=fg, font=f_title)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
