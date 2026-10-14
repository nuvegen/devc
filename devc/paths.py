"""Alle Host-Pfade an einer Stelle (SPEC 5.2)."""

import os
from dataclasses import dataclass
from pathlib import Path

APP = "devc"
OVERRIDABLE = ("addons", "templates", "secrets", "state", "backups")


def _base(env, variable: str, fallback: str) -> Path:
    value = env.get(variable, "")
    # Die XDG-Spezifikation verlangt absolute Pfade; relative werden ignoriert.
    if value and os.path.isabs(value):
        return Path(value)
    home = env.get("HOME") or os.path.expanduser("~")
    return Path(home) / fallback


def config_home(env=None) -> Path:
    env = os.environ if env is None else env
    return _base(env, "XDG_CONFIG_HOME", ".config") / APP


def data_home(env=None) -> Path:
    env = os.environ if env is None else env
    return _base(env, "XDG_DATA_HOME", ".local/share") / APP


def default_config_file(env=None) -> Path:
    return config_home(env) / "config.toml"


def expand(value: str, env=None) -> Path:
    """Expandiert ein führendes `~` gegen HOME der übergebenen Umgebung."""
    env = os.environ if env is None else env
    if value == "~" or value.startswith("~/"):
        home = env.get("HOME") or os.path.expanduser("~")
        return Path(home + value[1:])
    return Path(value)


@dataclass(frozen=True)
class Paths:
    config_file: Path
    token: Path
    addons: Path
    templates: Path
    secrets: Path
    state: Path
    backups: Path

    @property
    def shared_secrets(self) -> Path:
        return self.secrets / "_shared"

    def container_secrets(self, name: str) -> Path:
        return self.secrets / name

    @property
    def db(self) -> Path:
        return self.state / "devc.sqlite"

    @property
    def containers(self) -> Path:
        return self.state / "containers"

    def container_dir(self, name: str) -> Path:
        return self.containers / name

    def instance_file(self, name: str) -> Path:
        return self.containers / name / "devc.toml"

    def applied_file(self, name: str) -> Path:
        """Zuletzt angewendeter Stand der devc.toml; Grundlage für `config apply`."""
        return self.containers / name / "applied.toml"

    @property
    def logs(self) -> Path:
        return self.state / "logs"

    def job_log(self, job_id: str) -> Path:
        return self.logs / f"{job_id}.log"

    @property
    def snapshots(self) -> Path:
        return self.state / "snapshots"

    def snapshot_dir(self, name: str, snapshot: str) -> Path:
        return self.snapshots / name / snapshot

    @property
    def uploads(self) -> Path:
        """Hochgeladene Backups zwischen `restore/inspect` und `restore`."""
        return self.state / "uploads"

    @property
    def locks(self) -> Path:
        return self.state / "locks"

    def lock_file(self, name: str) -> Path:
        return self.locks / f"{name}.lock"


def resolve(config_file=None, overrides=None, env=None) -> Paths:
    """Baut die Pfade aus Umgebung (XDG) und `paths` der config.toml."""
    env = os.environ if env is None else env
    overrides = overrides or {}
    cfg = config_home(env)
    state = data_home(env)

    def pick(key: str, default: Path) -> Path:
        value = overrides.get(key)
        return expand(value, env) if value else default

    state = pick("state", state)
    return Paths(
        config_file=Path(config_file) if config_file else cfg / "config.toml",
        token=cfg / "token",
        addons=pick("addons", cfg / "addons"),
        templates=pick("templates", cfg / "templates"),
        secrets=pick("secrets", cfg / "secrets"),
        state=state,
        backups=pick("backups", state / "backups"),
    )
