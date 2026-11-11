import argparse
from collections import deque
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import pty
import select
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from snapshot import save_snapshot
from wifi_link import Lines, Session, WifiError, WifiServer, client_handshake, load_profile, proof
from wifi_setup import config_line, exchange, setup


def profile(port=45873):
    return {"version": 1, "id": "a" * 32, "key": "b" * 64,
            "host": "test-mac.local", "port": port, "enabled": True}


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


class Puck:
    def __init__(self, config):
        self.sock = socket.create_connection(("127.0.0.1", config["port"]), timeout=2)
        self.codec = client_handshake(self.sock, bytes.fromhex(config["key"]))
        self.lines, self.pending = Lines(), deque()
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.heartbeat)
        self.thread.start()

    def heartbeat(self):
        while not self.stopped.wait(2):
            try: self.send(b"ping")
            except (OSError, ValueError): return

    def send(self, payload):
        with self.lock:
            frame = self.codec.encode(payload)
            self.sock.sendall(frame)
            return frame

    def until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while self.pending:
                value = self.pending.popleft()
                if predicate(value): return value
            if select.select([self.sock], [], [], .1)[0]:
                data = self.sock.recv(4096)
                if not data: raise AssertionError("Wireless peer disconnected")
                self.pending.extend(self.codec.decode(line) for line in self.lines.feed(data))
        raise AssertionError("Expected wireless frame was not received")

    def state(self, count, *, after=None):
        line = self.until(lambda line: line.split()[:2] == [b"@D3", b"STATE"]
                          and line.split()[3:6] == [str(count).encode(), b"0", b"0"]
                          and line.split()[2] != after)
        return line.split()[2]

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=3)
        self.sock.close()


