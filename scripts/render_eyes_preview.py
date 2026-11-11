"""Render the eye concept as a looping GIF and PNG frames, without hardware writes.

The inline prototype's motion JSON is the common animation source.
"""
import argparse
import json
import math
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/eyes"
SOURCE = OUT / "dotty-eyes.html"
SCALE = 3
BG = "#080d12"
MUTED = "#969fa5"
TEXT = "#e5e9ea"
EYES = ("#49d8b0", "#ef7e82")
LONG, SHORT = "#49d8b0", "#617bff"
FONT = "/System/Library/Fonts/Menlo.ttc"
MOTION = json.loads(re.search(r'<script id="dotty-eyes-motion" type="application/json">(.*?)</script>', SOURCE.read_text(), re.S)[1])


def state_at(t):
    keys = MOTION["keyframes"]
    i = 0
    while i < len(keys) - 2 and t >= keys[i+1]["t"]:
        i += 1
    a, b = keys[i:i+2]
    u = min(1, max(0, (t-a["t"])/(b["t"]-a["t"])))
    u = u*u*(3-2*u)
    state = dict(a)
    for key in ("x", "y", "left", "right", "pupil", "joy", "mouth"):
        state[key] = a[key] + (b[key]-a[key])*u
    return state


def blink(t, index):
    opened = 1
    for center, duration in MOTION["blinks"]:
        u = (t-center-index*.013)/duration+.5
        if 0 < u < 1:
            opened = min(opened, max(.025, 1-math.sin(math.pi*u)**2))
    return opened


def dim(hex_color, alpha):
    rgb = Image.new("RGB", (1, 1), hex_color).getpixel((0, 0))
    bg = Image.new("RGB", (1, 1), BG).getpixel((0, 0))
    return tuple(round(a*alpha+b*(1-alpha)) for a,b in zip(rgb,bg))


