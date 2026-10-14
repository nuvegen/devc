"""Instanz-Konfiguration devc.toml: Prüfungen L10–L12, L15, Zusammenführen (7.5), Unterschiede."""

import re
import tomllib
from pathlib import Path

from . import addons as addons_module
from . import names, templates, tomlw
from .addons import check_table
from .errors import Finding, SecurityError, UsageError, ValidationError

_ORIGIN_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}@\d+\.\d+\.\d+$")

_INSTANCE = {
    "name": (True, None),
    "template": (False, lambda v: None if v == "" or (isinstance(v, str) and _ORIGIN_RE.match(v)) else 'muss "<id>@<version>" oder leer sein'),
    "platform": (False, None),
    **templates._BODY,
}


def _leftovers(value, path: str, findings: list) -> None:
    """Meldet verbliebene Template-Platzhalter ${…} in allen Strings."""
    if isinstance(value, str):
        if "${" in value:
            findings.append(Finding("L10", path, f"Nicht aufgelöster Platzhalter ${{…}} in {path}"))
    elif isinstance(value, dict):
        for key, item in value.items():
            _leftovers(item, f"{path}.{key}" if path else key, findings)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _leftovers(item, f"{path}[{index}]", findings)


def platform_of(data: dict) -> str:
    return data.get("platform", "lxd")


def check_data(data: dict, catalog, store_names=None) -> list[Finding]:
    """L10 (ohne Namens-Eindeutigkeit), L15, dazu L2, L5, W1 für geparste Daten."""
    findings: list[Finding] = []
    check_table(data, _INSTANCE, "L10", "", findings)
    for key in templates.HEAD_KEYS:
        if key in data:
            # check_table meldet sie schon als unbekannt; hier der verständlichere Hinweis
            findings[:] = [f for f in findings if f.path != key]
            findings.append(Finding("L10", key, f"{key} ist ein Template-Schlüssel und in einer Instanz nicht erlaubt"))
    if "name" in data:
        finding = names.check("container", data["name"], "name")
        if finding:
            findings.append(finding)
    if platform_of(data) != "lxd":
        findings.append(Finding("L10", "platform", 'platform muss "lxd" sein'))
    findings += templates.check_body(data, catalog, "L10", store_names)
    _leftovers(data, "", findings)

    given = data.get("params") if isinstance(data.get("params"), dict) else {}
    listed = data.get("addons", [])
    if not (isinstance(listed, list) and all(isinstance(a, str) for a in listed)):
        listed = []
    for addon_id in addons_module.active_ids(listed):
        addon = catalog.get(addon_id)
        if addon is None:
            continue
        values = given.get(addon_id) if isinstance(given.get(addon_id), dict) else {}
        for param in addon.params:
            if param.type != "secret" and param.required and not param.has_default and param.name not in values:
                findings.append(
                    Finding("L10", f"params.{addon_id}.{param.name}", f"Pflichtparameter fehlt: {addon_id}.{param.name}")
                )
        if platform_of(data) not in addon.platforms:
            findings.append(Finding("L15", "addons", f"Addon {addon_id} unterstützt die Plattform {platform_of(data)} nicht"))
    return findings


def check_text(text: str, catalog, store_names=None) -> tuple[dict, list[Finding]]:
    data = templates.parse_toml(text, "L10", "devc.toml")
    return data, check_data(data, catalog, store_names)


def check_shared(data: dict, allowed_roots) -> list[Finding]:
    """L11. Liegt der Shared Folder außerhalb von allowed_roots: SecurityError (E_SECURITY)."""
    area = data.get("data")
    shared = area.get("shared") if isinstance(area, dict) else None
    if shared is None:
        return []
    if not isinstance(shared, str) or not shared.startswith("/"):
        return [Finding("L11", "data.shared", "data.shared muss ein absoluter Host-Pfad sein")]
    path = Path(shared)
    if not path.is_dir():
        return [Finding("L11", "data.shared", f"Shared Folder existiert nicht als Verzeichnis: {shared}")]
    resolved = path.resolve()
    for root in allowed_roots:
        if resolved.is_relative_to(Path(root).resolve()):
            return []
    raise SecurityError(
        f"Shared Folder liegt nicht unter allowed_roots: {shared}", {"check": "L11", "path": "data.shared"}
    )


def secret_slots(data: dict, catalog) -> dict[str, dict]:
    """Alle Secret-Slots der Instanz: Slot -> {required, store}."""
    slots: dict[str, dict] = {}
    for addon_id in addons_module.active_ids(data.get("addons", [])):
        addon = catalog.get(addon_id)
        if addon is None:
            continue
        for param in addon.params:
            if param.type == "secret":
                slots[f"{addon_id}.{param.name}"] = {"required": param.required, "store": None}
    mapping = data.get("secrets") if isinstance(data.get("secrets"), dict) else {}
    for slot, store in mapping.items():
        slots.setdefault(slot, {"required": False, "store": None})["store"] = store
    if isinstance(data.get("git"), dict) and data["git"].get("remote", "").startswith("ssh://"):
        slots.setdefault(names.GIT_KEY_SLOT, {"required": False, "store": None})
    return slots


def missing_secrets(data: dict, catalog, store_names, provided=()) -> list[str]:
    """L12: Slots, die befüllt sein müssen, aber weder Store-Eintrag noch Wert haben."""
    missing = []
    for slot, info in secret_slots(data, catalog).items():
        if slot in provided:
            continue
        if info["store"] is not None:
            if info["store"] not in store_names:
                missing.append(slot)
        elif info["required"]:
            missing.append(slot)
    return missing


