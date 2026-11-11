#include <assert.h>
#include <stdio.h>
#include "../firmware/DottyHello/ProviderGesture.h"
#include "../firmware/DottyHello/ClearProtocol.h"

struct Trace {
  TouchLatch touch;
  ProviderGesture gesture;
  int opens = 0, clears = 0;
  ProviderGestureAction sample(TouchSample sample, uint32_t now, int8_t hit = 0,
                               bool connected = true, bool available = true) {
    auto action = gesture.update(sample, touch.update(sample, now), hit, now, connected, available);
    if (action.type == ProviderAction::Open) ++opens;
    if (action.type == ProviderAction::Clear) ++clears;
    return action;
  }
  void release(uint32_t now) {
    sample(TouchSample::Idle, now);
    sample(TouchSample::Idle, now + 40);
  }
};

int main() {
  using S = TouchSample;
  Trace tap;
  tap.sample(S::Pressed, 100);
  assert(tap.opens == 0 && tap.clears == 0); // Do not open before distinguishing a hold.
  tap.release(112);
  assert(tap.opens == 1 && tap.clears == 0);

  Trace hold;
  for (uint32_t t = 0; t < 1000; t += 4) hold.sample(S::Pressed, t, 1);
  assert(hold.clears == 0);
  auto action = hold.sample(S::Pressed, 1000, 1);
  assert(action.type == ProviderAction::Clear && action.target == 1);
  for (uint32_t t = 1004; t < 3000; t += 4) hold.sample(S::Pressed, t, 1);
  hold.release(3000);
  assert(hold.clears == 1 && hold.opens == 0);
  hold.sample(S::Pressed, 3100, 0);
  hold.release(3120);
  assert(hold.opens == 1 && hold.clears == 1);

  Trace drag;
  drag.sample(S::Pressed, 0, 0);
  drag.sample(S::Pressed, 100, -1);
  for (uint32_t t = 104; t < 1500; t += 4) drag.sample(S::Pressed, t, 1);
  drag.release(1500);
  assert(drag.opens == 0 && drag.clears == 0);

  Trace error;
  error.sample(S::Pressed, 0);
  for (uint32_t t = 4; t < 1200; t += 4) error.sample(S::Error, t);
  error.sample(S::Pressed, 1200);
  assert(error.clears == 0); // Error time must not manufacture a hold.
  for (uint32_t t = 1204; t <= 2200; t += 4) error.sample(S::Pressed, t);
  assert(error.clears == 1);
  error.release(2204);
  assert(error.opens == 0);

  Trace offline;
  offline.sample(S::Pressed, 0);
  for (uint32_t t = 4; t < 1500; t += 4) offline.sample(S::Pressed, t, 0, false);
  offline.release(1500);
  assert(offline.opens == 0 && offline.clears == 0);

  Trace disabled;
  for (uint32_t t = 0; t < 1500; t += 4) disabled.sample(S::Pressed, t, 0, true, false);
  disabled.release(1500);
  assert(disabled.clears == 0 && disabled.opens == 1);

  Trace rollover;
  uint32_t start = UINT32_MAX - 500;
  for (uint32_t t = 0; t <= 1200; t += 4) rollover.sample(S::Pressed, start + t);
  rollover.release(start + 1204);
  assert(rollover.clears == 1 && rollover.opens == 0);

  ClearAck ack;
  assert(parseClearAck("@D5 CLEARED 42 1 12 0", ack));
  assert(ack.revision == 42 && ack.target == 1 && ack.action == 12 && ack.status == 0);
  const char *bad[] = {"@D5 CLEARED 42 2 12 0", "@D5 CLEARED 42 1 12 3",
    "@D5 CLEARED 42 1 4294967296 0", "@D5 CLEARED 42 1 12 0 trailing", "@D5 CLEARED 42 1 12"};
  for (const auto *line : bad) { assert(!parseClearAck(line, ack)); assert(ack.action == 12); }
  puts("PASS: short-release opens, one-second holds, no duplicate/open after clear, drag/error/offline cancellation, rollover, guarded ACK parsing");
}
