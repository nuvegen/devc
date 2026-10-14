"""T-WS: Handshake, Frame-Parsing (maskiert, fragmentiert, >125/>65535 Bytes), Resize, Origin-Ablehnung."""

import base64
import io
import json
import os
import socket
import struct
import unittest

from devc import core, terminal
from devc.terminal import OP_BINARY, OP_CLOSE, OP_CONT, OP_PING, OP_PONG, OP_TEXT, FrameReader, ProtocolError, encode_frame
from tests.test_server import INSTANCE, ServerTestCase

MASK = b"\x37\xfa\x21\x3d"


def reader(data: bytes, sent=None, require_mask=True):
    stream = io.BytesIO(data)
    return FrameReader(stream.read, sent.append if sent is not None else None, require_mask)


class FrameTest(unittest.TestCase):
    def test_accept_key_rfc_example(self):
        self.assertEqual(terminal.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_masked_text_rfc_example(self):
        frame = bytes.fromhex("818537fa213d7f9f4d5158")  # "Hello", maskiert (RFC 6455 5.7)
        self.assertEqual(reader(frame).read_message(), (OP_TEXT, b"Hello"))
        self.assertEqual(encode_frame(OP_TEXT, b"Hello", MASK), frame)
        self.assertEqual(encode_frame(OP_TEXT, b"Hello"), bytes.fromhex("810548656c6c6f"))

    def test_lengths(self):
        for length in (0, 125, 126, 65535, 65536, 200000):
            payload = bytes(index % 251 for index in range(length))
            frame = encode_frame(OP_BINARY, payload, MASK)
            header = 2 + (0 if length < 126 else 2 if length < 65536 else 8) + 4
            self.assertEqual(len(frame), header + length)
            self.assertEqual(reader(frame).read_message(), (OP_BINARY, payload), length)

    def test_fragmented_message_with_ping_in_between(self):
        sent = []
        data = (encode_frame(OP_TEXT, b"Hel", MASK, fin=False) + encode_frame(OP_PING, b"p", MASK)
                + encode_frame(OP_CONT, b"lo ", MASK, fin=False) + encode_frame(OP_CONT, b"Welt", MASK))
        self.assertEqual(reader(data, sent).read_message(), (OP_TEXT, b"Hello Welt"))
        self.assertEqual(sent, [encode_frame(OP_PONG, b"p")])

    def test_close_and_pong(self):
        data = encode_frame(OP_PONG, b"", MASK) + encode_frame(OP_CLOSE, struct.pack("!H", 1000), MASK)
        self.assertEqual(reader(data).read_message(), (OP_CLOSE, struct.pack("!H", 1000)))

    def test_protocol_violations(self):
        cases = {
            "unmaskiert": encode_frame(OP_TEXT, b"x"),
            "zu groß": encode_frame(OP_BINARY, b"", MASK)[:1] + bytes([0x80 | 127]) + struct.pack("!Q", terminal.MAX_FRAME + 1) + MASK,
            "reservierte Bits": bytes([0xC1, 0x80]) + MASK,
            "Fortsetzung ohne Anfang": encode_frame(OP_CONT, b"x", MASK),
            "neue Nachricht im Fragment": encode_frame(OP_TEXT, b"a", MASK, fin=False) + encode_frame(OP_TEXT, b"b", MASK),
            "fragmentierter Kontroll-Frame": encode_frame(OP_PING, b"x", MASK, fin=False),
            "langer Kontroll-Frame": encode_frame(OP_PING, b"x" * 126, MASK),
            "unbekannter Opcode": encode_frame(0x3, b"x", MASK),
        }
        for label, data in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ProtocolError):
                    reader(data).read_message()
        total = b"".join(encode_frame(OP_BINARY if i == 0 else OP_CONT, b"x" * 600000, MASK, fin=False) for i in range(2))
        with self.assertRaises(ProtocolError):
            reader(total).read_message()
        with self.assertRaises(ConnectionError):
            reader(encode_frame(OP_TEXT, b"Hello", MASK)[:-2]).read_message()

    def test_resize_message(self):
        self.assertEqual(terminal.parse_resize(b'{"type":"resize","cols":120,"rows":40}'), (40, 120))
        for bad in (b"kein json", b'{"type":"x","cols":1,"rows":1}', b'{"type":"resize","cols":"80","rows":24}',
                    b'{"type":"resize","cols":0,"rows":24}', b'{"type":"resize","cols":80,"rows":100000}', b"[1]", b'{"type":"resize","cols":true,"rows":1}'):
            self.assertIsNone(terminal.parse_resize(bad), bad)

    def test_set_size_on_pty(self):
        import fcntl
        import pty
        import termios

        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        terminal.set_size(master, 40, 120)
        rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(slave, termios.TIOCGWINSZ, b"\0" * 8))
        self.assertEqual((rows, cols), (40, 120))

    def test_origin_must_match_host(self):
        ok = [("http://localhost:8740", "localhost:8740", False), ("https://dev.example", "dev.example", True),
              ("http://127.0.0.1", "127.0.0.1:80", False), ("http://[::1]:8740", "[::1]:8740", False), ("HTTP://LocalHost:8740", "localhost:8740", False)]
        for origin, host, tls in ok:
            self.assertTrue(terminal.origin_matches(origin, host, tls), origin)
        bad = [("http://evil.example:8740", "localhost:8740", False), ("http://localhost:9999", "localhost:8740", False),
               ("https://localhost:8740", "localhost:8740", False), ("http://localhost:8740", "localhost:8740", True),
               (None, "localhost:8740", False), ("null", "localhost:8740", False), ("http://localhost:8740", None, False),
               ("http://localhost.evil.example:8740", "localhost:8740", False)]
        for origin, host, tls in bad:
            self.assertFalse(terminal.origin_matches(origin, host, tls), origin)


