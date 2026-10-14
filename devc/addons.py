"""Addons laden, prüfen (L3–L8, L17), auflösen (SPEC 7.6) und sortieren (SPEC 7.7)."""

import io
import os
import re
import stat
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import names, placeholders
from .errors import Finding, ValidationError

PARAM_TYPES = ("string", "int", "bool", "choice", "secret", "ports")
LINK_PORTS = (1024, 65535)  # wie der Proxy (SPEC 8.11)
MAX_PORTS = 32
_PORT_ENTRY_RE = re.compile(r"([1-9][0-9]{0,4})(?:=([^\s=]{1,40}))?")


def parse_ports(value) -> list[tuple[int, str]]:
    """Wert eines Parameters vom Typ `ports` (SPEC 7.8): `<port>[=<beschriftung>]`, durch Leerzeichen getrennt.

    Liefert (Port, Beschriftung oder "") in der Reihenfolge der Angabe; wirft ValueError mit einem Satzteil.
    """
    if not isinstance(value, str):
        raise ValueError("muss ein String sein")
    low, high = LINK_PORTS
    entries = []
    for entry in value.split():
        match = _PORT_ENTRY_RE.fullmatch(entry)
        if not match or not entry.isprintable():
            raise ValueError(f"enthält einen ungültigen Eintrag {entry!r}; erwartet <port> oder <port>=<beschriftung>")
        port = int(match.group(1))
        if not (low <= port <= high):
            raise ValueError(f"Port {port} liegt nicht zwischen {low} und {high}")
        if port in [p for p, _ in entries]:
            raise ValueError(f"nennt Port {port} doppelt")
        entries.append((port, match.group(2) or ""))
    if len(entries) > MAX_PORTS:
        raise ValueError(f"nennt mehr als {MAX_PORTS} Ports")
    return entries
PLATFORMS = ("lxd", "apple")
OWNERS = ("dev", "root")
BASE = "base"
DEFAULT_MAX_LENGTH = 1024
MODE_RE = re.compile(r"^0[0-7]{3}$")


def _builtin_root() -> Path:
    """Verzeichnis der mitgelieferten Addons und Templates.

    Im Quellbaum liegt es neben diesem Modul. Aus `devc.pyz` heraus gibt es dort kein echtes
    Verzeichnis; dann wird `devc/builtin/` einmalig in den Cache des Nutzers entpackt, weil Addons
    als Dateien gebraucht werden (Archiv packen, Dateien in den Container kopieren).
    """
    here = Path(__file__).resolve().parent / "builtin"
    if here.is_dir():
        return here
    archive = Path(__file__).resolve()
    while not archive.is_file():
        if archive.parent == archive:
            return here
        archive = archive.parent
    from . import __version__

    info = archive.stat()
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    target = cache / "devc" / f"builtin-{__version__}-{int(info.st_mtime)}-{info.st_size}"
    if not (target / ".complete").is_file():
        target.mkdir(parents=True, exist_ok=True)
        prefix = "devc/builtin/"
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                relative = member.filename[len(prefix):]
                if not member.filename.startswith(prefix) or not relative or ".." in relative.split("/"):
                    continue
                destination = target / relative
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.read(member))
        (target / ".complete").write_text("")
    return target


BUILTIN = _builtin_root()

ORIGIN_LABELS = {
    "builtin": "mitgeliefert",
    "own": "eigen",
    "override": "eigen (überschreibt mitgeliefert)",
}


# --- kleine Typprüfer; liefern None oder einen Satzteil für die Meldung -----


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def t_str(value):
    return None if isinstance(value, str) else "muss ein String sein"


def t_bool(value):
    return None if isinstance(value, bool) else "muss true oder false sein"


def t_text(minimum: int, maximum: int):
    def test(value):
        if isinstance(value, str) and minimum <= len(value) <= maximum and "\n" not in value:
            return None
        return f"muss ein einzeiliger String mit {minimum}–{maximum} Zeichen sein"

    return test


def t_version(value):
    return None if names.is_version(value) else "muss eine Version x.y.z sein"


def t_id_list(value):
    if isinstance(value, list) and all(names.is_id(item) for item in value):
        return None
    return "muss eine Liste gültiger IDs sein"


def t_platforms(value):
    if isinstance(value, list) and value and all(item in PLATFORMS for item in value):
        return None
    return 'muss eine nicht leere Liste aus "lxd", "apple" sein'


def t_str_list(value):
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return None
    return "muss eine Liste nicht leerer Strings sein"


def t_mode(value):
    return None if isinstance(value, str) and MODE_RE.match(value) else 'muss ein Oktalwert wie "0644" sein'


def t_owner(value):
    return None if value in OWNERS else 'muss "dev" oder "root" sein'


def t_abs_path(value):
    if isinstance(value, str) and value.startswith("/") and ".." not in value.split("/"):
        return None
    return "muss ein absoluter Pfad ohne .. sein"


def t_rel_path(value):
    if isinstance(value, str) and value and not value.startswith("/") and ".." not in value.split("/"):
        return None
    return "muss ein relativer Pfad ohne .. sein"