def frame(t):
    s = state_at(t)
    image = Image.new("RGB", (240*SCALE, 240*SCALE), BG)
    draw = ImageDraw.Draw(image)

    def ellipse(x, y, rx, ry, fill):
        ry = max(.7, ry)
        draw.ellipse(tuple(v*SCALE for v in (x-rx,y-ry,x+rx,y+ry)), fill=fill)

    def arc(x, y, radius, start, end, color, width, alpha=1):
        box = tuple(v*SCALE for v in (x-radius,y-radius,x+radius,y+radius))
        draw.arc(box, start=start, end=end, fill=dim(color,alpha), width=max(1,round(width*SCALE)))

    def text(value, x, y, size, color):
        font = ImageFont.truetype(FONT, round(size*SCALE))
        draw.text((x*SCALE,y*SCALE), value, font=font, fill=color, anchor="mm")

    pending = s["codex"] + s["claude"] > 0
    alpha = .6+.3*math.sin(t*5) if pending else .35
    arc(120,120,116,0,360,EYES[1 if s["question"] else 0] if pending else MUTED,1.3,alpha)
    text("D O T T Y",120,22,11,TEXT)
    draw.rectangle((91*SCALE,37*SCALE,104*SCALE,43*SCALE), outline=MUTED, width=2)
    draw.rectangle((93*SCALE,39*SCALE,100*SCALE,41*SCALE), fill=LONG)
    draw.rectangle((104*SCALE,39*SCALE,106*SCALE,41*SCALE), fill=LONG)
    text("3.80V",129,40,7,MUTED)
    for index,x in enumerate((67,173)):
        y, accent = 106,EYES[index]
        count = s["claude"] if index else s["codex"]
        for radius,used,color in ((49,65 if index else 8,LONG),(41,42 if index else 32,SHORT)):
            arc(x,y,radius,0,360,color,3,.13)
            arc(x,y,radius,-90,-90+360*used/100,color,3)
        if count:
            arc(x,y,35.2,0,360,accent,1.5,.4+.3*math.sin(t*5))
        opened = (s["right"] if index else s["left"])*blink(t,index)
        h = 29*opened*(1-s["joy"]*.7)
        if s["joy"] > .7:
            ry = max(1,12*opened)
            draw.arc(tuple(v*SCALE for v in (x-24,y+5-ry,x+24,y+5+ry)),180,360,fill=accent,width=7*SCALE)
        else:
            eye = Image.new("RGB",image.size,BG)
            eye_draw = ImageDraw.Draw(eye)
            mask = Image.new("L",image.size,0)
            ImageDraw.Draw(mask).ellipse(tuple(v*SCALE for v in (x-30,y-max(h,.7),x+30,y+max(h,.7))),fill=255)
            eye_draw.rectangle((0,0,image.width,image.height),fill=accent)
            px,py = x+s["x"]*10,y+s["y"]*8
            rx,ry = 12*s["pupil"],13*s["pupil"]
            eye_draw.ellipse(tuple(v*SCALE for v in (px-rx,py-ry,px+rx,py+ry)),fill=BG)
            eye_draw.ellipse(tuple(v*SCALE for v in (px-6.1,py-6.7,px-1.5,py-2.1)),fill="#f4ffff")
            image.paste(eye,(0,0),mask)
            draw = ImageDraw.Draw(image)
        text("CLAUDE" if index else "CODEX",x,167,8.5,TEXT)
        count_label = f"{count} {'question' if s['question'] else 'pending'}" if index else f"{count} unread"
        text(count_label if count else "All read",x,180,7.5,accent if count else MUTED)
        text(f"5h {42 if index else 32}%",x,196,7.5,SHORT)
        text(f"7d {65 if index else 8}%",x,208,7.5,LONG)
    # Quadratic smile, as in the interactive prototype.
    points=[]
    for i in range(31):
        u=i/30
        points.append(((112+16*u)*SCALE,(162+2*(1-u)*u*s["mouth"]*9)*SCALE))
    draw.line(points,fill=MUTED,width=round(1.5*SCALE))
    text("Claude needs you" if s["question"] else "New Codex reply" if s["codex"] else "All caught up",120,224,6.8,MUTED)
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fps",type=int,choices=(10,20,25,50),default=20)
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    frames_dir = ROOT / ".cache/eye-animation-frames"
    frames_dir.mkdir(parents=True,exist_ok=True)
    frames=[]
    for i in range(round(MOTION["duration"]*args.fps)):
        image=frame(i/args.fps)
        image.save(frames_dir/f"frame-{i:04d}.png")
        frames.append(image.resize((480,480),Image.Resampling.LANCZOS))
    frames[0].save(OUT/"dotty-eyes.gif",save_all=True,append_images=frames[1:],duration=round(1000/args.fps),loop=0,optimize=True)
    with Image.open(OUT/"dotty-eyes.gif") as gif:
        encoded_frames=gif.n_frames
        duration_ms=sum(item.info.get("duration",0) for item in ImageSequence.Iterator(gif))
        assert duration_ms==MOTION["duration"]*1000
    frame(2.6).save(OUT/"dotty-eyes-poster.png")
    tiles=[]
    for t in (0,2.6,5.5,7.2,9.3):
        tile=frame(t).resize((360,360),Image.Resampling.LANCZOS)
        tiles.append(tile)
    sheet=Image.new("RGB",(360*len(tiles),410),"#11171d")
    pen=ImageDraw.Draw(sheet)
    label_font=ImageFont.truetype(FONT,17)
    for i,(tile,label) in enumerate(zip(tiles,("Idle","Curious","New reply","Question","Happy"))):
        sheet.paste(tile,(360*i,0))
        pen.text((360*i+180,382),label,font=label_font,fill=TEXT,anchor="mm")
    sheet.save(OUT/"dotty-eyes-storyboard.png")
    (OUT/"animation.json").write_text(json.dumps({"fps":args.fps,"rendered_frames":len(frames),"gif_frames":encoded_frames,"seconds":duration_ms/1000,"screen_pixels":[240,240],"preview_pixels":[720,720],"counts_are_illustrative":True,"usage_values_static":True},indent=2)+"\n")
    print(f"Rendered {len(frames)} frames; GIF: {OUT/'dotty-eyes.gif'}")
    print("PNG frames:",frames_dir)


if __name__ == "__main__":
    main()
