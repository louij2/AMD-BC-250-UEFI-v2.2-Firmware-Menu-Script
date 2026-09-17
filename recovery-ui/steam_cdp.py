#!/usr/bin/env python3
"""Minimal Chrome DevTools Protocol client for Steam's UI. Standard library only.

Steam's web UI listens for DevTools on 127.0.0.1:8080 (loopback only, and it
must stay that way: it has no authentication). Run this on the BC-250 itself;
from elsewhere, SSH in over Tailscale first.

Usage:
  steam_cdp.py pages
  steam_cdp.py eval 'JS expression'          # runs in SharedJSContext
  steam_cdp.py ping                          # exit 0 if the UI answers
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request

HOST, PORT = "127.0.0.1", 8080
UI_PAGE = "SharedJSContext"


def pages(timeout=3):
    with urllib.request.urlopen(f"http://{HOST}:{PORT}/json", timeout=timeout) as resp:
        return json.load(resp)


def find_page(title=UI_PAGE, timeout=3):
    for page in pages(timeout):
        if page.get("title") == title:
            return page
    return None


class CDP:
    def __init__(self, ws_url, timeout=5):
        path = "/" + ws_url.split("://", 1)[1].split("/", 1)[1]
        self.sock = socket.create_connection((HOST, PORT), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: {HOST}:{PORT}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("DevTools closed the handshake")
            resp += chunk
        head, self.buf = resp.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.decode(errors="replace"))
        self.next_id = 0

    def _frame(self, opcode, data):
        hdr = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("DevTools connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv(self):
        message = b""
        while True:
            b1, b2 = self._read(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("DevTools closed the connection")
            if opcode == 0x9:
                self._frame(0xA, payload)
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if fin:
                    return message.decode()

    def call(self, method, params=None):
        self.next_id += 1
        mid = self.next_id
        self._frame(0x1, json.dumps({"id": mid, "method": method, "params": params or {}}).encode())
        while True:
            msg = json.loads(self._recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "awaitPromise": True, "returnByValue": True,
        })
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            raise RuntimeError(details.get("exception", {}).get("description") or details.get("text"))
        return result["result"].get("value")

    def close(self):
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()


def evaluate(expression, title=UI_PAGE, timeout=5):
    page = find_page(title, timeout)
    if page is None:
        raise LookupError(f"Steam UI page {title!r} not found")
    client = CDP(page["webSocketDebuggerUrl"], timeout)
    try:
        return client.evaluate(expression)
    finally:
        client.close()


def ping(timeout=5):
    """True if Steam's UI JavaScript context answers within the timeout."""
    try:
        start = time.monotonic()
        ok = evaluate("typeof SteamClient === 'object' ? 1 : 0", timeout=timeout) == 1
        return ok and time.monotonic() - start < timeout
    except Exception:
        return False


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == "pages":
        for page in pages():
            print(f"{page.get('title')!r:40} {page.get('url')}")
    elif cmd == "eval" and len(sys.argv) == 3:
        print(json.dumps(evaluate(sys.argv[2]), indent=2))
    elif cmd == "ping":
        ok = ping()
        print("responding" if ok else "NOT responding")
        sys.exit(0 if ok else 1)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