def check_table(data: dict, spec: dict, check: str, prefix: str, findings: list) -> None:
    """Prüft Pflichtfelder, unbekannte Schlüssel und Typen einer Tabelle.

    spec: Schlüssel -> (pflicht, prüfer). Ein Prüfer None heißt: wird woanders geprüft.
    """
    for key, (required, _) in spec.items():
        if required and key not in data:
            findings.append(Finding(check, prefix + key, f"Pflichtfeld fehlt: {prefix}{key}"))
    for key, value in data.items():
        path = prefix + key
        if key not in spec:
            findings.append(Finding(check, path, f"Unbekannter Schlüssel: {path}"))
            continue
        test = spec[key][1]
        problem = test(value) if test else None
        if problem:
            findings.append(Finding(check, path, f"{path} {problem}"))


def table_list(data: dict, key: str, check: str, findings: list) -> list[dict]:
    """Liefert `[[key]]` als Liste von Tabellen; meldet einen falschen Typ."""
    value = data.get(key, [])
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    findings.append(Finding(check, key, f"{key} muss eine Liste von Tabellen ([[{key}]]) sein"))
    return []


# --- Datenmodell ------------------------------------------------------------


def _is_pattern(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > 500:
        return False
    try:
        re.compile(value)
    except re.error:
        return False
    return True


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    default: object = None
    has_default: bool = False
    required: bool = False
    options: tuple = ()
    min: int | None = None
    max: int | None = None
    max_length: int = DEFAULT_MAX_LENGTH
    pattern: str = ""
    description: str = ""
    label: str = ""
    group: str = ""
    show_if: tuple = ()  # (Name eines choice- oder bool-Parameters, erlaubte Werte als Strings)
    option_labels: tuple = ()
    hidden: bool = False

    def problem(self, value) -> str | None:
        """Passt der Wert zum Typ und zu den Grenzen? None oder Satzteil."""
        if self.type == "secret":
            return "ist ein Secret und darf keinen Wert tragen"
        if self.type == "string":
            if not isinstance(value, str):
                return "muss ein String sein"
            if len(value) > self.max_length:
                return f"ist länger als {self.max_length} Zeichen"
            if self.pattern and re.fullmatch(self.pattern, value) is None:
                return "hat nicht die erlaubte Form (" + (self.description or "Muster " + self.pattern) + ")"
        elif self.type == "int":
            if not is_int(value):
                return "muss eine Ganzzahl sein"
            if self.min is not None and value < self.min:
                return f"ist kleiner als {self.min}"
            if self.max is not None and value > self.max:
                return f"ist größer als {self.max}"
        elif self.type == "bool":
            if not isinstance(value, bool):
                return "muss true oder false sein"
        elif self.type == "choice":
            if not isinstance(value, str) or value not in self.options:
                return "muss einer von " + ", ".join(self.options) + " sein"
        elif self.type == "ports":
            try:
                parse_ports(value)
            except ValueError as exc:
                return str(exc)
        return None

    def to_dict(self) -> dict:
        data = {"name": self.name, "type": self.type, "required": self.required, "description": self.description}
        if self.has_default:
            data["default"] = self.default
        if self.type == "choice":
            data["options"] = list(self.options)
        if self.type == "int":
            data.update({k: v for k, v in (("min", self.min), ("max", self.max)) if v is not None})
        if self.type == "string":
            data["max_length"] = self.max_length
            if self.pattern:
                data["pattern"] = self.pattern
        data.update(self.view())
        return data

    def view(self) -> dict:
        """Angaben nur für die Darstellung (SPEC 6.2): Beschriftung, Gruppe, Sichtbarkeit."""
        data = {}
        if self.label:
            data["label"] = self.label
        if self.group:
            data["group"] = self.group
        if self.show_if:
            data["show_if"] = {"param": self.show_if[0], "values": list(self.show_if[1])}
        if self.option_labels:
            data["option_labels"] = list(self.option_labels)
        if self.hidden:
            data["hidden"] = True
        return data


@dataclass(frozen=True)
class Addon:
    id: str
    title: str
    version: str
    description: str = ""
    platforms: tuple = PLATFORMS
    requires: tuple = ()
    conflicts: tuple = ()
    install: tuple = ()
    uninstall: tuple = ()
    params: tuple = ()
    files: tuple = ()
    archives: tuple = ()
    actions: tuple = ()
    links: tuple = ()
    tiles: tuple = ()
    path: Path | None = None
    origin: str = "own"

    def param(self, name: str) -> Param | None:
        for param in self.params:
            if param.name == name:
                return param
        return None

    def action(self, action_id: str) -> dict | None:
        for action in self.actions:
            if action["id"] == action_id:
                return action
        return None

    def tile(self, tile_id: str) -> dict | None:
        for tile in self.tiles:
            if tile["id"] == tile_id:
                return tile
        return None

    @property
    def all_requires(self) -> tuple:
        """`requires` mit implizit vorangestelltem base (außer bei base selbst)."""
        if self.id == BASE:
            return ()
        return (BASE,) + tuple(r for r in self.requires if r != BASE)

    def secret_slots(self) -> list[str]:
        return [f"{self.id}.{p.name}" for p in self.params if p.type == "secret"]

    def values(self, given: dict | None = None) -> dict:
        """Werte aller Nicht-Secret-Parameter: aus der Instanz, sonst der Default."""
        given = given or {}
        values = {}
        for param in self.params:
            if param.type == "secret":
                continue
            if param.name in given:
                values[param.name] = given[param.name]
            elif param.has_default:
                values[param.name] = param.default
        return values

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "version": self.version,
            "description": self.description,
            "origin": self.origin,
            "origin_label": ORIGIN_LABELS[self.origin],
            "actions_count": len(self.actions),
            "requires": list(self.all_requires),
        }

    def to_dict(self) -> dict:
        data = self.summary()
        data.update(
            platforms=list(self.platforms),
            requires=list(self.all_requires),
            conflicts=list(self.conflicts),
            install=list(self.install),
            uninstall=list(self.uninstall),
            params=[p.to_dict() for p in self.params],
            files=[dict(f) for f in self.files],
            archives=[dict(a) for a in self.archives],
            actions=[dict(a) for a in self.actions],
            links=[dict(link) for link in self.links],
            tiles=[dict(tile) for tile in self.tiles],
        )
        return data


