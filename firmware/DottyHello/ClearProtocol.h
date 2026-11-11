#pragma once
#include "NotificationProtocol.h"

struct ClearAck {
  uint32_t revision = 0, action = 0;
  uint8_t target = 0, status = 2;
};

inline bool parseClearAck(const char *line, ClearAck &ack) {
  constexpr char prefix[] = "@D5 CLEARED ";
  if (strncmp(line, prefix, sizeof(prefix) - 1) != 0) return false;
  const char *cursor = line + sizeof(prefix) - 1;
  uint32_t values[4] = {};
  const uint32_t limits[] = {UINT32_MAX, 1, UINT32_MAX, 2};
  for (uint8_t i = 0; i < 4; ++i) {
    if (!notificationNumber(cursor, limits[i], values[i])) return false;
    if (i < 3 && *cursor++ != ' ') return false;
  }
  if (*cursor) return false;
  ack.revision = values[0]; ack.target = values[1];
  ack.action = values[2]; ack.status = values[3];
  return true;
}
