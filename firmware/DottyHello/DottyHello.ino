// XIAO ESP32-C6 + Seeed Round Display. USB and paired home-network Wi-Fi.
#include <Arduino.h>
#include "driver.h"
#include <TFT_eSPI.h>
#include <Wire.h>
#include <stdarg.h>
#include "TouchLatch.h"
#include "NotificationProtocol.h"
#include "UsageProtocol.h"
#include "RingDashboard.h"
#include "ProviderGesture.h"
#include "ClearProtocol.h"
#include "WifiLink.h"

#if !defined(ARDUINO_XIAO_ESP32C6)
#error "Select XIAO_ESP32C6 under esp32 boards."
#endif

constexpr uint8_t kRotation = 1;  // 90-degree rotation; touch uses the same orientation.
constexpr uint8_t kTouchAddress = 0x2e;
constexpr uint32_t kTouchPollMs = 4;
constexpr uint16_t kBackground = 0x0862;
constexpr uint16_t kPanel = 0x18e5;
constexpr uint16_t kMuted = 0x94d3;
constexpr uint16_t kAccents[] = {0x4ef5, 0xfc4d, 0xaef0};

TFT_eSPI display;
TFT_eSprite frame(&display);
bool buffered = false;
uint32_t count = 0;
uint8_t accentIndex = 0;
int8_t pressedButton = -1;
TouchLatch touch;
uint32_t touchPackets = 0, touchErrors = 0, touchDowns = 0;
uint32_t fullDrawUs = 0, updateDrawUs = 0;
NotificationState notifications;
bool notificationMode = false;
int8_t notificationPressed = -1;
bool notificationConnected = false, pendingOpen = false;
uint32_t lastNotification = 0, pendingOpenRevision = 0, pendingOpenSince = 0;
uint8_t pendingOpenTarget = 0;
UsagePacket usage[3];
uint32_t lastUsage[3] = {};
uint16_t claudePendingCount = 0;
bool haveClaudeCount = false;
uint32_t lastClaudeCount = 0;
ProviderGesture providerGesture;
uint32_t gestureRevision = 0, clearSequence = 0, clearRevision = 0, clearSince = 0, clearFeedbackSince = 0;
uint8_t clearTarget = 0, clearResult = 2;
bool pendingClearSend = false, clearWaiting = false, clearFeedback = false;
enum class Link { None, Usb, Wifi };
Link activeLink = Link::None;
unsigned long activeWifiEpoch = 0;
PuckBattery::Monitor battery;

void cancelLink() {
  providerGesture.cancel();
  notificationConnected = false;
  notificationPressed = -1;
  pendingOpen = pendingClearSend = clearWaiting = clearFeedback = false;
  haveClaudeCount = false;
  for (uint8_t i = 0; i < 3; ++i) { usage[i] = UsagePacket(); lastUsage[i] = 0; }
}

bool sendControl(const char *line) {
  size_t length = strlen(line);
  if (activeLink == Link::Wifi) {
    if (!length || length >= 96) return false;
    char payload[96]; strcpy(payload, line);
    if (payload[length - 1] == '\n') payload[length - 1] = 0;
    return DottyWifi::send(payload);
  }
  return activeLink == Link::Usb && Serial.availableForWrite() >= length &&
         Serial.write(reinterpret_cast<const uint8_t *>(line), length) == length;
}

// USB logging must never stall the UI when the host stops reading its buffer.
void logLine(const char *format, ...) {
  char text[192];
  va_list args;
  va_start(args, format);
  int length = vsnprintf(text, sizeof(text), format, args);
  va_end(args);
  if (length > 0 && length < static_cast<int>(sizeof(text)) &&
      Serial.availableForWrite() >= length) {
    Serial.write(reinterpret_cast<const uint8_t *>(text), length);
  }
}

struct Button {
  int16_t x, y, w, h;
  const char *label;
  bool contains(int16_t px, int16_t py) const {
    // Slightly larger touch targets, including the narrow gap between buttons.
    constexpr int16_t padding = 6;
    return px >= x - padding && px < x + w + padding &&
           py >= y - padding && py < y + h + padding;
  }
};
const Button addButton = {38, 153, 76, 40, "+1"};
const Button colorButton = {126, 153, 76, 40, "Color"};

