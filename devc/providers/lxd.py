"""LXD-Provider: steuert LXD ausschließlich über `lxc` mit Argumentlisten (SPEC 8.3)."""

# Die Methode `list` verdeckt im Klassenrumpf den Typ list; Annotationen daher nicht auswerten.
from __future__ import annotations

import json
import logging
import os
import pty
import shutil
import subprocess
from urllib.parse import quote

from .. import names
from ..errors import EngineError, Finding, NotFoundError
from .base import AREAS, USERS, ContainerState, CreateSpec, ExecResult, Provider, Volume

log = logging.getLogger("devc.lxc")

CONTAINER_UID = 1000
MOUNTS = {"work": "/work", "home": "/home/dev"}
# Schlüssel, die `devc init` bei einem bestehenden Projekt angleicht
SYNCED_KEYS = (
    "restricted.snapshots",
    "restricted.devices.disk",
    "restricted.devices.disk.paths",
    "restricted.idmap.uid",
    "restricted.idmap.gid",
)


def _finding(point: str, severity: str, message: str, check: str = "doctor") -> Finding:
    return Finding(check, point, message, severity)


class LxdProvider(Provider):
    name = "lxd"

    def __init__(self, project, pool, network, shared_mode="shift", allowed_roots=(), uid=1000, gid=1000, lxc="lxc"):
        self.project = project
        self.pool = pool
        self.network = network
        self.shared_mode = shared_mode
        self.allowed_roots = list(allowed_roots)
        self.uid = uid
        self.gid = gid
        self.lxc = lxc
        self._ptys = {}

    # --- Aufruf von lxc -------------------------------------------------

    def _argv(self, args, scoped=True) -> list[str]:
        prefix = [self.lxc, "--project", self.project] if scoped else [self.lxc]
        return prefix + [str(arg) for arg in args]

    def _run(self, *args, scoped=True, stdin=None, timeout=None, check=True, stdout=None) -> ExecResult:
        argv = self._argv(args, scoped)
        # Nur die Argumentliste loggen; stdin kann Secret-Werte tragen.
        log.info("%s", " ".join(argv))
        if isinstance(stdin, str):
            stdin = stdin.encode("utf-8")
        options = {"stdout": subprocess.PIPE if stdout is None else stdout, "stderr": subprocess.PIPE, "timeout": timeout}
        if stdin is None:
            options["stdin"] = subprocess.DEVNULL
        elif isinstance(stdin, (bytes, bytearray)):
            options["input"] = stdin
        else:
            options["stdin"] = stdin  # Dateiobjekt: Daten werden durchgereicht, nicht in den Speicher geladen
        try:
            proc = subprocess.run(argv, **options)
        except FileNotFoundError:
            raise EngineError("lxc wurde nicht im PATH gefunden") from None
        except subprocess.TimeoutExpired:
            raise EngineError(f"lxc {args[0]}: Zeitüberschreitung nach {timeout} s", {"timeout": True}) from None
        except OSError as exc:
            raise EngineError(f"lxc ließ sich nicht starten: {exc.strerror}") from None
        result = ExecResult(
            proc.returncode,
            (proc.stdout or b"").decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
        )
        if check and result.rc != 0:
            raise self._error(args, result)
        return result

    @staticmethod
    def _error(args, result: ExecResult):
        lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        # lxc hängt an manche Fehler einen Hinweis an; die eigentliche Meldung beginnt mit "Error:".
        errors = [line for line in lines if line.startswith("Error: ")]
        message = errors[-1] if errors else (lines[-1] if lines else f"Exit {result.rc}")
        if message.startswith("Error: "):
            message = message[len("Error: "):]
        command = " ".join(str(a) for a in args[:2])
        if "not found" in message.lower():
            return NotFoundError(f"lxc {command}: {message}")
        return EngineError(f"lxc {command}: {message}")

    def _json(self, *args, scoped=True):
        result = self._run(*args, scoped=scoped)
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise EngineError(f"lxc {args[0]}: Ausgabe ist kein JSON") from None

    def _query(self, path: str):
        """GET auf die LXD-API; das Projekt steht ausdrücklich in der URL."""
        return self._json("query", path, scoped=False)

    # --- Projekt --------------------------------------------------------

    def project_config(self) -> dict[str, str]:
        config = {
            "features.images": "false",
            "features.profiles": "true",
            "features.storage.volumes": "true",
            "features.networks": "false",
            "restricted": "true",
            # Ohne dies verbietet ein eingeschränktes Projekt Volume- und Container-Snapshots.
            "restricted.snapshots": "allow",
        }
        if self.allowed_roots:
            config["restricted.devices.disk"] = "allow"
            config["restricted.devices.disk.paths"] = ",".join(self.allowed_roots)
        else:
            # Ein leeres restricted.devices.disk.paths würde alle Host-Pfade erlauben.
            config["restricted.devices.disk"] = "managed"
        if self.shared_mode == "idmap":
            config["restricted.idmap.uid"] = str(self.uid)
            config["restricted.idmap.gid"] = str(self.gid)
        return config

    def _projects(self) -> dict:
        return {p["name"]: p for p in self._json("project", "list", "--format", "json", scoped=False)}

    def _profile_devices(self) -> dict:
        profile = self._query(f"/1.0/profiles/default?project={quote(self.project, safe='')}")
        return profile.get("devices") or {}

    def _restriction_diff(self, have: dict) -> list[tuple[str, str | None]]:
        want = self.project_config()
        diff = []
        for key in SYNCED_KEYS:
            if have.get(key) != want.get(key):
                diff.append((key, want.get(key)))
        return diff

    def init_project(self) -> list[str]:
        steps = []
        projects = self._projects()
        if self.project not in projects:
            args = ["project", "create", self.project]
            for key, value in self.project_config().items():
                args += ["-c", f"{key}={value}"]
            self._run(*args, scoped=False)
            steps.append(f"Projekt {self.project} angelegt")
        else:
            # Reihenfolge so, dass nie disk=allow ohne Pfadliste aktiv ist (das hieße: alle Host-Pfade):
            # erst setzen (Pfadliste vor disk), zuletzt entfernen.
            diff = self._restriction_diff(projects[self.project].get("config") or {})
            for key, value in sorted(diff, key=lambda item: (item[1] is None, not item[0].endswith(".paths"))):
                if value is None:
                    self._run("project", "unset", self.project, key, scoped=False)
                else:
                    self._run("project", "set", self.project, key, value, scoped=False)
                steps.append(f"Projekt {self.project}: {key} angeglichen")
        devices = self._profile_devices()
        if "root" not in devices:
            self._run("profile", "device", "add", "default", "root", "disk", "path=/", f"pool={self.pool}")
            steps.append("Profil default: Root-Disk ergänzt")
        if "eth0" not in devices:
            self._run("profile", "device", "add", "default", "eth0", "nic", f"network={self.network}", "name=eth0")
            steps.append("Profil default: Netz eth0 ergänzt")
        return steps

    def check(self) -> list[Finding]:
        found = []
        if shutil.which(self.lxc) is None:
            return [_finding("lxc", "error", "lxc wurde nicht im PATH gefunden")]
        try:
            server = self._query("/1.0")
        except EngineError as exc:
            return [_finding("lxc", "error", f"LXD ist nicht erreichbar: {exc.message}")]
        environment = server.get("environment") or {}
        if server.get("auth") != "trusted":
            return [_finding("lxc", "error", "Kein Zugriff auf LXD (Nutzer in Gruppe lxd?)")]
        found.append(_finding("lxc", "ok", f"LXD {environment.get('server_version', '?')} erreichbar"))

        try:
            projects = self._projects()
            exists = self.project in projects
            if not exists:
                found.append(_finding("project", "error", f"Projekt {self.project} fehlt; devc init ausführen"))
            else:
                diff = self._restriction_diff(projects[self.project].get("config") or {})
                if diff:
                    keys = ", ".join(key for key, _ in diff)
                    found.append(_finding("project", "warning", f"Projekt {self.project}: {keys} weicht ab; devc init ausführen"))
                else:
                    found.append(_finding("project", "ok", f"Projekt {self.project} vorhanden"))
                missing = [d for d in ("root", "eth0") if d not in self._profile_devices()]
                if missing:
                    found.append(_finding("project", "error", f"Profil default ohne {', '.join(missing)}; devc init ausführen"))

            pools = [p["name"] for p in self._json("storage", "list", "--format", "json", scoped=exists)]
            if self.pool in pools:
                found.append(_finding("pool", "ok", f"Storage-Pool {self.pool} vorhanden"))
            else:
                found.append(_finding("pool", "error", f"Storage-Pool {self.pool} fehlt; lxd.storage_pool anpassen"))

            networks = {n["name"]: n for n in self._json("network", "list", "--format", "json", scoped=exists)}
            if networks.get(self.network, {}).get("managed"):
                found.append(_finding("network", "ok", f"Netz {self.network} vorhanden"))
            else:
                found.append(_finding("network", "error", f"Netz {self.network} fehlt; lxd.network anpassen"))
        except EngineError as exc:
            found.append(_finding("lxc", "error", exc.message))

        shift = (environment.get("kernel_features") or {}).get("idmapped_mounts") == "true"
        if self.shared_mode == "shift" and self.allowed_roots:
            # Auf LXD 6.9 geprüft: "The shift property cannot be used with a restricted source path".
            found.append(_finding(
                "shift", "warning",
                'Shared Folder mit shift=true lehnt LXD im eingeschränkten Projekt ab; lxd.shared_mode = "idmap" setzen und devc init ausführen',
            ))
        elif shift:
            found.append(_finding("shift", "ok", "Kernel unterstützt idmapped mounts (shift)"))
        elif self.shared_mode == "shift":
            found.append(_finding("shift", "warning", 'Kein shift (idmapped mounts); lxd.shared_mode = "idmap" verwenden'))
        else:
            found.append(_finding("shift", "ok", "Kein shift, shared_mode ist idmap"))
        return found

    # --- Container ------------------------------------------------------

    @staticmethod
    def _state(instance: dict) -> ContainerState:
        ip = None
        network = (instance.get("state") or {}).get("network") or {}
        for address in (network.get("eth0") or {}).get("addresses") or []:
            if address.get("family") == "inet" and address.get("scope") == "global":
                ip = address.get("address")
                break
        return ContainerState(
            name=instance["name"],
            status=str(instance.get("status", "")).lower(),
            ip=ip,
            created=instance.get("created_at"),
        )

    def list(self) -> list[ContainerState]:
        return [self._state(i) for i in self._json("list", "--format", "json")]

    def state(self, name: str) -> ContainerState:
        # `lxc list <name>` filtert nach Präfix, darum hier exakt vergleichen.
        for instance in self._json("list", name, "--format", "json"):
            if instance.get("name") == name:
                return self._state(instance)
        raise NotFoundError(f"Container fehlt: {name}")

    def create(self, spec: CreateSpec) -> None:
        areas = ["home"] if spec.shared else ["work", "home"]
        for area in areas:
            try:
                self._run("storage", "volume", "create", self.pool, names.volume_name(spec.name, area))
            except EngineError as exc:
                # Nach `rm --keep-data` sind die Volumes noch da und werden wiederverwendet.
                if "already exists" not in exc.message:
                    raise
        self._run(
            "init", spec.image, spec.name,
            "-c", f"limits.cpu={spec.cpu}",
            "-c", f"limits.memory={spec.memory}",
            "-d", f"root,size={spec.disk}",
        )
        if spec.shared:
            device = ["config", "device", "add", spec.name, "work", "disk", f"source={spec.shared}", "path=/work"]
            if self.shared_mode == "idmap":
                self._run(*device)
                self._run("config", "set", spec.name, "raw.idmap", f"both {self.uid} {CONTAINER_UID}")
            else:
                self._run(*device, "shift=true")
        for area in areas:
            self._run(
                "config", "device", "add", spec.name, area, "disk",
                f"pool={self.pool}", f"source={names.volume_name(spec.name, area)}", f"path={MOUNTS[area]}",
            )

    def start(self, name: str) -> None:
        self._run("start", name)

    def stop(self, name: str, timeout: int = 30) -> None:
        try:
            self._run("stop", name, "--timeout", timeout)
        except NotFoundError:
            raise
        except EngineError as exc:
            if "already stopped" in exc.message:
                return
            self._run("stop", name, "--force")

    def delete(self, name: str, keep_data: bool) -> None:
        try:
            self._run("delete", name, "--force")
        except NotFoundError:
            pass  # Aufräumen nach abgebrochener Anlage: Volumes trotzdem behandeln
        if keep_data:
            return
        for area in AREAS:
            try:
                self._run("storage", "volume", "delete", self.pool, names.volume_name(name, area))
            except NotFoundError:
                pass  # bei Shared Folder gibt es kein Code-Volume

    def set_limits(self, name, cpu, memory, disk) -> None:
        self._run("config", "set", name, f"limits.cpu={cpu}", f"limits.memory={memory}")
        self._run("config", "device", "set", name, "root", f"size={disk}")

    # --- Befehle und Dateien -------------------------------------------

    def _exec_args(self, name, argv, user, cwd, env, tty=False) -> list[str]:
        uid, gid, home = USERS[user]
        args = ["exec", name]
        if tty:
            args.append("-t")
        args += ["--user", str(uid), "--group", str(gid), "--cwd", cwd, "--env", f"HOME={home}"]
        for key, value in (env or {}).items():
            args += ["--env", f"{key}={value}"]
        return args + ["--", *argv]

    def exec(self, name, argv, user="dev", cwd="/work", env=None, stdin=None, timeout=None, stdout=None) -> ExecResult:
        return self._run(
            *self._exec_args(name, argv, user, cwd, env), stdin=stdin, timeout=timeout, check=False, stdout=stdout
        )

    def exec_stream(self, name, argv, user="dev", cwd="/work", env=None, on_line=None) -> int:
        full = self._argv(self._exec_args(name, argv, user, cwd, env))
        log.info("%s", " ".join(full))
        try:
            proc = subprocess.Popen(
                full,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            raise EngineError("lxc wurde nicht im PATH gefunden") from None
        with proc:
            for line in proc.stdout:
                if on_line is not None:
                    on_line(line.rstrip("\r\n"))
        return proc.returncode

    def exec_pipe(self, name, argv, user="dev", cwd="/work"):
        full = self._argv(self._exec_args(name, argv, user, cwd, None))
        log.info("%s", " ".join(full))
        try:
            return subprocess.Popen(full, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except FileNotFoundError:
            raise EngineError("lxc wurde nicht im PATH gefunden") from None

    def shell_argv(self, name, user="dev", cwd="/work") -> list[str]:
        return self._argv(self._exec_args(name, ["bash", "-l"], user, cwd, None, tty=True))

    def exec_pty(self, name, argv, user="dev", cwd="/work") -> tuple[int, int]:
        # Das Web-Terminal ist xterm.js. `lxc exec` reicht sonst das TERM seines Aufrufers weiter; der Dienst hat keins.
        full = self._argv(self._exec_args(name, argv, user, cwd, {"TERM": "xterm-256color"}, tty=True))
        log.info("%s", " ".join(full))
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(full, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        except FileNotFoundError:
            os.close(master)
            raise EngineError("lxc wurde nicht im PATH gefunden") from None
        finally:
            os.close(slave)
        self._ptys[proc.pid] = proc
        return proc.pid, master

    def pty_wait(self, pid: int, timeout: float | None = None) -> int | None:
        proc = self._ptys.get(pid)
        if proc is None:
            return None
        try:
            rc = proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None
        del self._ptys[pid]
        return rc

    def push(self, name, host_path, dest, mode="0644", uid=1000, gid=1000) -> None:
        self._run(
            "file", "push", str(host_path), f"{name}{dest}",
            "--uid", uid, "--gid", gid, "--mode", mode, "--create-dirs",
        )

    def pull(self, name, src, host_path) -> None:
        self._run("file", "pull", f"{name}{src}", str(host_path))

    # --- Volumes und Snapshots -----------------------------------------

    def volumes(self) -> list[Volume]:
        found: dict[str, list[str]] = {}
        for volume in self._json("storage", "volume", "list", self.pool, "--format", "json"):
            if volume.get("type") != "custom":
                continue
            name, _, snapshot = volume.get("name", "").partition("/")
            if not name.startswith("devc-"):
                continue
            found.setdefault(name, [])
            if snapshot:
                found[name].append(snapshot)
        return [Volume(name, tuple(snaps)) for name, snaps in sorted(found.items())]

    def volume_snapshot(self, name, area, snap) -> None:
        self._run("storage", "volume", "snapshot", self.pool, names.volume_name(name, area), snap)

    def volume_restore(self, name, area, snap) -> None:
        running = self.state(name).status == "running"
        if running:
            self.stop(name)
        self._run("storage", "volume", "restore", self.pool, names.volume_name(name, area), snap)
        if running:
            self.start(name)

    def volume_snapshot_delete(self, name, area, snap) -> None:
        self._run("storage", "volume", "delete", self.pool, f"{names.volume_name(name, area)}/{snap}")

    def _while_stopped(self, name, *args) -> None:
        """Führt einen lxc-Aufruf bei gestopptem Container aus und startet ihn danach wieder.

        Im eingeschränkten Projekt lässt LXD beim Restore keine Änderung an volatile.*-Schlüsseln
        zu. Ein laufend erstellter Snapshot trägt volatile.eth0.host_name und wäre später nicht
        mehr zurückspielbar; Container-Snapshots entstehen und landen darum immer gestoppt.
        """
        running = self.state(name).status == "running"
        if running:
            self.stop(name)
        try:
            self._run(*args)
        finally:
            if running:
                self.start(name)

    def full_snapshot(self, name, snap) -> None:
        self._while_stopped(name, "snapshot", name, snap)

    def full_restore(self, name, snap) -> None:
        self._while_stopped(name, "restore", name, snap)

    def full_snapshot_delete(self, name, snap) -> None:
        self._run("delete", f"{name}/{snap}")