# --- Prüfungen L3, L6, L7, L8 ----------------------------------------------

_HEAD = {
    "id": (True, None),
    "title": (True, t_text(1, 60)),
    "version": (True, t_version),
    "description": (False, t_text(0, 200)),
    "platforms": (False, t_platforms),
    "requires": (False, t_id_list),
    "conflicts": (False, t_id_list),
    "install": (False, t_str_list),
    "uninstall": (False, t_str_list),
    "params": (False, None),
    "files": (False, None),
    "archives": (False, None),
    "actions": (False, None),
    "links": (False, None),
    "tiles": (False, None),
}
_PARAM = {
    "name": (True, None),
    "type": (True, None),
    "default": (False, None),
    "required": (False, t_bool),
    "options": (False, None),
    "min": (False, None),
    "max": (False, None),
    "max_length": (False, None),
    "pattern": (False, None),
    "description": (False, t_str),
    "label": (False, t_text(1, 40)),
    "group": (False, t_text(1, 40)),
    "show_if": (False, None),
    "option_labels": (False, None),
    "hidden": (False, t_bool),
}
_SHOW_IF_RE = re.compile(r"([a-z][a-z0-9_]{0,31})=([^|=]{0,80}(?:\|[^|=]{0,80})*)")
_FILE = {
    "src": (True, t_rel_path),
    "dest": (True, t_abs_path),
    "mode": (False, t_mode),
    "owner": (False, t_owner),
    "overwrite": (False, t_bool),
}
_ARCHIVE = {"src": (True, t_rel_path), "dest": (True, t_abs_path)}
_ACTION = {
    "id": (True, None),
    "label": (True, t_text(1, 40)),
    "run": (False, None),
    "stops": (False, None),
    "detach": (False, t_bool),
    "user": (False, t_owner),
    "timeout": (False, lambda v: None if is_int(v) and 1 <= v <= 3600 else "muss eine Ganzzahl von 1 bis 3600 sein"),
    "description": (False, t_str),
}
_LINK = {"label": (True, t_text(1, 40)), "url": (False, t_str), "port": (False, None), "ports": (False, None), "path": (False, t_str), "public": (False, t_bool), "when": (False, t_str)}
TILE_KINDS = ("view", "chat", "link", "panel")
VIEW_TIMEOUT = (1, 60)
PANEL_TIMEOUT = (1, 300)
VIEW_REFRESH = (2, 3600)  # 0 = kein automatisches Neuladen
_TILE = {
    "id": (True, None),
    "title": (True, t_text(1, 40)),
    "kind": (True, None),
    "description": (False, t_text(0, 200)),
    "run": (False, None),
    "timeout": (False, None),
    "refresh": (False, None),
    "port": (False, None),
    "path": (False, t_str),
    "url": (False, t_str),
}
_PORT_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]{0,31})\}")