class WifiTest(unittest.TestCase):
    def test_setup_greeting_retries_after_a_usb_reset(self):
        from dotty_companion import connect
        master, slave = pty.openpty()
        fd = connect(os.ttyname(slave))
        errors = []
        request = "1" * 32
        def device():
            try:
                buffer = bytearray(); greetings = 0; deadline = time.monotonic() + 4
                while greetings < 2 and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]: buffer.extend(os.read(master, 4096))
                    while b"\n" in buffer:
                        _, _, tail = buffer.partition(b"\n"); buffer[:] = tail; greetings += 1
                self.assertEqual(greetings, 2)
                os.write(master, f"@P1 READY {request}\n".encode())
            except BaseException as error: errors.append(error)
        thread = threading.Thread(target=device); thread.start()
        try: exchange(fd, request, f"@P1 HELLO {request}\n".encode(), b"READY", 4)
        finally:
            thread.join(timeout=5); os.close(fd); os.close(master); os.close(slave)
        if errors: raise errors[0]

    def test_frame_integrity_order_direction_and_session_replay(self):
        key = proof(bytes.fromhex("b" * 64), "session", "1" * 32, "2" * 32)
        host, puck = Session(key, "S"), Session(key, "C")
        wire = host.encode(b"@D3 STATE 1 1 0 0 5 5").rstrip(b"\n")
        self.assertEqual(puck.decode(wire), b"@D3 STATE 1 1 0 0 5 5")
        with self.assertRaisesRegex(WifiError, "authentication"):
            Session(key, "C").decode(wire.replace(b"1 1 0", b"1 0 0"))
        for bad in (wire, wire.replace(b"1 1 0", b"1 0 0"), b"@D5 CLEAR 1 0 1", b"x" * 257):
            with self.assertRaises(WifiError): puck.decode(bad)
        with self.assertRaises(WifiError): host.decode(wire)  # Reflection is not a client action.
        fresh = Session(proof(bytes.fromhex("b" * 64), "session", "3" * 32, "2" * 32), "C")
        with self.assertRaises(WifiError): fresh.decode(wire)
        wrong = Session(key, "C")
        with self.assertRaises(WifiError): wrong.decode(host.encode(b"ping").rstrip(b"\n"))
        host.sent = 0xFFFFFFFF
        with self.assertRaises(WifiError): host.encode(b"ping")

    def test_fragmented_and_oversized_wire_frames(self):
        lines = Lines()
        self.assertEqual(lines.feed(b"first\nsec"), [b"first"])
        self.assertEqual(lines.feed(b"ond\n"), [b"second"])
        with self.assertRaises(WifiError): lines.feed(b"x" * 257)

    def test_pairing_profile_is_private_metadata_and_invalid_data_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pairing.json"
            self.assertIsNone(load_profile(path))
            save_snapshot(profile(), path)
            self.assertEqual(load_profile(path), profile())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for value in ([], {}, {**profile(), "enabled": 1}, {**profile(), "key": "PRIVATE"}, {**profile(), "port": 65536}):
                save_snapshot(value, path)
                with self.assertRaises(WifiError): load_profile(path)

    def test_usb_configuration_preserves_utf8_and_never_contains_plaintext_password(self):
        request, config = "1" * 32, profile()
        line = config_line(request, "Home café", "PRIVATE pass 123", config)
        fields = line.decode().strip().split(" ")
        self.assertEqual(bytes.fromhex(fields[3]).decode(), "Home café")
        self.assertEqual(bytes.fromhex(fields[4]).decode(), "PRIVATE pass 123")
        self.assertNotIn(b"PRIVATE", line)
        self.assertLess(len(line), 768)
        for name, password in (("x" * 33, "12345678"), ("", "12345678"), ("home", "short"), ("home", "a" * 65)):
            with self.assertRaises(WifiError): config_line(request, name, password, config)

    def test_usb_setup_success_and_failed_join_keep_profile_and_secrets_out_of_output(self):
        for success in (True, False):
            with self.subTest(success=success), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = {**profile(), "enabled": False}
                save_snapshot(config, root / "wifi-pairing.json")
                master, slave = pty.openpty()
                errors = []
                def device():
                    try:
                        buffer = bytearray()
                        for _ in range(2):
                            deadline = time.monotonic() + 4
                            while b"\n" not in buffer and time.monotonic() < deadline:
                                if select.select([master], [], [], .1)[0]: buffer.extend(os.read(master, 4096))
                            line, _, tail = buffer.partition(b"\n"); buffer[:] = tail
                            fields = line.split()
                            if fields[1] == b"HELLO": response = b"@P1 READY " + fields[2]
                            else:
                                self.assertEqual(fields[:2], [b"@P1", b"SET"])
                                response = b"@P1 SAVED " + fields[2] if success else b"@P1 FAIL " + fields[2] + b" network"
                            os.write(master, response + b"\n")
                    except BaseException as error: errors.append(error)
                thread = threading.Thread(target=device); thread.start()
                output = io.StringIO()
                try:
                    with patch("builtins.input", return_value="PRIVATE_NETWORK"), patch("getpass.getpass", return_value="PRIVATE_PASSWORD"), redirect_stdout(output):
                        args = argparse.Namespace(port=os.ttyname(slave), runtime=root)
                        if success: setup(args)
                        else:
                            with self.assertRaises(WifiError): setup(args)
                    self.assertEqual(load_profile(root / "wifi-pairing.json")["enabled"], success)
                    self.assertNotIn("PRIVATE", output.getvalue())
                    self.assertNotIn("PRIVATE", (root / "wifi-pairing.json").read_text())
                finally:
                    thread.join(timeout=5); os.close(master); os.close(slave)
                self.assertFalse(thread.is_alive())
                if errors: raise errors[0]

    def test_authenticated_bidirectional_link_usb_priority_and_old_session_rejection(self):
        stopped = threading.Event()
        config = profile(free_port())
        server = WifiServer(config, stopped, bind="127.0.0.1", discovery=False)
        server.start()
        puck, fd = None, None
        try:
            with socket.create_connection(("127.0.0.1", config["port"]), timeout=2) as wrong:
                with self.assertRaises((WifiError, OSError)): client_handshake(wrong, b"x" * 32)
            self.assertIsNone(server.take_link())
            puck = Puck(config)
            deadline = time.monotonic() + 3
            while fd is None and time.monotonic() < deadline:
                fd = server.take_link(); time.sleep(.01)
            self.assertIsNotNone(fd)
            os.write(fd, b"@D3 STATE 17 1 0 0 5 5\n")
            self.assertEqual(puck.state(1), b"17")
            old = puck.send(b"@D5 CLEAR 17 0 1")
            self.assertTrue(select.select([fd], [], [], 2)[0])
            self.assertEqual(os.read(fd, 1024), b"@D5 CLEAR 17 0 1\n")
            server.usb_active(True)
            self.assertTrue(select.select([fd], [], [], 2)[0])
            self.assertEqual(os.read(fd, 1024), b"")
            puck.close(); puck = None; os.close(fd); fd = None
            server.usb_active(False)
            deadline = time.monotonic() + 3
            while server.active is not None and time.monotonic() < deadline: time.sleep(.01)
            puck = Puck(config)
            while fd is None and time.monotonic() < deadline:
                fd = server.take_link(); time.sleep(.01)
            self.assertIsNotNone(fd)
            puck.sock.sendall(old)
            self.assertTrue(select.select([fd], [], [], 2)[0])
            self.assertEqual(os.read(fd, 1024), b"")
        finally:
            if puck: puck.close()
            if fd is not None: os.close(fd)
            stopped.set(); server.close()


if __name__ == "__main__": unittest.main()
