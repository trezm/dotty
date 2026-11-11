#include <cassert>
#include <cstring>
#include <string>
#include "../firmware/DottyHello/WifiProtocol.h"

int main() {
  using namespace DottyWireless;
  const std::string nonce(32, 'a'), id(32, 'b'), key(64, 'c'), tag(64, 'd');
  std::string command = "@P1 SET " + nonce + " 486f6d6520636166c3a9 736563726574313233 746573742e6c6f63616c 45873 " + id + " " + key;
  Config cfg; char request[33] = {};
  assert(provision(command.c_str(), request, cfg));
  assert(std::strcmp(cfg.ssid, "Home caf\xc3\xa9") == 0);
  assert(std::strcmp(cfg.password, "secret123") == 0);
  assert(cfg.port == 45873 && std::strcmp(cfg.host, "test.local") == 0);
  assert(!provision((command + " extra").c_str(), request, cfg));
  assert(!provision(("@P1 SET " + nonce + " 00 - 74657374 1 " + id + " " + key).c_str(), request, cfg));
  assert(!provision(std::string(768, 'x').c_str(), request, cfg));
  char payload[96], signature[65]; uint32_t sequence;
  const std::string wire = "@W1 DATA 4294967295 " + tag + " @D5 CLEAR 17 0 1";
  assert(frame(wire.c_str(), sequence, signature, payload));
  assert(sequence == UINT32_MAX && std::strcmp(payload, "@D5 CLEAR 17 0 1") == 0);
  for (const std::string &bad : {
      "@W1 DATA 0 " + tag + " ping", "@W1 DATA 01 " + tag + " ping",
      "@W1 DATA 4294967296 " + tag + " ping", std::string("@W1 DATA 1 short ping"),
      "@W1 DATA 1 " + tag + " ", "@W1 DATA 1 " + tag + " line\nbreak",
      "@W1 DATA 1 " + tag + " " + std::string(96, 'x')})
    assert(!frame(bad.c_str(), sequence, signature, payload));
}
