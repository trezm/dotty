#pragma once
#include <stdint.h>
#include <math.h>

// Geometry only. Notifications and quotas always come from the live protocol;
// the animation cannot manufacture a reply, question, or unread count.
namespace DottyEyes {
constexpr int16_t bandX = 34, bandY = 66, bandWidth = 172, bandHeight = 86;
constexpr uint32_t frameMs = 40;
struct Pose {
  float x = 0, y = 0;
  float width[2] = {51, 51}, height[2] = {55, 55};
  float lift[2] = {0, 0}, tilt[2] = {0, 0};
  float joy = 0;
};
enum Expression : uint8_t { Relaxed, Right, Left, Curious, Listening, Happy, Skeptical, Sleepy };
inline float ease(float u) { return u * u * (3 - 2 * u); }
inline Pose expression(Expression which) {
  Pose p;
  switch (which) {
    case Right: p.x=.72f; p.y=-.12f; p.width[0]=48; p.width[1]=54;
      p.height[0]=51; p.height[1]=56; p.lift[1]=-1; p.tilt[0]=1; break;
    case Left: p.x=-.68f; p.y=.08f; p.width[0]=54; p.width[1]=48;
      p.height[1]=50; p.lift[0]=-1; p.lift[1]=1; p.tilt[1]=-1; break;
    case Curious: p.x=.35f; p.y=-.18f; p.width[0]=49; p.width[1]=53;
      p.height[0]=31; p.height[1]=61; p.lift[0]=4; p.lift[1]=-6;
      p.tilt[0]=-4; p.tilt[1]=1; break;
    case Listening: p.y=-.12f; for (uint8_t i=0;i<2;++i) {
      p.width[i]=54; p.height[i]=63; p.lift[i]=-3; } break;
    case Happy: for (uint8_t i=0;i<2;++i) {
      p.width[i]=56; p.height[i]=26; p.lift[i]=-5; } p.joy=1; break;
    case Skeptical: p.x=-.12f; p.height[0]=25; p.height[1]=40;
      p.width[1]=50; p.lift[0]=1; p.tilt[0]=-6; p.tilt[1]=5; break;
    case Sleepy: p.y=.25f; p.height[0]=13; p.height[1]=15;
      p.lift[0]=5; p.lift[1]=4; p.tilt[0]=1; p.tilt[1]=-1; break;
    default: break;
  }
  return p;
}
inline Pose blend(const Pose &a, const Pose &b, float u) {
  Pose p;
  p.x=a.x+(b.x-a.x)*u; p.y=a.y+(b.y-a.y)*u; p.joy=a.joy+(b.joy-a.joy)*u;
  for (uint8_t i=0;i<2;++i) {
    p.width[i]=a.width[i]+(b.width[i]-a.width[i])*u;
    p.height[i]=a.height[i]+(b.height[i]-a.height[i])*u;
    p.lift[i]=a.lift[i]+(b.lift[i]-a.lift[i])*u;
    p.tilt[i]=a.tilt[i]+(b.tilt[i]-a.tilt[i])*u;
  }
  return p;
}
inline float blink(uint32_t now, uint8_t eye) {
  const float t=(now % 14000) * .001f;
  constexpr float events[][2]={{.65f,.17f},{2.28f,.15f},{5.6f,.16f},
    {8.7f,.15f},{8.98f,.15f},{11.5f,.46f},{12.08f,.19f}};
  for (const auto &event:events) {
    float u=(t-event[0]-eye*.009f)/event[1]+.5f;
    if (u>0 && u<1) return fmaxf(.035f,u<.4f ? 1-ease(u/.4f) : ease((u-.4f)/.6f));
  }
  return 1;
}
inline Pose sample(uint32_t now, uint8_t pending = 0, uint8_t pressed = 0, uint8_t clearing = 0) {
  struct Key { uint16_t ms; Expression pose; };
  // Long holds, fast glances, and independent lids from the approved study.
  constexpr Key keys[]={{0,Relaxed},{1040,Relaxed},{1180,Right},{2160,Right},
    {2330,Left},{3080,Left},{3450,Curious},{4320,Curious},{4500,Listening},
    {6750,Listening},{8280,Happy},{9200,Happy},{9480,Skeptical},
    {10150,Skeptical},{10650,Sleepy},{11780,Sleepy},{12170,Listening},
    {12500,Relaxed},{14000,Relaxed}};
  const uint16_t t=now%14000;
  uint8_t k=0;
  while (k<sizeof(keys)/sizeof(keys[0])-2 && t>=keys[k+1].ms) ++k;
  Pose p=blend(expression(keys[k].pose),expression(keys[k+1].pose),
               ease(float(t-keys[k].ms)/(keys[k+1].ms-keys[k].ms)));
  if (pending) {
    // A waiting provider perks up its own eye. The other keeps a relaxed lid.
    Pose alert;
    for (uint8_t i=0;i<2;++i) if (pending & (1<<i)) {
      alert.width[i]=56; alert.height[i]=64; alert.lift[i]=-4;
    }
    p=blend(p,alert,.85f);
  }
  p.y+=sinf((now%14000)*.0018f)*.08f;
  for (uint8_t i=0;i<2;++i) {
    p.height[i]=fmaxf(2.2f,p.height[i]*blink(now,i));
    p.tilt[i]*=blink(now,i);
    if ((pressed|clearing)&(1<<i)) { p.height[i]=9; p.tilt[i]=0; }
  }
  return p;
}

struct Point { float x,y; };
struct Contour {
  Point points[64]; uint8_t count=0;
  void line(float x,float y) { points[count++]={x,y}; }
  void quadratic(float cx,float cy,float x,float y) {
    const Point a=points[count-1];
    for (uint8_t i=1;i<=6;++i) {
      float u=i/6.f,v=1-u;
      line(v*v*a.x+2*v*u*cx+u*u*x,v*v*a.y+2*v*u*cy+u*u*y);
    }
  }
};
inline Contour contour(const Pose &p,uint8_t eye) {
  float x=(eye?163:77)+p.x*9, y=111+p.y*6+p.lift[eye];
  float w=p.width[eye],h=p.height[eye],tilt=p.tilt[eye];
  float l=x-w/2,r=x+w/2,c=fminf(9,fminf(w*.22f,h*.3f));
  float tl=y-h/2+tilt,tr=y-h/2-tilt,b=y+h/2,curve=p.joy*fminf(1,h/26);
  Contour shape;
  shape.line(l+c,tl);
  shape.quadratic(x,y-h/2-curve*10,r-c,tr);
  shape.quadratic(r,tr,r,tr+c);
  shape.line(r,b-c); shape.quadratic(r,b,r-c,b);
  shape.quadratic(x,b-curve*33,l+c,b);
  shape.quadratic(l,b,l,b-c);
  shape.line(l,tl+c); shape.quadratic(l,tl,l+c,tl);
  return shape;
}
}  // namespace DottyEyes
