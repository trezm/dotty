"""Export Dotty's Vector-inspired expression study; no device or firmware writes."""
import json
import math
from pathlib import Path
import re

from PIL import Image,ImageDraw,ImageFilter,ImageFont,ImageSequence

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/eyes'
SOURCE=OUT/'dotty-vector-eyes.html'
MOTION=json.loads(re.search(r'<script id="dotty-vector-motion" type="application/json">(.*?)</script>',SOURCE.read_text(),re.S)[1])
NUMERIC=('x','y','wl','wr','hl','hr','yl','yr','tl','tr','joy')
SCALE=3
BG='#05090c'
FONT='/System/Library/Fonts/Menlo.ttc'


def ease(u):
    return u*u*(3-2*u)


def resolve(item):
    return {**MOTION['poses'][item['pose']],**item}


def state_at(t):
    timeline=MOTION['timeline'];i=0
    while i<len(timeline)-2 and t>=timeline[i+1]['t']:
        i+=1
    a,b=map(resolve,timeline[i:i+2]);u=ease(min(1,max(0,(t-a['t'])/(b['t']-a['t']))))
    s=dict(a if u<.5 else b)
    for key in NUMERIC:
        s[key]=a[key]+(b[key]-a[key])*u
    return s


def blink(t,index):
    opened=1
    for center,duration in MOTION['blinks']:
        u=(t-center-index*.009)/duration+.5
        if 0<u<1:
            opened=min(opened,max(.035,1-ease(u/.4) if u<.4 else ease((u-.4)/.6)))
    return opened


def eye_path(x,y,w,h,tilt,joy):
    left,right=x-w/2,x+w/2
    radius=min(9,w*.22,h*.3)
    tl,tr,b=y-h/2+tilt,y-h/2-tilt,y+h/2
    curve=joy*min(1,h/26)
    points=[(left+radius,tl)]
    def line(p):
        points.append(p)
    def q(control,end):
        start=points[-1]
        for i in range(1,15):
            u=i/14
            points.append(tuple((1-u)**2*a+2*(1-u)*u*b+u*u*c for a,b,c in zip(start,control,end)))
    q((x,y-h/2-curve*10),(right-radius,tr))
    q((right,tr),(right,tr+radius));line((right,b-radius))
    q((right,b),(right-radius,b));q((x,b-curve*33),(left+radius,b))
    q((left,b),(left,b-radius));line((left,tl+radius))
    q((left,tl),(left+radius,tl))
    return [(round(a*SCALE),round(b*SCALE)) for a,b in points]