template <typename Canvas>
void paintTitle(Canvas &canvas) {
  const uint16_t accent = kAccents[accentIndex];
  canvas.setTextDatum(MC_DATUM);
  canvas.setTextColor(accent, kBackground);
  canvas.drawString("D O T T Y", 120, 41, 2);
}

template <typename Canvas>
void paintCounter(Canvas &canvas) {
  canvas.fillRect(32, 109, 176, 28, kBackground);
  canvas.setTextDatum(MC_DATUM);
  canvas.setTextColor(kMuted, kBackground);
  char text[32];
  snprintf(text, sizeof(text), "Taps: %lu", static_cast<unsigned long>(count));
  canvas.drawString(text, 120, 123, 2);
}

template <typename Canvas>
void paintButtons(Canvas &canvas) {
  const uint16_t accent = kAccents[accentIndex];
  canvas.setTextDatum(MC_DATUM);
  int8_t index = 0;
  for (const Button *button : {&addButton, &colorButton}) {
    bool down = pressedButton == index++;
    uint16_t fill = down ? kPanel : accent;
    canvas.fillRoundRect(button->x, button->y, button->w, button->h, 12, fill);
    canvas.drawRoundRect(button->x, button->y, button->w, button->h, 12, accent);
    canvas.setTextColor(down ? TFT_WHITE : kBackground, fill);
    canvas.drawString(button->label, button->x + button->w / 2,
                      button->y + button->h / 2, 2);
  }
}

template <typename Canvas>
void paint(Canvas &canvas) {
  canvas.fillScreen(kBackground);
  canvas.drawCircle(120, 120, 116, kPanel);
  paintTitle(canvas);
  canvas.setTextColor(TFT_WHITE, kBackground);
  canvas.drawString("Hello, world!", 120, 88, 4);
  RingDashboard::paintBattery(canvas, battery.reading, 62);
  paintCounter(canvas);
  paintButtons(canvas);
  canvas.setTextColor(kMuted, kBackground);
  canvas.drawString("XIAO ESP32-C6", 120, 211, 1);
}

void drawScreen() {
  const uint32_t start = micros();
  if (buffered) {
    paint(frame);
    frame.pushSprite(0, 0);
  } else {
    paint(display);  // Still usable if a full-screen sprite cannot be allocated.
  }
  fullDrawUs = micros() - start;
}

template <typename Canvas>
void paintChanges(Canvas &canvas, bool title, bool counter, bool buttons) {
  if (title) paintTitle(canvas);
  if (counter) paintCounter(canvas);
  if (buttons) paintButtons(canvas);
}

void drawChanges(bool title, bool counter, bool buttons) {
  const uint32_t start = micros();
  if (buffered) {
    paintChanges(frame, title, counter, buttons);
    // Full-width bands are contiguous in sprite memory: one SPI transfer per
    // band. Unchanged text and the rest of the screen stay on the display.
    if (title) frame.pushSprite(0, 29, 0, 29, 240, 25);
    if (counter) frame.pushSprite(0, 109, 0, 109, 240, 28);
    if (buttons) frame.pushSprite(0, 149, 0, 149, 240, 48);
  } else {
    paintChanges(display, title, counter, buttons);
  }
  updateDrawUs = micros() - start;
}

