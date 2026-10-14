"""Secret-Store auf dem Host und Push in den Container (SPEC 11.3).

Werte sind Rohbytes. Sie verlassen dieses Modul nur über stdin von `exec`, nie über
Argumente, Umgebungsvariablen, Logs oder Fehlermeldungen.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from . import names
from .errors import NotFoundError, ValidationError

RUN_DIR = "/run/devc"
SECRETS_DIR = "/run/devc/secrets"
ACTIONS_DIR = "/run/devc/actions"
_SOURCE_SUFFIX = ".src"
# Der Pfad kommt als Argument, der Wert über stdin.
_PUSH = 'umask 077; rm -f "$1"; cat > "$1" && chown 1000:1000 "$1" && chmod 0400 "$1"'


def container_path(slot: str) -> str:
    return f"{SECRETS_DIR}/{slot}"


def _check(kind: str, value: str) -> None:
    finding = names.check(kind, value)
    if finding:
        raise ValidationError([finding])


def _write(file: Path, value: bytes) -> None:
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    file.parent.chmod(0o700)
    tmp = file.with_name(file.name + ".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(value)
    os.replace(tmp, file)


def _modified(file: Path) -> str:
    return datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- geteilter Store --------------------------------------------------------


def shared_names(paths) -> set[str]:
    directory = paths.shared_secrets
    if not directory.is_dir():
        return set()
    return {p.name for p in directory.iterdir() if p.is_file() and names.is_store(p.name)}


def shared_set(paths, name: str, value: bytes) -> None:
    _check("store", name)
    paths.secrets.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write(paths.shared_secrets / name, value)


def shared_get(paths, name: str) -> bytes:
    _check("store", name)
    try:
        return (paths.shared_secrets / name).read_bytes()
    except FileNotFoundError:
        raise NotFoundError(f"Store-Eintrag fehlt: {name}") from None


def shared_remove(paths, name: str) -> None:
    _check("store", name)
    try:
        (paths.shared_secrets / name).unlink()
    except FileNotFoundError:
        raise NotFoundError(f"Store-Eintrag fehlt: {name}") from None


def shared_list(paths) -> list[dict]:
    """Namen, Nutzung und Änderungsdatum – nie Werte."""
    usage: dict[str, list[str]] = {}
    if paths.secrets.is_dir():
        for directory in sorted(paths.secrets.iterdir()):
            if not directory.is_dir() or directory.name == "_shared":
                continue
            for entry in local_list(paths, directory.name):
                if entry["source"].startswith("store:"):
                    usage.setdefault(entry["source"][len("store:"):], []).append(f"{directory.name}/{entry['slot']}")
    return [
        {"name": name, "modified": _modified(paths.shared_secrets / name), "used_by": usage.get(name, [])}
        for name in sorted(shared_names(paths))
    ]


# --- Secrets eines Containers ----------------------------------------------


def _slot_file(paths, container: str, slot: str) -> Path:
    _check("container", container)
    _check("slot", slot)
    return paths.container_secrets(container) / slot


def local_set(paths, container: str, slot: str, value: bytes, source: str = "local") -> None:
    """Legt die Kopie eines Secrets für einen Container ab; `source` ist `local` oder `store:<name>`."""
    file = _slot_file(paths, container, slot)
    paths.secrets.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write(file, value)
    _write(file.with_name(file.name + _SOURCE_SUFFIX), source.encode("utf-8"))


def local_set_from_store(paths, container: str, slot: str, store: str) -> None:
    local_set(paths, container, slot, shared_get(paths, store), f"store:{store}")


def local_get(paths, container: str, slot: str) -> bytes | None:
    try:
        return _slot_file(paths, container, slot).read_bytes()
    except FileNotFoundError:
        return None


def local_slots(paths, container: str) -> list[str]:
    directory = paths.container_secrets(container)
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file() and names.is_slot(p.name))


def local_list(paths, container: str) -> list[dict]:
    result = []
    for slot in local_slots(paths, container):
        file = paths.container_secrets(container) / slot
        try:
            source = file.with_name(slot + _SOURCE_SUFFIX).read_text(encoding="utf-8").strip() or "local"
        except FileNotFoundError:
            source = "local"
        result.append({"slot": slot, "source": source, "modified": _modified(file)})
    return result


def local_remove(paths, container: str, slot: str) -> bool:
    file = _slot_file(paths, container, slot)
    existed = file.exists()
    for target in (file, file.with_name(file.name + _SOURCE_SUFFIX)):
        try:
            target.unlink()
        except FileNotFoundError:
            pass
    return existed


def local_remove_all(paths, container: str) -> None:
    """Entfernt alle Secret-Kopien eines Containers (nur bei `devc rm`)."""
    _check("container", container)
    directory = paths.container_secrets(container)
    if not directory.is_dir():
        return
    for file in directory.iterdir():
        if file.is_file():
            file.unlink()
    directory.rmdir()


# --- Container --------------------------------------------------------------


def prepare_runtime(provider, container: str) -> None:
    """Laufzeitverzeichnisse anlegen (SPEC 8.4 Schritt 5); /run ist nach jedem Start leer."""
    result = provider.exec(
        container,
        ["install", "-d", "-o", "1000", "-g", "1000", "-m", "0700", RUN_DIR, SECRETS_DIR, ACTIONS_DIR],
        user="root",
        cwd="/",
    )
    if result.rc != 0:
        from .errors import EngineError

        raise EngineError("Laufzeitverzeichnisse unter /run/devc ließen sich nicht anlegen")


def push(provider, container: str, slot: str, value: bytes) -> None:
    _check("slot", slot)
    result = provider.exec(
        container, ["sh", "-c", _PUSH, "sh", container_path(slot)], user="root", cwd="/", stdin=value
    )
    if result.rc != 0:
        from .errors import EngineError

        # stderr nicht durchreichen: könnte Teile der Eingabe enthalten
        raise EngineError(f"Secret {slot} ließ sich nicht in den Container schreiben")


def push_all(provider, paths, container: str) -> list[str]:
    """Schreibt alle Secret-Kopien des Containers nach /run/devc/secrets; liefert die Slots."""
    slots = local_slots(paths, container)
    for slot in slots:
        push(provider, container, slot, local_get(paths, container, slot))
    return slots


def remove_from_container(provider, container: str, slot: str) -> None:
    _check("slot", slot)
    provider.exec(container, ["rm", "-f", container_path(slot)], user="root", cwd="/")
