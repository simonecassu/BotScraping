import io

from PIL import Image

from pokebot import collage


def _png(color):
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), color).save(buf, format="PNG")
    return buf.getvalue()


def test_build_collage(monkeypatch):
    monkeypatch.setattr(collage, "_fetch", lambda url: Image.open(io.BytesIO(_png("red"))).convert("RGB") if url else None)
    rows = [{"image": "https://a", "price": "8,00 €", "source": "vinted", "title": "Pokemon Hisuian-Zorua 145/128 Eng molto lungo titolo che va a capo", "location": ""},
            {"image": "", "price": "15,00 €", "source": "ebay", "title": "Zorua", "location": "Roma", "lot": True},
            {"image": "https://c", "price": "", "source": "wallapop", "title": "x"}]
    png = collage.build_collage("Hisuian Zorua 145/128", rows)
    assert png and png[:4] == b"\x89PNG"
    img = Image.open(io.BytesIO(png))
    assert img.size == (collage.W, 56 + collage.ROW * 3 + collage.PAD)
    # nessuna immagine scaricabile -> None (si ripiega su album/carta)
    monkeypatch.setattr(collage, "_fetch", lambda url: None)
    assert collage.build_collage("x", rows) is None
