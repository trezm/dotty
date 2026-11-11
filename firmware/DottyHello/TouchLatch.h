#pragma once
#include <stdint.h>

enum class TouchSample { Idle, Pressed, Error };
enum class TouchEvent { None, Down, Up };

// Capacitive touches already arrive as validated digital coordinates. Accept
// the first good packet immediately; debounce only release, so a short gap in
// reports cannot generate extra taps. A failed read is not a finger release.
class TouchLatch {
 public:
  static constexpr uint32_t kReleaseMs = 40;

  TouchEvent update(TouchSample sample, uint32_t now) {
    if (sample == TouchSample::Pressed) {
      releasing_ = false;
      if (down_) return TouchEvent::None;
      down_ = true;
      return TouchEvent::Down;
    }
    if (sample == TouchSample::Error) {
      releasing_ = false;
      return TouchEvent::None;
    }
    if (!down_) return TouchEvent::None;
    if (!releasing_) {
      releasing_ = true;
      releaseSince_ = now;
    }
    if (uint32_t(now - releaseSince_) < kReleaseMs) return TouchEvent::None;
    down_ = false;
    releasing_ = false;
    return TouchEvent::Up;
  }

 private:
  bool down_ = false;
  bool releasing_ = false;
  uint32_t releaseSince_ = 0;
};
