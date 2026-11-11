"""Interactive USB-only Wi-Fi provisioning. Secrets are never command-line arguments."""
from __future__ import annotations

import getpass
import os
import re
import secrets
import select
import time
import warnings

from snapshot import save_snapshot
from wifi_link import WifiError, create_profile


def config_line(request, ssid, password, profile):
    network, secret, host = ssid.encode("utf-8"), password.encode("utf-8"), profile["host"].encode("ascii")
    if not 1 <= len(network) <= 32 or b"\0" in network:
        raise WifiError("Network name must contain 1–32 UTF-8 bytes")
    if b"\0" in secret or not (not secret or 8 <= len(secret) <= 63
                              or len(secret) == 64 and re.fullmatch(rb"[0-9a-fA-F]{64}", secret)):
        raise WifiError("Wi-Fi password must contain 8–63 bytes, or a 64-digit hexadecimal key")
    return (f"@P1 SET {request} {network.hex()} {secret.hex() or '-'} {host.hex()} "
            f"{profile['port']} {profile['id']} {profile['key']}\n").encode("ascii")


def exchange(fd, request, line, expected, timeout):
    deadline, pending, incoming = time.monotonic() + timeout, bytearray(line), bytearray()
    retry_at = time.monotonic() + 1
    while time.monotonic() < deadline:
        if expected == b"READY" and not pending and time.monotonic() >= retry_at:
            pending.extend(line)  # A board reset when USB opens can discard the first greeting.
            retry_at = time.monotonic() + 1
        readable, writable, _ = select.select([fd], [fd] if pending else [], [], .1)
        if writable:
            try:
                sent = os.write(fd, pending)
                del pending[:sent]
            except BlockingIOError:
                pass
        if readable:
            data = os.read(fd, 1024)
            if not data:
                raise WifiError("USB disconnected during Wi-Fi setup")
            incoming.extend(data)
            while b"\n" in incoming:
                row, _, tail = incoming.partition(b"\n")
                incoming[:] = tail
                parts = bytes(row).strip().split(b" ")
                if len(parts) >= 3 and parts[0] == b"@P1" and parts[2] == request.encode():
                    if parts[1] == expected:
                        return
                    if parts[1] == b"FAIL":
                        reason = parts[3] if len(parts) == 4 else b"setup"
                        reasons = {b"network": "Puck could not join Wi-Fi; previous configuration was kept",
                                   b"storage": "Puck could not save Wi-Fi configuration",
                                   b"busy": "Puck is already testing a Wi-Fi configuration",
                                   b"invalid": "Puck rejected the configuration format"}
                        raise WifiError(reasons.get(reason, "Puck rejected Wi-Fi setup"))
            if len(incoming) > 1024:
                incoming.clear()
    raise WifiError("Wi-Fi setup timed out; confirm the puck has the wireless firmware")


def setup(args):
    from dotty_companion import choose_port, connect
    import fcntl
    import termios
    fd = connect(choose_port(args.port))
    try:
        request = secrets.token_hex(16)
        exchange(fd, request, f"@P1 HELLO {request}\n".encode(), b"READY", 5)
        profile_path = args.runtime / "wifi-pairing.json"
        profile = create_profile(profile_path)
        print("Choose the home Wi-Fi network used by your Mac (2.4 GHz must be available).")
        ssid = input("Wi-Fi network name: ")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("Wi-Fi password (hidden; empty for an open network): ")
        except getpass.GetPassWarning as error:
            raise WifiError("Use an interactive terminal that supports hidden password entry") from error
        packet = config_line(request, ssid, password, profile)
        print("Testing Wi-Fi on the puck…", flush=True)
        try:
            exchange(fd, request, packet, b"SAVED", 35)
        finally:
            password, packet = None, None
        profile["enabled"] = True
        save_snapshot(profile, profile_path)
        print("Wi-Fi saved on puck. Run ./dotty, then unplug USB for wireless status.")
    finally:
        try:
            fcntl.ioctl(fd, termios.TIOCNXCL)
        except OSError:
            pass
        os.close(fd)
