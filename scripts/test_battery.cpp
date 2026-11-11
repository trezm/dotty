#include <assert.h>
#include <stdio.h>
#include "../firmware/DottyHello/BatteryMonitor.h"

static void sample(PuckBattery::Monitor &monitor, uint32_t mv, uint32_t now) {
  for (uint8_t i = 0; i < PuckBattery::Monitor::samples; ++i) monitor.add(mv, now);
}

int main() {
  PuckBattery::Monitor battery;
  assert(battery.reading.bars == -1);
  for (uint8_t i = 0; i < 19; ++i) assert(!battery.add(1900, 100));
  assert(battery.reading.bars == -1);  // A partial batch cannot invent a charge level.
  assert(battery.add(1900, 100));
  assert(battery.adcMillivolts == 1900 && battery.reading.millivolts == 3800);
  assert(battery.reading.bars == 2);
  assert(!battery.expire(15100));
  assert(battery.expire(15101) && battery.reading.bars == -1);
  sample(battery, 1650, 16000);
  assert(battery.reading.millivolts == 3300 && battery.reading.bars == 0);
  sample(battery, 0, 17000);
  assert(battery.reading.bars == -1 && battery.reading.millivolts == 0);
  sample(battery, 2500, 18000);
  assert(battery.reading.bars == -1);  // Reject non-cell/invalid ADC voltages.
  sample(battery, 2100, 19000);
  assert(battery.reading.bars == 4 && battery.reading.millivolts == 4200);
  battery = PuckBattery::Monitor();
  for (uint8_t i = 0; i < 20; ++i) battery.add(i == 0 ? 0 : i == 19 ? 4000 : 1900, 20000);
  assert(battery.reading.millivolts == 3800);  // Discard individual outliers.
  for (uint8_t i = 0; i < 20; ++i) battery.add(i % 2 ? 1500 : 2150, 21000);
  assert(battery.reading.bars == -1);  // Sustained instability is unknown, not an average charge.
  sample(battery, 1920, 22000);
  assert(battery.reading.bars == 2);
  sample(battery, 1930, 23000);
  assert(battery.reading.bars == 2);  // A few millivolts at a threshold cannot flicker bars.
  for (uint8_t i = 0; i < 20; ++i) sample(battery, 1980, 24000 + i);
  assert(battery.reading.bars == 3);
  sample(battery, 1900, UINT32_MAX - 100);
  assert(!battery.expire(100));  // Freshness remains correct across millis() wrap.
  assert(battery.expire(16000));
  puts("PASS: battery divider, unknown/low/full states, trimmed samples, stability, hysteresis, expiry");
}
