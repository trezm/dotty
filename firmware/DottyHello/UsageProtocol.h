#pragma once
#include "NotificationProtocol.h"
#include <stdio.h>

struct UsageWindow {
  uint8_t used = 255;  // Unknown is distinct from a measured 0%.
  uint32_t minutes = 0, resetMinutes = 0;  // Reset zero: not reported; sample still requires a fresh heartbeat.
};

struct UsagePacket {
  uint32_t revision = 0;
  uint8_t provider = 0;  // 0: Codex, 1: GPT, 2: Claude subscription.
  UsageWindow windows[2];
};

inline bool parseUsage(const char *line, UsagePacket &packet) {
  if (strncmp(line, "@D4 USAGE ", 10) != 0) return false;
  const char *cursor = line + 10;
  uint32_t values[8] = {};
  const uint32_t maximum[] = {UINT32_MAX, 2, 255, 525600, 525600, 255, 525600, 525600};
  for (uint8_t i = 0; i < 8; ++i) {
    if (!notificationNumber(cursor, maximum[i], values[i])) return false;
    if (i < 7 && *cursor++ != ' ') return false;
  }
  if (*cursor != '\0') return false;
  for (uint8_t i = 2; i <= 5; i += 3) {
    if (values[i] == 255) {
      if (values[i + 1] || values[i + 2]) return false;
    } else if (values[i] > 100 || !values[i + 1]) {
      return false;
    }
  }
  UsagePacket parsed;
  parsed.revision = values[0];
  parsed.provider = values[1];
  for (uint8_t i = 0; i < 2; ++i) {
    parsed.windows[i].used = values[2 + i * 3];
    parsed.windows[i].minutes = values[3 + i * 3];
    parsed.windows[i].resetMinutes = values[4 + i * 3];
  }
  packet = parsed;
  return true;
}

inline bool sameUsage(const UsageWindow &a, const UsageWindow &b) {
  return a.used == b.used && a.minutes == b.minutes && a.resetMinutes == b.resetMinutes;
}

inline int16_t usageConsumed(const UsageWindow &window) {
  return window.used <= 100 ? window.used : -1;
}

// Quota slot order is not stable: this account's primary bucket is weekly.
// Store short-window usage first and weekly/long usage second, independently
// of primary/secondary order. The dashboard draws the longer window outside.
inline void orderUsage(const UsagePacket &packet, bool fresh, UsageWindow (&ordered)[2]) {
  ordered[0] = UsageWindow(); ordered[0].minutes = 300;
  ordered[1] = UsageWindow(); ordered[1].minutes = 10080;
  if (!fresh) return;
  for (uint8_t i = 0; i < 2; ++i) {
    const UsageWindow &candidate = packet.windows[i];
    if (usageConsumed(candidate) < 0) continue;
    uint8_t slot = candidate.minutes >= 1440 ? 1 : 0;
    if (usageConsumed(ordered[slot]) < 0 || candidate.used > ordered[slot].used)
      ordered[slot] = candidate;
  }
}

struct ClaudeCountPacket { uint32_t revision = 0; uint16_t count = 0; };

inline bool parseClaudeCount(const char *line, ClaudeCountPacket &packet) {
  if (strncmp(line, "@D4 COUNT ", 10) != 0) return false;
  const char *cursor = line + 10;
  uint32_t revision, provider, count;
  if (!notificationNumber(cursor, UINT32_MAX, revision) || *cursor++ != ' ' ||
      !notificationNumber(cursor, 2, provider) || provider != 2 || *cursor++ != ' ' ||
      !notificationNumber(cursor, 999, count) || *cursor) return false;
  packet.revision = revision;
  packet.count = count;
  return true;
}

inline void usageDuration(uint32_t minutes, char *text, size_t size) {
  if (minutes && minutes % 1440 == 0) snprintf(text, size, "%lud", (unsigned long)(minutes / 1440));
  else if (minutes && minutes % 60 == 0) snprintf(text, size, "%luh", (unsigned long)(minutes / 60));
  else snprintf(text, size, "%lum", (unsigned long)minutes);
}
