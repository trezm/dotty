"""Render the shared firmware painter with current metadata; no hardware writes.

This is a layout preview, not a photograph/capture. Font rasterization is the
host equivalent; labels, drawing commands, colors and positions are shared.
"""
import argparse
import json
import re
import math
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))
from snapshot import RUNTIME
from usage import read_windows
from dotty_companion import read_state
from claude_status import code_count_status

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--example", action="store_true", help="Clearly named synthetic example, not live metadata")
parser.add_argument("--battery-mv", type=int, help="Illustrative battery voltage for layout review; not a live measurement")
parser.add_argument("--time-ms", type=int, default=0, help="Animation instant for the host layout preview")
parser.add_argument("--commands", action="store_true", help="Also save drawing commands under .cache for debugging")
args = parser.parse_args()
if args.example:
    rows = [(32, 300, 120), (8, 10080, 9000), (42, 300, 160), (65, 10080, 8000)]
    counts = [(1, 1), (2, 1)]
    name = "eye-dashboard-example"
else:
    rows = list(read_windows(RUNTIME / "usage-openai.json", "codex"))
    rows += list(read_windows(RUNTIME / "usage-claude.json", "claude"))
    codex, _, available = read_state(RUNTIME / "app-server-live.json", 180)
    claude, claude_available = code_count_status()
    counts = [(codex, bool(available & 1)), (claude, claude_available)]
    name = "eye-dashboard-live-preview"
payload = " ".join(str(int(value)) for row in rows + counts for value in row)
if args.battery_mv is not None:
    payload += " " + str(args.battery_mv)
    name += "-battery-layout"
else:
    payload += " 0"
payload += " " + str(args.time_ms)
with tempfile.TemporaryDirectory() as folder:
    executable = Path(folder) / "preview"
    subprocess.run(["c++", "-std=c++17", str(ROOT / "scripts/render_ring_dashboard.cpp"), "-o", str(executable)], check=True)
    output = subprocess.run([str(executable)], input=payload, capture_output=True, text=True, check=True).stdout
commands = [json.loads(line) for line in output.splitlines()]
scale = 4
image = Image.new("RGB", (240 * scale, 240 * scale))
draw = ImageDraw.Draw(image)
font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
mono_path = "/System/Library/Fonts/Menlo.ttc"
fonts = {1: ImageFont.truetype(mono_path, 8 * scale), 2: ImageFont.truetype(font_path, 15 * scale)}
glyph_source=(ROOT/".cache/upstream/Seeed_GFX/Fonts/glcdfont.c").read_text().split("{",1)[1].split("}",1)[0]
glyph_source=re.sub(r"//[^\n]*|/\*.*?\*/","",glyph_source,flags=re.S)
glyph_bytes=bytes(int(value,16) for value in re.findall(r"0x([0-9a-fA-F]{2})",glyph_source))


def rgb(color):
    return ((color >> 11 & 31) * 255 // 31, (color >> 5 & 63) * 255 // 63, (color & 31) * 255 // 31)


for cmd in commands:
    op = cmd["op"]
    color = rgb(cmd["color"])
    if op == "fill":
        draw.rectangle((0, 0, 240 * scale, 240 * scale), fill=color)
    elif op == "rect":
        x,y,w,h=(cmd[k]*scale for k in ("x","y","w","h"))
        draw.rectangle((x,y,x+w-1,y+h-1),fill=color)
    elif op == "pixel":
        x, y = cmd["x"] * scale, cmd["y"] * scale
        draw.rectangle((x, y, x + scale - 1, y + scale - 1), fill=color)
    elif op in ("circle", "disk"):
        x, y, radius = (cmd[k] * scale for k in ("x", "y", "r"))
        box = (x - radius, y - radius, x + radius, y + radius)
        if op == "disk": draw.ellipse(box, fill=color)
        else: draw.ellipse(box, outline=color, width=scale)
    elif op == "arc":
        radius = cmd["r"] * scale
        mid_radius=(cmd["r"]+cmd["ir"])*scale/2
        x, y = cmd["x"] * scale, cmd["y"] * scale
        start, end = cmd["start"] + 90, cmd["end"] + 90
        if end < start: end += 360
        draw.arc((x - radius, y - radius, x + radius, y + radius), start, end,
                 fill=color, width=(cmd["r"] - cmd["ir"]) * scale)
        if cmd.get("rounded"):
            cap=(cmd["r"]-cmd["ir"])*scale/2
            for angle in (start,end):
                cx=x+mid_radius*math.cos(math.radians(angle))
                cy=y+mid_radius*math.sin(math.radians(angle))
                draw.ellipse((cx-cap,cy-cap,cx+cap,cy+cap),fill=color)
    elif op == "text":
        if cmd["font"]==1:
            # Match the actual TFT 5x7 glyphs in their 6x8 cells.
            x,y=cmd["x"]-len(cmd["text"])*3,cmd["y"]-4
            for char in cmd["text"]:
                for column,bits in enumerate(glyph_bytes[ord(char)*5:ord(char)*5+5]):
                    for row in range(8):
                        if bits&(1<<row):
                            px,py=(x+column)*scale,(y+row)*scale
                            draw.rectangle((px,py,px+scale-1,py+scale-1),fill=color)
                x+=6
        else:
            draw.text((cmd["x"] * scale, cmd["y"] * scale), cmd["text"], font=fonts[cmd["font"]],
                      fill=color, anchor="mm")
out = ROOT / "docs" / "ui"
out.mkdir(parents=True, exist_ok=True)
image.save(out / f"{name}.png")
image.resize((240, 240), Image.Resampling.LANCZOS).save(out / f"{name}-240.png")
if args.commands:
    cache=ROOT/".cache/ui"
    cache.mkdir(parents=True,exist_ok=True)
    (cache/f"{name}.json").write_text(json.dumps(commands,indent=2)+"\n")
print(out / f"{name}.png")