def effective_params(data: dict, catalog) -> dict[str, dict]:
    """Je aktivem Addon die wirksamen Nicht-Secret-Parameter (Instanz, sonst Default)."""
    given = data.get("params") if isinstance(data.get("params"), dict) else {}
    result = {}
    for addon_id in addons_module.active_ids(data.get("addons", [])):
        addon = catalog.get(addon_id)
        if addon is not None:
            result[addon_id] = addon.values(given.get(addon_id))
    return result


# --- Zusammenführen von CLI-Optionen (SPEC 7.5) -----------------------------


def _typed(param, raw: str):
    if param.type == "int":
        try:
            return int(raw, 10)
        except ValueError:
            raise UsageError(f"--param {param.name}: {raw!r} ist keine Ganzzahl") from None
    if param.type == "bool":
        if raw not in ("true", "false"):
            raise UsageError(f"--param {param.name}: nur true oder false")
        return raw == "true"
    return raw


def parse_param(spec: str, catalog) -> tuple[str, str, object]:
    """Zerlegt `<addon>.<param>=<wert>` und wandelt den Wert in den Typ des Parameters."""
    target, sep, raw = spec.partition("=")
    addon_id, dot, name = target.partition(".")
    if not sep or not dot or not names.is_id(addon_id) or not names.is_param(name):
        raise UsageError(f"--param erwartet <addon>.<param>=<wert>: {spec}")
    addon = catalog.get(addon_id)
    param = addon.param(name) if addon else None
    if param is None:
        raise UsageError(f"--param: Addon {addon_id} hat keinen Parameter {name}")
    if param.type == "secret":
        raise UsageError(f"--param: {addon_id}.{name} ist ein Secret; --secret verwenden")
    return addon_id, name, _typed(param, raw)


def merge(text: str, catalog, addons=(), params=(), secret_stores=None, shared=None, git=None,
          image=None, cpu=None, memory=None, disk=None) -> str:
    """Wendet CLI-Optionen auf einen Entwurf an. Ohne Optionen bleibt der Text unverändert.

    Mit Optionen wird die Datei neu geschrieben; Kommentare des Entwurfs gehen dabei verloren.
    """
    secret_stores = secret_stores or {}
    options = [addons, params, secret_stores, shared, git, image, cpu, memory, disk]
    if not any(option not in (None, (), [], {}) for option in options):
        return text
    data = tomllib.loads(text)
    listed = list(data.get("addons", []))
    for addon_id in addons:
        if addon_id not in listed:
            listed.append(addon_id)
    data["addons"] = listed
    for spec in params:
        addon_id, name, value = parse_param(spec, catalog)
        data.setdefault("params", {}).setdefault(addon_id, {})[name] = value
    for slot, store in secret_stores.items():
        data.setdefault("secrets", {})[slot] = store
    if shared is not None:
        data["data"] = {"shared": shared}
    if git is not None:
        data.setdefault("git", {})["remote"] = git
    if image is not None:
        data["image"] = image
    for key, value in (("cpu", cpu), ("memory", memory), ("disk", disk)):
        if value is not None:
            data.setdefault("resources", {})[key] = value
    comment = text.splitlines()[0] + "\n" if text.startswith("#") else ""
    return comment + tomlw.dumps(data)


def remove_addon(text: str, addon_id: str) -> str:
    """Nimmt ein Addon samt seiner Parameter und Secret-Verweise aus einer devc.toml."""
    data = tomllib.loads(text)
    if addon_id not in data.get("addons", []):
        raise UsageError(f"Addon {addon_id} steht nicht in addons")
    data["addons"] = [a for a in data["addons"] if a != addon_id]
    if isinstance(data.get("params"), dict):
        data["params"].pop(addon_id, None)
        if not data["params"]:
            del data["params"]
    if isinstance(data.get("secrets"), dict):
        data["secrets"] = {k: v for k, v in data["secrets"].items() if not k.startswith(addon_id + ".")}
        if not data["secrets"]:
            del data["secrets"]
    comment = text.splitlines()[0] + "\n" if text.startswith("#") else ""
    return comment + tomlw.dumps(data)


# --- Unterschiede für config apply (SPEC 8.4) -------------------------------


def resources_of(data: dict, defaults: dict) -> dict:
    given = data.get("resources") if isinstance(data.get("resources"), dict) else {}
    return {key: given.get(key, defaults[key]) for key in ("cpu", "memory", "disk")}


def diff(old: dict, new: dict, catalog, defaults: dict) -> dict:
    """Unterschiede zwischen zwei Instanz-Konfigurationen."""
    old_ids = addons_module.active_ids(old.get("addons", []))
    new_ids = addons_module.active_ids(new.get("addons", []))
    old_params = effective_params(old, catalog)
    new_params = effective_params(new, catalog)
    result = {
        "addons_added": [a for a in new_ids if a not in old_ids],
        "addons_removed": [a for a in old_ids if a not in new_ids],
        "params_changed": [a for a in new_ids if a in old_ids and old_params.get(a) != new_params.get(a)],
        "resources": resources_of(old, defaults) != resources_of(new, defaults),
        "image": old.get("image", defaults["image"]) != new.get("image", defaults["image"]),
        "data": old.get("data") != new.get("data"),
        "secrets": old.get("secrets", {}) != new.get("secrets", {}),
        "git": old.get("git", {}) != new.get("git", {}),
        "files": old.get("files", []) != new.get("files", []),
        "docs": old.get("docs", {}) != new.get("docs", {}),
    }
    result["changed"] = any(result.values())
    return result


def require_valid(findings) -> None:
    if any(f.severity == "error" for f in findings):
        raise ValidationError(findings)
