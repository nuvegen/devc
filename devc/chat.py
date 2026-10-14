"""Chat-Sitzungen für Kacheln der Art chat (SPEC 8.13).

Im Container läuft die Bridge des Addons. devc spricht mit ihr über stdin und stdout, eine JSON-Zeile
je Nachricht, und reicht die Ereignisse per WebSocket an die Browser weiter. devc kennt den Agenten
dahinter nicht; es zeichnet nur die Ereignisse aus dem Protokoll.
"""

import json
import os
import re
import secrets
import select
import struct
import threading
import time

from . import core
from .errors import ConflictError, NotFoundError, SecurityError, UsageError
from .terminal import OP_CLOSE, OP_PING, OP_TEXT, Client, FrameReader, ProtocolError, encode_frame, handshake

MAX_SESSIONS = 4  # je Container
MAX_LINE = 1024 * 1024  # längste Zeile der Bridge
MAX_EVENTS = 5000  # Ereignisse, die ein neu verbundener Browser nachgeliefert bekommt
MAX_BUFFER = 8 * 1024 * 1024
MAX_TEXT = 200 * 1024  # längste Nachricht des Nutzers
END_SECONDS = 30  # so lange darf die Bridge nach »Beenden« aufräumen, zum Beispiel einen Titel schreiben
EVENTS = ("ready", "user", "text_delta", "text", "tool_start", "tool_end", "permission", "permission_done",
          "status", "turn_end", "error")
STATES = ("idle", "working", "waiting", "retry", "ending")
BUSY = ("working", "waiting", "retry")
_SETTING_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_RESUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def parse_command(payload: bytes) -> dict | None:
    """Befehl vom Browser: `send`, `permit`, `set` oder `interrupt`; None bei allem anderen."""
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("type")
    if kind == "send":
        text = data.get("text")
        if isinstance(text, str) and text.strip() and len(text) <= MAX_TEXT:
            return {"type": "send", "text": text}
    elif kind == "permit":
        if isinstance(data.get("id"), str) and 0 < len(data["id"]) <= 200 and isinstance(data.get("allow"), bool):
            return {"type": "permit", "id": data["id"], "allow": data["allow"], "always": data.get("always") is True,
                    "folder": data.get("folder") is True}
    elif kind == "set":
        name, value = data.get("name"), data.get("value")
        if isinstance(name, str) and _SETTING_RE.fullmatch(name) and isinstance(value, str) and len(value) <= 200:
            return {"type": "set", "name": name, "value": value}
    elif kind == "interrupt":
        return {"type": "interrupt"}
    return None


def parse_event(line: bytes) -> dict | None:
    """Zeile der Bridge. Alles, was kein Ereignis des Protokolls ist, wird zu einer Fehlermeldung
    (so sieht man zum Beispiel »python3: command not found«)."""
    text = line.decode("utf-8", "replace").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("type") in EVENTS:
        return data
    return {"type": "error", "text": text[:2000]}


def check_resume(value) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _RESUME_RE.match(value):
        raise UsageError("resume ist keine gültige Kennung einer Unterhaltung")
    return value