def _parse_params(data: dict, findings: list) -> list[Param]:
    params = []
    seen = set()
    for index, raw in enumerate(table_list(data, "params", "L3", findings)):
        prefix = f"params[{index}]."
        before = len(findings)
        check_table(raw, _PARAM, "L3", prefix, findings)
        name, kind = raw.get("name"), raw.get("type")
        if "name" in raw and not names.is_param(name):
            findings.append(Finding("L2", prefix + "name", f"Parametername ungültig: {name!r}"))
        if "type" in raw and kind not in PARAM_TYPES:
            findings.append(Finding("L3", prefix + "type", f"{prefix}type muss einer von {', '.join(PARAM_TYPES)} sein"))
        if len(findings) > before:
            continue
        label = f"Parameter {name}"
        if name in seen:
            findings.append(Finding("L6", prefix + "name", f"{label} ist doppelt"))
            continue
        seen.add(name)
        if name in placeholders.SYSTEM_NAMES:
            findings.append(Finding("L6", prefix + "name", f"{label} kollidiert mit einer Systemgröße"))
        ok = True
        options = raw.get("options")
        if kind == "choice":
            if not (isinstance(options, list) and options and all(isinstance(o, str) for o in options)):
                findings.append(Finding("L6", prefix + "options", f"{label}: choice braucht options mit mindestens einem String"))
                ok = False
        elif options is not None:
            findings.append(Finding("L6", prefix + "options", f"{label}: options ist nur bei choice erlaubt"))
            ok = False
        for bound in ("min", "max"):
            if bound in raw and (kind != "int" or not is_int(raw[bound])):
                findings.append(Finding("L6", prefix + bound, f"{label}: {bound} ist nur bei int erlaubt und muss eine Ganzzahl sein"))
                ok = False
        if ok and "min" in raw and "max" in raw and raw["min"] > raw["max"]:
            findings.append(Finding("L6", prefix + "min", f"{label}: min ist größer als max"))
            ok = False
        if "max_length" in raw and (kind != "string" or not is_int(raw["max_length"]) or raw["max_length"] < 1):
            findings.append(Finding("L6", prefix + "max_length", f"{label}: max_length ist nur bei string erlaubt und muss ≥ 1 sein"))
            ok = False
        if "pattern" in raw and not (kind == "string" and _is_pattern(raw["pattern"])):
            findings.append(Finding("L6", prefix + "pattern", f"{label}: pattern ist nur bei string erlaubt und muss ein gültiger regulärer Ausdruck sein"))
            ok = False
        labels = raw.get("option_labels")
        if labels is not None and not (kind == "choice" and isinstance(labels, list) and isinstance(options, list) and len(labels) == len(options)
                                       and all(isinstance(t, str) and 1 <= len(t) <= 40 for t in labels)):
            findings.append(Finding("L6", prefix + "option_labels", f"{label}: option_labels ist nur bei choice erlaubt, je Option ein Text mit 1 bis 40 Zeichen"))
            ok = False
        show_if = ()
        if "show_if" in raw:
            match = _SHOW_IF_RE.fullmatch(raw["show_if"]) if isinstance(raw["show_if"], str) else None
            if match is None:
                findings.append(Finding("L6", prefix + "show_if", f"{label}: show_if muss die Form name=wert oder name=wert1|wert2 haben"))
                ok = False
            else:
                show_if = (match.group(1), tuple(match.group(2).split("|")))
        if not ok:
            continue
        param = Param(
            name=name,
            type=kind,
            default=raw.get("default"),
            has_default="default" in raw,
            required=raw.get("required", False),
            options=tuple(options or ()),
            min=raw.get("min"),
            max=raw.get("max"),
            max_length=raw.get("max_length", DEFAULT_MAX_LENGTH),
            pattern=raw.get("pattern", ""),
            description=raw.get("description", ""),
            label=raw.get("label", ""),
            group=raw.get("group", ""),
            show_if=show_if,
            option_labels=tuple(labels or ()),
            hidden=raw.get("hidden", False),
        )
        if param.has_default:
            if kind == "secret":
                findings.append(Finding("L6", prefix + "default", f"{label}: secret darf keinen default haben"))
                continue
            problem = param.problem(param.default)
            if problem:
                findings.append(Finding("L6", prefix + "default", f"{label}: default {problem}"))
                continue
        params.append(param)
    _check_show_if(params, findings)
    return params


def _check_show_if(params: list[Param], findings: list) -> None:
    """show_if muss auf einen anderen choice- oder bool-Parameter mit passenden Werten zeigen."""
    by_name = {p.name: p for p in params}
    for index, param in enumerate(params):
        if not param.show_if:
            continue
        name, values = param.show_if
        other = by_name.get(name)
        allowed = other.options if other and other.type == "choice" else ("true", "false") if other and other.type == "bool" else None
        if other is param or allowed is None:
            findings.append(Finding("L6", f"params[{index}].show_if", f"Parameter {param.name}: show_if braucht einen anderen Parameter vom Typ choice oder bool"))
        elif any(v not in allowed for v in values):
            findings.append(Finding("L6", f"params[{index}].show_if", f"Parameter {param.name}: show_if nennt einen Wert, den {name} nicht hat"))


def template_problems(template: str, params: list[Param]) -> list[str]:
    """Probleme mit Platzhaltern einer Vorlage: Syntax, unbekannter Name, Secret."""
    try:
        used = placeholders.names_in(template)
    except placeholders.PlaceholderError as exc:
        return [str(exc)]
    by_name = {p.name: p for p in params}
    problems = []
    for name in used:
        if name in placeholders.SYSTEM_NAMES:
            continue
        if name not in by_name:
            problems.append(f"Unbekannter Platzhalter {{{name}}}")
        elif by_name[name].type == "secret":
            problems.append(f"Platzhalter {{{name}}} ist ein Secret")
        elif not by_name[name].has_default and not by_name[name].required:
            problems.append(f"Platzhalter {{{name}}} hat keinen Default und ist nicht required")
    return problems


