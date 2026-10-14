#!/usr/bin/env python3
"""Bridge zwischen dem Chat von devc und Codex CLI.

devc startet dieses Skript im Container und spricht mit ihm über stdin und stdout, eine JSON-Zeile je
Nachricht (Protokoll: Handbuch von devc, Abschnitt »Eigene Kacheln«). Das Skript startet `codex app-server`
(JSON-RPC über stdin und stdout) und übersetzt in beide Richtungen. Nur Standardbibliothek.

Aufruf: bridge.py [--model <name>]      Unterhaltung führen
        bridge.py --sessions            frühere Unterhaltungen als JSON nennen
        bridge.py --show <kennung>      Verlauf einer früheren Unterhaltung ausgeben
        bridge.py --stats <kennung>     Statistik einer Unterhaltung als JSON ({"stats": Markdown})
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "devc-chat" / "codex"
MAX_OUTPUT = 20000  # Zeichen je Tool-Eingabe und Tool-Ergebnis
STORED = ("user", "text", "tool_start", "tool_end", "turn_end", "error")  # stehen im Verlauf
SESSION_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
NO_KEY = ("Der Dienst hat die Anmeldung abgelehnt. Unter »Secrets« den API-Key setzen (codex.api_key) und prüfen, "
          "ob Adresse und Modell in den Einstellungen des Addons stimmen.")
TITLE_PROMPT = ("Fasse die folgende Unterhaltung in höchstens 10 Wörtern als Titel zusammen. Antworte nur mit dem Titel, "
                "in der Sprache der Unterhaltung, ohne Anführungszeichen und ohne Satzzeichen am Ende. Benutze kein Werkzeug.")
TITLE_SECONDS = 25
# Was Codex gerade tut, für die Zeile »arbeitet …« im Chat
THINKING, ANSWERING, REQUESTING, COMPACTING, EVALUATING = ("denkt nach", "schreibt", "wartet auf das Modell", "fasst den bisherigen Verlauf zusammen",
                                                         "wertet das Ergebnis aus")
# Wie viel Codex ohne Rückfrage darf. Der Nutzer wählt es im Chat unter »Einstellungen«; die Wahl gilt für den Container.
PERMISSIONS = (
    ("ask", "Nachfragen", "Codex liest ohne Rückfrage. Vor Befehlen, die etwas ändern, und vor Änderungen an Dateien fragt es."),
    ("all", "Alles erlauben", "Codex fragt nicht mehr. Es darf im Container alles, was der Nutzer dev darf: Dateien ändern, Befehle ausführen, das Netz nutzen."),
)
SETTINGS_FILE = STATE / "settings.json"
NO_QUESTIONS = "Rückfragen als Auswahl gibt es hier nicht. Stelle sie als normalen Text; der Nutzer antwortet im Chat."
QUIET_ITEMS = ("userMessage", "agentMessage", "reasoning", "plan", "hookPrompt", "functionCallOutput", "enteredReviewMode", "exitedReviewMode", "subAgentActivity")
SHELL_RE = re.compile(r"^(?:/usr)?/bin/(?:ba|z)?sh -l?c (.+)$", re.S)

_out = threading.Lock()


class Recorder:
    """Schreibt den Verlauf mit, damit eine fortgesetzte Unterhaltung ihre alten Nachrichten zeigt."""

    def __init__(self):
        self.session = None
        self._file = None
        self._waiting = []  # Ereignisse, bevor Codex die Kennung der Unterhaltung genannt hat

    def path(self, session: str) -> Path:
        return STATE / f"{session}.jsonl"

    def title_path(self, session: str) -> Path:
        return STATE / f"{session}.title"

    def stats_path(self, session: str) -> Path:
        return STATE / f"{session}.stats.json"

    def open(self, session: str) -> None:
        if session == self.session or not SESSION_RE.match(session):
            return
        STATE.mkdir(parents=True, exist_ok=True)
        self.session = session
        self._file = open(self.path(session), "a", encoding="utf-8")
        for event in self._waiting:
            self._write(event)
        self._waiting = []

    def _write(self, event: dict) -> None:
        self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._file.flush()

    def add(self, event: dict) -> None:
        if self._file:
            self._write(event)
        else:
            self._waiting.append(event)


recorder = Recorder()


def emit(kind: str, **fields) -> None:
    event = {"type": kind, **fields}
    with _out:
        if kind in STORED:
            recorder.add(event)
        sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def cut(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + f"\n… gekürzt ({len(text)} Zeichen)"


def pretty(data) -> str:
    return cut(json.dumps(data, ensure_ascii=False, indent=2))


def command_line(item: dict) -> str:
    """Der Befehl ohne die Hülle `/bin/bash -lc '…'`, in einer Zeile."""
    actions = item.get("commandActions")
    if isinstance(actions, list) and len(actions) == 1 and isinstance(actions[0], dict) and isinstance(actions[0].get("command"), str):
        text = actions[0]["command"]
    else:
        text = str(item.get("command") or "")
        found = SHELL_RE.match(text)
        if found:
            text = found[1].strip()
            if len(text) > 1 and text[0] == text[-1] and text[0] in "'\"":
                text = text[1:-1]
    return " ".join(text.split())[:300]


def tool_of(item: dict) -> tuple[str, str, object]:
    """Name, Zusammenfassung und Eingabe eines Werkzeugs aus einem Eintrag von Codex."""
    kind = item.get("type")
    if kind == "commandExecution":
        return "Shell", command_line(item), {"command": item.get("command"), "cwd": item.get("cwd")}
    if kind == "fileChange":
        changes = [change for change in item.get("changes") or [] if isinstance(change, dict)]
        paths = [str(change.get("path") or "") for change in changes]
        return "Patch", ", ".join(paths)[:300], {"changes": changes}
    if kind in ("mcpToolCall", "dynamicToolCall"):
        return ".".join(str(part) for part in (item.get("server") or item.get("namespace"), item.get("tool")) if part), "", item.get("arguments")
    if kind == "webSearch":
        return "WebSearch", str(item.get("query") or "")[:300], {"query": item.get("query")}
    if kind == "collabAgentToolCall":
        return "Agent", " ".join(str(item.get("prompt") or "").split())[:300], {"prompt": item.get("prompt"), "tool": item.get("tool")}
    if kind == "imageView":
        return "Bild", str(item.get("path") or "")[:300], {"path": item.get("path")}
    return str(kind or "Werkzeug"), "", {key: value for key, value in item.items() if key not in ("type", "id")}


def load_permissions() -> str:
    try:
        value = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("permissions")
    except (OSError, ValueError, AttributeError):
        value = None
    return value if value in [name for name, _, _ in PERMISSIONS] else "ask"


def save_permissions(value: str) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps({"permissions": value}) + "\n", encoding="utf-8")
    except OSError:
        pass  # dann gilt die Wahl nur für diese Unterhaltung


def settings(permissions: str) -> list:
    """Die Einstellungen für das Fenster »Einstellungen« im Chat (Protokoll: `ready.settings`)."""
    return [{"name": "permissions", "label": "Freigaben", "value": permissions,
             "hint": "Gilt sofort und für alle Unterhaltungen in diesem Container.",
             "options": [{"value": name, "label": label, "hint": hint} for name, label, hint in PERMISSIONS]}]


def clean_title(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return " ".join((lines[0] if lines else "").strip("#*_`\"'„“”»« ").split())[:80]


def sessions() -> None:
    found = []
    files = sorted(STATE.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True) if STATE.is_dir() else []
    for file in files[:50]:
        title = ""
        try:
            # Der Titel stammt aus der Zusammenfassung beim Beenden; ohne sie ist es der Anfang der ersten Nachricht.
            if recorder.title_path(file.stem).is_file():
                title = clean_title(recorder.title_path(file.stem).read_text(encoding="utf-8"))
            with open(file, encoding="utf-8") as handle:
                for line in handle if not title else ():
                    event = json.loads(line)
                    if event.get("type") == "user":
                        title = " ".join(str(event.get("text", "")).split())[:80]
                        break
        except (OSError, ValueError):
            continue
        found.append({"id": file.stem, "title": title, "updated": file.stat().st_mtime})
    print(json.dumps({"sessions": found}, ensure_ascii=False))


def replay(session: str) -> None:
    try:
        with open(recorder.path(session), encoding="utf-8") as handle, _out:
            for line in handle:
                if line.strip():
                    sys.stdout.write(line if line.endswith("\n") else line + "\n")
            sys.stdout.flush()
    except OSError:
        pass


def excerpt(session: str, limit: int = 6000) -> str:
    """Nachrichten der Unterhaltung als Text, für den Titel."""
    parts = []
    try:
        with open(recorder.path(session), encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                if event.get("type") in ("user", "text") and isinstance(event.get("text"), str):
                    parts.append(("Nutzer: " if event["type"] == "user" else "Agent: ") + event["text"][:800])
    except (OSError, ValueError):
        pass
    return "\n\n".join(parts)[:limit]


# --- Statistik ---------------------------------------------------------------------
# Die Bridge zählt selbst mit und legt die Zahlen je Unterhaltung in <kennung>.stats.json ab.


def load_stats(session: str) -> dict:
    try:
        data = json.loads(recorder.stats_path(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    data.setdefault("turns", 0)
    data.setdefault("turn_ms", 0)
    data.setdefault("tools", {})
    data.setdefault("tokens", {})
    return data


def clock(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"


def count(number) -> str:
    return f"{int(number):,}".replace(",", ".") if isinstance(number, (int, float)) else "–"


def stats_text(session: str) -> str:
    if not recorder.stats_path(session).is_file():
        return "Zu dieser Unterhaltung gibt es noch keine Zahlen. Sie entstehen mit der ersten Antwort."
    data = load_stats(session)
    tools = sorted(data["tools"].items(), key=lambda entry: -entry[1].get("ms", 0))
    tool_ms = sum(entry.get("ms", 0) for _, entry in tools)
    tokens = data["tokens"]
    lines = ["## Zeit", "", "| Was | Dauer |", "|---|---|",
             f"| Arbeitszeit von Codex, alle Antworten | {clock(data['turn_ms'] / 1000)} |",
             f"| davon Werkzeuge | {clock(tool_ms / 1000)} |",
             f"| Antworten | {data['turns']} |", "",
             "## Tokens", "", "| Was | Zahl |", "|---|---|",
             f"| Eingabe gesamt | {count(tokens.get('inputTokens'))} |",
             f"| davon aus dem Cache gelesen | {count(tokens.get('cachedInputTokens'))} |",
             f"| Ausgabe | {count(tokens.get('outputTokens'))} |",
             f"| davon Nachdenken | {count(tokens.get('reasoningOutputTokens'))} |",
             f"| Kontext beim letzten Aufruf | {count(data.get('context'))} |"]
    if data.get("window"):
        lines.append(f"| Größe des Kontextfensters | {count(data['window'])} |")
    if data.get("model"):
        lines.append(f"| Modell | `{data['model']}` |")
    if tools:
        lines += ["", "## Werkzeuge", "", "| Werkzeug | Aufrufe | Dauer |", "|---|---|---|"]
        lines += [f"| {name} | {entry.get('count', 0)} | {clock(entry.get('ms', 0) / 1000)} |" for name, entry in tools[:20]]
    lines += ["", "Die Zahlen zählt die Bridge mit, solange die Unterhaltung über die Kachel läuft."]
    return "\n".join(lines) + "\n"


# --- Unterhaltung ------------------------------------------------------------------


class Bridge:
    def __init__(self, codex: subprocess.Popen, model: str, resume: str | None):
        self.codex = codex
        self.model = model
        self.resume = resume
        self._in = threading.Lock()
        self._lock = threading.RLock()  # Zustand; Befehle von devc und Ereignisse von Codex kommen aus zwei Threads
        self.next_id = 0
        self.calls = {}  # Nummer einer Anfrage an Codex -> wofür
        self.thread = None  # Kennung der Unterhaltung bei Codex
        self.turn = None  # Kennung der laufenden Antwort
        self.busy = False
        self.queue = []  # Nachrichten des Nutzers, die auf das Ende der laufenden Antwort warten
        self.items = {}  # Kennung -> Eintrag von Codex (Befehl, Patch …)
        self.outputs = {}  # Kennung -> Ausgabe eines Befehls
        self.started = {}  # Kennung -> Startzeit
        self.pending = {}  # Kennung der Freigabe -> (Anfrage-Nummer von Codex, Methode, Art, Kennung des Eintrags)
        self.always = set()  # Arten, die der Nutzer für diese Unterhaltung freigegeben hat
        self.permissions = load_permissions()
        self.interrupted = False
        self.stopping = False  # der Abbruch ist schon an Codex geschickt
        self.failure = None  # Fehlermeldung der laufenden Antwort
        self.dirty = False
        self.end = False
        self.doing = None
        self.title_thread = None
        self.title_text = ""
        self.stats = None

    # --- Leitung zu Codex ---------------------------------------------------

    def write(self, message: dict) -> None:
        with self._in:
            try:
                self.codex.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self.codex.stdin.flush()
            except (OSError, ValueError):
                pass

    def call(self, method: str, params: dict, purpose: str) -> None:
        with self._lock:
            self.next_id += 1
            self.calls[self.next_id] = purpose
            number = self.next_id
        self.write({"id": number, "method": method, "params": params})

    def hang_up(self) -> None:
        try:
            self.codex.stdin.close()
        except OSError:
            pass

    def thread_params(self) -> dict:
        # Der Container ist die Grenze: keine zweite Sandbox von Codex. Gefragt wird über die Freigaben.
        return {"cwd": os.getcwd(), "approvalPolicy": "untrusted", "sandbox": "danger-full-access"}

    def activity(self, text: str) -> None:
        if self.pending or text == self.doing:
            return
        self.doing = text
        emit("status", state="working", text=text)

    # --- von devc -----------------------------------------------------------

    def commands(self) -> None:
        for line in sys.stdin:
            try:
                command = json.loads(line)
            except ValueError:
                continue
            kind = command.get("type")
            with self._lock:
                if kind == "end":
                    self.finish()
                    return
                if kind == "send" and isinstance(command.get("text"), str):
                    self.dirty = True
                    emit("user", text=command["text"])
                    self.queue.append(command["text"])
                    self.pump()
                elif kind == "permit" and command.get("id") in self.pending:
                    allow = command.get("allow") is True
                    if allow and command.get("always") is True:
                        self.always.add(self.pending[command["id"]][2])
                    self.answer(command["id"], allow)
                    emit("status", state="working", **({"text": self.doing} if self.doing else {}))
                elif kind == "set":
                    self.configure(command.get("name"), command.get("value"))
                elif kind == "interrupt":
                    for key in list(self.pending):
                        self.answer(key, False)
                    self.queue.clear()
                    if self.busy:
                        self.interrupted = True
                        self.stop_turn()
        self.hang_up()  # devc hat die Leitung geschlossen
        try:
            self.codex.wait(3)
        except subprocess.TimeoutExpired:
            self.codex.terminate()

    def stop_turn(self) -> None:
        """Bricht die laufende Antwort ab. Kennt die Bridge deren Kennung noch nicht, folgt der Abbruch, sobald Codex sie nennt."""
        if self.interrupted and self.turn and self.thread and not self.stopping:
            self.stopping = True
            self.call("turn/interrupt", {"threadId": self.thread, "turnId": self.turn}, "interrupt")

    def pump(self) -> None:
        """Schickt die nächste wartende Nachricht, wenn Codex bereit ist."""
        if self.busy or not self.thread or not self.queue or self.end:
            return
        self.busy = True
        self.interrupted, self.stopping, self.failure, self.doing = False, False, None, None
        emit("status", state="working")
        self.call("turn/start", {"threadId": self.thread, "input": [{"type": "text", "text": self.queue.pop(0), "text_elements": []}]}, "turn")

    def configure(self, name, value) -> None:
        if name != "permissions" or value not in [item for item, _, _ in PERMISSIONS]:
            return
        self.permissions = value
        save_permissions(value)
        emit("ready", settings=settings(value))
        if value == "all":
            for key in list(self.pending):
                self.answer(key, True)
            if self.doing:
                emit("status", state="working", text=self.doing)

    def answer(self, key: str, allow: bool) -> None:
        found = self.pending.pop(key, None)
        if found is None:
            return
        number, method, _, item = found
        if item in self.started:
            self.started[item] = time.monotonic()  # das Warten auf die Freigabe zählt nicht zur Dauer des Werkzeugs
        if method in ("applyPatchApproval", "execCommandApproval"):  # ältere Fassung der Anfrage
            decision = "approved" if allow else {"denied": {"rejection": "Vom Nutzer abgelehnt"}}
        else:
            decision = "accept" if allow else "decline"
        self.write({"id": number, "result": {"decision": decision}})
        emit("permission_done", id=key, allow=allow)

    def finish(self) -> None:
        """»Beenden«: vor dem Ende einen Titel schreiben lassen, wenn in diesem Lauf etwas dazukam."""
        self.end = True
        emit("status", state="ending")
        for key in list(self.pending):
            self.answer(key, False)
        if self.dirty and recorder.session and not self.busy:
            timer = threading.Timer(TITLE_SECONDS, self.hang_up)
            timer.daemon = True
            timer.start()
            self.call("thread/start", {"cwd": os.getcwd(), "approvalPolicy": "never", "sandbox": "read-only", "ephemeral": True}, "title-thread")
        else:
            self.hang_up()

    # --- von Codex ----------------------------------------------------------

    def events(self) -> None:
        for line in self.codex.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            with self._lock:
                if "method" in message and "id" in message:
                    self.request(message["id"], message["method"], message.get("params") or {})
                elif "method" in message:
                    self.notice(message["method"], message.get("params") or {})
                elif "id" in message:
                    self.result(self.calls.pop(message["id"], None), message.get("result"), message.get("error"))

    def result(self, purpose, result, error) -> None:
        result = result if isinstance(result, dict) else {}
        problem = str((error or {}).get("message") or "")[:2000] if isinstance(error, dict) else ""
        if purpose == "init":
            found = re.search(r"/(\d+(?:\.\d+)+)", str(result.get("userAgent") or ""))
            if found:
                emit("ready", agent="Codex", version=found[1])
            self.write({"method": "initialized"})
            if self.resume:
                self.call("thread/resume", {"threadId": self.resume, **self.thread_params()}, "thread")
            else:
                self.call("thread/start", self.thread_params(), "thread")
        elif purpose == "thread":
            thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
            if problem or not isinstance(thread.get("id"), str):
                emit("error", text="Codex konnte die Unterhaltung nicht öffnen" + (f": {problem}" if problem else "."))
                self.hang_up()
                return
            self.thread = thread["id"]
            with _out:
                recorder.open(self.thread)
            self.stats = load_stats(self.thread)
            model = result.get("model") or thread.get("model") or self.model
            if isinstance(model, str) and model:
                self.stats["model"] = model
            emit("ready", agent="Codex", session=self.thread, **({"model": model} if isinstance(model, str) and model else {}))
            self.pump()
        elif purpose == "turn":
            turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
            if problem:
                self.turn_done(False, problem, None)
            elif isinstance(turn.get("id"), str):
                self.turn = turn["id"]
                self.stop_turn()
        elif purpose == "title-thread":
            thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
            if problem or not isinstance(thread.get("id"), str):
                self.hang_up()
                return
            self.title_thread = thread["id"]
            text = TITLE_PROMPT + "\n\n" + excerpt(recorder.session)
            self.call("turn/start", {"threadId": self.title_thread, "input": [{"type": "text", "text": text, "text_elements": []}]}, "title-turn")
        elif purpose == "title-turn" and problem:
            self.hang_up()

    def request(self, number, method: str, params: dict) -> None:
        """Codex fragt etwas: fast immer eine Freigabe."""
        if params.get("threadId") == self.title_thread and self.title_thread:
            self.write({"id": number, "result": {"decision": "decline"}})
            return
        kinds = {"item/commandExecution/requestApproval": "Shell", "execCommandApproval": "Shell",
                 "item/fileChange/requestApproval": "Patch", "applyPatchApproval": "Patch"}
        if method == "item/permissions/requestApproval":
            self.write({"id": number, "result": {"permissions": {}, "scope": "turn"}})  # keine zusätzlichen Rechte
            return
        if method not in kinds:
            self.write({"id": number, "error": {"code": -32601, "message": NO_QUESTIONS if method == "item/tool/requestUserInput" else "nicht unterstützt"}})
            return
        kind = kinds[method]
        item = self.items.get(params.get("itemId")) or {}
        if kind == "Shell":
            source = {"command": params.get("command") or item.get("command"), "commandActions": params.get("commandActions") or item.get("commandActions")}
            if isinstance(source["command"], list):  # ältere Fassung: Liste der Argumente
                source["command"] = " ".join(str(part) for part in source["command"])
            summary = command_line(source)
            data = {"command": source["command"], "cwd": params.get("cwd") or item.get("cwd")}
        else:
            _, summary, data = tool_of({"type": "fileChange", "changes": item.get("changes") or []})
            if not summary and isinstance(params.get("fileChanges"), dict):
                summary, data = ", ".join(params["fileChanges"])[:300], {"changes": params["fileChanges"]}
        if isinstance(params.get("reason"), str) and params["reason"]:
            data["reason"] = params["reason"]
        key = f"a{number}"
        if self.permissions == "all" or kind in self.always:
            self.write({"id": number, "result": {"decision": "approved" if method in ("applyPatchApproval", "execCommandApproval") else "accept"}})
            return
        self.pending[key] = (number, method, kind, params.get("itemId"))
        emit("permission", id=key, tool=kind, summary=summary, input=pretty(data))
        emit("status", state="waiting")

    def notice(self, method: str, params: dict) -> None:
        if self.title_thread and params.get("threadId") == self.title_thread:
            self.title_notice(method, params)
            return
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        kind = item.get("type")
        if method == "turn/started":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            self.turn = turn.get("id") or self.turn
            self.stop_turn()
            self.activity(REQUESTING)
        elif method == "item/agentMessage/delta":
            if isinstance(params.get("delta"), str) and params["delta"]:
                self.activity(ANSWERING)
                emit("text_delta", text=params["delta"])
        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            self.activity(THINKING)
        elif method == "item/commandExecution/outputDelta":
            key = params.get("itemId")
            if isinstance(params.get("delta"), str) and len(self.outputs.get(key, "")) <= MAX_OUTPUT:
                self.outputs[key] = self.outputs.get(key, "") + params["delta"]
        elif method == "item/started":
            if kind == "reasoning":
                self.activity(THINKING)
            elif kind == "contextCompaction":
                self.activity(COMPACTING)
            elif kind and kind not in QUIET_ITEMS and isinstance(item.get("id"), str):
                self.items[item["id"]] = item
                self.started[item["id"]] = time.monotonic()
                name, summary, data = tool_of(item)
                emit("tool_start", id=item["id"], name=name, summary=summary, input=pretty(data))
                self.activity(name + (f": {summary[:120]}" if summary else ""))
        elif method == "item/completed":
            if kind == "agentMessage":
                if isinstance(item.get("text"), str) and item["text"]:
                    emit("text", text=item["text"])
            elif isinstance(item.get("id"), str) and item["id"] in self.items:
                self.tool_done(item)
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") if isinstance(params.get("tokenUsage"), dict) else {}
            if self.stats is not None and isinstance(usage.get("total"), dict):
                self.stats["tokens"] = usage["total"]
                last = usage.get("last") if isinstance(usage.get("last"), dict) else {}
                self.stats["context"] = last.get("inputTokens")
                self.stats["window"] = usage.get("modelContextWindow")
        elif method == "error":
            error = params.get("error") if isinstance(params.get("error"), dict) else {}
            text = str(error.get("message") or "")[:2000]
            if params.get("willRetry"):
                emit("status", state="retry", text="Der Dienst antwortet nicht, neuer Versuch …")
            elif text:
                self.failure = text
        elif method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
            self.turn_done(turn.get("status") == "completed", str(error.get("message") or self.failure or "")[:2000], turn.get("durationMs"))

    def tool_done(self, item: dict) -> None:
        key = item["id"]
        self.items.pop(key, None)
        output = self.outputs.pop(key, "") or str(item.get("aggregatedOutput") or "")
        status = item.get("status")
        ok = status not in ("failed", "declined") and item.get("exitCode") in (None, 0) and not item.get("error")
        if item.get("type") == "commandExecution" and item.get("exitCode") not in (None, 0):
            output = (output.rstrip("\n") + "\n" if output else "") + f"Exit {item['exitCode']}"
        elif status == "declined":
            output = output or "Vom Nutzer abgelehnt"
        elif not output and item.get("type") != "commandExecution":
            output = pretty(item.get("result") if item.get("result") is not None else item.get("error") or "")
        emit("tool_end", id=key, ok=ok, output=cut(output))
        name = tool_of(item)[0]
        entry = self.stats["tools"].setdefault(name, {"count": 0, "ms": 0}) if self.stats is not None else {"count": 0, "ms": 0}
        entry["count"] += 1
        entry["ms"] += int((time.monotonic() - self.started.pop(key, time.monotonic())) * 1000)
        self.activity(EVALUATING)

    def turn_done(self, ok: bool, problem: str, duration) -> None:
        fields = {"ok": ok and not self.interrupted}
        if isinstance(duration, (int, float)):
            fields["duration_ms"] = int(duration)
        if self.interrupted:
            fields["text"] = "Abgebrochen."
        elif not ok:
            fields["text"] = problem or "Die Antwort endete mit einem Fehler."
            if re.search(r"\b(401|403)\b|unauthorized|invalid api key|authentication", fields["text"], re.I):
                fields["text"] = NO_KEY + "\n\n" + fields["text"][:500]
        self.busy, self.turn, self.interrupted, self.stopping, self.failure, self.doing = False, None, False, False, None, None
        if self.stats is not None and recorder.session:
            self.stats["turns"] += 1
            self.stats["turn_ms"] += fields.get("duration_ms", 0)
            try:
                recorder.stats_path(recorder.session).write_text(json.dumps(self.stats) + "\n", encoding="utf-8")
            except OSError:
                pass
        emit("turn_end", **fields)
        if self.end:
            self.hang_up()
            return
        emit("status", state="idle")
        self.pump()

    def title_notice(self, method: str, params: dict) -> None:
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if method == "item/completed" and item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
            self.title_text = item["text"]
        elif method == "turn/completed":
            title = clean_title(self.title_text)
            if title and recorder.session:
                try:
                    recorder.title_path(recorder.session).write_text(title + "\n", encoding="utf-8")
                except OSError:
                    pass
            self.hang_up()


def main(argv: list[str]) -> int:
    if "--sessions" in argv:
        sessions()
        return 0
    if "--stats" in argv[:-1]:
        session = argv[argv.index("--stats") + 1]
        print(json.dumps({"stats": stats_text(session) if SESSION_RE.match(session) else ""}, ensure_ascii=False))
        return 0
    if "--show" in argv[:-1]:
        session = argv[argv.index("--show") + 1]
        if SESSION_RE.match(session):
            replay(session)
        return 0
    model = argv[argv.index("--model") + 1] if "--model" in argv[:-1] else ""
    try:
        start = json.loads(sys.stdin.readline() or "{}")
    except ValueError:
        start = {}
    resume = start.get("resume") if isinstance(start.get("resume"), str) and SESSION_RE.match(start["resume"]) else None

    codex = shutil.which("codex")
    if not codex:
        emit("error", text="codex wurde im Container nicht gefunden. Das Addon codex neu einrichten (Konfiguration, »Anwenden«).")
        return 1
    if resume:
        replay(resume)
    process = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding="utf-8", errors="replace")
    bridge = Bridge(process, model, resume)
    emit("ready", agent="Codex", session=resume, settings=settings(bridge.permissions), **({"model": model} if model else {}))
    emit("status", state="idle")
    bridge.call("initialize", {"clientInfo": {"name": "devc", "title": "devc", "version": "1.0.0"}}, "init")
    errors = []

    def read_errors() -> None:
        for line in process.stderr:  # laufend lesen: Sonst bleibt Codex stehen, wenn die Leitung voll ist
            errors.append(line.rstrip("\n"))
            del errors[:-5]

    reader = threading.Thread(target=read_errors, daemon=True)
    reader.start()
    threading.Thread(target=bridge.commands, daemon=True).start()
    bridge.events()
    code = process.wait()
    if bridge.end:
        return 0
    reader.join(2)
    if code != 0 and errors:
        emit("error", text=re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(errors))[:2000])
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
