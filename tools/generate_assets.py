from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "static" / "img"
OUT.mkdir(parents=True, exist_ok=True)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def rounded_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def make_hero() -> None:
    w, h = 1800, 1180
    img = Image.new("RGB", (w, h), "#fff5dc")
    draw = ImageDraw.Draw(img)

    for y in range(h):
        r = int(255 - y / h * 16)
        g = int(246 - y / h * 24)
        b = int(221 - y / h * 36)
        draw.line((0, y, w, y), fill=(r, g, b))

    # warm storefront bands
    draw.rectangle((0, 0, w, 142), fill="#8f1f1d")
    draw.rectangle((0, 142, w, 188), fill="#d7a62f")

    rounded_rect(draw, (910, 310, 1665, 955), 34, "#fbf1da", "#e0bf65", 4)
    draw.text((970, 365), "Featured Frozen Products", font=font(43, True), fill="#2b1b17")
    draw.text((973, 422), "Longganisa - Tocino - Tapa - Bacon - Sausages", font=font(25), fill="#725148")

    pack_specs = [
        (980, 545, "#8f2724", "GARLIC", "LONGGANISA", "PHP 60"),
        (1190, 520, "#d95f53", "TOCINO", "ALA EH", "PHP 70"),
        (1400, 545, "#7a1d1d", "BEEF TAPA", "ALA EH", "PHP 99"),
        (1085, 720, "#315241", "CHEESY", "SAUSAGE", "PHP 129"),
        (1305, 720, "#563a2d", "SMOKED", "BACON", "PHP 129"),
    ]
    for x, y, color, top, title, price in pack_specs:
        rounded_rect(draw, (x, y, x + 180, y + 138), 16, "#fff8ee", "#e5c176", 4)
        rounded_rect(draw, (x + 14, y + 15, x + 166, y + 122), 12, color)
        draw.text((x + 28, y + 34), "BATANGAS", font=font(17, True), fill="#fff8ee")
        draw.text((x + 28, y + 58), top, font=font(17, True), fill="#fff8ee")
        draw.text((x + 28, y + 82), title, font=font(18, True), fill="#fff8ee")
        draw.text((x + 28, y + 105), price, font=font(16, True), fill="#ffe1a3")

    overlay = Image.new("RGBA", (w, h), (24, 14, 12, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, 0, 920, h), fill=(255, 245, 220, 12))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=115, threshold=3))
    img.save(OUT / "hero-meat-counter.png", quality=92)


def make_product_story() -> None:
    w, h = 1000, 720
    img = Image.new("RGB", (w, h), "#fff5dc")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w, 145), fill="#8f1f1d")
    draw.text((62, 42), "Store favorites", font=font(44, True), fill="#fff2df")
    draw.text((64, 96), "Processed meats made for convenient everyday cooking", font=font(22), fill="#ffe4a8")
    for i, (title, detail, color) in enumerate(
        [
            ("Premium Line", "Longganisa, Tapa, Tocino", "#8f2724"),
            ("Gold Line", "Sausage, Deli Beef, Rebusado", "#caa052"),
            ("Seasonal", "Hamon Ala Eh and promos", "#415c4d"),
        ]
    ):
        x = 76 + i * 300
        rounded_rect(draw, (x, 230, x + 240, 575), 24, "#fffaf3", "#e1d5c6", 2)
        rounded_rect(draw, (x + 50, 272, x + 190, 412), 18, color, "#53332e", 4)
        draw.text((x + 78, 314), "BP", font=font(44, True), fill="#fff8ee")
        draw.text((x + 42, 448), title, font=font(31, True), fill="#2f211d")
        draw.text((x + 42, 492), detail, font=font(19), fill="#755c52")
    img.save(OUT / "batch-flow.png", quality=92)


def make_reseller_card() -> None:
    w, h = 1000, 720
    img = Image.new("RGB", (w, h), "#8f1f1d")
    draw = ImageDraw.Draw(img)
    rounded_rect(draw, (70, 82, 930, 640), 30, "#f8f0e5", "#d9c8b3", 3)
    draw.text((112, 126), "City Outlet Partnership", font=font(40, True), fill="#2c1e1a")
    draw.text((114, 178), "Bring Batangas Premium products nearer to every area.", font=font(23), fill="#6f5449")
    for i, (label, value) in enumerate([("Visibility", "Local outlet"), ("Credibility", "Premium setup"), ("Profitability", "Area focus")]):
        y = 258 + i * 98
        rounded_rect(draw, (112, y, 842, y + 68), 16, "#fffaf5", "#e6d7c5", 2)
        draw.text((142, y + 19), label, font=font(23, True), fill="#53322a")
        draw.text((590, y + 18), value, font=font(24, True), fill="#8f2724")
    draw.line((112, 550, 842, 550), fill="#d7c7b6", width=2)
    draw.text((112, 575), "Reseller and outlet inquiries", font=font(26, True), fill="#315241")
    img.save(OUT / "reseller-card.png", quality=92)


if __name__ == "__main__":
    make_hero()
    make_product_story()
    make_reseller_card()
