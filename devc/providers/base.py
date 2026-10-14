"""Provider-Schnittstelle (SPEC 8.2).

Alle Methoden werfen bei Engine-Problemen EngineError (E_ENGINE) und bei
fehlendem Objekt NotFoundError (E_NOT_FOUND).
"""

# Die Methode `list` verdeckt im Klassenrumpf den Typ list; Annotationen daher nicht auswerten.
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

AREAS = ("work", "home")
USERS = {"dev": (1000, 1000, "/home/dev"), "root": (0, 0, "/root")}


@dataclass(frozen=True)
class ContainerState:
    name: str
    status: str  # running, stopped oder der kleingeschriebene Engine-Status
    ip: str | None = None
    created: str | None = None


@dataclass(frozen=True)
class CreateSpec:
    name: str
    image: str
    cpu: int
    memory: str
    disk: str
    shared: str | None = None  # Host-Pfad für /work; None = Code-Volume


@dataclass(frozen=True)
class ExecResult:
    rc: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class Volume:
    name: str
    snapshots: tuple[str, ...] = field(default_factory=tuple)


class Provider(ABC):
    name = ""

    @abstractmethod
    def check(self) -> list:
        """Prüfpunkte für `doctor` als Liste von Finding (severity ok/warning/error)."""

    @abstractmethod
    def init_project(self) -> list[str]:
        """Projekt, Profil und Restriktionen anlegen (idempotent); liefert die Schritte."""

    @abstractmethod
    def list(self) -> list[ContainerState]: ...

    @abstractmethod
    def state(self, name: str) -> ContainerState: ...

    @abstractmethod
    def create(self, spec: CreateSpec) -> None:
        """Container und Volumes anlegen, nicht starten."""

    @abstractmethod
    def start(self, name: str) -> None: ...

    @abstractmethod
    def stop(self, name: str, timeout: int = 30) -> None: ...

    @abstractmethod
    def delete(self, name: str, keep_data: bool) -> None: ...

    @abstractmethod
    def exec(self, name, argv, user="dev", cwd="/work", env=None, stdin=None, timeout=None, stdout=None) -> ExecResult:
        """Befehl im Container. `stdin`: Bytes oder Dateiobjekt. `stdout`: Dateiobjekt für Rohdaten
        (dann ist ExecResult.stdout leer) – für Datenströme wie tar."""

    @abstractmethod
    def exec_stream(self, name, argv, user="dev", cwd="/work", env=None, on_line=None) -> int: ...

    @abstractmethod
    def exec_pipe(self, name, argv, user="dev", cwd="/work"):
        """Lang laufender Befehl mit Pipes; liefert ein `subprocess.Popen` mit `stdin` und `stdout`
        (Bytes, stderr in stdout). Für Kacheln der Art chat (SPEC 8.13)."""

    @abstractmethod
    def exec_pty(self, name, argv, user="dev", cwd="/work") -> tuple[int, int]:
        """Interaktive Sitzung; liefert (pid, master_fd)."""

    @abstractmethod
    def pty_wait(self, pid: int, timeout: float | None = None) -> int | None:
        """Wartet auf das Ende einer exec_pty-Sitzung; liefert den Exit-Code oder None."""

    @abstractmethod
    def shell_argv(self, name, user="dev", cwd="/work") -> list[str]:
        """Argumentliste für `devc shell` (os.execvp)."""

    @abstractmethod
    def push(self, name, host_path, dest, mode="0644", uid=1000, gid=1000) -> None: ...

    @abstractmethod
    def pull(self, name, src, host_path) -> None: ...

    @abstractmethod
    def set_limits(self, name, cpu, memory, disk) -> None: ...

    @abstractmethod
    def volumes(self) -> list[Volume]:
        """Von devc angelegte Volumes (`devc-…`) im Projekt, für Waisen-Prüfung W3."""

    @abstractmethod
    def volume_snapshot(self, name, area, snap) -> None: ...

    @abstractmethod
    def volume_restore(self, name, area, snap) -> None: ...

    @abstractmethod
    def volume_snapshot_delete(self, name, area, snap) -> None: ...

    @abstractmethod
    def full_snapshot(self, name, snap) -> None: ...

    @abstractmethod
    def full_restore(self, name, snap) -> None: ...

    @abstractmethod
    def full_snapshot_delete(self, name, snap) -> None: ...
