// Run: clang++ -std=c++11 scripts/test_touch.cpp -o /tmp/dotty-test-touch && /tmp/dotty-test-touch
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "../firmware/DottyHello/TouchLatch.h"

struct Trace {
  TouchLatch latch;
  int taps = 0, releases = 0;
  void sample(TouchSample state, uint32_t time) {
    TouchEvent event = latch.update(state, time);
    if (event == TouchEvent::Down) ++taps;
    if (event == TouchEvent::Up) ++releases;
  }
};

int main() {
  using S = TouchSample;
  // A brief tap must act on its first packet, even if shorter than the old
  // 25 ms debounce window. Release confirmation must not delay that action.
  Trace quick;
  quick.sample(S::Pressed, 100);
  assert(quick.taps == 1);
  for (uint32_t t = 108; t <= 160; t += 4) quick.sample(S::Idle, t);
  assert(quick.taps == 1 && quick.releases == 1);

  // Holding must count once despite brief gaps and failed reads; errors by
  // themselves cannot rearm a button or make up a touch.
  Trace hold;
  for (uint32_t t = 0; t < 3000; t += 4) {
    S state = (t % 40 == 0) ? S::Idle : (t % 44 == 0) ? S::Error : S::Pressed;
    hold.sample(state, t);
  }
  assert(hold.taps == 1 && hold.releases == 0);
  for (uint32_t t = 3000; t < 3200; t += 4) hold.sample(S::Error, t);
  hold.sample(S::Pressed, 3200);
  assert(hold.taps == 1 && hold.releases == 0);
  for (uint32_t t = 3204; t < 3260; t += 4) hold.sample(S::Idle, t);
  assert(hold.releases == 1);

  // Distinct quick taps and double taps remain distinct, at eight per second.
  Trace repeated;
  for (uint32_t t = 0; t < 2500; t += 4) {
    repeated.sample((t % 124 < 12) ? S::Pressed : S::Idle, t);
  }
  assert(repeated.taps == 21 && repeated.releases == 20);

  // The millis() rollover after ~49 days must not break release timing.
  Trace rollover;
  rollover.sample(S::Pressed, UINT32_MAX - 20);
  rollover.sample(S::Idle, UINT32_MAX - 12);
  rollover.sample(S::Idle, 26);
  assert(rollover.releases == 0);
  rollover.sample(S::Idle, 27);
  rollover.sample(S::Pressed, 28);
  assert(rollover.releases == 1 && rollover.taps == 2);

  Trace errors;
  for (uint32_t t = 0; t < 1000; t += 4) errors.sample(S::Error, t);
  assert(errors.taps == 0 && errors.releases == 0);
  puts("PASS: quick taps, held touch, intermittent reports, read failures, rapid taps, clock rollover");
}
