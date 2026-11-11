#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "UsageProtocol.h"
#include "BatteryMonitor.h"
#include "EyeAnimation.h"

// Shared by the physical screen and host preview. Short quotas are blue,
// long quotas mint, with Codex on the left and Claude on the right.
namespace RingDashboard {
constexpr uint16_t rgb(uint8_t r,uint8_t g,uint8_t b) {
  return ((r>>3)<<11)|((g>>2)<<5)|(b>>3);
}
constexpr uint16_t background=rgb(5,9,12), panel=rgb(24,38,46), muted=rgb(104,123,128);
constexpr uint16_t accents[2]={rgb(77,255,206),rgb(77,255,206)};
constexpr uint16_t windowColors[2]={rgb(113,139,255),rgb(73,216,176)};
constexpr int16_t centerX[2]={77,163},centerY=111;
constexpr uint8_t centerDatum=4;
struct Provider {
  bool available=false,pressed=false,countExact=true,clearing=false;
  uint16_t count=0;
  bool pending=false;
  UsageWindow windows[2];
};
struct State {
  Provider providers[2];
  uint16_t border=background;
  const char *footer="Waiting for Mac";
  PuckBattery::Reading battery;
  uint32_t now=0;
};
inline uint16_t dim(uint16_t color,uint8_t strength) {
  return ((((color>>11)&31)*strength/255)<<11)|
         ((((color>>5)&63)*strength/255)<<5)|((color&31)*strength/255);
}
inline uint16_t pulseColor(uint32_t now,bool active,uint16_t color) {
  if (!active) return background;
  uint8_t phase=(now/100)%24;
  return dim(color,75+(phase<12?phase:23-phase)*16);
}
inline int8_t hitTest(int16_t x,int16_t y) {
  // Fixed targets don't chase the animation or overlap the quota arcs.
  for (uint8_t i=0;i<2;++i)
    if (x>=centerX[i]-34 && x<=centerX[i]+34 && y>=74 && y<=158) return i;
  return -1;
}
template <typename Canvas>
void paintBattery(Canvas &canvas,const PuckBattery::Reading &battery,int16_t y=39) {
  uint16_t color=battery.bars<0?muted:battery.bars==0?0xf986:
                 battery.bars==1?0xfd20:windowColors[1];
  constexpr int16_t x=84;
  for (int16_t dx=0;dx<20;++dx) {
    canvas.drawPixel(x+dx,y-4,color); canvas.drawPixel(x+dx,y+4,color);
  }
  for (int16_t dy=-3;dy<=3;++dy) {
    canvas.drawPixel(x,y+dy,color); canvas.drawPixel(x+19,y+dy,color);
  }
  for (int16_t dy=-1;dy<=1;++dy) canvas.drawPixel(x+20,y+dy,color);
  for (int8_t bar=0;bar<battery.bars;++bar)
    for (int16_t dx=0;dx<3;++dx)
      for (int16_t dy=-2;dy<=2;++dy) canvas.drawPixel(x+3+bar*4+dx,y+dy,color);
  char label[16];
  if (battery.bars<0) snprintf(label,sizeof(label),"--");
  else snprintf(label,sizeof(label),"%u.%02uV",battery.millivolts/1000,(battery.millivolts%1000)/10);
  canvas.setTextDatum(centerDatum); canvas.setTextColor(color,background);
  canvas.drawString(label,132,y,1);
}
template <typename Canvas>
void paintBorder(Canvas &canvas,uint16_t color) { canvas.drawCircle(120,120,116,color); }

// TFT angles start at six o'clock and run clockwise. Each half starts near
// the top and fills toward the bottom; the long period is the outer arc.
struct Arc { uint16_t start,end; };
inline Arc gaugeArc(uint8_t index,int16_t used=100) {
  const uint16_t span=(150*used+50)/100;
  return index?Arc{195,uint16_t(195+span)}:Arc{uint16_t(165-span),165};
}
template <typename Canvas>
void paintGauge(Canvas &canvas,uint8_t index,uint8_t slot,const UsageWindow &window) {
  int16_t used=usageConsumed(window);
  if (used<0) return;  // Missing quota has no misleading empty track.
  const int16_t radius=slot?113:107;
  Arc track=gaugeArc(index);
  canvas.drawSmoothArc(120,120,radius,radius-3,track.start,track.end,panel,background,true);
  if (used) {
    Arc fill=gaugeArc(index,used);
    canvas.drawSmoothArc(120,120,radius,radius-3,fill.start,fill.end,windowColors[slot],background,true);
  }
}

template <typename Canvas>
void paintEye(Canvas &canvas,const DottyEyes::Pose &pose,const Provider &provider,uint8_t index) {
  const auto shape=DottyEyes::contour(pose,index);
  struct Row { float crossings[8]; uint8_t count=0; };
  Row rows[DottyEyes::bandHeight];
  // Intersect the quadratic contour with each scanline. Both happy crescents
  // and slanted lids can have two spans; no bitmap assets or heap allocation.
  for (int16_t row=0;row<DottyEyes::bandHeight;++row) {
    float y=DottyEyes::bandY+row+.5f;
    for (uint8_t i=0,j=shape.count-1;i<shape.count;j=i++) {
      auto a=shape.points[j],b=shape.points[i];
      if ((a.y<=y && b.y>y)||(b.y<=y && a.y>y)) {
        float x=a.x+(y-a.y)*(b.x-a.x)/(b.y-a.y);
        auto &scan=rows[row];
        if (scan.count<8) {
          uint8_t at=scan.count++;
          while (at && scan.crossings[at-1]>x) {
            scan.crossings[at]=scan.crossings[at-1]; --at;
          }
          scan.crossings[at]=x;
        }
      }
    }
  }
  const uint8_t strength=provider.available?255:65;
  // Three restrained halo passes, then the scanlined mint gradient.
  for (int16_t d=3;d>=1;--d) for (int16_t row=0;row<DottyEyes::bandHeight;++row) {
    for (int16_t dy=-d;dy<=d;++dy) {
      int16_t neighbor=row+dy;
      if (neighbor<0 || neighbor>=DottyEyes::bandHeight) continue;
      const auto &scan=rows[neighbor];
      for (uint8_t s=0;s+1<scan.count;s+=2) {
        float l=scan.crossings[s]-d,r=scan.crossings[s+1]+d;
        canvas.drawFastHLine(int16_t(floorf(l)),DottyEyes::bandY+row,
                     int16_t(ceilf(r)-floorf(l)),dim(accents[index],strength*(4-d)*8/255));
      }
    }
  }
  float mid=111+pose.y*6+pose.lift[index],height=pose.height[index];
  for (int16_t row=0;row<DottyEyes::bandHeight;++row) if (rows[row].count) {
    int16_t y=DottyEyes::bandY+row;
    float u=fmaxf(0,fminf(1,(y-(mid-height/2))/height));
    uint16_t color=rgb(uint8_t(173+(71-173)*u),uint8_t(255+(237-255)*u),uint8_t(231+(180-231)*u));
    color=dim(color,uint16_t(strength)*(y%2?255:232)/255);
    const auto &scan=rows[row];
    for (uint8_t s=0;s+1<scan.count;s+=2) {
      int16_t l=int16_t(ceilf(scan.crossings[s]-.5f)),r=int16_t(floorf(scan.crossings[s+1]-.5f));
      if (r>=l) canvas.drawFastHLine(l,y,r-l+1,color);
    }
  }
}
template <typename Canvas>
void paintEyes(Canvas &canvas,const State &state) {
  canvas.fillRect(DottyEyes::bandX,DottyEyes::bandY,DottyEyes::bandWidth,DottyEyes::bandHeight,background);
  uint8_t pending=0,pressed=0,clearing=0;
  for (uint8_t i=0;i<2;++i) {
    const Provider &p=state.providers[i];
    if (p.available && p.pending) pending|=1<<i;
    if (p.pressed) pressed|=1<<i;
    if (p.clearing) clearing|=1<<i;
  }
  const auto pose=DottyEyes::sample(state.now,pending,pressed,clearing);
  for (uint8_t i=0;i<2;++i) paintEye(canvas,pose,state.providers[i],i);
}
template <typename Canvas>
void paintProviderLabel(Canvas &canvas,const Provider &provider,uint8_t index) {
  canvas.setTextDatum(centerDatum); canvas.setTextColor(rgb(121,145,150),background);
  canvas.drawString(index?"CLAUDE":"CODEX",centerX[index],164,1);
  char count[24]={};
  if (!provider.available) snprintf(count,sizeof(count),"Offline");
  else if (provider.clearing) snprintf(count,sizeof(count),"Clearing");
  else if (provider.pressed) snprintf(count,sizeof(count),"Hold");
  else if (!provider.countExact && provider.pending) snprintf(count,sizeof(count),"New reply");
  else if (provider.count) snprintf(count,sizeof(count),"%u %s",provider.count,index?"pending":"unread");
  canvas.setTextColor(provider.available?windowColors[1]:muted,background);
  canvas.drawString(count,centerX[index],178,1);
}
template <typename Canvas>
void paintWindowLabel(Canvas &canvas,const Provider &provider,uint8_t index,uint8_t slot) {
  const UsageWindow &window=provider.windows[slot];
  char text[32],duration[10]; usageDuration(window.minutes,duration,sizeof(duration));
  int16_t used=usageConsumed(window);
  if (used<0) snprintf(text,sizeof(text),"%s --",duration);
  else snprintf(text,sizeof(text),"%s %u%%",duration,unsigned(used));
  canvas.setTextDatum(centerDatum); canvas.setTextColor(used<0?muted:windowColors[slot],background);
  // The hardware font is 6x8, a little wider than the browser study. Bring
  // these columns slightly inward so even 100% clears the lower arc.
  int16_t x=centerX[index]+(index?-6:6);
  int16_t halfWidth=strlen(text)*3,edge=slot?178:194;
  if (index && x+halfWidth>edge) x=edge-halfWidth;
  if (!index && x-halfWidth<240-edge) x=240-edge+halfWidth;
  canvas.drawString(text,x,190+slot*12,1);
}
template <typename Canvas>
void paint(Canvas &canvas,const State &state) {
  canvas.fillScreen(background); paintBorder(canvas,state.border);
  canvas.setTextDatum(centerDatum); canvas.setTextColor(rgb(139,156,159),background);
  canvas.drawString("D O T T Y",120,27,1); paintBattery(canvas,state.battery);
  for (uint8_t i=0;i<2;++i) {
    for (uint8_t slot=0;slot<2;++slot) {
      paintGauge(canvas,i,slot,state.providers[i].windows[slot]);
      paintWindowLabel(canvas,state.providers[i],i,slot);
    }
    paintProviderLabel(canvas,state.providers[i],i);
  }
  paintEyes(canvas,state);
  // Transient/error messages fit above the eyes, outside both moving lids
  // and the usage arcs. Keep the normal face free of repeated instructions.
  if (state.footer && strcmp(state.footer,"Tap / hold to clear")) {
    canvas.setTextDatum(centerDatum); canvas.setTextColor(muted,background);
    canvas.drawString(state.footer,120,54,1);
  }
}
}  // namespace RingDashboard
