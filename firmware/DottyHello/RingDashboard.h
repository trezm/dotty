#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "UsageProtocol.h"
#include "BatteryMonitor.h"

// Shared by the display and the host-rendered preview, so geometry, labels,
// missing-data states and touch targets are exercised from the same code.
namespace RingDashboard {
constexpr uint16_t background = 0x0862, panel = 0x18e5, muted = 0x94d3;
constexpr uint16_t accents[2] = {0x4ef5, 0xebae};
constexpr uint16_t windowColors[2] = {0x5bff, 0x4ef5};  // Short: blue; long: mint.
constexpr int16_t centerX[2] = {67, 173}, centerY = 111;
constexpr int16_t outerRadius = 49, innerRadius = 41, buttonRadius = 34;
constexpr uint8_t centerDatum = 4;  // TFT_eSPI MC_DATUM.

struct Provider {
  bool available = false, pressed = false, countExact = true, clearing = false;
  uint16_t count = 0;
  bool pending = false;
  UsageWindow windows[2];
};

struct State {
  Provider providers[2];
  uint16_t border = muted;
  const char *footer = "Waiting for Mac";
  PuckBattery::Reading battery;
};

template <typename Canvas>
void paintBattery(Canvas &canvas, const PuckBattery::Reading &battery, int16_t y = 47) {
  uint16_t color = battery.bars < 0 ? muted : battery.bars == 0 ? 0xf986 :
                   battery.bars == 1 ? 0xfd20 : windowColors[1];
  // Horizontal strokes keep the shared preview and hardware painter identical.
  constexpr int16_t x = 84;
  for (int16_t dx = 0; dx < 20; ++dx) {
    canvas.drawPixel(x + dx, y - 4, color);
    canvas.drawPixel(x + dx, y + 4, color);
  }
  for (int16_t dy = -3; dy <= 3; ++dy) {
    canvas.drawPixel(x, y + dy, color);
    canvas.drawPixel(x + 19, y + dy, color);
  }
  for (int16_t dy = -1; dy <= 1; ++dy) canvas.drawPixel(x + 20, y + dy, color);
  for (int8_t bar = 0; bar < battery.bars; ++bar)
    for (int16_t dx = 0; dx < 3; ++dx)
      for (int16_t dy = -2; dy <= 2; ++dy) canvas.drawPixel(x + 3 + bar * 4 + dx, y + dy, color);
  char label[16];
  if (battery.bars < 0) snprintf(label, sizeof(label), "--");
  else snprintf(label, sizeof(label), "%u.%02uV", battery.millivolts / 1000,
                (battery.millivolts % 1000) / 10);
  canvas.setTextDatum(centerDatum);
  canvas.setTextColor(color, background);
  canvas.drawString(label, 132, y, 1);
}

inline int8_t hitTest(int16_t x, int16_t y) {
  constexpr int32_t radius = buttonRadius + 6;
  for (uint8_t i = 0; i < 2; ++i) {
    int32_t dx = x - centerX[i], dy = y - centerY;
    if (dx * dx + dy * dy <= radius * radius) return i;
  }
  return -1;
}

inline uint16_t dim(uint16_t color, uint8_t strength) {
  return ((((color >> 11) & 31) * strength / 255) << 11) |
         ((((color >> 5) & 63) * strength / 255) << 5) |
         ((color & 31) * strength / 255);
}

inline uint16_t pulseColor(uint32_t now, bool active, uint16_t color) {
  if (!active) return muted;
  uint8_t phase = (now / 100) % 24;
  uint8_t strength = 75 + (phase < 12 ? phase : 23 - phase) * 16;
  return dim(color, strength);
}

template <typename Canvas>
void paintBorder(Canvas &canvas, uint16_t color) {
  canvas.drawCircle(120, 120, 116, color);
  canvas.drawCircle(120, 120, 115, color);
}

template <typename Canvas>
void paintGauge(Canvas &canvas, int16_t x, int16_t radius, const UsageWindow &window,
                uint16_t color) {
  int16_t used = usageConsumed(window);
  if (used < 0) return;
  canvas.drawSmoothArc(x, centerY, radius, radius - 3, 0, 360, dim(color, 32), background, false);
  if (used == 0) return;
  if (used == 100) {
    canvas.drawSmoothArc(x, centerY, radius, radius - 3, 0, 360, color, background, false);
  } else {
    uint16_t end = 180 + (360 * used + 50) / 100;
    if (end > 360) end -= 360;
    canvas.drawSmoothArc(x, centerY, radius, radius - 3, 180, end, color, background, false);
  }
}

template <typename Canvas>
void paintButton(Canvas &canvas, const Provider &provider, uint8_t index) {
  bool highlighted = provider.available && provider.pending;
  uint16_t fill = highlighted && !provider.pressed && !provider.clearing ? accents[index] : panel;
  uint16_t edge = highlighted ? accents[index] : muted;
  canvas.fillCircle(centerX[index], centerY, buttonRadius, fill);
  canvas.drawCircle(centerX[index], centerY, buttonRadius, edge);
  canvas.setTextDatum(centerDatum);
  canvas.setTextColor(highlighted && !provider.pressed && !provider.clearing ? background : 0xffff, fill);
  canvas.drawString(provider.clearing ? "Clearing" : provider.pressed ? "Hold" : index == 0 ? "Codex" : "Claude",
                    centerX[index], centerY - 7, 2);
  char count[24];
  if (!provider.available) snprintf(count, sizeof(count), "Offline");
  else if (!provider.countExact && provider.pending) snprintf(count, sizeof(count), "New reply");
  else if (provider.count > 99) snprintf(count, sizeof(count), "%u new", provider.count);
  else snprintf(count, sizeof(count), "%u %s", provider.count, index == 0 ? "unread" : "pending");
  canvas.drawString(count, centerX[index], centerY + 12, 1);
}

template <typename Canvas>
void paintWindowLabel(Canvas &canvas, const Provider &provider, uint8_t index, uint8_t slot) {
  const UsageWindow &window = provider.windows[slot];
  char text[32], duration[10];
  usageDuration(window.minutes, duration, sizeof(duration));
  int16_t used = usageConsumed(window);
  bool anyKnown = usageConsumed(provider.windows[0]) >= 0 || usageConsumed(provider.windows[1]) >= 0;
  if (used < 0) snprintf(text, sizeof(text), "%s %s", duration,
                         index == 0 && anyKnown ? "n/a" : "--");
  else snprintf(text, sizeof(text), "%s %u%%", duration, unsigned(used));
  canvas.setTextDatum(centerDatum);
  canvas.setTextColor(used < 0 ? muted : windowColors[slot], background);
  canvas.drawString(text, centerX[index], 176 + (1 - slot) * 15, 1);
}

template <typename Canvas>
void paint(Canvas &canvas, const State &state) {
  canvas.fillScreen(background);
  paintBorder(canvas, state.border);
  canvas.setTextDatum(centerDatum);
  canvas.setTextColor(muted, background);
  canvas.drawString("D O T T Y", 120, 27, 2);
  paintBattery(canvas, state.battery);
  for (uint8_t i = 0; i < 2; ++i) {
    const Provider &provider = state.providers[i];
    paintGauge(canvas, centerX[i], outerRadius, provider.windows[1], windowColors[1]);
    paintGauge(canvas, centerX[i], innerRadius, provider.windows[0], windowColors[0]);
    paintButton(canvas, provider, i);
    paintWindowLabel(canvas, provider, i, 0);
    paintWindowLabel(canvas, provider, i, 1);
  }
  canvas.setTextDatum(centerDatum);
  canvas.setTextColor(muted, background);
  canvas.drawString(state.footer, 120, 213, 1);
}
}  // namespace RingDashboard
