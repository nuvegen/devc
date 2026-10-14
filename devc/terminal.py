"""Web-Terminal: WebSocket nach RFC 6455 und PTY (SPEC 8.7)."""

import base64
import errno
import fcntl
import hashlib
import json
import os
import secrets
import select
import signal
import struct
import termios
import threading
import time
from urllib.parse import urlsplit

from . import core, security
from . import vm as vm_module
from .errors import ConflictError, NotFoundError, SecurityError, UsageError

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 1024 * 1024
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
SCROLLBACK = 512 * 1024  # Ausgabe, die ein neu verbundener Browser nachgeliefert bekommt
MAX_SESSIONS = 8  # je Container


class ProtocolError(Exception):
    """Verstoß gegen RFC 6455 oder die Größengrenze."""


def available() -> bool:
    """xterm.js liegt bei; sonst zeigt die GUI nur den Befehl `devc shell <name>`."""
    return core.web_file("vendor", "xterm.js").is_file()


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


origin_matches = security.origin_matches


def encode_frame(opcode: int, payload: bytes = b"", mask: bytes | None = None, fin: bool = True) -> bytes:
    head = bytearray([(0x80 if fin else 0) | opcode])
    masked = 0x80 if mask else 0
    length = len(payload)
    if length < 126:
        head.append(masked | length)
    elif length < 65536:
        head.append(masked | 126)
        head += struct.pack("!H", length)
    else:
        head.append(masked | 127)
        head += struct.pack("!Q", length)
    if mask:
        head += mask
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return bytes(head) + payload


class FrameReader:
    """Liest Nachrichten vom Client: entmaskiert, setzt Fragmente zusammen, beantwortet Pings."""

    def __init__(self, read, send=None, require_mask: bool = True):
        self._read = read
        self._send = send
        self._require_mask = require_mask

    def _exact(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = self._read(count - len(data))
            if not chunk:
                raise ConnectionError("Verbindung beendet")
            data += chunk
        return data

    def read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._exact(2)
        if first & 0x70:
            raise ProtocolError("Reservierte Bits gesetzt")
        fin, opcode = bool(first & 0x80), first & 0x0F
        masked, length = bool(second & 0x80), second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._exact(8))[0]
        if length > MAX_FRAME:
            raise ProtocolError("Frame ist größer als 1 MiB")
        if self._require_mask and not masked:
            raise ProtocolError("Frames vom Client müssen maskiert sein")
        if opcode >= 0x8 and (not fin or length > 125):
            raise ProtocolError("Ungültiger Kontroll-Frame")
        mask = self._exact(4) if masked else None
        payload = self._exact(length)
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return fin, opcode, payload

    def read_message(self) -> tuple[int, bytes]:
        """Liefert (opcode, payload) der nächsten Text-, Binär- oder Close-Nachricht."""
        opcode, parts, size = None, [], 0
        while True:
            fin, code, payload = self.read_frame()
            if code == OP_PING:
                if self._send:
                    self._send(encode_frame(OP_PONG, payload))
                continue
            if code == OP_PONG:
                continue
            if code == OP_CLOSE:
                return OP_CLOSE, payload
            if code == OP_CONT:
                if opcode is None:
                    raise ProtocolError("Fortsetzung ohne Anfang")
            elif code in (OP_TEXT, OP_BINARY):
                if opcode is not None:
                    raise ProtocolError("Neue Nachricht mitten in einer fragmentierten")
                opcode = code
            else:
                raise ProtocolError(f"Unbekannter Opcode {code}")
            size += len(payload)
            if size > MAX_FRAME:
                raise ProtocolError("Nachricht ist größer als 1 MiB")
            parts.append(payload)
            if fin:
                return opcode, b"".join(parts)


