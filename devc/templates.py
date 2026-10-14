"""Templates laden, prüfen (L9, W1) und Entwürfe erzeugen (SPEC 6.3, 7.4)."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import addons as addons_module
from . import names, tomlw
from .addons import check_table, is_int, t_bool, t_mode, t_platforms, t_str, t_text, t_version
from .errors import Finding, ValidationError, has_errors

HEAD_KEYS = ("id", "title", "version", "description", "platforms")
BUILTIN = addons_module.BUILTIN / "templates"
FILE_ROOTS = ("/work/", "/home/dev/")
WORKDIR = "/work"

_TABLE_RE = re.compile(r"^\s*\[")
_HEAD_LINE_RE = re.compile(r"^\s*(id|title|version|description|platforms)\s*=")
_REMOTE_RE = re.compile(r"^(?:(?:ssh|https)://\S+|[\w.-]+@[\w.\[\]-]+:\S+)$")
_LINE_RE = re.compile(r"\(at line (\d+), column \d+\)")


def _t_image(value):
    return None if isinstance(value, str) and value.strip() else "muss ein nicht leerer String sein"


def _t_table(value):
    return None if isinstance(value, dict) else "muss eine Tabelle sein"


_BODY = {
    "image": (False, _t_image),
    "addons": (False, addons_module.t_id_list),
    "resources": (False, _t_table),
    "data": (False, _t_table),
    "params": (False, _t_table),
    "secrets": (False, _t_table),
    "git": (False, _t_table),
    "files": (False, None),
    "docs": (False, _t_table),
}
_TEMPLATE = {
    "id": (True, None),
    "title": (True, t_text(1, 60)),
    "version": (True, t_version),
    "description": (False, t_text(0, 200)),
    "platforms": (False, t_platforms),
    **_BODY,
}
_RESOURCES = {
    "cpu": (False, lambda v: None if is_int(v) and v >= 1 else "muss eine Ganzzahl ≥ 1 sein"),
    "memory": (False, lambda v: None if names.is_size(v) else "muss dem Muster ^\\d+(MiB|GiB|TiB)$ entsprechen"),
    "disk": (False, lambda v: None if names.is_size(v) else "muss dem Muster ^\\d+(MiB|GiB|TiB)$ entsprechen"),
}
_FILE = {"dest": (True, None), "content": (True, t_str), "mode": (False, t_mode), "overwrite": (False, t_bool)}
_DOCS = {"readme": (False, t_str), "ai": (False, t_str)}
_GIT = {"remote": (False, None)}


def parse_toml(text: str, check: str, source: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        match = _LINE_RE.search(str(exc))
        line = int(match.group(1)) if match else None
        raise ValidationError.single(check, source, f"Kein gültiges TOML: {exc}", line=line) from None


def check_body(data: dict, catalog, check: str, store_names=None) -> list[Finding]:
    """Prüft die Felder, die Template und Instanz gemeinsam haben (L9 bzw. L10, dazu L5, W1).

    store_names: Namen der vorhandenen Store-Einträge für W1; None = W1 nicht prüfen.
    """
    findings: list[Finding] = []
    listed = data.get("addons", [])
    if not (isinstance(listed, list) and all(isinstance(a, str) for a in listed)):
        listed = []
    active = addons_module.active_ids(listed)
    for addon_id in active:
        if catalog.get(addon_id) is None:
            if addon_id in catalog.broken:
                findings.append(Finding(check, "addons", f"Addon {addon_id} ist fehlerhaft (devc addon validate)"))
            else:
                findings.append(Finding(check, "addons", f"Addon {addon_id} existiert nicht"))
    _, order_findings = addons_module.resolve_order(listed, catalog)
    findings += order_findings

    if isinstance(data.get("resources"), dict):
        check_table(data["resources"], _RESOURCES, check, "resources.", findings)

    area = data.get("data")
    if not isinstance(area, dict) or (("code" in area) == ("shared" in area)):
        findings.append(Finding(check, "data", "Genau eins von data.code und data.shared ist nötig"))
    if isinstance(area, dict):
        for key in area:
            if key not in ("code", "shared"):
                findings.append(Finding(check, f"data.{key}", f"Unbekannter Schlüssel: data.{key}"))
        if "code" in area and area["code"] != "volume":
            findings.append(Finding(check, "data.code", 'data.code kennt nur den Wert "volume"'))
        if "shared" in area and not (isinstance(area["shared"], str) and area["shared"].startswith("/")):
            findings.append(Finding(check, "data.shared", "data.shared muss ein absoluter Host-Pfad sein"))

    params = data.get("params")
    if isinstance(params, dict):
        for addon_id, values in params.items():
            path = f"params.{addon_id}"
            addon = catalog.get(addon_id)
            if not isinstance(values, dict):
                findings.append(Finding(check, path, f"{path} muss eine Tabelle sein"))
            elif addon_id not in active:
                findings.append(Finding(check, path, f"{path}: Addon {addon_id} ist nicht aktiv"))
            elif addon is not None:
                for name, value in values.items():
                    param = addon.param(name)
                    if param is None:
                        findings.append(Finding(check, f"{path}.{name}", f"Addon {addon_id} hat keinen Parameter {name}"))
                        continue
                    problem = param.problem(value)
                    if problem:
                        findings.append(Finding(check, f"{path}.{name}", f"{path}.{name} {problem}"))

    secrets = data.get("secrets")
    if isinstance(secrets, dict):
        for slot, store in secrets.items():
            path = f'secrets."{slot}"'
            parts = names.split_slot(slot)
            if parts is None:
                findings.append(Finding(check, path, f"Secret-Slot ungültig: {slot}"))
            elif slot != names.GIT_KEY_SLOT:
                addon = catalog.get(parts[0])
                param = addon.param(parts[1]) if addon is not None and parts[0] in active else None
                if param is None or param.type != "secret":
                    findings.append(Finding(check, path, f"{slot} ist kein Secret-Parameter eines aktiven Addons"))
            if not names.is_store(store):
                findings.append(Finding(check, path, f"Store-Name ungültig: {str(store)[:80]!r}"))
            elif store_names is not None and store not in store_names:
                findings.append(Finding("W1", path, f"Store-Eintrag {store} existiert nicht", "warning"))

    git = data.get("git")
    if isinstance(git, dict):
        check_table(git, _GIT, check, "git.", findings)
        remote = git.get("remote", "")
        if not isinstance(remote, str) or (remote and not _REMOTE_RE.match(remote)):
            findings.append(Finding(check, "git.remote", "git.remote muss eine SSH- oder HTTPS-URL sein"))

    files = data.get("files", [])
    if not (isinstance(files, list) and all(isinstance(item, dict) for item in files)):
        findings.append(Finding(check, "files", "files muss eine Liste von Tabellen ([[files]]) sein"))
        files = []
    for index, item in enumerate(files):
        prefix = f"files[{index}]."
        check_table(item, _FILE, check, prefix, findings)
        dest = item.get("dest")
        if "dest" in item and not (
            isinstance(dest, str)
            and dest.startswith(FILE_ROOTS)
            and ".." not in dest.split("/")
            and not dest.endswith("/")
        ):
            findings.append(Finding(check, prefix + "dest", f"{prefix}dest muss eine Datei unter /work/ oder /home/dev/ sein"))

    if isinstance(data.get("docs"), dict):
        check_table(data["docs"], _DOCS, check, "docs.", findings)
    return findings


def _head_lines(text: str) -> dict[str, str]:
    """Zeilen der Kopf-Schlüssel vor der ersten Tabellen-Kopfzeile."""
    found = {}
    for line in text.splitlines():
        if _TABLE_RE.match(line):
            break
        match = _HEAD_LINE_RE.match(line)
        if match:
            found.setdefault(match.group(1), line)
    return found


def check_text(text: str, expected_id: str | None, catalog, store_names=None) -> tuple[dict, list[Finding]]:
    """L9 (und W1) für den Text eines Templates. Wirft bei ungültigem TOML ValidationError."""
    data = parse_toml(text, "L9", f"{expected_id}.toml" if expected_id else "template")
    findings: list[Finding] = []
    check_table(data, _TEMPLATE, "L9", "", findings)
    template_id = data.get("id")
    if "id" in data:
        if not names.is_id(template_id):
            findings.append(Finding("L2", "id", f"Template-ID ungültig: {template_id!r}"))
        elif expected_id is not None and template_id != expected_id:
            findings.append(Finding("L9", "id", f"id {template_id} entspricht nicht dem Dateinamen {expected_id}.toml"))
    lines = _head_lines(text)
    for key in HEAD_KEYS:
        if key not in data:
            continue
        single = False
        if key in lines:
            try:
                single = tomllib.loads(lines[key]) == {key: data[key]}
            except tomllib.TOMLDecodeError:
                single = False
        if not single:
            findings.append(Finding("L9", key, f"{key} muss als key = value auf einer Zeile im Kopf stehen"))
    findings += check_body(data, catalog, "L9", store_names)
    return data, findings


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    version: str
    description: str
    origin: str
    path: Path
    text: str
    data: dict

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "version": self.version,
            "description": self.description,
            "origin": self.origin,
            "origin_label": addons_module.ORIGIN_LABELS[self.origin],
            "addons": list(self.data.get("addons", [])),
        }


def _files(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    return {p.stem: p for p in sorted(root.glob("*.toml")) if p.is_file()}


def locate(own_dir, builtin_dir=None) -> dict[str, tuple[Path, str]]:
    """Template-ID -> (Datei, Herkunft); eigene überschreiben mitgelieferte (SPEC 7.6)."""
    builtin = _files(BUILTIN if builtin_dir is None else Path(builtin_dir))
    own = _files(Path(own_dir))
    found = {key: (path, "builtin") for key, path in builtin.items()}
    for key, path in own.items():
        found[key] = (path, "override" if key in builtin else "own")
    return found


def read(template_id: str, path: Path, origin: str) -> Template:
    """Liest ein Template ohne inhaltliche Prüfung (nur TOML)."""
    text = path.read_text(encoding="utf-8")
    data = parse_toml(text, "L9", path.name)
    return Template(
        id=template_id,
        title=str(data.get("title", template_id)),
        version=str(data.get("version", "")),
        description=str(data.get("description", "")),
        origin=origin,
        path=path,
        text=text,
        data=data,
    )


# --- Entwurf (SPEC 7.4) -----------------------------------------------------


def draft(text: str, name: str, catalog, expected_id: str | None = None) -> str:
    """Erzeugt aus einem Template-Text den Text einer devc.toml für den Container `name`."""
    data, findings = check_text(text, expected_id, catalog)
    if has_errors(findings):
        raise ValidationError(findings)
    origin = f"{data['id']}@{data['version']}"
    kept = []
    in_head = True
    for line in text.splitlines():
        if in_head and _TABLE_RE.match(line):
            in_head = False
        if in_head and _HEAD_LINE_RE.match(line):
            continue
        kept.append(line)
    head = [
        f"# Entwurf aus {origin} – gilt nur für diese Instanz",
        f"name = {tomlw.dumps({'v': name}).split(' = ', 1)[1].rstrip()}",
        f'template = "{origin}"',
        'platform = "lxd"',
    ]
    result = "\n".join(head + kept) + "\n"
    return result.replace("${name}", name).replace("${workdir}", WORKDIR)


def blank_draft(name: str, defaults: dict) -> str:
    """Entwurf ohne Template aus den Defaults der config.toml."""
    return tomlw.dumps(
        {
            "name": name,
            "platform": "lxd",
            "image": defaults["image"],
            "addons": list(defaults["addons"]),
            "resources": {"cpu": defaults["cpu"], "memory": defaults["memory"], "disk": defaults["disk"]},
            "data": {"code": "volume"},
        }
    )
