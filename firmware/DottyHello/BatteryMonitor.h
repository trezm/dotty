#pragma once
#include <stdint.h>

// Round Display v1.0/v1.1: R28/R29 are both 470k, so A0 sees half VBAT.
// Voltage-derived bars are approximate; this is not a charge-counting gauge.
namespace PuckBattery {
struct Reading {
  uint16_t millivolts = 0;
  int8_t bars = -1;  // Unknown is distinct from a measured low battery.
};

inline int8_t level(uint16_t mv) {
  if (mv < 3500) return 0;
  if (mv < 3700) return 1;
  if (mv < 3850) return 2;
  if (mv < 4050) return 3;
  return 4;
}

class Monitor {
 public:
  static constexpr uint8_t samples = 20;
  Reading reading;
  uint16_t adcMillivolts = 0;

  bool add(uint32_t mv, uint32_t now) {
    values[count++] = mv > 65535 ? 65535 : static_cast<uint16_t>(mv);
    if (count < samples) return false;
    count = 0;
    // Trim both tails so one radio/display transient does not move the gauge.
    for (uint8_t i = 1; i < samples; ++i) {
      uint16_t value = values[i]; int j = i - 1;
      while (j >= 0 && values[j] > value) { values[j + 1] = values[j]; --j; }
      values[j + 1] = value;
    }
    uint32_t total = 0;
    for (uint8_t i = 2; i < samples - 2; ++i) total += values[i];
    adcMillivolts = (total + 8) / 16;
    uint32_t battery = uint32_t(adcMillivolts) * 2;
    Reading next;
    if (battery >= 3000 && battery <= 4350 && values[17] - values[2] <= 100) {
      uint16_t filtered = reading.bars < 0 ? battery : (3 * reading.millivolts + battery + 2) / 4;
      next.millivolts = ((filtered + 5) / 10) * 10;
      next.bars = level(next.millivolts);
      // Small voltage fluctuations near a boundary must not flicker a bar.
      const uint16_t thresholds[] = {3500, 3700, 3850, 4050};
      if (reading.bars >= 0 && next.bars > reading.bars &&
          next.millivolts < thresholds[reading.bars] + 40) next.bars = reading.bars;
      if (reading.bars > 0 && next.bars < reading.bars &&
          next.millivolts + 40 > thresholds[reading.bars - 1]) next.bars = reading.bars;
    }
    bool changed = next.millivolts != reading.millivolts || next.bars != reading.bars;
    reading = next;
    lastBatch = now;
    return changed;
  }

  bool expire(uint32_t now) {
    if (reading.bars < 0 || uint32_t(now - lastBatch) <= 15000) return false;
    reading = Reading();
    return true;
  }

 private:
  uint16_t values[samples] = {};
  uint8_t count = 0;
  uint32_t lastBatch = 0;
};
}  // namespace PuckBattery
