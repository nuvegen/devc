#!/usr/bin/env python3
"""Bridge zwischen dem Chat von devc und Claude Code.

devc startet dieses Skript im Container und spricht mit ihm über stdin und stdout, eine JSON-Zeile je
Nachricht (Protokoll: Handbuch von devc, Abschnitt »Eigene Kacheln«). Das Skript startet `claude` im
Modus stream-json und übersetzt in beide Richtungen. Nur Standardbibliothek.

Aufruf: bridge.py [--model <name>] [--updates true|false]      Unterhaltung führen
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
import uuid
from datetime import datetime
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "devc-chat" / "claude-code"
MAX_OUTPUT = 20000  # Zeichen je Tool-Eingabe und Tool-Ergebnis
STORED = ("user", "text", "tool_start", "tool_end", "turn_end", "error")  # stehen im Verlauf
SESSION_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
NO_LOGIN = ("Claude ist im Container nicht angemeldet. Unter »Secrets« den Abo-Token (claude-code.oauth_token) oder den API-Key "
            "(claude-code.api_key) setzen, oder im Terminal einmal `claude` starten und anmelden. "
            "Mit einem anderen Dienst gilt nur dessen Schlüssel (claude-code.auth_token).")
TITLE_PROMPT = ("Fasse diese Unterhaltung in höchstens 10 Wörtern als Titel zusammen. Antworte nur mit dem Titel, "
                "in der Sprache der Unterhaltung, ohne Anführungszeichen und ohne Satzzeichen am Ende.")
TITLE_SECONDS = 25
# Zusatz zum Systemprompt (Parameter chat_updates): Im Chat sieht der Nutzer Werkzeuge nur eingeklappt.
UPDATES_PROMPT = ("Du schreibst mit dem Nutzer in einem Chat-Fenster im Browser. Deine Werkzeugaufrufe sieht er dort nur eingeklappt. "
                  "Halte ihn bei längerer Arbeit auf dem Laufenden: Sage vor dem ersten Werkzeug in einem Satz, was du vorhast. "
                  "Melde dich danach nach jedem größeren Schritt mit ein bis zwei kurzen Sätzen: was erledigt ist, was du gefunden hast, "
                  "was als Nächstes kommt. Keine langen Zwischenberichte; das Ergebnis fasst du am Ende zusammen.")
# Was Claude Code gerade tut, für die Zeile »arbeitet …« im Chat
THINKING, ANSWERING, REQUESTING, COMPACTING, EVALUATING = ("denkt nach", "schreibt", "wartet auf das Modell", "fasst den bisherigen Verlauf zusammen",
                                                         "wertet das Ergebnis aus")
FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")  # ändern Dateien; für sie gibt es die Freigabe je Ordner
INIT_ID = "devc-init"  # Anfrage an Claude Code beim Start: Version, Modelle, Befehle
MAX_COMMANDS = 200
USAGE_RE = re.compile(r"^Usage:\s*(/\S.*)$")
OPTION_RE = re.compile(r"^\s+([A-Za-z][\w.-]*)=(\S.*)$")
MARKDOWN_RE = re.compile(r"^\s*(#{1,4}\s|\||```)")
# Wie viel Claude ohne Rückfrage darf. Der Nutzer wählt es im Chat unter »Einstellungen«; die Wahl gilt für den Container.
PERMISSIONS = (
    ("ask", "Nachfragen", "Claude fragt vor jeder Änderung an einer Datei und vor jedem Befehl."),
    ("files", "Dateien im Projekt frei", "Claude ändert Dateien im Projektordner ohne Rückfrage. Vor Befehlen und allem anderen fragt es."),
    ("all", "Alles erlauben", "Claude fragt nicht mehr. Es darf im Container alles, was der Nutzer dev darf: Dateien ändern, Befehle ausführen, das Netz nutzen."),
)
SETTINGS_FILE = STATE / "settings.json"
NO_QUESTIONS = "Dieses Werkzeug gibt es hier nicht. Stelle Rückfragen als normalen Text; der Nutzer antwortet im Chat."

_out = threading.Lock()


class Recorder:
    """Schreibt den Verlauf mit, damit eine fortgesetzte Unterhaltung ihre alten Nachrichten zeigt."""

    def __init__(self):
        self.session = None
        self._file = None
        self._waiting = []  # Ereignisse, bevor Claude die Kennung der Unterhaltung genannt hat

    def path(self, session: str) -> Path:
        return STATE / f"{session}.jsonl"

    def title_path(self, session: str) -> Path:
        return STATE / f"{session}.title"

    def open(self, session: str) -> None:
        if session == self.session or not SESSION_RE.match(session):
            return
        STATE.mkdir(parents=True, exist_ok=True)
        previous = self.session
        if self._file:
            self._file.close()
        if previous and self.path(previous).is_file() and not self.path(session).exists():
            shutil.copyfile(self.path(previous), self.path(session))  # Claude hat die Kennung gewechselt
            if self.title_path(previous).is_file():
                shutil.copyfile(self.title_path(previous), self.title_path(session))
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


def summary(tool: str, data) -> str:
    """Eine Zeile, die sagt, was das Tool tun soll."""
    if not isinstance(data, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
        if isinstance(data.get(key), str) and data[key]:
            return data[key].strip().splitlines()[0][:300]
    return ""


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict):
            parts.append(block.get("text") if block.get("type") == "text" else f"[{block.get('type', 'Inhalt')}]")
    return "\n".join(part for part in parts if part)


def configured_model(model: str) -> str:
    """Das Modell, mit dem Claude Code starten wird: `chat_model`, sonst dessen eigene Einstellung."""
    if model:
        return model
    if os.environ.get("ANTHROPIC_MODEL"):
        return os.environ["ANTHROPIC_MODEL"]
    try:
        found = json.loads((Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8")).get("model")
    except (OSError, ValueError, AttributeError):
        found = None
    return found if isinstance(found, str) else ""


def resolved_model(name: str, models) -> str:
    """Macht aus einem Kurznamen wie `opus` den vollen Namen, den Claude Code dafür nennt."""
    for item in models if isinstance(models, list) else []:
        if isinstance(item, dict) and item.get("value") == (name or "default") and isinstance(item.get("resolvedModel"), str):
            return item["resolvedModel"]
    return name


def command_list(found) -> list:
    """Die Befehle von Claude Code (`/config`, `/clear`, …) für die Hilfe im Eingabefeld."""
    commands = []
    for item in found if isinstance(found, list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]:
            text = " ".join(str(item.get("description") or "").split())
            if item["name"].startswith("_") or text.startswith("(removed)"):
                continue  # interne und abgeschaffte Befehle
            commands.append({"name": item["name"][:80], "hint": " ".join(str(item.get("argumentHint") or "").split())[:120],
                             "description": text if len(text) <= 140 else text[:139] + "…"})
    return sorted(commands, key=lambda c: (":" in c["name"], c["name"].lower()))[:MAX_COMMANDS]  # Befehle aus Plugins zuletzt


def local_output(text: str) -> str:
    """Die Ausgabe eines Befehls wie `/config` ist für ein Terminal gesetzt. Hier wird daraus Markdown:
    eine Liste `name=wert|wert` wird zur Tabelle, sonst behält jede Zeile ihren Umbruch."""
    lines = text.strip("\n").splitlines()
    head = USAGE_RE.match(lines[0]) if lines else None
    options = [OPTION_RE.match(line) for line in lines[1:]]
    if head and len(options) >= 2 and all(options):
        rows = [f"| `{m[1]}` | {', '.join(f'`{value.strip()}`' for value in m[2].split('|') if value.strip())} |" for m in options]
        return f"Aufruf: `{head[1].strip()}`\n\n| Einstellung | Werte |\n|---|---|\n" + "\n".join(rows)
    if any(MARKDOWN_RE.match(line) for line in lines):
        return text
    out = []
    for index, line in enumerate(lines):
        more = index + 1 < len(lines) and lines[index + 1].strip()
        out.append(line.strip() + ("  " if line.strip() and more else ""))
    return "\n".join(out)


def installed_version(claude: str) -> str | None:
    """Ältere Fassungen von Claude Code nennen ihre Version beim Start nicht; dann hilft `claude --version`."""
    try:
        result = subprocess.run([claude, "--version"], stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.match(r"\s*(\d+(?:\.\d+)+\S*)", result.stdout)
    return found[1] if found else None


def tool_folder(tool, data) -> str | None:
    """Der Ordner der Datei, die ein Werkzeug ändern will; None bei anderen Werkzeugen."""
    path = data.get("file_path") or data.get("notebook_path") if isinstance(data, dict) else None
    if tool not in FILE_TOOLS or not isinstance(path, str) or not path:
        return None
    # realpath löst `..` und Verweise auf: Sonst käme man mit /work/a/../../etc aus einem freigegebenen Ordner heraus.
    return os.path.dirname(os.path.realpath(path))


def inside(folder: str, allowed) -> bool:
    return any(folder == base or folder.startswith(base.rstrip("/") + "/") for base in allowed)


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


def clean_title(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return " ".join((lines[0] if lines else "").strip("#*_`\"'„“”»« ").split())[:80]


def make_title(claude: str, session: str, model: str) -> None:
    """Lässt Claude die Unterhaltung als Titel zusammenfassen. Das läuft als Abzweig, der nicht gespeichert wird:
    Die Unterhaltung selbst bleibt, wie sie ist."""
    command = [claude, "-p", "--resume", session, "--fork-session", "--no-session-persistence", "--output-format", "text"]
    if model:
        command += ["--model", model]
    try:
        result = subprocess.run(command + [TITLE_PROMPT], stdin=subprocess.DEVNULL, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=TITLE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return
    title = clean_title(result.stdout) if result.returncode == 0 else ""
    if title:
        try:
            recorder.title_path(session).write_text(title + "\n", encoding="utf-8")
        except OSError:
            pass


# --- Statistik ---------------------------------------------------------------------
# Quelle ist das Transkript von Claude Code (~/.claude/projects/<projekt>/<kennung>.jsonl). Das Format ist intern
# und nicht dokumentiert; fehlt ein Feld, fehlt die Zahl.


def transcript_files(session: str) -> tuple[Path | None, list[Path]]:
    """(Transkript, Transkripte der Unter-Agenten) zu einer Unterhaltung."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    found = sorted(base.glob(f"*/{session}.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True) if base.is_dir() else []
    if not found:
        return None, []
    return found[0], sorted((found[0].parent / session).glob("**/*.jsonl"))


