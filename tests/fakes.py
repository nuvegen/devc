"""FakeProvider und gefälschtes `lxc` für Tests ohne echtes LXD (SPEC 12)."""

import json
import os
import stat
import sys
from pathlib import Path

from devc.errors import NotFoundError
from devc.providers.base import ContainerState, ExecResult, Provider

FAKE_LXC = '''#!{python}
import json, os, sys
directory = os.environ["FAKE_LXC_DIR"]
args = sys.argv[1:]
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
with open(os.path.join(directory, "calls.jsonl"), "a") as handle:
    handle.write(json.dumps({{"argv": args, "stdin": stdin}}) + "\\n")
with open(os.path.join(directory, "responses.json")) as handle:
    rules = json.load(handle)
for rule in rules:
    match = rule["match"]
    if any(args[i:i + len(match)] == match for i in range(len(args) - len(match) + 1)):
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("rc", 0))
'''


class FakeLxc:
    """Legt ein Skript `lxc` an, das Aufrufe protokolliert und Antworten aus Regeln liefert.

    Eine Regel passt, wenn ihre `match`-Liste zusammenhängend in den Argumenten vorkommt;
    die erste passende Regel gewinnt. Ohne Regel: Exit 0, keine Ausgabe.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "lxc"
        self.path.write_text(FAKE_LXC.format(python=sys.executable), encoding="utf-8")
        self.path.chmod(self.path.stat().st_mode | stat.S_IXUSR)
        self.rules = []
        self._write()
        self._saved = (os.environ.get("PATH"), os.environ.get("FAKE_LXC_DIR"))
        os.environ["PATH"] = f"{self.directory}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["FAKE_LXC_DIR"] = str(self.directory)

    def close(self):
        for key, value in zip(("PATH", "FAKE_LXC_DIR"), self._saved):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _write(self):
        (self.directory / "responses.json").write_text(json.dumps(self.rules), encoding="utf-8")

    def on(self, match, stdout="", stderr="", rc=0, first=False):
        if not isinstance(stdout, str):
            stdout = json.dumps(stdout)
        rule = {"match": list(match), "stdout": stdout, "stderr": stderr, "rc": rc}
        if first:
            self.rules.insert(0, rule)
        else:
            self.rules.append(rule)
        self._write()

    def reset(self):
        self.rules = []
        self._write()
        self.clear()

    def clear(self):
        (self.directory / "calls.jsonl").unlink(missing_ok=True)

    def records(self):
        file = self.directory / "calls.jsonl"
        if not file.exists():
            return []
        return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()]

    def calls(self):
        return [record["argv"] for record in self.records()]


# Bridge für Tests: meldet die Startzeile zurück, beantwortet jede Nachricht und kennt ein paar Sonderwörter.
PIPE_ECHO = r'''
import json, sys
def emit(**event):
    sys.stdout.write(json.dumps(event) + "\n"); sys.stdout.flush()
start = json.loads(sys.stdin.readline())
emit(type="ready", agent="Echo", session=start.get("resume") or "s-1", start=start)
emit(type="status", state="idle")
for line in sys.stdin:
    command = json.loads(line)
    if command["type"] == "send":
        text = command["text"]
        emit(type="user", text=text)
        if text == "frag":
            emit(type="permission", id="p-1", tool="Bash", summary="ls")
            continue
        if text == "müll":
            sys.stdout.write("kein json\n"); sys.stdout.flush()
            continue
        if text == "ende":
            break
        emit(type="text_delta", text="Echo: ")
        if text == "halb":
            continue
        emit(type="text", text="Echo: " + text)
        emit(type="turn_end", ok=True)
    elif command["type"] == "permit":
        emit(type="permission_done", id=command["id"], allow=command["allow"], always=command.get("always", False))
    elif command["type"] == "interrupt":
        emit(type="turn_end", ok=False, text="abgebrochen")
    elif command["type"] == "end":
        emit(type="status", state="ending")
        emit(type="text", text="Titel geschrieben")
        break
'''


class FakeProvider(Provider):
    """Zeichnet Aufrufe auf und simuliert Zustände."""

    name = "lxd"

    def __init__(self):
        self.calls = []
        self.containers = {}  # name -> ContainerState
        self.volume_list = []
        self.findings = []
        self.init_steps = []
        self.exec_results = []  # (argv-Präfix, ExecResult); erster Treffer gewinnt
        self.fail = {}  # Methodenname -> Exception
        self.files = {}  # name -> {Pfad: Inhalt}
        self.pids = {}  # name -> {PID-Datei: PID} laufender detach-Aktionen
        self.next_pid = 400
        self.ptys = {}
        self.areas = {}  # name -> {Verzeichnis: tar.gz-Bytes}
        self.realpaths = {}  # Pfad -> Ergebnis von realpath -m (für Symlink-Fälle)
        self.blobs = {}  # name -> {Pfad: Bytes}, für `cat <pfad>` in ein Dateiobjekt
        self.unpacked = {}  # name -> [(Zielordner, tar-Bytes)]
        import gzip, io, tarfile
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz"):
            pass
        self.empty_tar = buffer.getvalue()
        self.secrets = {}  # name -> {Pfad: Rohbytes}
        self.stream_rc = {}  # Skriptname -> Exit-Code für exec_stream
        self.pipe_script = PIPE_ECHO  # Python-Quelltext, der bei exec_pipe statt des Befehls läuft
        self.pipes = []

    def _call(self, method, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        if method in self.fail:
            raise self.fail[method]

    def called(self, method):
        return [call for call in self.calls if call[0] == method]

    def add(self, name, status="running", ip="10.0.0.5"):
        self.containers[name] = ContainerState(name, status, ip if status == "running" else None, "2026-10-10T07:00:00Z")

    def _need(self, name):
        if name not in self.containers:
            raise NotFoundError(f"Container fehlt: {name}")
        return self.containers[name]

    def check(self):
        self._call("check")
        return list(self.findings)

    def init_project(self):
        self._call("init_project")
        return list(self.init_steps)

    def list(self):
        self._call("list")
        return list(self.containers.values())

    def state(self, name):
        self._call("state", name)
        return self._need(name)

    def create(self, spec):
        self._call("create", spec)
        self.add(spec.name, "stopped")

    def start(self, name):
        self._call("start", name)
        self._need(name)
        self.add(name, "running")

    def stop(self, name, timeout=30):
        self._call("stop", name, timeout)
        self._need(name)
        self.add(name, "stopped")

    def delete(self, name, keep_data):
        self._call("delete", name, keep_data)
        self.containers.pop(name, None)

    def exec(self, name, argv, user="dev", cwd="/work", env=None, stdin=None, timeout=None, stdout=None):
        if stdin is not None and not isinstance(stdin, (bytes, bytearray, str)):
            stdin = stdin.read()  # Dateiobjekt
        self._call("exec", name, list(argv), user=user, cwd=cwd, env=dict(env or {}), stdin=stdin, timeout=timeout)
        self._need(name)
        data = self._simulate_data(name, list(argv), stdin, stdout)
        if data is not None:
            return data
        simulated = self._simulate(name, list(argv), stdin)
        if simulated is not None:
            return simulated
        for prefix, result in self.exec_results:
            if list(argv)[: len(prefix)] == list(prefix):
                return result
        return ExecResult(0, "", "")

    def _simulate_data(self, name, argv, stdin, stdout):
        """Datenbereiche als tar.gz: `tar -C <dir> -czf - .` liefert, `tar -xzf - -C <dir>` nimmt entgegen."""
        areas = self.areas.setdefault(name, {})
        if argv[:2] == ["tar", "-C"] and "-czf" in argv:
            stdout.write(areas.get(argv[2], self.empty_tar))
            return ExecResult(0)
        if argv[:2] == ["tar", "-C"] and "-cf" in argv:  # Backup: unkomprimiert, gepackt wird in devc
            import gzip

            stdout.write(gzip.decompress(areas.get(argv[2], self.empty_tar)))
            return ExecResult(0)
        from devc import core

        if argv[:2] == ["sh", "-c"] and argv[2] == core._RESTORE_AREA:
            areas[argv[4]] = stdin
            return ExecResult(0)
        if argv[:2] == ["sh", "-c"] and argv[2] == core._CAT_FILE and stdout is not None:
            blob = self.blobs.get(name, {}).get(argv[4])
            if blob is None:
                return ExecResult(44)
            stdout.write(blob)
            return ExecResult(0)
        if argv[:2] == ["sh", "-c"] and argv[2] == core._UNPACK:
            self.unpacked.setdefault(name, []).append((argv[4], stdin))
            return ExecResult(0)
        if argv[:2] == ["realpath", "-m"]:
            import posixpath

            return ExecResult(0, self.realpaths.get(argv[2], posixpath.normpath(argv[2])) + "\n")
        return None

    def _simulate(self, name, argv, stdin):
        """Bildet die Datei-Befehle aus core nach, damit Tests Inhalte prüfen können."""
        from devc import core, secrets

        from devc import actions

        files = self.files.setdefault(name, {})
        pids = self.pids.setdefault(name, {})
        if argv[:2] == ["bash", "-lc"] and "setsid nohup" in argv[2]:
            pid_file = argv[2].rsplit("> ", 1)[1]
            self.next_pid += 1
            pids[pid_file] = self.next_pid
            return ExecResult(0)
        if argv[:2] != ["sh", "-c"]:
            return None
        if argv[2] == actions._STATUS:
            return ExecResult(0, f"{pids[argv[4]]}\n") if argv[4] in pids else ExecResult(3)
        if argv[2] == actions._STOP:
            return ExecResult(0) if pids.pop(argv[4], None) else ExecResult(3)
        script, args = argv[2], argv[4:]
        if script == core._WRITE_FILE:
            if args[2] == "false" and args[0] in files:
                return ExecResult(0)
            files[args[0]] = stdin.decode("utf-8")
            return ExecResult(0)
        if script.startswith('if [ -e "$1" ]; then cat "$1"'):
            if args[0] in files:
                return ExecResult(0, files[args[0]])
            return ExecResult(44)
        if "ln -sfn CLAUDE.md AGENTS.md" in script:
            if files.get("/work/AGENTS.md", "-> CLAUDE.md") != "-> CLAUDE.md":
                return ExecResult(45)
            files["/work/AGENTS.md"] = "-> CLAUDE.md"
            return ExecResult(0)
        if script == secrets._PUSH:
            self.secrets.setdefault(name, {})[args[0]] = stdin
            return ExecResult(0)
        if "/etc/devc/addons.toml" in script:
            files["/etc/devc/addons.toml"] = stdin.decode("utf-8")
            return ExecResult(0)
        return None

    def exec_stream(self, name, argv, user="dev", cwd="/work", env=None, on_line=None):
        self._call("exec_stream", name, list(argv), user=user, cwd=cwd, env=dict(env or {}))
        self._need(name)
        for prefix, result in self.exec_results:
            if list(argv)[: len(prefix)] == list(prefix):
                for line in result.stdout.splitlines():
                    if on_line:
                        on_line(line)
                return result.rc
        if on_line:
            on_line(f"läuft: {argv[-1]}")
        return self.stream_rc.get(argv[-1], 0)

    def exec_pipe(self, name, argv, user="dev", cwd="/work"):
        """Startet lokal ein Python-Skript mit Pipes, das eine Chat-Bridge spielt."""
        import subprocess

        self._call("exec_pipe", name, list(argv), user=user, cwd=cwd)
        self._need(name)
        proc = subprocess.Popen([sys.executable, "-c", self.pipe_script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
        self.pipes.append(proc)
        return proc

    def exec_pty(self, name, argv, user="dev", cwd="/work"):
        """Startet lokal einen Prozess an einem PTY, der Eingaben zurückgibt wie eine Shell mit Echo."""
        import pty
        import subprocess

        self._call("exec_pty", name, list(argv), user=user, cwd=cwd)
        self._need(name)
        master, slave = pty.openpty()
        # Gibt Eingaben zurück und meldet bei SIGWINCH die Größe des Terminals, wie es `lxc exec` weiterreicht.
        script = (
            "import os, signal\n"
            "def winch(*_):\n"
            "    size = os.get_terminal_size(0)\n"
            "    os.write(1, f'SIZE {size.lines}x{size.columns}\\n'.encode())\n"
            "signal.signal(signal.SIGWINCH, winch)\n"
            "while True:\n"
            "    data = os.read(0, 1024)\n"
            "    if not data:\n"
            "        break\n"
            "    os.write(1, data)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", script], stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        self.ptys[proc.pid] = proc
        return proc.pid, master

    def pty_wait(self, pid, timeout=None):
        self._call("pty_wait", pid)
        proc = self.ptys.pop(pid, None)
        return proc.wait(timeout) if proc else None

    def shell_argv(self, name, user="dev", cwd="/work"):
        self._call("shell_argv", name, user=user, cwd=cwd)
        return ["lxc", "--project", "devcontainers", "exec", name, "-t", "--", "bash", "-l"]

    def push(self, name, host_path, dest, mode="0644", uid=1000, gid=1000):
        self._call("push", name, str(host_path), dest, mode=mode, uid=uid, gid=gid)

    def pull(self, name, src, host_path):
        self._call("pull", name, src, str(host_path))

    def set_limits(self, name, cpu, memory, disk):
        self._call("set_limits", name, cpu, memory, disk)

    def volumes(self):
        self._call("volumes")
        return list(self.volume_list)

    def volume_snapshot(self, name, area, snap):
        self._call("volume_snapshot", name, area, snap)

    def volume_restore(self, name, area, snap):
        self._call("volume_restore", name, area, snap)

    def volume_snapshot_delete(self, name, area, snap):
        self._call("volume_snapshot_delete", name, area, snap)

    def full_snapshot(self, name, snap):
        self._call("full_snapshot", name, snap)

    def full_restore(self, name, snap):
        self._call("full_restore", name, snap)

    def full_snapshot_delete(self, name, snap):
        self._call("full_snapshot_delete", name, snap)
