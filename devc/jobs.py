"""Jobs: lang laufende Operationen mit ID, Zustand und Log (SPEC 8.8).

In der CLI laufen Jobs im Vordergrund; im Server laufen sie in einem eigenen Thread.
"""

import json
import os
import secrets as _random
import time

from . import store as store_module
from .errors import DevcError, InternalError

PHASES = (
    "template", "create", "mount", "secrets", "addon", "files", "git", "docs",
    "action", "snapshot", "backup", "restore", "done", "error",
)


PROGRESS_INTERVAL = 0.5


def progress_file(log_file):
    """Datei mit dem Stand des laufenden Schritts, neben dem Log des Jobs."""
    return log_file.with_suffix(".progress")


def new_id() -> str:
    return "j-" + _random.token_hex(6)


class Job:
    def __init__(self, job_id: str, kind: str, container: str | None, log_file, clock, on_line=None, on_progress=None):
        self.id = job_id
        self.kind = kind
        self.container = container
        self.log_file = log_file
        self.progress_file = progress_file(log_file)
        self.warnings: list[str] = []
        self._clock = clock
        self._on_line = on_line
        self._on_progress = on_progress
        self._shown = (None, None, 0.0)  # Beschriftung, Prozent, Zeitpunkt der letzten Meldung

    def log(self, phase: str, text: str) -> None:
        """Schreibt `[HH:MM:SS] <phase> <text>`. Aufrufer dürfen hier nie Secret-Werte übergeben."""
        stamp = self._clock().astimezone().strftime("%H:%M:%S")
        for part in str(text).splitlines() or [""]:
            line = f"[{stamp}] {phase:<8} {part}"
            with open(self.log_file, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            if self._on_line is not None:
                self._on_line(line)

    def warn(self, phase: str, text: str) -> None:
        self.warnings.append(text)
        self.log(phase, text)

    def progress(self, label: str, done: int, total: int | None = None, exact: bool = False) -> None:
        """Meldet den Stand eines langen Schritts (SPEC 8.8): Bytes bisher und, wenn bekannt, insgesamt.

        Der Stand steht nicht im Log, sondern in einer eigenen Datei neben dem Log, die jede Meldung
        ersetzt. Gemeldet wird höchstens zweimal je Sekunde oder wenn sich die Prozentzahl ändert.
        Ohne `exact` ist `total` eine Schätzung: Die Prozentzahl zeigt dann höchstens 99.
        """
        percent = None
        if total:
            percent = min(100 if exact else 99, done * 100 // total)
        now = time.monotonic()
        if (label, percent) == self._shown[:2] and now - self._shown[2] < PROGRESS_INTERVAL:
            return
        self._shown = (label, percent, now)
        data = {"label": label, "done": int(done), "total": int(total) if total else None, "percent": percent, "exact": bool(exact and total)}
        tmp = self.progress_file.with_name(self.progress_file.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.progress_file)
        if self._on_progress is not None:
            self._on_progress(data)

    def progress_end(self) -> None:
        """Der lange Schritt ist vorbei: Stand entfernen."""
        if self._shown[0] is None and not self.progress_file.exists():
            return
        self._shown = (None, None, 0.0)
        self.progress_file.unlink(missing_ok=True)
        if self._on_progress is not None:
            self._on_progress(None)


def run(config, store, kind: str, container: str | None, func, clock, on_line=None, job_id: str | None = None, on_start=None, on_progress=None):
    """Führt `func(job)` als Job aus und liefert dessen Ergebnis.

    Ein Fehler setzt den Job auf `failed` und wird mit `details.job` weitergereicht.
    """
    job_id = job_id or new_id()
    config.paths.logs.mkdir(parents=True, exist_ok=True)
    job = Job(job_id, kind, container, config.paths.job_log(job_id), clock, on_line, on_progress)
    store.job_add(job_id, container, kind, "running", store_module.iso(clock()))
    if on_start is not None:
        on_start(job_id)
    try:
        try:
            result = func(job)
        finally:
            job.progress_end()
    except DevcError as error:
        job.log("error", f"{error.code}: {error.message}")
        store.job_update(job_id, "failed", error.code, finished_at=store_module.iso(clock()))
        error.details.setdefault("job", job_id)
        raise
    except BaseException as error:
        # Nur der Typ: die Meldung einer fremden Ausnahme könnte Secret-Werte enthalten.
        job.log("error", f"E_INTERNAL: {type(error).__name__}")
        store.job_update(job_id, "failed", "E_INTERNAL", finished_at=store_module.iso(clock()))
        if isinstance(error, Exception):
            raise InternalError(f"Unerwarteter Fehler ({type(error).__name__})", {"job": job_id}) from error
        raise
    job.log("done", "fertig")
    store.job_update(job_id, "ok", finished_at=store_module.iso(clock()))
    return result, job


def events(config, store, job_id: str, sleep, poll: float = 0.3):
    """Ereignisse eines Jobs: ("log", zeile), dann ("state", {...}), dann ("end", None).

    Dazwischen ("progress", {...}) bei jedem neuen Stand eines langen Schritts und ("progress", None),
    wenn der Schritt vorbei ist.

    Liest das Log von der Platte und fragt den Zustand ab; funktioniert damit für Jobs des
    Servers wie der CLI. Ein beendeter Job liefert das gesamte Log, dann state und end.
    """
    log_file = config.paths.job_log(job_id)
    offset = 0
    pending = b""
    shown = None
    while True:
        row = store.job(job_id)
        finished = row is None or row["state"] in ("ok", "failed")
        try:
            with open(log_file, "rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
        except FileNotFoundError:
            chunk = b""
        offset += len(chunk)
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for line in lines:
            yield "log", line.decode("utf-8", "replace")
        try:
            current = progress_file(log_file).read_text(encoding="utf-8")
        except OSError:
            current = None
        if current != shown:
            shown = current
            try:
                yield "progress", json.loads(current) if current else None
            except ValueError:
                pass
        if finished and not chunk:
            if pending:
                yield "log", pending.decode("utf-8", "replace")
            yield "state", {"state": row["state"] if row else "failed", "error": row["error_code"] if row else "E_NOT_FOUND"}
            yield "end", None
            return
        if not chunk:
            sleep(poll)
