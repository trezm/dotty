"""Illustrate the firmware layout; this is a preview, not a hardware capture."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BG, PANEL, MUTED, ACCENT = "#080c10", "#181c28", "#909898", "#48dca8"
image = Image.new("RGB", (240, 240), BG)
draw = ImageDraw.Draw(image)
font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"


def text(value, xy, size, fill):
    font = ImageFont.truetype(font_path, size)
    draw.text(xy, value, font=font, fill=fill, anchor="mm")


draw.ellipse((4, 4, 236, 236), outline=PANEL, width=1)
text("D O T T Y", (120, 41), 15, ACCENT)
text("Hello, world!", (120, 88), 26, "#ffffff")
text("Taps: 0", (120, 123), 16, MUTED)
for x, label in [(38, "+1"), (126, "Color")]:
    draw.rounded_rectangle((x, 153, x + 75, 192), radius=12, fill=ACCENT)
    text(label, (x + 38, 173), 16, BG)
text("XIAO ESP32-C6", (120, 211), 9, MUTED)
image.save(ROOT / "docs/display-texture.png")
image.resize((960, 960), Image.Resampling.NEAREST).save(ROOT / "docs/display-preview.png")