RingDashboard::State dashboardState() {
  RingDashboard::State state;
  state.battery = battery.reading;
  state.providers[0].available = notificationConnected && (notifications.available & 1);
  state.providers[0].count = notifications.codex;
  state.providers[0].pending = notifications.codex > 0;
  state.providers[1].available = notificationConnected && (notifications.available & 4);
  state.providers[1].pending = notifications.claude != 0;
  state.providers[1].count = haveClaudeCount ? claudePendingCount : notifications.claude;
  state.providers[1].countExact = haveClaudeCount || !notifications.claude;
  for (uint8_t i = 0; i < 2; ++i) {
    uint8_t source = i == 0 ? 0 : 2;
    state.providers[i].pressed = notificationPressed == i;
    state.providers[i].clearing = clearWaiting && clearTarget == i;
    orderUsage(usage[source], notificationConnected && lastUsage[source], state.providers[i].windows);
  }
  bool active = notificationConnected &&
                ((state.providers[0].available && state.providers[0].pending) ||
                 (state.providers[1].available && state.providers[1].pending));
  uint16_t color = state.providers[0].available && state.providers[0].pending ?
                   RingDashboard::accents[0] : RingDashboard::accents[1];
  state.border = RingDashboard::pulseColor(millis(), active, color);
  bool codexUsage = usageConsumed(state.providers[0].windows[0]) >= 0 ||
                    usageConsumed(state.providers[0].windows[1]) >= 0;
  bool claudeUsage = usageConsumed(state.providers[1].windows[0]) >= 0 ||
                     usageConsumed(state.providers[1].windows[1]) >= 0;
  state.footer = !notificationConnected ? (DottyWifi::configured() ?
                    (DottyWifi::networkReady() ? "Waiting for Mac" : "Connecting Wi-Fi") : "Connect USB helper") :
                 !state.providers[0].available || !state.providers[1].available ? "Feed incomplete" :
                 !codexUsage && !claudeUsage ? "Waiting for usage" :
                 !claudeUsage ? "Claude usage offline" :
                 !codexUsage ? "Codex usage offline" : "Tap / hold to clear";
  if (notificationConnected) {
    if (clearWaiting) state.footer = clearTarget == 0 ? "Clearing Codex" : "Clearing Claude";
    else if (clearFeedback) state.footer = clearResult == 0 ?
        (clearTarget == 0 ? "Codex cleared" : "Claude cleared") :
        clearResult == 1 ? "Some alerts remain" : "Clear failed";
    else if (notificationPressed >= 0) state.footer = "Hold 1s to clear";
  }
  return state;
}

void drawNotifications(bool buttonOnly = false) {
  RingDashboard::State state = dashboardState();
  if (buffered) {
    if (buttonOnly) {
      for (uint8_t i = 0; i < 2; ++i) {
        RingDashboard::paintButton(frame, state.providers[i], i);
        constexpr int16_t radius = RingDashboard::buttonRadius + 1;
        int16_t x = RingDashboard::centerX[i] - radius;
        int16_t y = RingDashboard::centerY - radius;
        frame.pushSprite(x, y, x, y, radius * 2 + 1, radius * 2 + 1);
      }
    } else {
      RingDashboard::paint(frame, state);
      frame.pushSprite(0, 0);
    }
  } else if (buttonOnly) {
    for (uint8_t i = 0; i < 2; ++i) RingDashboard::paintButton(display, state.providers[i], i);
  } else {
    RingDashboard::paint(display, state);
  }
}

void receiveNotification(const char *line, bool fromUsb = true, unsigned long wifiEpoch = 0) {
  if (fromUsb && DottyWifi::handleUsb(line)) return;
  const Link source = fromUsb ? Link::Usb : Link::Wifi;
  NotificationState incomingState;
  bool isState = parseNotificationState(line, incomingState);
  if (isState && fromUsb) DottyWifi::noteUsb();
  if (!fromUsb && (DottyWifi::usbPreferred() || !DottyWifi::connected() || wifiEpoch != DottyWifi::epoch())) return;
  if (source != activeLink) {
    if (!isState) return;
    cancelLink(); activeLink = source;
  }
  if (isState && !fromUsb) activeWifiEpoch = wifiEpoch;
  ClearAck ack;
  if (parseClearAck(line, ack)) {
    if (!clearWaiting || ack.action != clearSequence || ack.revision != clearRevision ||
        ack.target != clearTarget) return;
    clearWaiting = pendingClearSend = false;
    clearResult = ack.status;
    clearFeedback = true;
    clearFeedbackSince = millis();
    drawNotifications();
    return;
  }
  ClaudeCountPacket count;
  if (parseClaudeCount(line, count)) {
    if (!notificationMode || count.revision != notifications.revision ||
        bool(count.count) != bool(notifications.claude)) return;
    bool changed = !haveClaudeCount || count.count != claudePendingCount;
    claudePendingCount = count.count;
    haveClaudeCount = true;
    lastClaudeCount = millis();
    if (changed) drawNotifications();
    return;
  }
  UsagePacket packet;
  if (parseUsage(line, packet)) {
    if (!notificationMode || packet.revision != notifications.revision) return;
    uint8_t provider = packet.provider;
    bool changed = !lastUsage[provider] ||
                   !sameUsage(usage[provider].windows[0], packet.windows[0]) ||
                   !sameUsage(usage[provider].windows[1], packet.windows[1]);
    usage[provider] = packet;
    lastUsage[provider] = millis();
    if (changed && provider != 1) drawNotifications();
    return;
  }
  NotificationState state;
  if (!parseNotificationState(line, state)) return;
  bool changed = !notificationMode || !notificationConnected ||
                 state.codex != notifications.codex || state.chatgpt != notifications.chatgpt ||
                 state.claude != notifications.claude ||
                 state.available != notifications.available || state.monitored != notifications.monitored ||
                 state.localFeeds != notifications.localFeeds;
  if (state.claude != notifications.claude) haveClaudeCount = false;
  notifications = state;
  notificationMode = notificationConnected = true;
  lastNotification = millis();
  if (changed) drawNotifications();
}