class TerminalSessionTest(ServerTestCase):
    ws_host = ""  # leer: 127.0.0.1:<port>
    ws_forward: list = []
    ws_origin = ""

    def setUp(self):
        super().setUp()
        self.make()

    def open_session(self, user="dev"):
        status, data = self.request("POST", "/api/containers/ab/terminals", {"user": user})
        self.assertEqual(status, 201)
        return data["id"]

    def frames(self, sock, stream):
        return FrameReader(stream.read, lambda frame: sock.sendall(encode_frame(frame[0] & 0x0F, frame[2:], MASK)), require_mask=False)

    def read_until(self, frames, needle, received=b""):
        while needle not in received:
            opcode, payload = frames.read_message()
            self.assertEqual(opcode, OP_BINARY)  # PTY-Ausgabe kommt als Binärframe
            received += payload
        return received

    def terminal_logs(self, count):
        import time
        for _ in range(100):
            jobs = self.request("GET", "/api/jobs?container=ab")[1]["jobs"]
            terminals = [self.request("GET", f"/api/jobs/{j['id']}")[1] for j in jobs if j["kind"] == "terminal"]
            if len(terminals) >= count:
                break
            time.sleep(0.05)
        return " ".join(t["log"] for t in terminals)

    def connect(self, origin=None, path=None, key=None, token=True):
        path = path or f"/api/containers/ab/terminal?session={self.open_session()}"
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        self.addCleanup(sock.close)
        key = key or base64.b64encode(os.urandom(16)).decode()
        lines = [f"GET {path} HTTP/1.1", f"Host: {self.ws_host or f'127.0.0.1:{self.port}'}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
                 f"Origin: {origin or self.ws_origin or f'http://127.0.0.1:{self.port}'}", *self.ws_forward]
        if token:
            lines.append(f"Authorization: Bearer {self.token}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        stream = sock.makefile("rb")
        status = stream.readline().decode().strip()
        headers = {}
        while True:
            line = stream.readline().decode().strip()
            if not line:
                break
            name, _, value = line.partition(":")
            headers[name.lower()] = value.strip()
        return sock, stream, status, headers, key

    def test_session_echo_and_resize(self):
        sid = self.open_session()
        sock, stream, status, headers, key = self.connect(path=f"/api/containers/ab/terminal?session={sid}")
        self.assertEqual(status, "HTTP/1.1 101 Switching Protocols")
        self.assertEqual((headers["upgrade"], headers["sec-websocket-accept"]), ("websocket", terminal.accept_key(key)))
        frames = self.frames(sock, stream)
        # zwei Frames in einem Paket: beide müssen ankommen
        sock.sendall(encode_frame(OP_TEXT, json.dumps({"type": "resize", "cols": 100, "rows": 30}).encode(), MASK)
                     + encode_frame(OP_BINARY, b"hallo terminal\n", MASK))
        # zweimal: Echo des PTY und Echo des Prozesses – erst dann hat er seinen SIGWINCH-Handler gesetzt
        received = self.read_until(frames, b"hallo terminal\r\nhallo terminal")
        self.assertEqual(self.provider.called("exec_pty")[0][1:], (("ab", ["bash", "-l"]), {"user": "dev", "cwd": "/work"}))
        # Größenänderungen müssen beim Prozess ankommen – beide Richtungen, auch mehrfach (SIGWINCH)
        for rows, cols in ((30, 100), (55, 180), (20, 180)):
            sock.sendall(encode_frame(OP_TEXT, json.dumps({"type": "resize", "cols": cols, "rows": rows}).encode(), MASK))
            received = self.read_until(frames, f"SIZE {rows}x{cols}".encode(), received)

    def test_session_survives_disconnect_and_replays_output(self):
        sid = self.open_session()
        path = f"/api/containers/ab/terminal?session={sid}"
        sock, stream, *_ = self.connect(path=path)
        frames = self.frames(sock, stream)
        sock.sendall(encode_frame(OP_BINARY, b"vor dem neuladen\n", MASK))
        self.read_until(frames, b"vor dem neuladen")
        sock.sendall(encode_frame(OP_CLOSE, struct.pack("!H", 1000), MASK))
        self.assertEqual(frames.read_message()[0], OP_CLOSE)
        sock.close()
        # Die Sitzung läuft ohne Browser weiter und ist nicht als beendet vermerkt
        sessions = self.request("GET", "/api/containers/ab/terminals")[1]["sessions"]
        self.assertEqual([(s["id"], s["user"], s["number"]) for s in sessions], [(sid, "dev", 1)])
        self.assertEqual(self.provider.called("pty_wait"), [])
        # Neu verbunden: bisherige Ausgabe kommt nach, Eingaben gehen an denselben Prozess
        sock, stream, status, *_ = self.connect(path=path)
        self.assertEqual(status.split()[1], "101")
        frames = self.frames(sock, stream)
        received = self.read_until(frames, b"vor dem neuladen")
        sock.sendall(encode_frame(OP_BINARY, b"danach\n", MASK))
        self.read_until(frames, b"danach", received)
        self.assertEqual(len(self.provider.called("exec_pty")), 1)

    def test_close_ends_session_and_logs_without_content(self):
        first, second = self.open_session(), self.open_session("root")
        sock, stream, *_ = self.connect(path=f"/api/containers/ab/terminal?session={first}")
        frames = self.frames(sock, stream)
        sock.sendall(encode_frame(OP_BINARY, b"hallo terminal\n", MASK))
        self.read_until(frames, b"hallo terminal")
        self.assertEqual(self.request("DELETE", f"/api/containers/ab/terminals/{first}")[0], 200)
        while True:
            opcode, payload = frames.read_message()
            if opcode == OP_CLOSE:
                break
        self.assertEqual(struct.unpack("!H", payload)[0], 1000)
        logs = self.terminal_logs(3)
        self.assertIn("Terminal-Sitzung als dev gestartet", logs)
        self.assertIn("Terminal-Sitzung als dev beendet", logs)
        self.assertNotIn("Terminal-Sitzung als root beendet", logs)
        self.assertNotIn("hallo terminal", logs)
        # die zweite Sitzung ist unberührt; die geschlossene gibt es nicht mehr
        sessions = self.request("GET", "/api/containers/ab/terminals")[1]["sessions"]
        self.assertEqual([(s["id"], s["user"], s["number"]) for s in sessions], [(second, "root", 2)])
        self.assertEqual(self.request("DELETE", f"/api/containers/ab/terminals/{first}")[0], 404)
        self.assertEqual(self.connect(path=f"/api/containers/ab/terminal?session={first}")[2].split()[1], "404")

    def test_session_limit(self):
        for _ in range(terminal.MAX_SESSIONS):
            self.open_session()
        status, error = self.request("POST", "/api/containers/ab/terminals", {"user": "dev"})
        self.assertEqual((status, error["error"]), (409, "E_CONFLICT"))

    def test_server_close_ends_sessions(self):
        self.open_session()
        proc = next(iter(self.provider.ptys.values()))
        self.server.app.terminals.close_all()
        self.assertIsNotNone(proc.returncode)
        self.assertEqual(self.request("GET", "/api/containers/ab/terminals")[1]["sessions"], [])
        self.assertIn("Terminal-Sitzung als dev beendet", self.terminal_logs(2))

    def test_origin_rejected(self):
        sock, stream, status, headers, key = self.connect(origin="http://evil.example")
        self.assertEqual(status.split()[1], "403")
        self.assertEqual(json.loads(stream.read(int(headers["content-length"])))["error"], "E_SECURITY")

    def test_needs_auth_and_valid_request(self):
        self.assertEqual(self.connect(token=False)[2].split()[1], "401")
        self.assertEqual(self.request("POST", "/api/containers/ab/terminals", {"user": "admin"})[0], 400)
        self.assertEqual(self.request("POST", "/api/containers/fehlt/terminals", {"user": "dev"})[0], 404)
        self.assertEqual(self.connect(path="/api/containers/ab/terminal")[2].split()[1], "404")  # ohne Sitzung
        sid = self.open_session()
        self.assertEqual(self.connect(path=f"/api/containers/zz/terminal?session={sid}")[2].split()[1], "404")  # fremder Container
        self.assertEqual(self.connect(path=f"/api/containers/ab/terminal?session={sid}", key="kurz")[2].split()[1], "400")
        self.assertEqual(self.request("GET", f"/api/containers/ab/terminal?session={sid}")[0], 403)  # ohne Origin
        core.stop(self.cfg, "ab")
        self.assertEqual(self.request("POST", "/api/containers/ab/terminals", {"user": "dev"})[0], 409)

    def test_root_session(self):
        sock, stream, status, headers, key = self.connect(path=f"/api/containers/ab/terminal?session={self.open_session('root')}")
        self.assertEqual(status.split()[1], "101")
        self.assertEqual(self.provider.called("exec_pty")[0][2]["user"], "root")
        sock.sendall(encode_frame(OP_CLOSE, b"", MASK))


class TerminalDisabledTest(ServerTestCase):
    options = {"bind": "0.0.0.0", "allow_insecure_bind": True}

    def test_insecure_bind_blocks_terminal(self):
        self.make()
        status, data = self.request("GET", "/api/status")
        self.assertEqual((data["insecure"], data["terminal"], data["terminal_allowed"]), (True, False, False))
        status, error = self.request("GET", "/api/containers/ab/terminal", headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual((status, error["error"]), (403, "E_SECURITY"))
        for method, path in (("GET", "terminals"), ("POST", "terminals"), ("DELETE", "terminals/abcd")):
            self.assertEqual(self.request(method, f"/api/containers/ab/{path}")[1]["error"], "E_SECURITY", path)
        self.assertEqual(self.provider.called("exec_pty"), [])


class GuiFilesTest(unittest.TestCase):
    """GUI-Regel 8: keine externen Ressourcen in der ausgelieferten GUI."""

    def test_no_external_resources(self):
        import re

        for name in ("index.html", "app.js", "app.css"):
            text = core.web_file(name).read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", text), [], name)
            self.assertNotIn("fonts.googleapis", text)
            self.assertNotIn("@import", text)
            self.assertNotRegex(text, r"url\(\s*['\"]?https?:")
        self.assertNotIn("innerHTML", core.web_file("app.js").read_text(encoding="utf-8"))
        self.assertNotIn("<script>", core.web_file("index.html").read_text(encoding="utf-8"))  # CSP: keine Inline-Skripte

    def test_vendor_files_and_license(self):
        for name in ("xterm.js", "xterm.css", "LICENSE-xterm.txt"):
            self.assertTrue(core.web_file("vendor", name).is_file(), name)
        self.assertIn("Permission is hereby granted, free of charge", core.web_file("vendor", "LICENSE-xterm.txt").read_text())
        self.assertNotIn("url(", core.web_file("vendor", "xterm.css").read_text())
        self.assertTrue(terminal.available())

    def test_every_route_of_the_spec_exists(self):
        script = core.web_file("app.js").read_text(encoding="utf-8")
        for fragment in ("/new$/", "/new\\/edit$/", "/templates$/", "/templates\\/([^/]+)$/", "/addons$/", "/secrets$/", "/c\\/([^/]+)$/",
                         "\\/addons$/", "\\/terminal$/", "\\/tile\\/([^/]+)$/", "\\/files$/", "\\/config$/", "\\/secrets$/", "\\/log$/", "\\/snapshots$/",
                         "\\/backups$/", "\\/backups\\/new$/", "\\/restore$/"):
            self.assertIn(fragment, script, fragment)


if __name__ == "__main__":
    unittest.main()
