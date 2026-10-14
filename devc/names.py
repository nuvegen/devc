"""Namensmuster aus SPEC 5.4 und die zugehörige Prüfung L2."""

import re

from .errors import Finding

CONTAINER_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
STORE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SNAPSHOT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
AUTO_SNAPSHOT_RE = re.compile(r"^auto-\d{8}-\d{6}$")
JOB_RE = re.compile(r"^j-[0-9a-f]{12}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SIZE_RE = re.compile(r"^\d+(MiB|GiB|TiB)$")
# Basisdomain des Proxys. 215 Zeichen lassen Platz für `<port>-<container>.` davor (höchstens 38).
DOMAIN_RE = re.compile(r"^(?=.{1,215}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
BACKUP_FILE_RE = re.compile(r"^([a-z][a-z0-9-]{1,30})-(\d{8}-\d{6})(?:-([a-z0-9][a-z0-9-]{0,62}))?\.devc\.tar$")

GIT_KEY_SLOT = "git.key"
SLOT_MAX = 64


def _full(pattern: re.Pattern, value) -> bool:
    # fullmatch statt match: "$" ließe sonst einen abschließenden Zeilenumbruch zu
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def is_container(value) -> bool:
    return _full(CONTAINER_RE, value) and not value.endswith("-")


def is_id(value) -> bool:
    """Addon-ID oder Template-ID."""
    return _full(ID_RE, value)


def is_param(value) -> bool:
    """Parametername oder Aktions-ID."""
    return _full(PARAM_RE, value)


def is_store(value) -> bool:
    return _full(STORE_RE, value)


def is_snapshot(value) -> bool:
    return _full(SNAPSHOT_RE, value)


def is_auto_snapshot(value) -> bool:
    return _full(AUTO_SNAPSHOT_RE, value)


def is_job(value) -> bool:
    return _full(JOB_RE, value)


def is_version(value) -> bool:
    return _full(VERSION_RE, value)


def is_size(value) -> bool:
    return _full(SIZE_RE, value)


def is_domain(value) -> bool:
    return _full(DOMAIN_RE, value)


def is_backup_file(value) -> bool:
    return _full(BACKUP_FILE_RE, value)


def split_slot(value) -> tuple[str, str] | None:
    """Zerlegt `<addon-id>.<param>` bzw. `git.key`; None bei ungültigem Slot."""
    if not isinstance(value, str) or len(value) > SLOT_MAX:
        return None
    if value == GIT_KEY_SLOT:
        return ("git", "key")
    addon, sep, param = value.partition(".")
    if not sep or not is_id(addon) or not is_param(param):
        return None
    return (addon, param)


def is_slot(value) -> bool:
    return split_slot(value) is not None


def split_action(value) -> tuple[str, str] | None:
    """Zerlegt `<addon-id>.<aktions-id>` (SPEC 5.5)."""
    if not isinstance(value, str):
        return None
    addon, sep, action = value.partition(".")
    if not sep or not is_id(addon) or not is_param(action):
        return None
    return (addon, action)


def volume_name(container: str, area: str) -> str:
    return f"devc-{container}-{area}"


_KINDS = {
    "container": (is_container, "Containername"),
    "addon": (is_id, "Addon-ID"),
    "template": (is_id, "Template-ID"),
    "param": (is_param, "Parametername"),
    "action": (is_param, "Aktions-ID"),
    "store": (is_store, "Store-Name"),
    "slot": (is_slot, "Secret-Slot"),
    "snapshot": (is_snapshot, "Snapshot-Name"),
    "job": (is_job, "Job-ID"),
    "version": (is_version, "Version"),
    "size": (is_size, "Größe"),
    "backup": (is_backup_file, "Backup-Datei"),
}


def check(kind: str, value, path: str = "") -> Finding | None:
    """Prüfung L2 für einen Namen; liefert einen Befund oder None."""
    test, label = _KINDS[kind]
    if test(value):
        return None
    shown = value if isinstance(value, str) else repr(value)
    return Finding("L2", path or kind, f"{label} ungültig: {shown[:80]!r}")
