#include <assert.h>
#include <stdio.h>
#include <array>
#include <vector>
#include "../firmware/DottyHello/RingDashboard.h"

struct Canvas {
  std::array<uint16_t,240*240> pixels;
  struct Arc { int r,ir,start,end,color; };
  std::vector<Arc> arcs;
  bool partial=true;
  Canvas() { pixels.fill(0xdead); }
  void drawPixel(int x,int y,uint16_t color) {
    assert(x>=0 && x<240 && y>=0 && y<240);
    if (partial) assert(x>=DottyEyes::bandX && x<DottyEyes::bandX+DottyEyes::bandWidth &&
                        y>=DottyEyes::bandY && y<DottyEyes::bandY+DottyEyes::bandHeight);
    pixels[y*240+x]=color;
  }
  void fillRect(int x,int y,int w,int h,uint16_t color) {
    for (int yy=y;yy<y+h;++yy) for(int xx=x;xx<x+w;++xx) drawPixel(xx,yy,color);
  }
  void drawFastHLine(int x,int y,int w,uint16_t c) { fillRect(x,y,w,1,c); }
  void drawSmoothArc(int,int,int r,int ir,unsigned start,unsigned end,unsigned c,unsigned,bool) {
    arcs.push_back({r,ir,int(start),int(end),int(c)});
  }
  int brightPixels() const {
    int n=0;
    for (auto c:pixels) if (c!=0xdead && ((c>>5)&63)>25) ++n;
    return n;
  }
};
int main() {
  using namespace RingDashboard;
  State s;
  s.providers[0].available=s.providers[1].available=true;
  // Every contour, including wraparound, fits inside the partial transfer.
  for (uint32_t t=0;t<14000;t+=7) for (uint8_t pending=0;pending<4;++pending) {
    auto p=DottyEyes::sample(t,pending);
    for (uint8_t i=0;i<2;++i) {
      auto shape=DottyEyes::contour(p,i);
      assert(shape.count<=64);
      for (uint8_t k=0;k<shape.count;++k) {
        assert(isfinite(shape.points[k].x) && isfinite(shape.points[k].y));
        assert(shape.points[k].x-4>=DottyEyes::bandX);
        assert(shape.points[k].x+4<DottyEyes::bandX+DottyEyes::bandWidth);
        assert(shape.points[k].y-4>=DottyEyes::bandY);
        assert(shape.points[k].y+4<DottyEyes::bandY+DottyEyes::bandHeight);
      }
    }
  }
  for (uint32_t t: {0u,650u,1180u,2330u,3450u,4500u,8700u,9480u,10650u,UINT32_MAX-10,10u}) {
    s.now=t;
    Canvas canvas;
    paintEyes(canvas,s);
    assert(canvas.pixels[0]==0xdead && canvas.pixels[120*240+15]==0xdead);
  }
  s.now=0;
  Canvas open; paintEyes(open,s);
  s.now=630;
  Canvas blink; paintEyes(blink,s);
  assert(blink.brightPixels()<open.brightPixels()/3);
  s.now=0; s.providers[0].pressed=s.providers[1].clearing=true;
  Canvas held; paintEyes(held,s);
  assert(held.brightPixels()<open.brightPixels()/3);
  s.providers[0].pressed=s.providers[1].clearing=false;
  Canvas smile;
  paintEye(smile,DottyEyes::expression(DottyEyes::Happy),s.providers[0],0);
  // The bowed lower lid is concave: don't fill across its middle.
  assert(smile.pixels[117*240+77]==0xdead);
  assert(smile.pixels[98*240+77]!=0xdead);
  // Meter direction/ownership, long outside short, zero distinct from unknown.
  UsageWindow w; w.used=255;
  Canvas quotas; paintGauge(quotas,0,1,w); assert(quotas.arcs.empty());
  w.used=0; paintGauge(quotas,0,1,w);
  assert(quotas.arcs.size()==1 && quotas.arcs[0].r==113);
  w.used=32; paintGauge(quotas,1,0,w);
  assert(quotas.arcs.size()==3 && quotas.arcs[2].r==107);
  assert(quotas.arcs[2].start==195 && quotas.arcs[2].end==243);
  auto left=gaugeArc(0,32); assert(left.start==117 && left.end==165);
  assert(hitTest(77,111)==0 && hitTest(163,111)==1);
  assert(hitTest(120,111)==-1 && hitTest(15,111)==-1 && hitTest(77,193)==-1);
  puts("PASS: eye bounds, isolated redraws, blink/hold, concave happy lids, quota direction and missing data, stable touch targets");
}