class Chat:
    """Eine Unterhaltung: ein Prozess im Container. Sie läuft weiter, wenn kein Browser verbunden ist,
    und endet mit dem Prozess, mit `close()` oder mit dem Server."""

    def __init__(self, sid: str, name: str, tile: str, title: str, proc, on_end):
        self.id, self.name, self.tile, self.title = sid, name, tile, title
        self.created = time.time()
        self._proc, self._on_end = proc, on_end
        self._lock = threading.Lock()  # Puffer, Clients, Zustand
        self._write = threading.Lock()
        self._events: list[bytes] = []
        self._size = 0
        self._partial = ""  # Text der laufenden Antwort, noch ohne abschließendes `text`
        self._clients: list[Client] = []
        self.state = "starting"
        self.session = None  # Kennung der Unterhaltung beim Agenten, für `resume`
        self._busy = None  # seit wann der Agent arbeitet; für die Uhr im Browser nach einem Seitenwechsel
        self.closed = False
        self.done = threading.Event()

    def describe(self) -> dict:
        with self._lock:
            return {"id": self.id, "tile": self.tile, "title": self.title, "created": self.created,
                    "attached": len(self._clients), "state": self.state, "session": self.session,
                    "busy_for": None if self._busy is None else round(time.time() - self._busy, 1)}

    def start(self, first: dict) -> None:
        self.command(first)
        threading.Thread(target=self._run, name=f"chat-{self.id}", daemon=True).start()

    def _run(self) -> None:
        fd = self._proc.stdout.fileno()
        pending = b""
        try:
            while True:
                ready, _, _ = select.select([fd], [], [], 30)
                if not ready:
                    self._broadcast(encode_frame(OP_PING))
                    continue
                data = os.read(fd, 65536)
                if not data:
                    break
                pending += data
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    self._event(parse_event(line[:MAX_LINE]))
                if len(pending) > MAX_LINE:
                    self._event({"type": "error", "text": "Die Bridge hat eine zu lange Zeile geschickt"})
                    break
            self._event(parse_event(pending))
        finally:
            self._finish()

    def _event(self, event: dict | None) -> None:
        if event is None:
            return
        frame = encode_frame(OP_TEXT, json.dumps(event, ensure_ascii=False).encode("utf-8"))
        with self._lock:
            kind = event["type"]
            if kind == "text_delta":
                if isinstance(event.get("text"), str):
                    self._partial += event["text"]
            else:
                if kind in ("text", "turn_end", "tool_start", "exit"):
                    self._partial = ""
                if kind == "ready" and isinstance(event.get("session"), str):
                    self.session = event["session"]
                if kind == "status" and event.get("state") in STATES:
                    self.state = event["state"]
                    if self.state not in BUSY:
                        self._busy = None
                    elif self._busy is None:
                        self._busy = time.time()
                self._events.append(frame)
                self._size += len(frame)
                while len(self._events) > MAX_EVENTS or self._size > MAX_BUFFER:
                    self._size -= len(self._events.pop(0))
            self._send_all(frame)

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
        try:
            code = self._proc.wait(5)
        except Exception:  # noqa: BLE001 - der Prozess hängt; das Ende wird trotzdem vermerkt
            self._proc.kill()
            code = None
        frame = encode_frame(OP_TEXT, json.dumps({"type": "exit", "code": code}).encode("utf-8"))
        with self._lock:
            self.closed = True
            self.state = "ended"
            self._send_all(frame)
            self._send_all(encode_frame(OP_CLOSE, struct.pack("!H", 1000)))
            self._clients.clear()
        for stream in (self._proc.stdin, self._proc.stdout):
            try:
                stream.close()
            except OSError:
                pass
        try:
            self._on_end(self)
        finally:
            self.done.set()

    def attach(self, client: Client) -> bool:
        """Liefert dem Client den bisherigen Verlauf nach und hängt ihn an. False, wenn die Unterhaltung beendet ist."""
        with self._lock:
            if self.closed:
                return False
            for frame in self._events:
                client.send(frame)
            if self._partial:
                client.send(encode_frame(OP_TEXT, json.dumps({"type": "text_delta", "text": self._partial}, ensure_ascii=False).encode("utf-8")))
            self._clients.append(client)
            return True

    def detach(self, client: Client) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def command(self, command: dict) -> None:
        """Schickt der Bridge eine Zeile."""
        with self._write:
            try:
                self._proc.stdin.write(json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n")
                self._proc.stdin.flush()
            except (OSError, ValueError):
                pass  # der Prozess ist beendet; das Ende meldet der Lese-Thread

    def end(self) -> None:
        """Der Nutzer beendet die Unterhaltung: Die Bridge erfährt es mit `end` und darf noch aufräumen."""
        self.command({"type": "end"})
        self.close(END_SECONDS)

    def close(self, grace: float = 2) -> None:
        """Beendet die Unterhaltung: Die Bridge sieht das Ende ihrer Eingabe und hört auf; sonst wird sie beendet."""
        with self._write:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
        for delay, stop in ((grace, self._proc.terminate), (grace + 2, self._proc.kill)):
            timer = threading.Timer(delay, self._stop, [stop])
            timer.daemon = True
            timer.start()

    def _stop(self, stop) -> None:
        if not self.done.is_set():
            try:
                stop()
            except ProcessLookupError:
                pass


class Chats:
    """Die laufenden Unterhaltungen des Servers. Sie liegen nur im Speicher und enden mit dem Server."""

    def __init__(self):
        self._lock = threading.Lock()
        self._chats: dict[str, Chat] = {}

    def create(self, config, name: str, tile: str, resume=None) -> Chat:
        resume = check_resume(resume)
        with self._lock:
            mine = [c for c in self._chats.values() if c.name == name]
            if any(c.tile == tile for c in mine):
                raise ConflictError("Für diese Kachel läuft schon eine Unterhaltung; erst beenden")
            if len(mine) >= MAX_SESSIONS:
                raise ConflictError(f"Höchstens {MAX_SESSIONS} Unterhaltungen je Container; erst eine beenden")
            proc, title = core.chat_open(config, name, tile)
            chat = Chat(secrets.token_hex(8), name, tile, title, proc, lambda c: self._ended(config, c))
            self._chats[chat.id] = chat
        chat.start({"type": "start", "resume": resume})
        return chat

    def _ended(self, config, chat: Chat) -> None:
        with self._lock:
            self._chats.pop(chat.id, None)
        core.chat_event(config, chat.name, f"Unterhaltung {chat.tile} beendet")

    def list(self, name: str) -> list[dict]:
        with self._lock:
            found = [c for c in self._chats.values() if c.name == name]
        return [c.describe() for c in sorted(found, key=lambda c: c.created)]

    def get(self, name: str, sid: str) -> Chat:
        with self._lock:
            chat = self._chats.get(sid)
        if chat is None or chat.name != name:
            raise NotFoundError(f"Unterhaltung fehlt: {sid}")
        return chat

    def close_all(self, timeout: float = 5) -> None:
        with self._lock:
            found = list(self._chats.values())
        for chat in found:
            chat.close()
        for chat in found:
            chat.done.wait(timeout)


def stats(config, name: str, tile: str, session) -> dict:
    """Zahlen zu einer Unterhaltung (Zeit, Tokens, Werkzeuge) als Markdown der Bridge."""
    session = check_resume(session)
    if session is None:
        raise UsageError("session fehlt")
    return {"name": name, "tile": tile, "session": session, "text": core.chat_stats(config, name, tile, session)}


def transcript(config, name: str, tile: str, session) -> dict:
    """Verlauf einer früheren Unterhaltung zum Ansehen, ohne sie zu starten."""
    session = check_resume(session)
    if session is None:
        raise UsageError("session fehlt")
    events, size = [], 0
    for line in core.chat_show(config, name, tile, session).encode("utf-8").split(b"\n"):
        event = parse_event(line[:MAX_LINE])
        if event is None or event["type"] in ("text_delta", "status", "permission", "permission_done"):
            continue
        size += len(line)
        if len(events) >= MAX_EVENTS or size > MAX_BUFFER:
            break
        events.append(event)
    return {"name": name, "tile": tile, "session": session, "events": events}


def serve(handler, app, name: str, sid: str) -> None:
    """Verbindet den Browser mit einer laufenden Unterhaltung. Endet die Verbindung, läuft sie weiter."""
    if not app.mode.terminal:
        raise SecurityError("Der Chat ist für diesen Bind nicht freigegeben")
    chat = app.chats.get(name, sid)
    handshake(handler, handler.tls, handler.public_host)
    client = Client(handler)
    reader = FrameReader(handler.rfile.read, client.send)
    code = 1000
    try:
        if chat.attach(client):
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
                if opcode == OP_TEXT:
                    command = parse_command(payload)
                    if command is not None:
                        chat.command(command)
    finally:
        chat.detach(client)
        if not chat.closed:  # sonst hat die Unterhaltung den Close-Frame schon geschickt
            try:
                client.send(encode_frame(OP_CLOSE, struct.pack("!H", code)))
            except (OSError, ValueError):
                pass