def _parse_actions(data: dict, params: list[Param], findings: list) -> list[dict]:
    actions = []
    raws = table_list(data, "actions", "L3", findings)
    seen = set()
    for index, raw in enumerate(raws):
        prefix = f"actions[{index}]."
        before = len(findings)
        check_table(raw, _ACTION, "L3", prefix, findings)
        action_id = raw.get("id")
        if "id" in raw and not names.is_param(action_id):
            findings.append(Finding("L2", prefix + "id", f"Aktions-ID ungültig: {action_id!r}"))
        if len(findings) > before:
            continue
        label = f"Aktion {action_id}"
        if action_id in seen:
            findings.append(Finding("L7", prefix + "id", f"{label} ist doppelt"))
            continue
        seen.add(action_id)
        has_run, has_stops = "run" in raw, "stops" in raw
        if has_run == has_stops:
            findings.append(Finding("L7", prefix + "run", f"{label}: genau eins von run und stops ist nötig"))
            continue
        if has_stops and "detach" in raw:
            findings.append(Finding("L7", prefix + "detach", f"{label}: detach ist nur mit run erlaubt"))
            continue
        if has_run:
            if not isinstance(raw["run"], str) or not raw["run"].strip():
                findings.append(Finding("L7", prefix + "run", f"{label}: run muss ein nicht leerer String sein"))
                continue
            problems = template_problems(raw["run"], params)
            for problem in problems:
                findings.append(Finding("L7", prefix + "run", f"{label}: {problem}"))
            if problems:
                continue
        action = {"detach": False, "user": "dev", "timeout": 300, "description": ""}
        action.update(raw)
        actions.append(action)
    detached = {a["id"] for a in actions if a.get("detach")}
    valid = []
    for action in actions:
        if "stops" in action and action["stops"] not in detached:
            index = raws.index(next(r for r in raws if r.get("id") == action["id"]))
            findings.append(
                Finding("L7", f"actions[{index}].stops", f"Aktion {action['id']}: stops zeigt nicht auf eine detach-Aktion")
            )
            continue
        valid.append(action)
    return valid


def _parse_links(data: dict, params: list[Param], actions: list[dict], findings: list) -> list[dict]:
    links = []
    detached = {a["id"] for a in actions if a.get("detach")}
    for index, raw in enumerate(table_list(data, "links", "L3", findings)):
        prefix = f"links[{index}]."
        before = len(findings)
        check_table(raw, _LINK, "L3", prefix, findings)
        if len(findings) > before:
            continue
        label = f"Link {raw['label']}"
        kinds = [key for key in ("url", "port", "ports") if key in raw]
        if not kinds:
            findings.append(Finding("L3", prefix + "url", f"{label}: eins von url, port und ports ist nötig"))
            continue
        if len(kinds) > 1:
            findings.append(Finding("L8", prefix + "url", f"{label}: url, port und ports schließen sich aus"))
            continue
        link = {"label": raw["label"]}
        if "url" in raw:
            url = link["url"] = raw["url"]
            if "path" in raw:
                findings.append(Finding("L8", prefix + "path", f"{label}: path gibt es nur zusammen mit port"))
            if "public" in raw:
                findings.append(Finding("L8", prefix + "public", f"{label}: public gibt es nur zusammen mit port oder ports"))
            if not url.startswith(("http://", "https://")):
                findings.append(Finding("L8", prefix + "url", f"{label}: url muss mit http:// oder https:// beginnen"))
            for problem in template_problems(url, params):
                findings.append(Finding("L8", prefix + "url", f"{label}: {problem}"))
        else:
            kind = kinds[0]
            link[kind] = raw[kind]
            link["path"] = path = raw.get("path", "/")
            link["public"] = raw.get("public", False)
            problem = _link_port_problem(raw[kind], params, "int" if kind == "port" else "ports")
            if problem:
                findings.append(Finding("L8", prefix + kind, f"{label}: {problem}"))
            if not path.startswith("/") or path.startswith("//") or any(c.isspace() for c in path):
                findings.append(Finding("L8", prefix + "path", f"{label}: path muss mit genau einem / beginnen und darf keine Leerzeichen enthalten"))
            for problem in template_problems(path, params):
                findings.append(Finding("L8", prefix + "path", f"{label}: {problem}"))
        when = raw.get("when", "")
        if when and when not in detached:
            findings.append(Finding("L8", prefix + "when", f"{label}: when zeigt nicht auf eine detach-Aktion"))
        if len(findings) > before:
            continue
        links.append({**link, "when": when})
    return links


def _path_problems(path: str, params: list[Param]) -> list[str]:
    problems = []
    if not path.startswith("/") or path.startswith("//") or any(c.isspace() for c in path):
        problems.append("path muss mit genau einem / beginnen und darf keine Leerzeichen enthalten")
    return problems + template_problems(path, params)


