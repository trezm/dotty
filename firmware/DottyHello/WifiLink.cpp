#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <atomic>
#include <esp_random.h>
#include <mbedtls/md.h>
#include "WifiProtocol.h"
#include "WifiLink.h"

namespace DottyWifi {
using namespace DottyWireless;
struct Packet { uint32_t epoch; char text[96]; };
struct Setup { char request[33]; Config config; };
static QueueHandle_t incoming, outgoing, setups, replies;
static std::atomic<bool> haveConfig{false}, online{false};
static std::atomic<bool> haveNetwork{false};
static bool taskReady = false;
static std::atomic<uint32_t> usbAt{0}, generation{0};
static Config config;
static NetworkClient client;
static uint8_t sessionKey[32] = {};
static uint32_t sent = 0, received = 0;

bool configured() { return haveConfig; }
bool connected() { return online; }
bool networkReady() { return haveNetwork; }
void noteUsb() { usbAt = millis() ? millis() : 1; }
bool usbPreferred() { uint32_t at = usbAt; return at && uint32_t(millis() - at) < 10000; }
unsigned long epoch() { return generation; }

static bool mac(const uint8_t *key, const char *text, uint8_t digest[32]) {
  const mbedtls_md_info_t *algorithm = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  return algorithm && mbedtls_md_hmac(algorithm, key, 32,
           reinterpret_cast<const uint8_t *>(text), strlen(text), digest) == 0;
}
static void toHex(const uint8_t *input, size_t length, char *output) {
  static const char digits[] = "0123456789abcdef";
  for (size_t i = 0; i < length; ++i) { output[i * 2] = digits[input[i] >> 4]; output[i * 2 + 1] = digits[input[i] & 15]; }
  output[length * 2] = 0;
}
static void keyBytes(const char *input, uint8_t output[32]) {
  for (size_t i = 0; i < 32; ++i) output[i] = (nibble(input[i * 2]) << 4) | nibble(input[i * 2 + 1]);
}
static bool equalTag(const char *tag, const uint8_t digest[32]) {
  if (!hex(tag, 64)) return false;
  uint8_t difference = 0;
  for (size_t i = 0; i < 32; ++i) difference |= digest[i] ^ ((nibble(tag[i * 2]) << 4) | nibble(tag[i * 2 + 1]));
  return difference == 0;
}
static bool writeLine(const char *line) {
  size_t length = strlen(line);
  return client.write(reinterpret_cast<const uint8_t *>(line), length) == length;
}
static bool readLine(char *line, size_t capacity, uint32_t timeout) {
  uint32_t start = millis(); size_t length = 0;
  while (client.connected() && uint32_t(millis() - start) < timeout) {
    if (client.available()) {
      char value = client.read();
      if (value == '\n') { line[length] = 0; return true; }
      if (value < 32 || value > 126 || length + 1 >= capacity) return false;
      line[length++] = value;
    } else vTaskDelay(pdMS_TO_TICKS(10));
  }
  return false;
}
static void disconnect() {
  client.stop();
  if (online.exchange(false)) ++generation;
  xQueueReset(incoming); xQueueReset(outgoing);
  wipe(sessionKey, sizeof(sessionKey)); sent = received = 0;
}
static bool authenticate() {
  char line[257], server[33], nonce[33], canonical[128], tag[65];
  uint8_t entropy[16], key[32], digest[32] = {};
  if (!readLine(line, sizeof(line), 3000) || strncmp(line, "@W1 HELLO ", 10) || !hex(line + 10, 32)) return false;
  strcpy(server, line + 10);
  esp_fill_random(entropy, sizeof(entropy)); toHex(entropy, sizeof(entropy), nonce);
  keyBytes(config.key, key);
  snprintf(canonical, sizeof(canonical), "dotty-wifi-v1/client:%s:%s", server, nonce);
  bool ok = mac(key, canonical, digest);
  toHex(digest, sizeof(digest), tag);
  snprintf(line, sizeof(line), "@W1 AUTH %s %s\n", nonce, tag);
  ok = ok && writeLine(line) && readLine(line, sizeof(line), 3000);
  snprintf(canonical, sizeof(canonical), "dotty-wifi-v1/server:%s:%s", server, nonce);
  ok = ok && mac(key, canonical, digest) && !strncmp(line, "@W1 OK ", 7) && equalTag(line + 7, digest);
  snprintf(canonical, sizeof(canonical), "dotty-wifi-v1/session:%s:%s", server, nonce);
  ok = ok && mac(key, canonical, sessionKey);
  wipe(key, sizeof(key)); wipe(digest, sizeof(digest));
  return ok;
}
static bool sendPacket(const char *payload) {
  if (sent == UINT32_MAX || !*payload || strlen(payload) > maxPayload) return false;
  char canonical[128], tag[65], wire[257]; uint8_t digest[32];
  ++sent;
  snprintf(canonical, sizeof(canonical), "C:%lu:%s", static_cast<unsigned long>(sent), payload);
  if (!mac(sessionKey, canonical, digest)) return false;
  toHex(digest, sizeof(digest), tag);
  snprintf(wire, sizeof(wire), "@W1 DATA %lu %s %s\n", static_cast<unsigned long>(sent), tag, payload);
  return writeLine(wire);
}
static bool receivePacket(const char *wire) {
  uint32_t sequence; char tag[65], payload[96], canonical[128]; uint8_t digest[32];
  if (!frame(wire, sequence, tag, payload) || received == UINT32_MAX || sequence != received + 1) return false;
  snprintf(canonical, sizeof(canonical), "S:%lu:%s", static_cast<unsigned long>(sequence), payload);
  if (!mac(sessionKey, canonical, digest) || !equalTag(tag, digest)) return false;
  received = sequence;
  Packet packet = {}; packet.epoch = generation; strcpy(packet.text, payload);
  return xQueueSend(incoming, &packet, 0) == pdTRUE;
}
static void reply(const char *kind, const char *request, const char *reason = nullptr) {
  Packet packet = {};
  snprintf(packet.text, sizeof(packet.text), "@P1 %s %s%s%s", kind, request, reason ? " " : "", reason ? reason : "");
  xQueueSend(replies, &packet, 0);
}
static bool join(const Config &cfg) {
  haveNetwork = false;
  MDNS.end(); WiFi.disconnect(); WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.begin(cfg.ssid, cfg.password[0] ? cfg.password : nullptr);
  uint32_t start = millis();
  while ((WiFi.status() != WL_CONNECTED || !WiFi.localIP()) && uint32_t(millis() - start) < 20000) vTaskDelay(pdMS_TO_TICKS(50));
  if (WiFi.status() != WL_CONNECTED || !WiFi.localIP()) { WiFi.disconnect(); return false; }
  haveNetwork = true;
  char name[32]; snprintf(name, sizeof(name), "dotty-%08lx", static_cast<unsigned long>(ESP.getEfuseMac()));
  MDNS.begin(name);
  return true;
}
static bool openHelper() {
  int count = MDNS.queryService("dotty", "tcp");
  for (int i = 0; i < count; ++i) {
    if (MDNS.hasTxt(i, "id") && MDNS.txt(i, "id") == config.id &&
        client.connect(MDNS.address(i), MDNS.port(i), 1500)) return authenticate();
  }
  // Bonjour discovery is preferred; the provisioned .local hostname is a fallback.
  String host(config.host);
  IPAddress address;
  if (host.endsWith(".local")) address = MDNS.queryHost(host.substring(0, host.length() - 6), 1500);
  else WiFi.hostByName(config.host, address);
  return address && client.connect(address, config.port, 1500) && authenticate();
}
static void worker(void *) {
  WiFi.persistent(false);
  client.setConnectionTimeout(1500);
  uint32_t retryAt = 0, retryMs = 1000, lastRx = 0, lastPing = 0;
  char wire[257]; size_t length = 0;
  for (;;) {
    Setup setup;
    if (xQueueReceive(setups, &setup, 0) == pdTRUE) {
      disconnect();
      bool joined = join(setup.config);
      bool committed = false;
      if (joined) {
        Preferences preferences;
        bool saved = preferences.begin("dotty-wifi", false) &&
                     preferences.putBytes("config", &setup.config, sizeof(Config)) == sizeof(Config);
        preferences.end();
        if (saved) { config = setup.config; haveConfig = committed = true; reply("SAVED", setup.request); }
        else reply("FAIL", setup.request, "storage");
      } else reply("FAIL", setup.request, "network");
      wipe(&setup, sizeof(setup)); retryAt = millis(); retryMs = 1000;
      if (!committed) { MDNS.end(); WiFi.disconnect(); haveNetwork = false; }
    }
    uint32_t now = millis();
    haveNetwork = WiFi.status() == WL_CONNECTED && bool(WiFi.localIP());
    if (!haveConfig || usbPreferred()) {
      disconnect();
      if (WiFi.getMode() != WIFI_OFF) { MDNS.end(); WiFi.mode(WIFI_OFF); }
      haveNetwork = false;
      vTaskDelay(pdMS_TO_TICKS(100)); continue;
    }
    if (!online && static_cast<int32_t>(now - retryAt) >= 0) {
      bool joined = WiFi.status() == WL_CONNECTED || join(config);
      if (joined && openHelper()) {
        xQueueReset(incoming); xQueueReset(outgoing); ++generation; online = true;
        length = 0; sent = received = 0; lastRx = lastPing = millis(); retryMs = 1000;
      } else {
        disconnect(); retryAt = millis() + retryMs + esp_random() % 250;
        retryMs = min(retryMs * 2, uint32_t(30000));
      }
    }
    if (online) {
      haveNetwork = WiFi.status() == WL_CONNECTED && bool(WiFi.localIP());
      bool healthy = client.connected() && WiFi.status() == WL_CONNECTED && uint32_t(millis() - lastRx) < 10000;
      for (int budget = 0; healthy && budget < 1024 && client.available(); ++budget) {
        char value = client.read();
        if (value == '\n') {
          wire[length] = 0; healthy = receivePacket(wire); length = 0; lastRx = millis();
        } else if (value < 32 || value > 126 || length + 1 >= sizeof(wire)) healthy = false;
        else wire[length++] = value;
      }
      Packet packet;
      while (healthy && xQueueReceive(outgoing, &packet, 0) == pdTRUE)
        if (packet.epoch == generation) healthy = sendPacket(packet.text);
      if (healthy && uint32_t(millis() - lastPing) >= 3000) { healthy = sendPacket("ping"); lastPing = millis(); }
      if (!healthy) { disconnect(); retryAt = millis() + 1000; }
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

void begin() {
  incoming = xQueueCreate(12, sizeof(Packet)); outgoing = xQueueCreate(8, sizeof(Packet));
  setups = xQueueCreate(1, sizeof(Setup)); replies = xQueueCreate(4, sizeof(Packet));
  if (!incoming || !outgoing || !setups || !replies) return;
  Preferences preferences;
  if (preferences.begin("dotty-wifi", true) && preferences.getBytesLength("config") == sizeof(Config) &&
      preferences.getBytes("config", &config, sizeof(Config)) == sizeof(Config) && valid(config)) haveConfig = true;
  preferences.end();
  taskReady = xTaskCreate(worker, "dotty-wifi", 8192, nullptr, 1, nullptr) == pdPASS;
  if (!taskReady) haveConfig = false;
}
bool handleUsb(const char *line) {
  if (strncmp(line, "@P1 ", 4)) return false;
  if (!taskReady) return true;
  if (!strncmp(line, "@P1 HELLO ", 10) && hex(line + 10, 32)) reply("READY", line + 10);
  else if (!strncmp(line, "@P1 SET ", 8)) {
    Setup setup = {};
    bool ok = provision(line, setup.request, setup.config);
    if (ok) {
      if (xQueueSend(setups, &setup, 0) != pdTRUE) reply("FAIL", setup.request, "busy");
    } else if (hex(setup.request, 32)) reply("FAIL", setup.request, "invalid");
    wipe(&setup, sizeof(setup));
  }
  return true;
}
bool takeUsbReply(char line[96]) {
  Packet packet;
  if (!replies || xQueueReceive(replies, &packet, 0) != pdTRUE) return false;
  strcpy(line, packet.text); return true;
}
bool takeLine(char line[96], unsigned long &epoch) {
  Packet packet;
  while (incoming && xQueueReceive(incoming, &packet, 0) == pdTRUE)
    if (online && packet.epoch == generation && !usbPreferred()) {
      strcpy(line, packet.text); epoch = packet.epoch; return true;
    }
  return false;
}
bool send(const char *line) {
  if (!online || !outgoing || !*line || strlen(line) > maxPayload) return false;
  Packet packet = {}; packet.epoch = generation; strcpy(packet.text, line);
  return xQueueSend(outgoing, &packet, 0) == pdTRUE;
}
}  // namespace DottyWifi
