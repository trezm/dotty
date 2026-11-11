#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace DottyWireless {
constexpr size_t maxPayload = 95;
constexpr size_t maxWire = 256;
constexpr size_t maxProvision = 768;
constexpr uint32_t configMagic = 0x44545731;

inline void wipe(void *buffer, size_t length) {
  volatile uint8_t *bytes = static_cast<volatile uint8_t *>(buffer);
  while (length--) *bytes++ = 0;
}

inline bool hex(const char *value, size_t length) {
  if (strlen(value) != length) return false;
  for (size_t i = 0; i < length; ++i)
    if (!((value[i] >= '0' && value[i] <= '9') || (value[i] >= 'a' && value[i] <= 'f'))) return false;
  return true;
}

inline uint8_t nibble(char value) { return value <= '9' ? value - '0' : value - 'a' + 10; }

inline bool unhex(const char *value, char *output, size_t capacity) {
  if (strcmp(value, "-") == 0) { output[0] = 0; return true; }
  size_t length = strlen(value);
  if ((length & 1) || length / 2 >= capacity || !hex(value, length)) return false;
  for (size_t i = 0; i < length / 2; ++i) {
    output[i] = (nibble(value[i * 2]) << 4) | nibble(value[i * 2 + 1]);
    if (!output[i]) return false;
  }
  output[length / 2] = 0;
  return true;
}

inline bool number(const char *text, uint32_t limit, uint32_t &value) {
  if (*text < '1' || *text > '9') return false;
  uint64_t result = 0;
  while (*text) {
    if (*text < '0' || *text > '9') return false;
    result = result * 10 + (*text++ - '0');
    if (result > limit) return false;
  }
  value = static_cast<uint32_t>(result);
  return true;
}

struct Config {
  uint32_t magic = configMagic;
  char ssid[33] = {}, password[65] = {}, host[96] = {};
  uint16_t port = 0;
  char id[33] = {}, key[65] = {};
};

inline bool valid(const Config &cfg) {
  if (cfg.magic != configMagic || !cfg.port || !memchr(cfg.ssid, 0, sizeof(cfg.ssid)) ||
      !memchr(cfg.password, 0, sizeof(cfg.password)) || !memchr(cfg.host, 0, sizeof(cfg.host)) ||
      !memchr(cfg.id, 0, sizeof(cfg.id)) || !memchr(cfg.key, 0, sizeof(cfg.key)) ||
      !cfg.ssid[0] || !cfg.host[0] || !hex(cfg.id, 32) || !hex(cfg.key, 64)) return false;
  size_t pass = strlen(cfg.password);
  if (pass && (pass < 8 || (pass > 63 && pass != 64))) return false;
  if (pass == 64) {
    for (const char *p = cfg.password; *p; ++p)
      if (!((*p >= '0' && *p <= '9') || (*p >= 'a' && *p <= 'f') || (*p >= 'A' && *p <= 'F'))) return false;
  }
  for (const char *p = cfg.host; *p; ++p)
    if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') || (*p >= '0' && *p <= '9') || *p == '.' || *p == '-')) return false;
  const char first = cfg.host[0], last = cfg.host[strlen(cfg.host) - 1];
  if (first == '.' || first == '-' || last == '.' || last == '-') return false;
  return true;
}

inline bool provision(const char *line, char request[33], Config &cfg) {
  if (strlen(line) >= maxProvision) return false;
  char copy[maxProvision];
  strcpy(copy, line);
  struct Cleanup { char *p; ~Cleanup() { wipe(p, maxProvision); } } cleanup{copy};
  char *tokens[9], *save = nullptr;
  for (int i = 0; i < 9; ++i) {
    tokens[i] = strtok_r(i == 0 ? copy : nullptr, " ", &save);
    if (!tokens[i]) return false;
  }
  if (strtok_r(nullptr, " ", &save) || strcmp(tokens[0], "@P1") || strcmp(tokens[1], "SET") || !hex(tokens[2], 32)) return false;
  strcpy(request, tokens[2]);
  uint32_t port;
  if (!unhex(tokens[3], cfg.ssid, sizeof(cfg.ssid)) || !unhex(tokens[4], cfg.password, sizeof(cfg.password)) ||
      !unhex(tokens[5], cfg.host, sizeof(cfg.host)) || !number(tokens[6], 65535, port) ||
      !hex(tokens[7], 32) || !hex(tokens[8], 64)) return false;
  cfg.port = port;
  strcpy(cfg.id, tokens[7]); strcpy(cfg.key, tokens[8]);
  return valid(cfg);
}

inline bool frame(const char *line, uint32_t &sequence, char tag[65], char payload[96]) {
  if (strncmp(line, "@W1 DATA ", 9) || strlen(line) > maxWire) return false;
  const char *cursor = line + 9, *end = strchr(cursor, ' ');
  if (!end || end - cursor > 10) return false;
  char count[11] = {};
  memcpy(count, cursor, end - cursor);
  if (!number(count, UINT32_MAX, sequence)) return false;
  cursor = end + 1; end = strchr(cursor, ' ');
  if (!end || end - cursor != 64) return false;
  memcpy(tag, cursor, 64); tag[64] = 0;
  if (!hex(tag, 64)) return false;
  cursor = end + 1;
  size_t length = strlen(cursor);
  if (!length || length > maxPayload) return false;
  for (const char *p = cursor; *p; ++p) if (*p < 32 || *p > 126) return false;
  strcpy(payload, cursor);
  return true;
}
}  // namespace DottyWireless