def _parse_tiles(data: dict, params: list[Param], findings: list) -> list[dict]:
    """`[[tiles]]` (SPEC 6.2, L17): Kacheln eines Addons auf dem Container-Dashboard."""
    tiles = []
    seen = set()
    for index, raw in enumerate(table_list(data, "tiles", "L3", findings)):
        prefix = f"tiles[{index}]."
        before = len(findings)
        check_table(raw, _TILE, "L3", prefix, findings)
        tile_id, kind = raw.get("id"), raw.get("kind")
        if "id" in raw and not names.is_param(tile_id):
            findings.append(Finding("L2", prefix + "id", f"Kachel-ID ungültig: {tile_id!r}"))
        if "kind" in raw and kind not in TILE_KINDS:
            findings.append(Finding("L3", prefix + "kind", f"{prefix}kind muss einer von {', '.join(TILE_KINDS)} sein"))
        if len(findings) > before:
            continue
        label = f"Kachel {tile_id}"

        def bad(key: str, message: str) -> None:
            findings.append(Finding("L17", prefix + key, f"{label}: {message}"))

        if tile_id in seen:
            bad("id", "ist doppelt")
            continue
        seen.add(tile_id)
        tile = {"id": tile_id, "title": raw["title"], "kind": kind, "description": raw.get("description", "")}
        allowed = {"view": ("run", "timeout", "refresh"), "chat": ("run",), "link": ("port", "path", "url"), "panel": ("run", "timeout")}[kind]
        for key in ("run", "timeout", "refresh", "port", "path", "url"):
            if key in raw and key not in allowed:
                bad(key, f"{key} gibt es bei kind = \"{kind}\" nicht")
        if kind in ("view", "chat", "panel"):
            run = raw.get("run")
            if not isinstance(run, str) or not run.strip():
                bad("run", "run muss ein nicht leerer String sein")
            else:
                tile["run"] = run
                for problem in template_problems(run, params):
                    bad("run", problem)
        if kind == "view":
            for key, default, (low, high), zero in (("timeout", 10, VIEW_TIMEOUT, False), ("refresh", 0, VIEW_REFRESH, True)):
                value = tile[key] = raw.get(key, default)
                if not is_int(value) or not (low <= value <= high or (zero and value == 0)):
                    bad(key, f"{key} muss {'0 oder ' if zero else ''}eine Ganzzahl von {low} bis {high} sein")
        if kind == "panel":
            value = tile["timeout"] = raw.get("timeout", 30)
            if not is_int(value) or not PANEL_TIMEOUT[0] <= value <= PANEL_TIMEOUT[1]:
                bad("timeout", f"timeout muss eine Ganzzahl von {PANEL_TIMEOUT[0]} bis {PANEL_TIMEOUT[1]} sein")
        if kind == "link":
            if ("port" in raw) == ("url" in raw):
                bad("url", "genau eins von port und url ist nötig")
            elif "url" in raw:
                tile["url"] = raw["url"]
                if "path" in raw:
                    bad("path", "path gibt es nur zusammen mit port")
                if not raw["url"].startswith(("http://", "https://")):
                    bad("url", "url muss mit http:// oder https:// beginnen")
                for problem in template_problems(raw["url"], params):
                    bad("url", problem)
            else:
                tile["port"], tile["path"] = raw["port"], raw.get("path", "/")
                problem = _link_port_problem(raw["port"], params, "int")
                if problem:
                    bad("port", problem)
                for problem in _path_problems(tile["path"], params):
                    bad("path", problem)
        if len(findings) == before:
            tiles.append(tile)
    return tiles


def _link_port_problem(value, params: list[Param], kind: str) -> str | None:
    """`port` eines Links: feste Zahl oder genau ein Platzhalter eines int-Parameters. `ports`: Platzhalter eines ports-Parameters."""
    low, high = LINK_PORTS
    if kind == "int" and isinstance(value, int) and not isinstance(value, bool):
        return None if low <= value <= high else f"port muss zwischen {low} und {high} liegen"
    match = _PORT_PLACEHOLDER_RE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return "port muss eine Zahl oder genau ein Platzhalter wie {port} sein" if kind == "int" else "ports muss genau ein Platzhalter wie {ports} sein"
    param = {p.name: p for p in params}.get(match.group(1))
    if param is None or param.type != kind:
        return f"Platzhalter {{{match.group(1)}}} ist kein {kind}-Parameter dieses Addons"
    if not param.has_default and not param.required:
        return f"Platzhalter {{{match.group(1)}}} hat keinen Default und ist nicht required"
    return None


def parse(data: dict, dirname: str | None = None, path: Path | None = None, origin: str = "own"):
    """Prüft den Inhalt einer addon.toml (L3, L6–L8, L17). Liefert (Addon | None, Befunde)."""
    findings: list[Finding] = []
    check_table(data, _HEAD, "L3", "", findings)
    addon_id = data.get("id")
    if "id" in data:
        if not names.is_id(addon_id):
            findings.append(Finding("L2", "id", f"Addon-ID ungültig: {addon_id!r}"))
        elif dirname is not None and addon_id != dirname:
            findings.append(Finding("L3", "id", f"id {addon_id} entspricht nicht dem Verzeichnisnamen {dirname}"))
    params = _parse_params(data, findings)
    actions = _parse_actions(data, params, findings)
    links = _parse_links(data, params, actions, findings)
    tiles = _parse_tiles(data, params, findings)
    files = []
    for index, raw in enumerate(table_list(data, "files", "L3", findings)):
        check_table(raw, _FILE, "L3", f"files[{index}].", findings)
        files.append({"mode": "0644", "owner": "dev", "overwrite": True, **raw})
    archives = []
    for index, raw in enumerate(table_list(data, "archives", "L3", findings)):
        check_table(raw, _ARCHIVE, "L3", f"archives[{index}].", findings)
        if isinstance(raw.get("src"), str) and not raw["src"].endswith(".tar.gz"):
            findings.append(Finding("L3", f"archives[{index}].src", f"archives[{index}].src muss auf .tar.gz enden"))
        archives.append(dict(raw))
    if any(f.severity == "error" for f in findings):
        return None, findings
    addon = Addon(
        id=addon_id,
        title=data["title"],
        version=data["version"],
        description=data.get("description", ""),
        platforms=tuple(data.get("platforms", PLATFORMS)),
        requires=tuple(data.get("requires", ())),
        conflicts=tuple(data.get("conflicts", ())),
        install=tuple(data.get("install", ())),
        uninstall=tuple(data.get("uninstall", ())),
        params=tuple(params),
        files=tuple(files),
        archives=tuple(archives),
        actions=tuple(actions),
        links=tuple(links),
        tiles=tuple(tiles),
        path=path,
        origin=origin,
    )
    return addon, findings