void updateNotifications() {
  if (!notificationMode) return;
  const uint32_t now = millis();
  bool usageExpired = false;
  if (haveClaudeCount && uint32_t(now - lastClaudeCount) > 10000) {
    haveClaudeCount = false;
    usageExpired = true;
  }
  for (uint8_t provider = 0; provider < 3; ++provider) {
    if (lastUsage[provider] && uint32_t(now - lastUsage[provider]) > 10000) {
      usageExpired |= usage[provider].windows[0].used != 255 || usage[provider].windows[1].used != 255;
      usage[provider] = UsagePacket();
      lastUsage[provider] = 0;
    }
  }
  if (usageExpired) drawNotifications();
  if (notificationConnected && uint32_t(now - lastNotification) > 10000) {
    notificationConnected = false;
    providerGesture.cancel();
    notificationPressed = -1;
    pendingOpen = pendingClearSend = clearWaiting = clearFeedback = false;
    drawNotifications();
  }
  if (clearFeedback && uint32_t(now - clearFeedbackSince) >= 2200) {
    clearFeedback = false;
    drawNotifications();
  }
  if (clearWaiting && uint32_t(now - clearSince) >= 15000) {
    clearWaiting = pendingClearSend = false;
    clearFeedback = true;
    clearResult = 2;
    clearFeedbackSince = now;
    drawNotifications();
  }
  if (pendingClearSend) {
    char line[72];
    int length = snprintf(line, sizeof(line), "@D5 CLEAR %lu %u %lu\n",
                          static_cast<unsigned long>(clearRevision), clearTarget,
                          static_cast<unsigned long>(clearSequence));
    if (length > 0 && sendControl(line))
      pendingClearSend = false;
  }
  static uint32_t lastPulse = 0;
  if (notificationConnected && notifications.available &&
      notifications.codex + notifications.claude > 0 && uint32_t(now - lastPulse) >= 100) {
    lastPulse = now;
    // Pulse only the two thin display-border circles. Quota arcs and text
    // stay still, and animation never transfers a full-screen frame.
    RingDashboard::paintBorder(display, dashboardState().border);
  }
  if (pendingOpen) {
    if (uint32_t(now - pendingOpenSince) > 2000) {
      pendingOpen = false;
    } else {
      char line[40];
      int length = snprintf(line, sizeof(line), "@D2 OPEN %lu %u\n",
                            static_cast<unsigned long>(pendingOpenRevision), pendingOpenTarget);
      if (length > 0 && sendControl(line)) {
        pendingOpen = false;
      }
    }
  }
}