def parse_resize(text: bytes) -> tuple[int, int] | None:
    """`{"type":"resize","cols":<int>,"rows":<int>}` → (rows, cols); None bei allem anderen."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") != "resize":
        return None
    cols, rows = data.get("cols"), data.get("rows")
    if not all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 1000 for v in (cols, rows)):
        return None
    return rows, cols


def set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def handshake(handler, tls: bool, host: str | None = None) -> None:
    """Prüft die Anfrage und sendet `101 Switching Protocols`. `host` ersetzt hinter einem Proxy den Host-Kopf."""
    headers = handler.headers
    if not origin_matches(headers.get("Origin"), host or headers.get("Host"), tls):
        raise SecurityError("Origin passt nicht zum Host")
    key = headers.get("Sec-WebSocket-Key", "")
    try:
        valid_key = len(base64.b64decode(key, validate=True)) == 16
    except ValueError:
        valid_key = False
    if (headers.get("Upgrade", "").lower() != "websocket" or "upgrade" not in headers.get("Connection", "").lower()
            or headers.get("Sec-WebSocket-Version") != "13" or not valid_key):
        raise UsageError("Kein gültiger WebSocket-Handshake")
    handler._responded = True
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept_key(key))
    handler.end_headers()
    handler.wfile.flush()
    handler.close_connection = True


class Client:
    """Ein verbundener Browser. Ausgabe kommt aus dem Lese-Thread der Sitzung, Pong und Close aus dem
    Thread der Verbindung; die Sperre hält die Frames auseinander."""

    def __init__(self, handler):
        self._wfile = handler.wfile
        self._lock = threading.Lock()

    def send(self, frame: bytes) -> None:
        with self._lock:
            self._wfile.write(frame)
            self._wfile.flush()


class Session:
    """Eine Shell an einem PTY. Sie lebt weiter, wenn kein Browser verbunden ist (Neuladen, Seitenwechsel),
    und endet mit der Shell, mit `close()` oder mit dem Server."""

    def __init__(self, sid: str, number: int, name: str, user: str, provider, pid: int, master: int, on_end):
        self.id, self.number, self.name, self.user = sid, number, name, user
        self.created = time.time()
        self._provider, self._pid, self._master, self._on_end = provider, pid, master, on_end
        self._lock = threading.Lock()  # Puffer, Clients, closed
        self._io = threading.Lock()  # schützt den Deskriptor vor Benutzung nach dem Schließen
        self._buffer = bytearray()
        self._clients: list[Client] = []
        self.closed = False
        self.done = threading.Event()  # gesetzt, wenn aufgeräumt und das Ende vermerkt ist

    def describe(self) -> dict:
        with self._lock:
            return {"id": self.id, "number": self.number, "user": self.user, "created": self.created, "attached": len(self._clients)}

    def start(self) -> None:
        threading.Thread(target=self._run, name=f"terminal-{self.id}", daemon=True).start()

    def _run(self) -> None:
        try:
            while True:
                ready, _, _ = select.select([self._master], [], [], 30)
                if not ready:
                    self._broadcast(encode_frame(OP_PING))
                    continue
                try:
                    data = os.read(self._master, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO:  # EIO: der Prozess am PTY ist beendet
                        raise
                    data = b""
                if not data:
                    return
                with self._lock:
                    self._buffer += data
                    del self._buffer[:-SCROLLBACK]
                    self._send_all(encode_frame(OP_BINARY, data))
        finally:
            self._finish()

    def _send_all(self, frame: bytes) -> None:
        for client in list(self._clients):
            try:
                client.send(frame)
            except (OSError, ValueError):
                self._clients.remove(client)

    def _broadcast(self, frame: bytes) -> None:
        with self._lock:
            self._send_all(frame)

    def _finish(self) -> None:
        with self._lock:
            self.closed = True
            self._send_all(encode_frame(OP_CLOSE, struct.pack("!H", 1000)))
            self._clients.clear()
        try:
            os.kill(self._pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        with self._io:
            os.close(self._master)
            self._master = -1
        try:
            self._provider.pty_wait(self._pid, timeout=5)
            self._on_end(self)
        finally:
            self.done.set()

    def attach(self, client: Client) -> bool:
        """Liefert dem Client die bisherige Ausgabe nach und hängt ihn an. False, wenn die Sitzung beendet ist."""
        with self._lock:
            if self.closed:
                return False
            if self._buffer:
                client.send(encode_frame(OP_BINARY, bytes(self._buffer)))
            self._clients.append(client)
            return True

    def detach(self, client: Client) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def write(self, data: bytes) -> None:
        with self._io:
            if self._master >= 0:
                try:
                    os.write(self._master, data)
                except OSError:
                    pass

    def resize(self, rows: int, cols: int) -> None:
        with self._io:
            if self._master < 0:
                return
            set_size(self._master, rows, cols)
        # Das PTY ist nicht das steuernde Terminal von `lxc exec`; der Kernel schickt ihm darum
        # kein SIGWINCH. Ohne dieses Signal liest lxc die neue Größe nie und reicht sie nicht
        # in den Container weiter (top, nano blieben auf der Startgröße).
        self._signal(signal.SIGWINCH)

    def _signal(self, number: int) -> None:
        if not self.closed:
            try:
                os.kill(self._pid, number)
            except ProcessLookupError:
                pass

    def close(self) -> None:
        """Beendet die Shell; aufgeräumt wird im Lese-Thread, sobald das PTY endet."""
        # `lxc exec` beendet sich auf SIGHUP nicht zuverlässig (gemessen: gar nicht); erst SIGKILL
        # schließt die Verbindung zu LXD, das daraufhin die Shell im Container beendet.
        self._signal(signal.SIGHUP)
        timer = threading.Timer(0.5, self._signal, [signal.SIGKILL])
        timer.daemon = True
        timer.start()


class Sessions:
    """Die offenen Terminal-Sitzungen des Servers. Sie liegen nur im Speicher und enden mit dem Server."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._numbers: dict[str, int] = {}

    def create(self, config, name: str, user: str) -> Session:
        with self._lock:
            if sum(1 for s in self._sessions.values() if s.name == name) >= MAX_SESSIONS:
                raise ConflictError(f"Höchstens {MAX_SESSIONS} Terminal-Sitzungen je Container; erst eine schließen")
            if name == vm_module.NAME:  # die beschränkte Shell der VM (SPEC 8.14)
                provider, pid, master = core.vm_terminal_open(config)
            else:
                provider, pid, master = core.terminal_open(config, name, user)
            number = self._numbers[name] = self._numbers.get(name, 0) + 1
            session = Session(secrets.token_hex(8), number, name, user, provider, pid, master, lambda s: self._ended(config, s))
            self._sessions[session.id] = session
        session.start()
        return session

    def _ended(self, config, session: Session) -> None:
        with self._lock:
            self._sessions.pop(session.id, None)
            if not any(s.name == session.name for s in self._sessions.values()):
                self._numbers.pop(session.name, None)
        if session.name == vm_module.NAME:
            core.vm_event(config, f"VM-Terminal als {session.user} beendet")
        else:
            core.terminal_event(config, session.name, f"Terminal-Sitzung als {session.user} beendet")

    def list(self, name: str) -> list[dict]:
        with self._lock:
            found = [s for s in self._sessions.values() if s.name == name]
        return [s.describe() for s in sorted(found, key=lambda s: s.number)]

    def get(self, name: str, sid: str) -> Session:
        with self._lock:
            session = self._sessions.get(sid)
        if session is None or session.name != name:
            raise NotFoundError(f"Terminal-Sitzung fehlt: {sid}")
        return session

    def close_all(self, timeout: float = 5) -> None:
        """Beendet alle Sitzungen und wartet, bis ihr Ende vermerkt ist."""
        with self._lock:
            found = list(self._sessions.values())
        for session in found:
            session.close()
        for session in found:
            session.done.wait(timeout)