# --- Verzeichnis laden, L4 --------------------------------------------------


def _inside(root: Path, relative: str) -> Path | None:
    """Datei relativ zum Addon; None, wenn sie fehlt, kein reguläre Datei ist oder ausbricht."""
    target = root / relative
    try:
        resolved = target.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_file() or not resolved.is_relative_to(root.resolve()):
        return None
    return resolved


def load(directory, origin: str = "own"):
    """Lädt und prüft ein Addon-Verzeichnis (L3–L8). Liefert (Addon | None, Befunde)."""
    directory = Path(directory)
    manifest = directory / "addon.toml"
    if not directory.is_dir() or not manifest.is_file():
        return None, [Finding("L3", "addon.toml", f"addon.toml fehlt in {directory}")]
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return None, [Finding("L3", "addon.toml", f"Kein gültiges TOML: {exc}")]
    name = directory.resolve().name
    addon, findings = parse(data, name, directory, origin)

    for required in ("README.md", "AI.md"):
        if _inside(directory, required) is None:
            findings.append(Finding("L4", required, f"Pflichtdatei fehlt: {required}"))
    referenced = []
    for key in ("install", "uninstall"):
        value = data.get(key, [])
        if isinstance(value, list):
            referenced += [(f"{key}[{i}]", item) for i, item in enumerate(value) if isinstance(item, str)]
    for key in ("files", "archives"):
        value = data.get(key, [])
        if isinstance(value, list):
            referenced += [
                (f"{key}[{i}].src", item["src"])
                for i, item in enumerate(value)
                if isinstance(item, dict) and isinstance(item.get("src"), str)
            ]
    for path, relative in referenced:
        if relative.startswith("/") or ".." in relative.split("/"):
            findings.append(Finding("L4", path, f"Pfad muss relativ und ohne .. sein: {relative}"))
        elif _inside(directory, relative) is None:
            findings.append(Finding("L4", path, f"Datei fehlt oder liegt außerhalb des Addons: {relative}"))

    if addon is not None:
        ai = _inside(directory, "AI.md")
        if ai is not None:
            try:
                text = ai.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                findings.append(Finding("L4", "AI.md", "AI.md ist nicht UTF-8"))
            else:
                for problem in template_problems(text, list(addon.params)):
                    findings.append(Finding("L7", "AI.md", f"AI.md: {problem}"))
    if any(f.severity == "error" for f in findings):
        return None, findings
    return addon, findings


# --- Katalog (SPEC 7.6) -----------------------------------------------------


@dataclass
class Catalog:
    addons: dict = field(default_factory=dict)  # id -> Addon
    broken: dict = field(default_factory=dict)  # id -> Befunde

    def get(self, addon_id: str) -> Addon | None:
        return self.addons.get(addon_id)

    def sorted(self) -> list[Addon]:
        return [self.addons[key] for key in sorted(self.addons)]


