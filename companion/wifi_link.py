"""Paired LAN transport for Dotty's bounded status protocol.

HMAC authenticates peers and every sequenced frame. Metadata is not encrypted;
Wi-Fi passwords travel only over USB and are never kept in the Mac profile.
"""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import queue
import re
import secrets
import select
import socket
import subprocess
import threading
import time

from snapshot import save_snapshot

DEFAULT_PORT = 45873
MAX_LINE = 256
MAX_PAYLOAD = 95
HEX32 = re.compile(r"[0-9a-f]{32}")
HEX64 = re.compile(r"[0-9a-f]{64}")
HOST = re.compile(r"(?=.{1,95}\Z)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")


class WifiError(ValueError):
    pass


def local_hostname():
    try:
        result = subprocess.run(["/usr/sbin/scutil", "--get", "LocalHostName"],
                                capture_output=True, text=True, timeout=2, check=True)
        name = result.stdout.strip() + ".local"
    except (OSError, subprocess.SubprocessError):
        name = socket.gethostname().split(".")[0] + ".local"
    if not HOST.fullmatch(name):
        raise WifiError("Cannot determine a usable Mac hostname")
    return name


def load_profile(path: Path):
    try:
        if path.stat().st_size > 4096:
            raise WifiError("Invalid Wi-Fi pairing profile")
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise WifiError("Wi-Fi pairing profile is unreadable") from error
    if (not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1
            or not isinstance(data.get("id"), str) or not HEX32.fullmatch(data["id"])
            or not isinstance(data.get("key"), str) or not HEX64.fullmatch(data["key"])
            or not isinstance(data.get("host"), str) or not HOST.fullmatch(data["host"])
            or type(data.get("port")) is not int or not 0 < data["port"] < 65536
            or type(data.get("enabled")) is not bool):
        raise WifiError("Invalid Wi-Fi pairing profile")
    return {key: data[key] for key in ("version", "id", "key", "host", "port", "enabled")}


def create_profile(path: Path, port=DEFAULT_PORT):
    profile = load_profile(path)
    if profile is None:
        profile = {"version": 1, "id": secrets.token_hex(16), "key": secrets.token_hex(32),
                   "host": local_hostname(), "port": port, "enabled": False}
        save_snapshot(profile, path)
    return profile


def proof(key: bytes, role: str, server: str, client: str) -> bytes:
    if not HEX32.fullmatch(server) or not HEX32.fullmatch(client):
        raise WifiError("Invalid Wi-Fi handshake")
    return hmac.digest(key, f"dotty-wifi-v1/{role}:{server}:{client}".encode("ascii"), "sha256")


class Session:
    def __init__(self, key: bytes, send_direction: str):
        self.key, self.direction = key, send_direction
        self.sent = self.received = 0

    def encode(self, payload: bytes) -> bytes:
        if not 0 < len(payload) <= MAX_PAYLOAD or any(b < 32 or b > 126 for b in payload):
            raise WifiError("Invalid Wi-Fi payload")
        if self.sent == 0xFFFFFFFF:
            raise WifiError("Wi-Fi sequence exhausted")
        self.sent += 1
        number = str(self.sent).encode("ascii")
        signature = hmac.digest(self.key, self.direction.encode() + b":" + number + b":" + payload, "sha256").hex().encode()
        return b"@W1 DATA " + number + b" " + signature + b" " + payload + b"\n"

    def decode(self, line: bytes) -> bytes:
        parts = line.split(b" ", 4)
        if (len(line) > MAX_LINE or len(parts) != 5 or parts[:2] != [b"@W1", b"DATA"]
                or not re.fullmatch(rb"[1-9][0-9]{0,9}", parts[2])
                or not re.fullmatch(rb"[0-9a-f]{64}", parts[3])):
            raise WifiError("Invalid Wi-Fi frame")
        number, payload = int(parts[2]), parts[4]
        if (number != self.received + 1 or number > 0xFFFFFFFF or not 0 < len(payload) <= MAX_PAYLOAD
                or any(b < 32 or b > 126 for b in payload)):
            raise WifiError("Invalid Wi-Fi sequence or payload")
        direction = b"C" if self.direction == "S" else b"S"
        expected = hmac.digest(self.key, direction + b":" + parts[2] + b":" + payload, "sha256").hex().encode()
        if not hmac.compare_digest(expected, parts[3]):
            raise WifiError("Wi-Fi authentication failed")
        self.received = number
        return payload


class Lines:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes):
        self.buffer.extend(data)
        lines = []
        while b"\n" in self.buffer:
            line, _, tail = self.buffer.partition(b"\n")
            self.buffer[:] = tail
            if len(line) > MAX_LINE:
                raise WifiError("Wi-Fi frame too large")
            lines.append(bytes(line))
        if len(self.buffer) > MAX_LINE:
            raise WifiError("Wi-Fi frame too large")
        return lines


def read_handshake(sock):
    # One byte at a time only during the short handshake, retaining no surplus
    # bytes that could accidentally bypass the authenticated data decoder.
    line = bytearray()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(line) <= MAX_LINE:
        value = sock.recv(1)
        if not value:
            raise WifiError("Wi-Fi handshake disconnected")
        if value == b"\n":
            return bytes(line)
        line.extend(value)
    raise WifiError("Wi-Fi handshake timed out")