def transcript_rows(path: Path) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    row["_at"] = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")).timestamp()
                except (ValueError, KeyError, TypeError):
                    continue
                if row.get("type") in ("user", "assistant"):
                    rows.append(row)
    except OSError:
        return []
    rows.sort(key=lambda row: row["_at"])
    return rows


def row_blocks(row: dict) -> list:
    content = (row.get("message") or {}).get("content")
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def usage_of(rows: list[dict]) -> dict:
    """Tokens je Aufruf des Modells; eine Nachricht steht mehrfach im Transkript, es zählt ihr letzter Stand."""
    calls, order = {}, []
    for row in rows:
        message = row.get("message") or {}
        if row["type"] != "assistant" or not message.get("id") or not isinstance(message.get("usage"), dict):
            continue
        if message["id"] not in calls:
            order.append(message["id"])
        calls[message["id"]] = message
    total = {"calls": len(calls), "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "context": 0, "models": []}
    for key in order:
        usage = calls[key]["usage"]
        number = lambda name: usage.get(name) if isinstance(usage.get(name), int) else 0
        total["input"] += number("input_tokens")
        total["output"] += number("output_tokens")
        total["cache_read"] += number("cache_read_input_tokens")
        total["cache_write"] += number("cache_creation_input_tokens")
        total["context"] = number("input_tokens") + number("cache_read_input_tokens") + number("cache_creation_input_tokens")
        model = calls[key].get("model")
        if isinstance(model, str) and model and model != "<synthetic>" and model not in total["models"]:
            total["models"].append(model)
    return total


def timing_of(rows: list[dict]) -> dict:
    """Teilt die Zeit zwischen zwei Einträgen danach auf, womit die Lücke endet: Antwort des Modells, Ergebnis eines Werkzeugs, Nachricht des Nutzers."""
    names = {block.get("id"): block.get("name") or "?" for row in rows if row["type"] == "assistant" for block in row_blocks(row) if block.get("type") == "tool_use"}
    found = {"total": rows[-1]["_at"] - rows[0]["_at"] if rows else 0.0, "model": 0.0, "tool": 0.0, "wait": 0.0, "prompts": 0, "tools": {}}
    for index, row in enumerate(rows):
        gap = max(0.0, row["_at"] - rows[index - 1]["_at"]) if index else 0.0
        results = [block for block in row_blocks(row) if block.get("type") == "tool_result"] if row["type"] == "user" else []
        if row["type"] == "assistant":
            found["model"] += gap
        elif results:
            found["tool"] += gap
            for block in results:
                entry = found["tools"].setdefault(names.get(block.get("tool_use_id"), "?"), [0, 0.0])
                entry[0] += 1
                entry[1] += gap / len(results)
        else:
            found["wait"] += gap
            found["prompts"] += 0 if row.get("isMeta") else 1
    return found


def clock(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"


def count(number: int) -> str:
    return f"{number:,}".replace(",", ".")


def share(part: float, whole: float) -> str:
    return f"{round(100 * part / whole)} %" if whole else "–"


def stats_text(session: str) -> str:
    file, helpers = transcript_files(session)
    rows = [row for row in transcript_rows(file) if not row.get("isSidechain")] if file else []
    if not rows:
        return "Zu dieser Unterhaltung gibt es noch keine Zahlen. Sie entstehen mit der ersten Antwort."
    time, tokens = timing_of(rows), usage_of(rows)
    active = time["model"] + time["tool"]
    all_input = tokens["input"] + tokens["cache_read"] + tokens["cache_write"]
    lines = ["## Zeit", "", "| Was | Dauer | Anteil |", "|---|---|---|",
             f"| Gesamt, erste bis letzte Nachricht | {clock(time['total'])} | |",
             f"| Modell | {clock(time['model'])} | {share(time['model'], active)} der Arbeitszeit |",
             f"| Werkzeuge | {clock(time['tool'])} | {share(time['tool'], active)} der Arbeitszeit |",
             f"| Warten auf dich | {clock(time['wait'])} | {time['prompts']} {'Nachricht' if time['prompts'] == 1 else 'Nachrichten'} von dir |",
             "", "## Tokens", "", "| Was | Zahl |", "|---|---|",
             f"| Aufrufe des Modells | {count(tokens['calls'])} |",
             f"| Eingabe gesamt | {count(all_input)} |",
             f"| davon aus dem Cache gelesen | {count(tokens['cache_read'])} ({share(tokens['cache_read'], all_input)}) |",
             f"| davon in den Cache geschrieben | {count(tokens['cache_write'])} |",
             f"| davon ohne Cache | {count(tokens['input'])} |",
             f"| Ausgabe | {count(tokens['output'])} |",
             f"| Kontext beim letzten Aufruf | {count(tokens['context'])} |"]
    if tokens["models"]:
        lines.append("| Modell | " + ", ".join(f"`{model}`" for model in tokens["models"]) + " |")
    if time["tools"]:
        lines += ["", "## Werkzeuge", "", "| Werkzeug | Aufrufe | Zeit |", "|---|---|---|"]
        lines += [f"| {name} | {entry[0]} | {clock(entry[1])} |" for name, entry in sorted(time["tools"].items(), key=lambda item: -item[1][1])[:40]]
    extra = usage_of([row for helper in helpers for row in transcript_rows(helper)]) if helpers else None
    if extra and extra["calls"]:
        extra_input = extra["input"] + extra["cache_read"] + extra["cache_write"]
        lines += ["", "## Unter-Agenten", "", "| Was | Zahl |", "|---|---|", f"| Unter-Agenten | {len(helpers)} |", f"| Aufrufe des Modells | {count(extra['calls'])} |",
                  f"| Eingabe gesamt | {count(extra_input)} |", f"| Ausgabe | {count(extra['output'])} |"]
        lines += ["", "Die Tokens der Unter-Agenten stehen nur hier, nicht in der Tabelle darüber. Ihre Zeit steckt oben beim Werkzeug, das sie gestartet hat."]
    lines += ["", "Die Zeiten sind aus den Zeitstempeln im Verlauf von Claude Code geschätzt. Pausen von dir zählen als Warten, nicht als Arbeitszeit."]
    return "\n".join(lines) + "\n"


def replay(session: str) -> None:
    """Gibt den gespeicherten Verlauf aus, ohne ihn erneut zu speichern."""
    try:
        with open(recorder.path(session), encoding="utf-8") as handle, _out:
            for line in handle:
                if line.strip():
                    sys.stdout.write(line if line.endswith("\n") else line + "\n")
            sys.stdout.flush()
    except OSError:
        pass


class Bridge:
    def __init__(self, claude: subprocess.Popen, model: str = "", binary: str = "claude"):
        self.claude = claude
        self.binary = binary
        self.model = model  # das Modell, mit dem Claude Code gestartet ist
        self._in = threading.Lock()
        self.pending = {}  # Anfrage-Nummer -> Eingabe des Tools
        self.always = set()  # Tools, die der Nutzer für diese Unterhaltung freigegeben hat
        self.folders = set()  # Ordner (mit Unterordnern), in denen Dateien ohne Rückfrage geändert werden dürfen
        self.asked = {}  # Anfrage-Nummer -> Ordner, der dabei zur Freigabe angeboten wurde
        self.tools = {}  # Anfrage-Nummer -> Tool
        self.interrupted = False
        self.dirty = False  # in diesem Lauf kam eine Nachricht des Nutzers dazu
        self.end = False  # der Nutzer hat »Beenden« gewählt: vor dem Ende einen Titel schreiben
        self.doing = None  # zuletzt gemeldete Tätigkeit
        self.permissions = load_permissions()  # ask, files oder all
        self.project = os.path.realpath(os.getcwd())  # hier gilt »Dateien im Projekt frei«

    def free(self, tool, data) -> bool:
        """Darf das Werkzeug ohne Rückfrage laufen?"""
        if self.permissions == "all" or tool in self.always:
            return True
        folder = tool_folder(tool, data)
        return bool(folder) and inside(folder, self.folders | ({self.project} if self.permissions == "files" else set()))

    def configure(self, name, value) -> None:
        if name != "permissions" or value not in [item for item, _, _ in PERMISSIONS]:
            return
        self.permissions = value
        save_permissions(value)
        emit("ready", settings=settings(value))
        # Offene Fragen, die jetzt erlaubt sind, beantwortet die Bridge gleich mit.
        for request_id in [key for key, data in list(self.pending.items()) if self.free(self.tools.get(key), data)]:
            self.answer(request_id, True)
            emit("permission_done", id=request_id, allow=True)
        if not self.pending and self.doing:
            emit("status", state="working", text=self.doing)

    def activity(self, text: str) -> None:
        """Meldet, was Claude gerade tut. So sieht der Nutzer auch bei langer Arbeit, dass es weitergeht."""
        if self.pending or text == self.doing:
            return  # eine offene Freigabe bleibt der Zustand; dieselbe Tätigkeit nicht wiederholen
        self.doing = text
        emit("status", state="working", text=text)

    def tool_activity(self, name, data, prefix: str = "") -> str:
        what = summary(name, data)
        return f"{prefix}{name or 'Werkzeug'}" + (f": {what[:120]}" if what else "")

    def to_claude(self, message: dict) -> None:
        with self._in:
            try:
                self.claude.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self.claude.stdin.flush()
            except (OSError, ValueError):
                pass

    def answer(self, request_id: str, allow: bool, message: str = "Vom Nutzer abgelehnt") -> None:
        data = self.pending.pop(request_id, None)
        self.tools.pop(request_id, None)
        self.asked.pop(request_id, None)
        if data is None:
            return
        response = {"behavior": "allow", "updatedInput": data} if allow else {"behavior": "deny", "message": message}
        self.to_claude({"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": response}})

    # --- von devc ---------------------------------------------------------

    def commands(self) -> None:
        for line in sys.stdin:
            try:
                command = json.loads(line)
            except ValueError:
                continue
            kind = command.get("type")
            if kind == "end":
                self.end = True
                emit("status", state="ending")
                break
            if kind == "send" and isinstance(command.get("text"), str):
                self.interrupted = False
                self.dirty = True
                self.doing = None
                emit("user", text=command["text"])
                emit("status", state="working")
                self.to_claude({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": command["text"]}]}})
            elif kind == "permit" and command.get("id") in self.pending:
                allow = command.get("allow") is True
                if allow and command.get("always") is True:
                    self.always.add(self.tools.get(command["id"]))
                if allow and command.get("folder") is True and self.asked.get(command["id"]):
                    self.folders.add(self.asked[command["id"]])
                self.answer(command["id"], allow)
                emit("permission_done", id=command["id"], allow=allow)
                emit("status", state="working", **({"text": self.doing} if self.doing else {}))
            elif kind == "set":
                self.configure(command.get("name"), command.get("value"))
            elif kind == "interrupt":
                for request_id in list(self.pending):
                    self.answer(request_id, False, "Vom Nutzer abgebrochen")
                    emit("permission_done", id=request_id, allow=False)
                self.interrupted = True
                self.to_claude({"type": "control_request", "request_id": uuid.uuid4().hex, "request": {"subtype": "interrupt"}})
        # devc hat die Leitung geschlossen: Unterhaltung beenden
        try:
            self.claude.stdin.close()
        except OSError:
            pass
        try:
            self.claude.wait(3)
        except subprocess.TimeoutExpired:
            self.claude.terminate()

    # --- von Claude -------------------------------------------------------

    def events(self) -> None:
        for line in self.claude.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                self.handle(event)

    def handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "control_request":
            self.control(event)
            return
        if kind == "control_response":
            self.started(event.get("response") or {})
            return
        if event.get("parent_tool_use_id"):
            # Nachrichten eines Unter-Agenten; sein Ergebnis kommt als Tool-Ergebnis. Gezeigt wird nur, womit er beschäftigt ist.
            if kind == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        self.activity(self.tool_activity(block.get("name"), block.get("input"), "Unter-Agent · "))
            return
        if kind == "system" and event.get("subtype") == "init":
            session = event.get("session_id")
            if isinstance(session, str):
                with _out:
                    recorder.open(session)
            emit("ready", agent="Claude Code", version=event.get("claude_code_version"), model=event.get("model"), session=session)
        elif kind == "system" and event.get("subtype") == "status":
            if event.get("status") in ("requesting", "compacting"):
                self.activity(REQUESTING if event["status"] == "requesting" else COMPACTING)
        elif kind == "system" and event.get("subtype") == "api_retry":
            emit("status", state="retry", text="Der Dienst antwortet nicht, neuer Versuch …")
        elif kind == "stream_event":
            inner = event.get("event") or {}
            delta = inner.get("delta") or {}
            if inner.get("type") == "content_block_start":
                block = inner.get("content_block") or {}
                if block.get("type") in ("thinking", "text"):
                    self.activity(THINKING if block["type"] == "thinking" else ANSWERING)
            if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta" and delta.get("text"):
                emit("text_delta", text=delta["text"])
        elif kind == "assistant":
            message = event.get("message") or {}
            local = message.get("model") == "<synthetic>" or any(key.startswith("local_command") for key in event)
            for block in message.get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    emit("text", text=local_output(block["text"]) if local else block["text"])
                elif block.get("type") == "tool_use":
                    data = block.get("input")
                    emit("tool_start", id=block.get("id"), name=block.get("name"), summary=summary(block.get("name"), data),
                         input=cut(json.dumps(data, ensure_ascii=False, indent=2)))
                    self.activity(self.tool_activity(block.get("name"), data))
        elif kind == "user":
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    emit("tool_end", id=block.get("tool_use_id"), ok=not block.get("is_error"), output=cut(result_text(block.get("content"))))
                    self.activity(EVALUATING)
        elif kind == "result":
            failed = bool(event.get("is_error"))
            fields = {"ok": not failed, "cost_usd": event.get("total_cost_usd"), "duration_ms": event.get("duration_ms")}
            if self.interrupted:
                fields["text"] = "Abgebrochen."
            elif failed or event.get("subtype") != "success":
                fields["text"] = str(event.get("result") or event.get("subtype") or "")[:2000]
                if "/login" in fields["text"]:
                    fields["text"] = NO_LOGIN
            self.interrupted = False
            self.doing = None
            emit("turn_end", **fields)
            emit("status", state="idle")

    def started(self, response: dict) -> None:
        """Antwort auf `initialize`: Version, Modell und Befehle stehen so schon vor der ersten Nachricht in der Kopfzeile."""
        data = response.get("response")
        if response.get("request_id") != INIT_ID or response.get("subtype") != "success" or not isinstance(data, dict):
            return
        fields = {"version": data.get("claude_code_version") or installed_version(self.binary), "model": resolved_model(self.model, data.get("models")) or None,
                  "commands": command_list(data.get("commands"))}
        emit("ready", agent="Claude Code", **{key: value for key, value in fields.items() if value is not None})

    def control(self, event: dict) -> None:
        request = event.get("request") or {}
        request_id = event.get("request_id")
        if request.get("subtype") != "can_use_tool":
            self.to_claude({"type": "control_response", "response": {"subtype": "error", "request_id": request_id, "error": "nicht unterstützt"}})
            return
        tool, data = request.get("tool_name"), request.get("input") or {}
        self.pending[request_id], self.tools[request_id] = data, tool
        if tool == "AskUserQuestion":
            self.answer(request_id, False, NO_QUESTIONS)
        elif self.free(tool, data):
            self.answer(request_id, True)
        else:
            folder = tool_folder(tool, data)
            if folder:
                self.asked[request_id] = folder
            emit("permission", id=request_id, tool=tool, summary=summary(tool, data) or str(request.get("description") or ""),
                 input=cut(json.dumps(data, ensure_ascii=False, indent=2)), **({"folder": folder} if folder else {}))
            emit("status", state="waiting")


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
    updates = "--updates" in argv[:-1] and argv[argv.index("--updates") + 1].lower() in ("true", "1", "yes")
    try:
        start = json.loads(sys.stdin.readline() or "{}")
    except ValueError:
        start = {}
    resume = start.get("resume") if isinstance(start.get("resume"), str) and SESSION_RE.match(start["resume"]) else None

    claude = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    if not os.access(claude, os.X_OK):
        emit("error", text="claude wurde im Container nicht gefunden. Das Addon claude-code neu einrichten (Konfiguration, »Anwenden«).")
        return 1
    command = [claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--permission-prompt-tool", "stdio"]
    if model:
        command += ["--model", model]
    if updates:
        command += ["--append-system-prompt", UPDATES_PROMPT]
    if resume:
        replay(resume)
        recorder.open(resume)
        command += ["--resume", resume]

    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding="utf-8", errors="replace")
    start_model = configured_model(model)
    bridge = Bridge(process, start_model, claude)
    emit("ready", agent="Claude Code", session=resume, settings=settings(bridge.permissions), **({"model": start_model} if start_model else {}))
    emit("status", state="idle")
    bridge.to_claude({"type": "control_request", "request_id": INIT_ID, "request": {"subtype": "initialize"}})
    errors = []
    reader = threading.Thread(target=lambda: errors.extend(process.stderr.read().strip().splitlines()[-5:]), daemon=True)
    reader.start()
    threading.Thread(target=bridge.commands, daemon=True).start()
    bridge.events()
    code = process.wait()
    if bridge.end:
        if bridge.dirty and recorder.session:
            make_title(claude, recorder.session, model)
        return 0
    reader.join(2)  # sonst fehlt die Fehlerausgabe, wenn Claude sofort endet
    if code != 0 and errors:
        emit("error", text="\n".join(errors)[:2000])
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
