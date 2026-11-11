"""Lay out the six unmodified Blender renders as a labeled comparison sheet."""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/color-studies"
EDGY = "--edgy" in sys.argv
ZED = "--zed" in sys.argv
if EDGY:
    OUT = ROOT / "docs/color-studies-edgy"
if ZED:
    OUT = ROOT / "docs/color-studies-zed"
PALETTES = json.loads((OUT / "palettes.json").read_text())
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


width, margin, gap, tile = 3000, 80, 40, 920
columns = 3
if ZED:
    columns, width = 2, 2040
image_height, label_height, top = 844, 120, 190
row_height = image_height + label_height
height = top + 2 * row_height + gap + 86
canvas = Image.new("RGB", (width, height), "#0C0C12" if ZED else "#11151B" if EDGY else "#F3F0EB")
draw = ImageDraw.Draw(canvas)
ink, muted = ("#E1E0DB", "#B9B6B9") if ZED else ("#EDF0E8", "#96A0AB") if EDGY else ("#243137", "#66706F")
draw.text((margin, 40), "D O T T Y", font=font(64, True), fill=ink)
draw.text((margin + 370, 69), "/  ZED INSPIRED" if ZED else "/  AFTER HOURS" if EDGY else "/  COLOR STUDIES", font=font(24), fill="#FFFF00" if ZED else muted)
draw.text((width - margin, 48), f"{len(PALETTES):02d} PALETTES  /  BLENDER CYCLES", font=font(22), fill=ink, anchor="ra")
draw.text((width - margin, 85), "Black sand / neon yellow / muted violet" if ZED else "Bold color / dark studio / countersunk hardware" if EDGY else "Sliced puck + battery disk  |  Matte finish exploration", font=font(22), fill=muted, anchor="ra")
draw.line((margin, 144, width-margin, 144), fill="#FFFF00" if ZED else "#CBCAC2", width=2)

for i, palette in enumerate(PALETTES):
    col, row = i % columns, i // columns
    x, y = margin + col * (tile + gap), top + row * (row_height + gap)
    image = Image.open(OUT / (palette["slug"] + ".png")).convert("RGB")
    image = image.resize((tile, image_height), Image.Resampling.LANCZOS)
    mask = Image.new("L", image.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile-1, image_height-1), radius=15, fill=255)
    canvas.paste(image, (x, y), mask)
    draw.text((x, y + image_height + 22), f"0{i+1}", font=font(23), fill=muted)
    draw.text((x + 53, y + image_height + 14), palette["name"], font=font(35, True), fill=ink)
    for j, part in enumerate(("body", "bezel", "base")):
        cx = x + tile - 105 + 38*j
        cy = y + image_height + 38
        draw.ellipse((cx-13, cy-13, cx+13, cy+13), fill="#" + palette[part], outline="#D6D3CB", width=1)
    draw.text((x, y + image_height + 65), palette["subtitle"], font=font(21), fill=muted)

footer_y = height - 48
draw.line((margin, footer_y - 23, width-margin, footer_y - 23), fill="#CBCAC2", width=1)
draw.text((margin, footer_y), "Zed-inspired visual concepts. Screen colors are illustrative; firmware and CAD are unchanged." if ZED else "Existing CAD geometry. Illustrative screen + fasteners. Colors and finishes are visual studies.", font=font(20), fill=muted)
draw.text((width-margin, footer_y), "BODY  /  BEZEL  /  BASE", font=font(20), fill=muted, anchor="ra")
name = "dotty-zed-colorways.png" if ZED else "dotty-six-edgy-colorways.png" if EDGY else "dotty-six-colorways.png"
canvas.save(OUT / name, optimize=True)
print(OUT / name)