// Same five-byte protocol as Seeed's CHSC6X driver. Reject partial/invalid
// packets instead of reporting a false touch at (0, 0). No resistive calibration.
TouchSample readTouch(int16_t &x, int16_t &y) {
  if (digitalRead(D7) != LOW) return TouchSample::Idle;
  constexpr size_t packetSize = 5;
  uint8_t packet[packetSize];
  if (Wire.requestFrom(kTouchAddress, packetSize) != packetSize) {
    while (Wire.available()) Wire.read();
    ++touchErrors;
    return TouchSample::Error;
  }
  // requestFrom has already buffered all five bytes; no Stream timeout needed.
  for (size_t i = 0; i < packetSize; ++i) packet[i] = Wire.read();
  if (packet[0] == 0) return TouchSample::Idle;
  if (packet[0] != 1 || packet[2] >= 240 || packet[4] >= 240) {
    ++touchErrors;
    return TouchSample::Error;
  }
  ++touchPackets;
  x = packet[2];
  y = packet[4];
  for (uint8_t i = 0; i < kRotation; ++i) {
    int16_t oldX = x;
    x = y;
    y = 239 - oldX;
  }
  return TouchSample::Pressed;
}

void setup() {
  Serial.begin(115200);  // Do not wait for a serial monitor to be connected.
  Serial.setTxTimeoutMs(0);
  pinMode(D2, OUTPUT);   // Deselect the display board's unused microSD on shared SPI.
  digitalWrite(D2, HIGH);
  display.init();
  display.setRotation(kRotation);
  pinMode(D6, OUTPUT);
  digitalWrite(D6, HIGH);
  pinMode(D7, INPUT_PULLUP);
  Wire.begin(D4, D5);
  Wire.setClock(100000);
  Wire.setTimeOut(30);
  frame.setColorDepth(16);
  buffered = frame.createSprite(240, 240) != nullptr;
  pinMode(A0, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(A0, ADC_11db);
  analogReadMilliVolts(A0);  // Discard the initial conversion after ADC setup.
  DottyWifi::begin();
  if (DottyWifi::configured()) { notificationMode = true; drawNotifications(); }
  else drawScreen();
  logLine("Dotty ready (responsive touch). Tap +1 / Color. r: reset, s: status.\n");
  logLine("@D2 READY\n");
}

void loop() {
  static uint32_t lastPoll = 0;
  const uint32_t now = millis();
  static uint32_t lastBatterySample = 0;
  static uint8_t batterySamples = 0;
  uint32_t samplePeriod = batterySamples ? 25 : 4500;
  if (uint32_t(now - lastBatterySample) >= samplePeriod) {
    lastBatterySample = now;
    bool changed = battery.add(analogReadMilliVolts(A0), now);
    batterySamples = (batterySamples + 1) % PuckBattery::Monitor::samples;
    if (changed) {
      if (notificationMode) drawNotifications();
      else drawScreen();
    }
  }
  if (battery.expire(now)) {
    if (notificationMode) drawNotifications();
    else drawScreen();
  }
  if (activeLink == Link::Wifi && (!DottyWifi::connected() || activeWifiEpoch != DottyWifi::epoch())) {
    cancelLink(); activeLink = Link::None;
    if (notificationMode) drawNotifications();
  }
  if (!notificationMode && DottyWifi::configured()) { notificationMode = true; drawNotifications(); }
  static bool previousNetworkReady = false;
  bool networkReady = DottyWifi::networkReady();
  if (networkReady != previousNetworkReady) {
    previousNetworkReady = networkReady;
    if (notificationMode && !notificationConnected) drawNotifications();
  }
  char networkLine[96];
  unsigned long wifiEpoch;
  for (uint8_t budget = 0; budget < 12 && DottyWifi::takeLine(networkLine, wifiEpoch); ++budget)
    receiveNotification(networkLine, false, wifiEpoch);
  while (DottyWifi::takeUsbReply(networkLine)) logLine("%s\n", networkLine);
  if (uint32_t(now - lastPoll) >= kTouchPollMs) {
    lastPoll = now;
    int16_t x = 0, y = 0;
    TouchSample sample = readTouch(x, y);
    TouchEvent event = touch.update(sample, millis());
    if (notificationMode) {
      const uint32_t touchNow = millis();
      int8_t hit = sample == TouchSample::Pressed ? RingDashboard::hitTest(x, y) : -1;
      if (event == TouchEvent::Down) {
        gestureRevision = notifications.revision;
        if (clearWaiting) hit = -1;
      }
      bool clearAllowed = notifications.localFeeds && hit >= 0 &&
                          (notifications.available & (hit == 0 ? 1 : 4));
      ProviderGestureAction action = providerGesture.update(sample, event, hit, touchNow,
                                                            notificationConnected, clearAllowed);
      int8_t pressed = providerGesture.pressed();
      bool redraw = pressed != notificationPressed;
      notificationPressed = pressed;
      if (action.type == ProviderAction::Open) {
        pendingOpen = true;
        pendingOpenTarget = action.target;
        pendingOpenRevision = gestureRevision;
        pendingOpenSince = touchNow;
      } else if (action.type == ProviderAction::Clear) {
        pendingOpen = false;
        pendingClearSend = clearWaiting = true;
        clearFeedback = false;
        clearTarget = action.target;
        clearRevision = gestureRevision;
        ++clearSequence;
        clearSince = touchNow;
        redraw = true;
      }
      if (redraw) drawNotifications();
    }
    if (event == TouchEvent::Down) {
      ++touchDowns;
      if (!notificationMode && addButton.contains(x, y)) {
        pressedButton = 0;
        ++count;
        drawChanges(false, true, true);
      } else if (!notificationMode && colorButton.contains(x, y)) {
        pressedButton = 1;
        accentIndex = (accentIndex + 1) % (sizeof(kAccents) / sizeof(kAccents[0]));
        drawChanges(true, false, true);
      }
      logLine("Touch: %d, %d; button=%d; update=%lu us\n", x, y, pressedButton,
              static_cast<unsigned long>(updateDrawUs));
    } else if (event == TouchEvent::Up) {
      if (pressedButton != -1) {
        pressedButton = -1;
        if (!notificationMode) drawChanges(false, false, true);
      }
    }
  }
  static char serialLine[768];
  static size_t serialLength = 0;
  static bool serialOverflow = false;
  for (uint8_t budget = 0; budget < 64 && Serial.available(); ++budget) {
    const char command = Serial.read();
    if (command == '\n') {
      serialLine[serialLength] = '\0';
      if (!serialOverflow && serialLength) receiveNotification(serialLine);
      memset(serialLine, 0, sizeof(serialLine));  // Provisioning credentials must not remain in the parser buffer.
      serialLength = 0;
      serialOverflow = false;
    } else if (command == '\r') {
      continue;
    } else if (serialLength == 0 && !serialOverflow && command == 'r') {
      count = 0;
      if (!notificationMode) drawChanges(false, true, false);
    } else if (serialLength == 0 && !serialOverflow && command == 's') {
      logLine("Status: buffered=%d rotation=%d taps=%lu downs=%lu packets=%lu errors=%lu full=%lu us update=%lu us\n",
              buffered, kRotation, static_cast<unsigned long>(count),
              static_cast<unsigned long>(touchDowns), static_cast<unsigned long>(touchPackets),
              static_cast<unsigned long>(touchErrors), static_cast<unsigned long>(fullDrawUs),
              static_cast<unsigned long>(updateDrawUs));
      logLine("Notify: mode=%d connected=%d Codex=%u ChatGPT=%u Claude=%u available=%u monitored=%u local=%d\n",
              notificationMode, notificationConnected, notifications.codex,
              notifications.chatgpt, notifications.claude, notifications.available,
              notifications.monitored, notifications.localFeeds);
      logLine("Pending: Claude=%u exact=%d\n", claudePendingCount, haveClaudeCount);
      logLine("WiFi: configured=%d connected=%d\n", DottyWifi::configured(), DottyWifi::connected());
      logLine("Battery: adc_mV=%u battery_mV=%u bars=%d\n", battery.adcMillivolts,
              battery.reading.millivolts, battery.reading.bars);
      for (uint8_t provider = 0; provider < 3; ++provider)
        logLine("Usage: provider=%u one=%u two=%u\n", provider,
                usage[provider].windows[0].used, usage[provider].windows[1].used);
    } else if (serialLength < sizeof(serialLine) - 1 && !serialOverflow) {
      serialLine[serialLength++] = command;
    } else {
      serialOverflow = true;
    }
  }
  updateNotifications();
  delay(1);  // Yield to USB/RTOS; touch sampling has its own 4 ms schedule.
}
