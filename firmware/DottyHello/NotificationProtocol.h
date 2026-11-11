#pragma once
#include <stdint.h>
#include <string.h>

struct NotificationState {
  uint32_t revision = 0;
  uint16_t codex = 0, chatgpt = 0;
  uint8_t claude = 0;  // Presence of unread activity, not a conversation count.
  uint8_t available = 0;  // Bit 0: Codex; bit 1: ChatGPT; bit 2: Claude.
  uint8_t monitored = 7;
  bool localFeeds = false;  // D3: local Codex receipts and Claude Code notification queue.
};

inline bool notificationNumber(const char *&cursor, uint32_t maximum, uint32_t &value) {
  if (*cursor < '0' || *cursor > '9') return false;
  uint64_t number = 0;
  do {
    number = number * 10 + (*cursor++ - '0');
    if (number > maximum) return false;
  } while (*cursor >= '0' && *cursor <= '9');
  value = static_cast<uint32_t>(number);
  return true;
}

// Small, bounded ASCII protocol; malformed messages never replace good state.
inline bool parseNotificationState(const char *line, NotificationState &state) {
  bool v3 = strncmp(line, "@D3 STATE ", 10) == 0;
  bool v2 = v3 || strncmp(line, "@D2 STATE ", 10) == 0;
  if (!v2 && strncmp(line, "@D1 STATE ", 10) != 0) return false;
  const char *cursor = line + 10;
  uint32_t values[6] = {};
  const uint32_t maximum[] = {UINT32_MAX, 999, 999, v2 ? 1u : 3u, 7, 7};
  uint8_t fields = v3 ? 6 : v2 ? 5 : 4;
  for (uint8_t i = 0; i < fields; ++i) {
    if (!notificationNumber(cursor, maximum[i], values[i])) return false;
    if (i + 1 < fields && *cursor++ != ' ') return false;
  }
  if (*cursor != '\0') return false;
  if (v3 && (values[4] & ~values[5])) return false;
  state.revision = values[0];
  state.codex = values[1];
  state.chatgpt = values[2];
  state.claude = v2 ? values[3] : 0;
  state.available = values[v2 ? 4 : 3];
  state.monitored = v3 ? values[5] : 7;
  state.localFeeds = v3;
  return true;
}
