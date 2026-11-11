#pragma once

namespace DottyWifi {
void begin();
bool configured();
bool connected();
bool networkReady();
unsigned long epoch();
void noteUsb();
bool usbPreferred();
bool handleUsb(const char *line);
bool takeUsbReply(char line[96]);
bool takeLine(char line[96], unsigned long &epoch);
bool send(const char *line);
}