def serve(handler, app, name: str, sid: str) -> None:
    """Verbindet den Browser mit einer bestehenden Sitzung. Endet die Verbindung, läuft die Sitzung weiter."""
    if not app.mode.terminal:
        raise SecurityError("Das Web-Terminal ist für diesen Bind nicht freigegeben")
    session = app.terminals.get(name, sid)
    handshake(handler, handler.tls, handler.public_host)
    client = Client(handler)
    reader = FrameReader(handler.rfile.read, client.send)
    code = 1000
    try:
        if session.attach(client):
            # Blockierend lesen: Der gepufferte Leser kann mehrere Frames auf einmal geholt haben,
            # ein select auf dem Socket würde die dann nicht mehr melden.
            while True:
                try:
                    opcode, payload = reader.read_message()
                except ProtocolError:
                    code = 1002
                    break
                except (ConnectionError, OSError, ValueError):
                    code = 1001
                    break
                if opcode == OP_CLOSE:
                    break
                if opcode == OP_BINARY:
                    session.write(payload)
                elif opcode == OP_TEXT:
                    size = parse_resize(payload)
                    if size is not None:
                        session.resize(*size)
    finally:
        session.detach(client)
        try:
            client.send(encode_frame(OP_CLOSE, struct.pack("!H", code)))
        except (OSError, ValueError):
            pass
