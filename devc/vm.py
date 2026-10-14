"""Die VM, in der devc läuft: beschränktes Terminal und Update von devc (SPEC 8.14).

Beides richtet `vm/install.sh` ein. Fehlt die Einrichtung (devc läuft direkt auf einem Rechner), gibt es
weder die Kachel noch das Update.

Die Beschränkung ist ein Geländer, keine Sicherheitsgrenze: Wer `lxc` ausführen darf, kann in der VM alles.
Darum gilt dieselbe Regel wie für das Web-Terminal (SPEC 11.2).
"""

import hashlib
import os
import pty
import pwd
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from . import __version__
from .errors import ConflictError, EngineError, UsageError

NAME = "@vm"  # Schlüssel der Terminal-Sitzungen der VM; kein gültiger Containername
SHELL_DIR = Path("/opt/devc/vmshell")  # bin/ mit den erlaubten Befehlen, bashrc
UPDATE_HELPER = Path("/opt/devc/install/devc-update")  # läuft per sudo als root
STAGE = Path("/var/lib/devc/update/devc.pyz")  # hier holt der Helfer die neue Fassung ab
SUDO = ["sudo", "-n"]
MAX_UPDATE = 64 * 1024 * 1024
_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([0-9A-Za-z.+-]{1,40})["']""", re.MULTILINE)


def user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def shell_ready() -> bool:
    return (SHELL_DIR / "bin").is_dir() and (SHELL_DIR / "bashrc").is_file() and shutil.which("bash") is not None


def update_ready() -> bool:
    return UPDATE_HELPER.is_file() and os.access(UPDATE_HELPER, os.X_OK)


def commands() -> list[str]:
    try:
        return sorted(entry.name for entry in (SHELL_DIR / "bin").iterdir())
    except OSError:
        return []


def shell_argv() -> list[str]:
    """Bash im beschränkten Modus: nur Befehle aus `bin/`, kein `cd`, keine Umleitung in Dateien."""
    return [shutil.which("bash") or "/bin/bash", "--restricted", "--noprofile", "--rcfile", str(SHELL_DIR / "bashrc"), "-i"]


SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def shell_env(term: str = "xterm-256color") -> dict:
    # Den PATH auf `bin/` setzt die bashrc, danach ist er gesperrt. Davor liest die Bash noch /etc/bash.bashrc;
    # das braucht die Werkzeuge des Systems (sonst: »groups: command not found«).
    env = {"PATH": SYSTEM_PATH, "HOME": str(Path.home()), "USER": user(), "LOGNAME": user(), "TERM": term,
           "LANG": os.environ.get("LANG") or "C.UTF-8", "LESSSECURE": "1", "SYSTEMD_PAGERSECURE": "1", "PAGER": "less"}
    if os.environ.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = os.environ["XDG_RUNTIME_DIR"]
    return env


def _need_shell() -> None:
    if not shell_ready():
        raise ConflictError("Das VM-Terminal ist hier nicht eingerichtet (das macht vm/install.sh)")


class Shells:
    """Startet die Shells der VM an einem PTY; Gegenstück zu `exec_pty` eines Providers."""

    def __init__(self):
        self._procs = {}

    def exec_pty(self) -> tuple[int, int]:
        _need_shell()
        setsid = shutil.which("setsid")
        if setsid is None:
            raise EngineError("setsid wurde nicht im PATH gefunden")
        master, slave = pty.openpty()
        try:
            # `setsid --ctty` macht das PTY zum steuernden Terminal der Shell; ohne das gäbe es kein Strg+C.
            proc = subprocess.Popen([setsid, "--ctty", *shell_argv()], stdin=slave, stdout=slave, stderr=slave,
                                    env=shell_env(), cwd=str(Path.home()))
        except OSError as error:
            os.close(master)
            raise EngineError(f"Die Shell der VM startet nicht: {error}") from None
        finally:
            os.close(slave)
        self._procs[proc.pid] = proc
        return proc.pid, master

    def pty_wait(self, pid: int, timeout: float | None = None) -> int | None:
        proc = self._procs.get(pid)
        if proc is None:
            return None
        try:
            code = proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None
        del self._procs[pid]
        return code


shells = Shells()


def shell() -> None:
    """Ersetzt den Prozess durch die beschränkte Shell (`devc vm shell`)."""
    _need_shell()
    argv = shell_argv()
    os.chdir(Path.home())
    os.execve(argv[0], argv, shell_env(os.environ.get("TERM") or "xterm-256color"))


# --- Update -------------------------------------------------------------------


def digest(path: Path) -> str:
    found = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            found.update(chunk)
    return found.hexdigest()[:12]


def running_pyz() -> Path | None:
    """Das laufende devc.pyz; None, wenn devc aus dem Quelltext läuft."""
    for parent in Path(__file__).parents:
        if parent.is_file():
            return parent
    return None


def _own_build() -> str | None:
    current = running_pyz()
    try:
        return digest(current) if current else None
    except OSError:
        return None


# Beim Start gemerkt: Ein Update tauscht die Datei aus, während dieser Prozess noch die alte Fassung ist.
BUILD = _own_build()


def info() -> dict:
    return {"terminal": shell_ready(), "commands": commands(), "update": update_ready(), "user": user(),
            "version": __version__, "build": BUILD}


def check_pyz(path: Path) -> dict:
    """Prüft, ob die Datei ein devc.pyz ist und startet; liefert Version und Kennung."""
    wrong = UsageError("Die Datei ist kein devc.pyz")
    try:
        if path.stat().st_size > MAX_UPDATE:
            raise UsageError(f"Die Datei ist größer als erlaubt ({MAX_UPDATE // (1024 * 1024)} MiB)")
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "__main__.py" not in names or "devc/__init__.py" not in names:
                raise wrong
            found = _VERSION_RE.search(archive.read("devc/__init__.py").decode("utf-8", "replace"))
    except (OSError, zipfile.BadZipFile):
        raise wrong from None
    if found is None:
        raise wrong
    try:
        result = subprocess.run(["python3", str(path), "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise UsageError("Das hochgeladene devc.pyz startet nicht") from None
    if result.returncode != 0 or not result.stdout.startswith("devc "):
        raise UsageError("Das hochgeladene devc.pyz startet nicht")
    return {"version": found[1], "build": digest(path)}


def update(source: Path) -> dict:
    """Legt die neue Fassung bereit und startet den Helfer. Der richtet sie ein und startet den Dienst neu."""
    if not update_ready():
        raise ConflictError("Das Update ist hier nicht eingerichtet (das macht vm/install.sh)")
    new = check_pyz(source)
    old = {"version": __version__, "build": BUILD}
    if old["build"] == new["build"]:
        raise ConflictError("Diese Fassung von devc läuft schon")
    try:
        STAGE.parent.mkdir(parents=True, exist_ok=True)
        part = STAGE.with_name(STAGE.name + ".part")
        shutil.copyfile(source, part)
        os.replace(part, STAGE)
    except OSError as error:
        raise EngineError(f"Die neue Fassung lässt sich nicht bereitlegen: {error.strerror or error}") from None
    try:
        result = subprocess.run([*SUDO, str(UPDATE_HELPER)], stdin=subprocess.DEVNULL, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EngineError(f"Das Update startet nicht: {error}") from None
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        raise EngineError("Das Update startet nicht: " + (lines[-1] if lines else f"Exit {result.returncode}"))
    return {"from": old, "to": new, "started": True}