def frame(t,usage=True):
    s=state_at(t)
    image=Image.new('RGB',(240*SCALE,240*SCALE),BG)
    draw=ImageDraw.Draw(image)
    def text(value,x,y,size,color):
        font=ImageFont.truetype(FONT,round(size*SCALE))
        ImageDraw.Draw(image).text((x*SCALE,y*SCALE),value,font=font,fill=color,anchor='mm')
    if usage:
        # Left and right provider halves both fill from top toward the bottom.
        # Pillow arcs run clockwise; reverse the left-side endpoints.
        for index,quota in enumerate(MOTION['usage']):
            start=-75 if index else -105
            direction=1 if index else -1
            for radius,used,color in ((112,quota['seven_day'],'#49d8b0'),(105,quota['five_hour'],'#718bff')):
                # Pillow strokes inward; offset the box to keep the same
                # centerline and round caps as the browser canvas renderer.
                box=tuple(v*SCALE for v in (120-radius-1.5,120-radius-1.5,120+radius+1.5,120+radius+1.5))
                def arc(end,fill):
                    a,b=(start,end) if index else (end,start)
                    draw.arc(box,start=a,end=b,fill=fill,width=3*SCALE)
                    for angle in (a,b):
                        x=120+radius*math.cos(math.radians(angle));y=120+radius*math.sin(math.radians(angle))
                        draw.ellipse(tuple(v*SCALE for v in (x-1.5,y-1.5,x+1.5,y+1.5)),fill=fill)
                arc(start+direction*150,'#18262e')
                if used is not None and used>0:
                    arc(start+direction*150*min(100,used)/100,color)
    if s['codex']+s['claude']>0:
        alpha=.3+.17*math.sin(t*5)
        color=tuple(round(a*alpha+b*(1-alpha)) for a,b in zip((77,255,206),(5,9,12)))
        draw.ellipse((4*SCALE,4*SCALE,236*SCALE,236*SCALE),outline=color,width=SCALE)
    text('D O T T Y',120,24,9,'#8b9c9f');text('3.80V',120,39,6.5,'#687b80')
    for index,center in enumerate((77,163)):
        side='r' if index else 'l';opened=blink(t,index)
        x=center+s['x']*9;y=111+s['y']*6+s['y'+side]+math.sin(t*1.8)*.5
        w=s['w'+side];h=max(2.2,s['h'+side]*opened)
        path=eye_path(x,y,w,h,s['t'+side]*opened,s['joy'])
        mask=Image.new('L',image.size,0);ImageDraw.Draw(mask).polygon(path,fill=255)
        glow=Image.new('RGB',image.size,'#4dffce')
        glow_mask=mask.filter(ImageFilter.GaussianBlur(3*SCALE)).point(lambda v:round(v*.22))
        image.paste(glow,(0,0),glow_mask)
        panel=Image.new('RGB',image.size,BG);panel_draw=ImageDraw.Draw(panel)
        light,core=(173,255,231),(71,237,180)
        y0,y1=round((y-h/2)*SCALE),round((y+h/2)*SCALE)
        # Sloped lids and the happy arch can extend beyond the base bounds.
        for sy in range(max(0,y0-32*SCALE),min(image.height,y1+32*SCALE+1)):
            u=min(1,max(0,(sy-y0)/max(1,y1-y0)))
            color=tuple(round(a+(b-a)*u) for a,b in zip(light,core))
            if sy%(2*SCALE)==0:
                color=tuple(round(a*.9+b*.1) for a,b in zip(color,(5,9,12)))
            panel_draw.line((0,sy,image.width,sy),fill=color)
        image.paste(panel,(0,0),mask)
        text('CLAUDE' if index else 'CODEX',center,164,7.5,'#799196')
        count=s['claude'] if index else s['codex']
        if count:
            label=f"{count} {'question' if s['question'] else 'pending'}" if index else f'{count} unread'
            text(label,center,178,7.5,'#47edb4')
        if usage:
            quota=MOTION['usage'][index]
            text(f"5h {quota['five_hour']}%",center,193,7,'#718bff')
            text(f"7d {quota['seven_day']}%",center,205,7,'#49d8b0')
    if not usage:
        text('Claude needs you' if s['question'] else 'New Codex reply' if s['codex'] else 'All caught up',120,210,7,'#687b80')
    return image


def main():
    fps=20
    images=[frame(i/fps).resize((480,480),Image.Resampling.LANCZOS) for i in range(round(MOTION['duration']*fps))]
    gif_path=OUT/'dotty-vector-eyes.gif'
    images[0].save(gif_path,save_all=True,append_images=images[1:],duration=50,loop=0,optimize=True)
    with Image.open(gif_path) as gif:
        count=gif.n_frames
        duration=sum(item.info.get('duration',0) for item in ImageSequence.Iterator(gif))
        assert duration==MOTION['duration']*1000
    frame(3.9).save(OUT/'dotty-vector-eyes-poster.png')
    frame(6.3,usage=True).save(OUT/'dotty-vector-eyes-with-usage.png')
    samples=[(0,'Relaxed'),(3.9,'Curious'),(9.85,'Skeptical'),(8.55,'Content'),(11.1,'Sleepy'),(6.35,'New reply')]
    sheet=Image.new('RGB',(320*len(samples),365),'#10171b');draw=ImageDraw.Draw(sheet)
    font=ImageFont.truetype(FONT,16)
    for i,(t,label) in enumerate(samples):
        sheet.paste(frame(t).resize((320,320),Image.Resampling.LANCZOS),(320*i,0))
        draw.text((320*i+160,342),label,font=font,fill='#cbd7db',anchor='mm')
    sheet.save(OUT/'dotty-vector-eyes-storyboard.png')
    (OUT/'vector-animation.json').write_text(json.dumps({'source':'dotty-vector-eyes.html','reference':'https://anki.bot/pages/meet-vector','seconds':duration/1000,'fps':fps,'rendered_frames':len(images),'gif_frames':count,'style':'Luminous rounded panels with split-provider perimeter usage arcs.','usage':MOTION['usage'],'usage_is_consumed':True,'weekly_radius':112,'five_hour_radius':105,'counts_are_illustrative':True,'firmware_changes':False},indent=2)+'\n')
    print('Rendered',len(images),'frames;',duration,'ms;',count,'optimized GIF frames.')
    print(gif_path)


if __name__=='__main__':
    main()
