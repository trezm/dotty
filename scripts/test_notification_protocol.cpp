#include <assert.h>
#include <stdio.h>
#include <initializer_list>
#include "../firmware/DottyHello/NotificationProtocol.h"
#include "../firmware/DottyHello/UsageProtocol.h"
#include "../firmware/DottyHello/RingDashboard.h"

int main() {
  NotificationState state;
  assert(parseNotificationState("@D1 STATE 4294967295 7 2 3", state));
  assert(state.revision == UINT32_MAX && state.codex == 7 && state.chatgpt == 2 && state.available == 3);
  const char *bad[] = {"@D1 STATE 1 0 0", "@D1 STATE 1 -1 0 3", "@D1 STATE 4294967296 0 0 3",
    "@D1 STATE 1 1000 0 3", "@D1 STATE 1 0 0 4", "@D1 STATE 1 0 0 3 trailing",
    "@D1 STATE 1 0 0 ", "r", "", "@D1 STATE 99999999999999999999999999999999 0 0 3"};
  for (const char *line : bad) {
    assert(!parseNotificationState(line, state));
    assert(state.revision == UINT32_MAX && state.codex == 7);  // Failed packets preserve good state.
  }
  assert(parseNotificationState("@D1 STATE 0 0 0 0", state));
  assert(state.available == 0 && state.codex == 0 && state.chatgpt == 0);
  assert(parseNotificationState("@D2 STATE 42 6 0 1 7", state));
  assert(state.revision == 42 && state.claude == 1 && state.available == 7);
  assert(!parseNotificationState("@D2 STATE 43 6 0 2 7", state));
  assert(!parseNotificationState("@D2 STATE 43 6 0 1 8", state));
  assert(state.revision == 42 && state.claude == 1);
  assert(parseNotificationState("@D1 STATE 44 6 0 3", state));
  assert(state.claude == 0 && state.available == 3);  // Legacy hosts remain supported.
  assert(state.monitored == 7 && !state.localFeeds);
  assert(parseNotificationState("@D3 STATE 45 0 0 0 5 5", state));
  assert(state.localFeeds && state.monitored == 5 && state.available == 5);
  assert(parseNotificationState("@D3 STATE 46 0 0 1 4 5", state));
  assert(state.claude == 1 && state.available == 4 && state.monitored == 5);
  assert(!parseNotificationState("@D3 STATE 47 0 0 0 7 5", state));
  assert(!parseNotificationState("@D3 STATE 47 0 0 0 5", state));
  assert(!parseNotificationState("@D3 STATE 47 0 0 0 5 8", state));
  assert(state.revision == 46 && state.monitored == 5 && state.localFeeds);
  assert(parseNotificationState("@D2 STATE 48 0 0 0 7", state));
  assert(!state.localFeeds && state.monitored == 7);
  puts("PASS: notification state parsing, limits, malformed messages, unchanged state on failure");
  UsagePacket usage;
  assert(parseUsage("@D4 USAGE 48 0 8 10080 9999 255 0 0", usage));
  assert(usage.revision == 48 && usage.windows[0].used == 8 && usage.windows[1].used == 255);
  assert(usageConsumed(usage.windows[0]) == 8);
  UsageWindow ordered[2];
  orderUsage(usage, true, ordered);
  assert(usageConsumed(ordered[0]) == -1 && ordered[1].used == 8 && ordered[1].minutes == 10080);
  for (const char *line : {"@D4 USAGE 49 3 8 300 40 255 0 0",
                          "@D4 USAGE 49 0 101 300 40 255 0 0",
                          "@D4 USAGE 49 0 255 300 40 255 0 0",
                          "@D4 USAGE 49 0 0 0 40 255 0 0",
                          "@D4 USAGE 49 0 0 300 40 255 0 0 trailing"}) {
    assert(!parseUsage(line, usage));
    assert(usage.revision == 48 && usage.windows[0].used == 8);
  }
  assert(parseUsage("@D4 USAGE 50 2 0 300 100 100 10080 9999", usage));
  assert(usage.windows[0].used == 0 && usage.windows[1].used == 100);
  assert(usageConsumed(usage.windows[0]) == 0 && usageConsumed(usage.windows[1]) == 100);
  orderUsage(usage, true, ordered);
  assert(ordered[0].used == 0 && ordered[1].used == 100);
  orderUsage(usage, false, ordered);
  assert(usageConsumed(ordered[0]) == -1 && usageConsumed(ordered[1]) == -1);
  assert(parseUsage("@D4 USAGE 51 2 0 300 0 13 10080 500", usage));
  orderUsage(usage, true, ordered);
  assert(usageConsumed(ordered[0]) == 0 && ordered[0].minutes == 300 && ordered[0].resetMinutes == 0);
  assert(usageConsumed(ordered[1]) == 13);
  assert(parseUsage("@D4 USAGE 52 2 8 300 0 255 0 0", usage));
  assert(usageConsumed(usage.windows[0]) == 8 && usage.windows[0].resetMinutes == 0);
  orderUsage(usage, false, ordered);
  assert(usageConsumed(ordered[0]) == -1);  // Unknown reset never bypasses heartbeat freshness.
  UsageWindow unknown;
  assert(usageConsumed(unknown) == -1);
  char duration[10];
  usageDuration(10080, duration, sizeof(duration)); assert(strcmp(duration, "7d") == 0);
  usageDuration(300, duration, sizeof(duration)); assert(strcmp(duration, "5h") == 0);
  puts("PASS: usage windows, unknown vs zero, bounds, malformed packet preservation");
  ClaudeCountPacket count;
  assert(parseClaudeCount("@D4 COUNT 50 2 12", count));
  assert(count.revision == 50 && count.count == 12);
  assert(!parseClaudeCount("@D4 COUNT 51 0 12", count));
  assert(!parseClaudeCount("@D4 COUNT 51 2 1000", count));
  assert(!parseClaudeCount("@D4 COUNT 51 2 1 trailing", count));
  assert(count.revision == 50 && count.count == 12);
  assert(RingDashboard::hitTest(67, 111) == 0);
  assert(RingDashboard::hitTest(173, 111) == 1);
  assert(RingDashboard::hitTest(120, 111) == -1);
  assert(RingDashboard::hitTest(67, 62) == -1);  // Usage arcs are not app buttons.
  assert(RingDashboard::hitTest(67, 78) == 0);
  assert(RingDashboard::pulseColor(0, false, 0x4ef5) == RingDashboard::background);
  assert(RingDashboard::pulseColor(0, true, 0x4ef5) != RingDashboard::pulseColor(1100, true, 0x4ef5));
  puts("PASS: provider counts, eye touch targets, percent-used slot ordering, border pulse");
}
