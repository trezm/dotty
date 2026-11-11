#pragma once
#include "TouchLatch.h"

enum class ProviderAction { None, Open, Clear };
struct ProviderGestureAction {
  ProviderAction type;
  int8_t target;
  ProviderGestureAction(ProviderAction action = ProviderAction::None, int8_t button = -1)
      : type(action), target(button) {}
};

class ProviderGesture {
 public:
  static constexpr uint32_t kHoldMs = 1000, kMaxSampleGapMs = 150;

  ProviderGestureAction update(TouchSample sample, TouchEvent event, int8_t hit,
                               uint32_t now, bool connected, bool canClear) {
    if (!connected) { cancel(); return {}; }
    if (event == TouchEvent::Down) {
      target_ = hit;
      cancelled_ = hit < 0;
      fired_ = false;
      clearAllowed_ = canClear;
      since_ = lastGood_ = now;
      return {};
    }
    if (event == TouchEvent::Up) {
      ProviderGestureAction action(!cancelled_ && !fired_ && target_ >= 0 ?
          ProviderAction::Open : ProviderAction::None, target_);
      cancel();
      return action;
    }
    if (sample != TouchSample::Pressed || target_ < 0 || cancelled_) return {};
    if (hit != target_) { cancelled_ = true; return {}; }
    // A stuck/erroring sensor is not proof of a continuous physical hold.
    if (uint32_t(now - lastGood_) > kMaxSampleGapMs) since_ = now;
    lastGood_ = now;
    if (!fired_ && clearAllowed_ && canClear && uint32_t(now - since_) >= kHoldMs) {
      fired_ = true;
      return {ProviderAction::Clear, target_};
    }
    return {};
  }

  int8_t pressed() const { return cancelled_ || fired_ ? -1 : target_; }
  void cancel() { target_ = -1; cancelled_ = true; fired_ = false; }

 private:
  int8_t target_ = -1;
  bool cancelled_ = true, fired_ = false, clearAllowed_ = false;
  uint32_t since_ = 0, lastGood_ = 0;
};
