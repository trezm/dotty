// Host canvas for previewing the exact firmware painter without a display.
#include <iostream>
#include <string>
#include "../firmware/DottyHello/RingDashboard.h"

struct Canvas {
  unsigned datum = 4, color = 0;
  void setTextDatum(unsigned value) { datum = value; }
  void setTextColor(unsigned foreground, unsigned) { color = foreground; }
  void drawPixel(int x, int y, unsigned c) {
    std::cout << "{\"op\":\"pixel\",\"x\":" << x << ",\"y\":" << y << ",\"color\":" << c << "}\n";
  }
  void fillScreen(unsigned c) { std::cout << "{\"op\":\"fill\",\"color\":" << c << "}\n"; }
  void fillRect(int x, int y, int w, int h, unsigned c) {
    std::cout << "{\"op\":\"rect\",\"x\":" << x << ",\"y\":" << y
              << ",\"w\":" << w << ",\"h\":" << h << ",\"color\":" << c << "}\n";
  }
  void drawFastHLine(int x, int y, int w, unsigned c) { fillRect(x,y,w,1,c); }
  void drawCircle(int x, int y, int r, unsigned c) { circle("circle", x, y, r, c); }
  void fillCircle(int x, int y, int r, unsigned c) { circle("disk", x, y, r, c); }
  void circle(const char *op, int x, int y, int r, unsigned c) {
    std::cout << "{\"op\":\"" << op << "\",\"x\":" << x << ",\"y\":" << y
              << ",\"r\":" << r << ",\"color\":" << c << "}\n";
  }
  void drawSmoothArc(int x, int y, int r, int ir, unsigned start, unsigned end,
                     unsigned c, unsigned, bool rounded) {
    std::cout << "{\"op\":\"arc\",\"x\":" << x << ",\"y\":" << y
              << ",\"r\":" << r << ",\"ir\":" << ir << ",\"start\":" << start
              << ",\"end\":" << end << ",\"rounded\":" << rounded << ",\"color\":" << c << "}\n";
  }
  void drawString(const char *text, int x, int y, unsigned font) {
    std::cout << "{\"op\":\"text\",\"text\":\"";
    for (const char *p = text; *p; ++p) {
      if (*p == '\\' || *p == '"') std::cout << '\\';
      std::cout << *p;
    }
    std::cout << "\",\"x\":" << x << ",\"y\":" << y << ",\"font\":" << font
              << ",\"datum\":" << datum << ",\"color\":" << color << "}\n";
  }
};

int main() {
  RingDashboard::State state;
  for (unsigned i = 0; i < 2; ++i) {
    UsagePacket packet;
    for (unsigned j = 0; j < 2; ++j) {
      unsigned used, duration, reset;
      if (!(std::cin >> used >> duration >> reset)) return 1;
      packet.windows[j].used = used;
      packet.windows[j].minutes = duration;
      packet.windows[j].resetMinutes = reset;
    }
    orderUsage(packet, true, state.providers[i].windows);
  }
  for (unsigned i = 0; i < 2; ++i) {
    unsigned count, available;
    if (!(std::cin >> count >> available)) return 1;
    state.providers[i].count = count;
    state.providers[i].available = available;
    state.providers[i].pending = count > 0;
  }
  bool active = state.providers[0].pending || state.providers[1].pending;
  unsigned batteryMv;
  if (std::cin >> batteryMv) {
    state.battery.millivolts = batteryMv;
    state.battery.bars = batteryMv >= 3000 && batteryMv <= 4350 ? PuckBattery::level(batteryMv) : -1;
  }
  state.border = RingDashboard::pulseColor(1100, active,
      RingDashboard::accents[state.providers[0].pending ? 0 : 1]);
  bool claude = usageConsumed(state.providers[1].windows[0]) >= 0 ||
                usageConsumed(state.providers[1].windows[1]) >= 0;
  state.footer = claude ? "Tap / hold to clear" : "Claude usage offline";
  unsigned now;
  if (std::cin >> now) state.now = now;
  Canvas canvas;
  RingDashboard::paint(canvas, state);
}