def _scan(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def catalog(own_dir, builtin_dir=None) -> Catalog:
    """Eigene Addons überschreiben mitgelieferte mit gleicher ID."""
    builtin_dir = BUILTIN / "addons" if builtin_dir is None else Path(builtin_dir)
    result = Catalog()
    builtin_ids = set()
    for directory in _scan(builtin_dir):
        builtin_ids.add(directory.name)
        addon, findings = load(directory, "builtin")
        if addon is None:
            result.broken[directory.name] = findings
        else:
            result.addons[addon.id] = addon
    for directory in _scan(Path(own_dir)):
        origin = "override" if directory.name in builtin_ids else "own"
        addon, findings = load(directory, origin)
        result.addons.pop(directory.name, None)
        result.broken.pop(directory.name, None)
        if addon is None:
            result.broken[directory.name] = findings
        else:
            result.addons[addon.id] = addon
    return result


# --- Aktive Addons, L5, Reihenfolge (SPEC 7.7) ------------------------------


def active_ids(listed) -> list[str]:
    """`addons` einer Instanz mit base als erstem Eintrag, ohne Duplikate."""
    ids = [BASE]
    for addon_id in listed or []:
        if addon_id not in ids:
            ids.append(addon_id)
    return ids


def resolve_order(listed, cat: Catalog):
    """Topologische Sortierung über requires. Liefert (Reihenfolge, Befunde L5).

    Unbekannte Addons werden übersprungen; sie meldet der Aufrufer (L9/L10).
    """
    ids = [a for a in active_ids(listed) if cat.get(a) is not None]
    active = set(active_ids(listed))
    findings = []
    for addon_id in ids:
        addon = cat.get(addon_id)
        for needed in addon.all_requires:
            if needed not in active:
                findings.append(Finding("L5", "addons", f"Addon {addon_id} benötigt {needed}"))
        for other in addon.conflicts:
            if other in active and other != addon_id:
                findings.append(Finding("L5", "addons", f"Addon {addon_id} verträgt sich nicht mit {other}"))
    order = []
    remaining = list(ids)
    while remaining:
        for addon_id in remaining:
            needs = [r for r in cat.get(addon_id).all_requires if r in ids]
            if all(r in order for r in needs):
                order.append(addon_id)
                remaining.remove(addon_id)
                break
        else:
            findings.append(Finding("L5", "addons", "Zyklus in requires: " + ", ".join(remaining)))
            break
    return order, findings


# --- Archiv: Export und Import (L16) ----------------------------------------

MAX_UNPACKED = 200 * 1024 * 1024


def pack(addon: Addon) -> bytes:
    """Addon als tar.gz mit genau einem Verzeichnis `<id>/` auf oberster Ebene."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", dereference=True) as archive:
        for file in [addon.path] + sorted(addon.path.rglob("*")):
            relative = file.relative_to(addon.path)
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            if not (file.is_dir() or file.is_file()):
                continue
            name = addon.id if file == addon.path else f"{addon.id}/{relative.as_posix()}"
            info = archive.gettarinfo(str(file), name)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            if file.is_file():
                with open(file, "rb") as handle:
                    archive.addfile(info, handle)
            else:
                archive.addfile(info)
    return buffer.getvalue()


def _l16(message: str) -> ValidationError:
    return ValidationError.single("L16", "archive", message)


def _members(data: bytes) -> list[tuple[str, bool, int, int, object]]:
    """Einträge als (name, ist_verzeichnis, größe, modus, leser); lehnt alles außer Dateien und Verzeichnissen ab."""
    stream = io.BytesIO(data)
    if zipfile.is_zipfile(stream):
        archive = zipfile.ZipFile(stream)
        result = []
        for info in archive.infolist():
            mode = info.external_attr >> 16
            # Ohne Typbits (z. B. unter Windows gepackt) gilt der Eintrag als normale Datei.
            if stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise _l16(f"Nur Dateien und Verzeichnisse sind erlaubt: {info.filename}")
            result.append((info.filename, info.is_dir(), info.file_size, mode & 0o777,
                           lambda info=info: archive.open(info)))
        return result
    stream.seek(0)
    try:
        archive = tarfile.open(fileobj=stream, mode="r:*")
        infos = archive.getmembers()
    except (tarfile.TarError, EOFError, OSError):
        raise _l16("Archiv ist weder .tar.gz noch .zip") from None
    result = []
    for info in infos:
        if not (info.isreg() or info.isdir()):
            raise _l16(f"Nur Dateien und Verzeichnisse sind erlaubt (keine Links, keine Gerätedateien): {info.name}")
        result.append((info.name, info.isdir(), info.size, info.mode & 0o777,
                       lambda info=info: archive.extractfile(info)))
    return result


def unpack(data: bytes, destination: Path) -> Path:
    """Prüft ein Addon-Archiv nach L16 und entpackt es nach `destination`; liefert das Addon-Verzeichnis."""
    members = _members(data)
    if not members:
        raise _l16("Archiv ist leer")
    tops = set()
    total = 0
    cleaned = []
    for name, is_dir, size, mode, reader in members:
        if name.startswith("/") or "\\" in name or "\x00" in name:
            raise _l16(f"Absoluter oder ungültiger Pfad im Archiv: {name}")
        parts = [part for part in name.split("/") if part not in ("", ".")]
        if not parts or ".." in parts:
            raise _l16(f"Pfad mit .. oder leerer Pfad im Archiv: {name}")
        tops.add(parts[0])
        if len(parts) == 1 and not is_dir:
            raise _l16("Archiv braucht genau ein Verzeichnis auf oberster Ebene")
        total += size
        if total > MAX_UNPACKED:
            raise _l16("Archiv ist entpackt größer als 200 MiB")
        cleaned.append((parts, is_dir, mode, reader))
    if len(tops) != 1:
        raise _l16("Archiv braucht genau ein Verzeichnis auf oberster Ebene")
    top = tops.pop()
    if not names.is_id(top):
        raise _l16(f"Verzeichnisname ist keine gültige Addon-ID: {top}")
    destination = Path(destination)
    root = destination.resolve()
    written = 0
    for parts, is_dir, mode, reader in cleaned:
        target = destination.joinpath(*parts)
        if not target.resolve().is_relative_to(root):
            raise _l16(f"Pfad verlässt das Zielverzeichnis: {'/'.join(parts)}")
        if is_dir:
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with reader() as source, open(target, "wb") as handle:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UNPACKED:  # die Größenangaben im Archiv können lügen
                    raise _l16("Archiv ist entpackt größer als 200 MiB")
                handle.write(chunk)
        target.chmod(0o755 if mode & 0o100 else 0o644)
    return destination / top