def client_handshake(sock, key):
    """Reference peer used by integration tests; the puck implements the same exchange."""
    hello = read_handshake(sock).split(b" ")
    if len(hello) != 3 or hello[:2] != [b"@W1", b"HELLO"]:
        raise WifiError("Invalid Wi-Fi greeting")
    server, client = hello[2].decode("ascii"), secrets.token_hex(16)
    signature = proof(key, "client", server, client).hex().encode()
    sock.sendall(b"@W1 AUTH " + client.encode() + b" " + signature + b"\n")
    expected = b"@W1 OK " + proof(key, "server", server, client).hex().encode()
    if not hmac.compare_digest(read_handshake(sock), expected):
        raise WifiError("Wi-Fi server authentication failed")
    return Session(proof(key, "session", server, client), "C")


class WifiServer:
    def __init__(self, profile, stopped, *, bind="0.0.0.0", discovery=True):
        self.profile, self.stopped, self.bind, self.discovery = profile, stopped, bind, discovery
        self.links = queue.Queue(maxsize=1)
        self.lock = threading.Lock()
        self.usb = False
        self.active = None
        self.connections = set()
        self.workers = []
        self.slots = threading.BoundedSemaphore(2)
        self.listener = self.publisher = None
        self.thread = threading.Thread(target=self.run, name="dotty-wifi-listener")

    def start(self):
        self.listener = socket.socket()
        try:
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind((self.bind, self.profile["port"]))
            self.listener.listen(2)
            self.listener.settimeout(.25)
            if self.discovery:
                try:
                    self.publisher = subprocess.Popen(["/usr/bin/dns-sd", "-R", "Dotty-" + self.profile["id"][:8],
                        "_dotty._tcp", "local", str(self.profile["port"]), "id=" + self.profile["id"]],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError:
                    print("Wi-Fi: Bonjour unavailable; using provisioned hostname", flush=True)
            self.thread.start()
            print(f"Wi-Fi listener ready on port {self.profile['port']}", flush=True)
        except BaseException:
            self.listener.close()
            if self.publisher is not None:
                self.publisher.terminate()
                self.publisher.wait(timeout=2)
            raise

    def usb_active(self, active):
        with self.lock:
            self.usb = active
            if active and self.active is not None:
                try:
                    self.active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def take_link(self):
        while True:
            try:
                fd, connection = self.links.get_nowait()
            except queue.Empty:
                return None
            with self.lock:
                usable = not self.usb and self.active is connection and connection.fileno() >= 0
            if usable:
                return fd
            os.close(fd)

    def run(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self.slots.acquire(blocking=False):
                connection.close()
                continue
            with self.lock:
                self.connections.add(connection)
            worker = threading.Thread(target=self.serve, args=(connection,), name="dotty-wifi-peer")
            self.workers = [thread for thread in self.workers if thread.is_alive()]
            self.workers.append(worker)
            worker.start()

    def serve(self, connection):
        peer = None
        try:
            connection.settimeout(2)
            server = secrets.token_hex(16)
            connection.sendall(b"@W1 HELLO " + server.encode() + b"\n")
            auth = read_handshake(connection).split(b" ")
            if len(auth) != 4 or auth[:2] != [b"@W1", b"AUTH"]:
                return
            client = auth[2].decode("ascii")
            key = bytes.fromhex(self.profile["key"])
            if not hmac.compare_digest(auth[3], proof(key, "client", server, client).hex().encode()):
                return
            codec = Session(proof(key, "session", server, client), "S")
            with self.lock:
                if self.usb or self.active is not None:
                    return
                self.active = connection
            connection.sendall(b"@W1 OK " + proof(key, "server", server, client).hex().encode() + b"\n")
            bridge, peer = socket.socketpair()
            peer.settimeout(2)
            fd = bridge.detach()
            try:
                self.links.put_nowait((fd, connection))
            except queue.Full:
                os.close(fd)
                return
            wire, local = Lines(), Lines()
            last_received = time.monotonic()
            while not self.stopped.is_set() and time.monotonic() - last_received <= 10:
                readable, _, _ = select.select([connection, peer], [], [], .1)
                for stream in readable:
                    data = stream.recv(4096)
                    if not data:
                        return
                    if stream is connection:
                        for line in wire.feed(data):
                            payload = codec.decode(line)
                            last_received = time.monotonic()
                            if payload != b"ping":
                                peer.sendall(payload + b"\n")
                    else:
                        for payload in local.feed(data):
                            connection.sendall(codec.encode(payload))
        except (OSError, ValueError, UnicodeError):
            # Do not log peer input, pairing material, challenges, or signatures.
            pass
        finally:
            if peer is not None:
                peer.close()
            with self.lock:
                if self.active is connection:
                    self.active = None
                self.connections.discard(connection)
            connection.close()
            self.slots.release()

    def close(self):
        if self.listener is not None:
            self.listener.close()
        with self.lock:
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        if self.thread.ident:
            self.thread.join(timeout=3)
        for worker in self.workers:
            worker.join(timeout=3)
        while True:
            try:
                fd, _ = self.links.get_nowait()
            except queue.Empty:
                break
            os.close(fd)
        if self.publisher is not None:
            self.publisher.terminate()
            try:
                self.publisher.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.publisher.kill()
                self.publisher.wait(timeout=2)
