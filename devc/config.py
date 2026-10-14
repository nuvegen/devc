"""config.toml laden, Defaults und Prüfung L1 (SPEC 6.1)."""

import copy
import os
import re
import tomllib
from pathlib import Path

from . import names, paths, tomlw
from .errors import Finding, ValidationError, has_errors

# allowed_roots: Standard [] laut Tabelle SPEC 6.1.
DEFAULTS = {
    "platform": "auto",
    "allowed_roots": [],
    "server": {
        "bind": "127.0.0.1",
        "port": 8740,
        "allowed_hosts": ["localhost", "127.0.0.1"],
        "terminal": True,
        "tls_cert": "",
        "tls_key": "",
        "base_domain": "",
        "public_port": 443,
        "behind_proxy": False,
    },
    "defaults": {
        "image": "ubuntu:24.04",
        "cpu": 4,
        "memory": "8GiB",
        "disk": "30GiB",
        "addons": ["claude-code"],
    },
    "lxd": {
        "project": "devcontainers",
        "storage_pool": "default",
        "network": "lxdbr0",
        "shared_mode": "idmap",
    },
    "paths": {},
}

PLATFORMS = ("auto", "lxd")
SHARED_MODES = ("shift", "idmap")
_LINE_RE = re.compile(r"\(at line (\d+), column \d+\)")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _nonempty(value):
    if not isinstance(value, str) or not value:
        return "muss ein nicht leerer String sein"
    return None


def _string(value):
    return None if isinstance(value, str) else "muss ein String sein"


def _choice(options):
    def test(value):
        if value in options and isinstance(value, str):
            return None
        return "muss einer von " + ", ".join(f'"{o}"' for o in options) + " sein"

    return test


def _port(value):
    if _is_int(value) and 1 <= value <= 65535:
        return None
    return "muss eine Ganzzahl von 1 bis 65535 sein"


def _cpu(value):
    return None if _is_int(value) and value >= 1 else "muss eine Ganzzahl ≥ 1 sein"


def _size(value):
    return None if names.is_size(value) else "muss dem Muster ^\\d+(MiB|GiB|TiB)$ entsprechen"


def _bool(value):
    return None if isinstance(value, bool) else "muss true oder false sein"


def _hosts(value):
    if _str_list(value) and all(value):
        return None
    return "muss eine Liste nicht leerer Strings sein"


def _domain(value):
    if value == "" or names.is_domain(value):
        return None
    return "muss leer oder ein DNS-Name in Kleinbuchstaben sein, ohne Schema und Port (höchstens 215 Zeichen)"


def _addon_ids(value):
    if _str_list(value) and all(names.is_id(item) for item in value):
        return None
    return "muss eine Liste gültiger Addon-IDs sein"


def _roots(value):
    if not _str_list(value):
        return "muss eine Liste von Strings sein"
    for item in value:
        if not os.path.isabs(item):
            return f"Pfad ist nicht absolut: {item}"
        if not os.path.isdir(item):
            return f"Verzeichnis existiert nicht: {item}"
    return None


def _path(value):
    if not isinstance(value, str) or not value:
        return "muss ein nicht leerer String sein"
    if not (os.path.isabs(value) or value == "~" or value.startswith("~/")):
        return "muss absolut sein oder mit ~/ beginnen"
    return None


_SCHEMA = {
    "platform": _choice(PLATFORMS),
    "allowed_roots": _roots,
    "server": {
        "bind": _nonempty,
        "port": _port,
        "allowed_hosts": _hosts,
        "terminal": _bool,
        "tls_cert": _string,
        "tls_key": _string,
        "base_domain": _domain,
        "public_port": _port,
        "behind_proxy": _bool,
    },
    "defaults": {
        "image": _nonempty,
        "cpu": _cpu,
        "memory": _size,
        "disk": _size,
        "addons": _addon_ids,
    },
    "lxd": {
        "project": _nonempty,
        "storage_pool": _nonempty,
        "network": _nonempty,
        "shared_mode": _choice(SHARED_MODES),
    },
    "paths": {key: _path for key in paths.OVERRIDABLE},
}


def _check(data: dict, schema: dict, prefix: str, findings: list) -> None:
    for key, value in data.items():
        path = f"{prefix}{key}"
        rule = schema.get(key)
        if rule is None:
            findings.append(Finding("L1", path, f"Unbekannter Schlüssel: {path}"))
        elif isinstance(rule, dict):
            if isinstance(value, dict):
                _check(value, rule, path + ".", findings)
            else:
                findings.append(Finding("L1", path, f"{path} muss eine Tabelle sein"))
        else:
            problem = rule(value)
            if problem:
                findings.append(Finding("L1", path, f"{path} {problem}"))


def validate(data: dict) -> list[Finding]:
    """Prüfung L1 auf bereits geparsten Daten."""
    findings: list[Finding] = []
    _check(data, _SCHEMA, "", findings)
    return findings


def parse(text: str, source: str = "config.toml") -> dict:
    """Parst und prüft den Text einer config.toml; wirft ValidationError (L1)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        match = _LINE_RE.search(str(exc))
        line = int(match.group(1)) if match else None
        raise ValidationError.single("L1", source, f"Kein gültiges TOML: {exc}", line=line) from None
    findings = validate(data)
    if has_errors(findings):
        raise ValidationError(findings)
    return data


def _merge(base: dict, extra: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class Config:
    """Geladene Konfiguration: Defaults, überlagert von config.toml."""

    def __init__(self, data: dict | None = None, config_file=None, env=None, exists: bool = False):
        self.data = _merge(DEFAULTS, data or {})
        self.exists = exists
        self.paths = paths.resolve(config_file, self.data["paths"], env)

    @property
    def config_file(self) -> Path:
        return self.paths.config_file

    @property
    def platform(self) -> str:
        return self.data["platform"]

    @property
    def allowed_roots(self) -> list[str]:
        return list(self.data["allowed_roots"])

    @property
    def server(self) -> dict:
        return self.data["server"]

    @property
    def defaults(self) -> dict:
        return self.data["defaults"]

    @property
    def lxd(self) -> dict:
        return self.data["lxd"]


def load(config_file=None, env=None) -> Config:
    """Lädt die Konfiguration. Fehlt die Datei, gelten alle Defaults."""
    env = os.environ if env is None else env
    file = Path(config_file) if config_file else paths.default_config_file(env)
    try:
        raw = file.read_bytes()
    except FileNotFoundError:
        return Config({}, file, env, exists=False)
    except OSError as exc:
        raise ValidationError.single("L1", str(file), f"Nicht lesbar: {exc.strerror}") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationError.single("L1", str(file), "Datei ist nicht UTF-8") from None
    return Config(parse(text, str(file)), file, env, exists=True)


def default_text(env=None) -> str:
    """Text einer config.toml mit allen Defaults, wie `devc init` sie schreibt."""
    data = copy.deepcopy(DEFAULTS)
    resolved = paths.resolve(env=env)
    data["paths"] = {key: str(getattr(resolved, key)) for key in paths.OVERRIDABLE}
    header = "# devc-Konfiguration – alle Werte sind Defaults (SPEC 6.1)\n"
    return header + tomlw.dumps(data)
