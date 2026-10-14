"""Kacheln der Art `panel`: Das Addon liefert Daten, devc zeichnet daraus eine bedienbare Seite (SPEC 8.13).

devc ruft den Befehl der Kachel im Container auf und gibt ihm die Anfrage als eine JSON-Zeile auf stdin:
eine Ansicht (`view`) oder eine Aktion (`action`). Die Antwort ist ein JSON-Objekt auf stdout. Dieses Modul
prüft beide Richtungen und reicht nur die bekannten Bausteine weiter; alles andere fällt weg.
"""

import json
import re

from . import actions, tiles
from .errors import EngineError, JobFailedError, UsageError

MAX_OUTPUT = 1024 * 1024  # Zeichen der Antwort eines Skripts
MAX_TEXT = 256 * 1024  # je Text- oder Code-Baustein
MAX_BLOCKS = 50
MAX_ROWS = 500
MAX_COLUMNS = 12
MAX_BUTTONS = 8
MAX_FIELDS = 20
MAX_ARGS = 20
MAX_ARG = 4096
MAX_VALUE = 64 * 1024
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
FIELD_TYPES = ("text", "multiline", "bool", "choice")
STYLES = ("primary", "danger")


def _is_id(value) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


# --- Anfrage -------------------------------------------------------------------


def request(view=None, action=None, args=None, values=None) -> dict:
    """Prüft die Anfrage des Nutzers; liefert das Objekt für das Skript."""
    if action is not None and view is not None:
        raise UsageError("Genau eins von view und action angeben")
    args = {} if args is None else args
    if not isinstance(args, dict) or len(args) > MAX_ARGS:
        raise UsageError("args muss ein Objekt mit höchstens 20 Einträgen sein")
    for key, value in args.items():
        if not _is_id(key) or not isinstance(value, str) or len(value) > MAX_ARG or "\x00" in value:
            raise UsageError(f"args.{key} ist ungültig")
    if action is None:
        if values:
            raise UsageError("values gibt es nur bei einer Aktion")
        if view not in (None, "") and not _is_id(view):
            raise UsageError("view ist ungültig")
        return {"view": view or "", "args": args}
    if not _is_id(action):
        raise UsageError("action ist ungültig")
    values = {} if values is None else values
    if not isinstance(values, dict) or len(values) > MAX_FIELDS:
        raise UsageError("values muss ein Objekt mit höchstens 20 Einträgen sein")
    for key, value in values.items():
        ok = isinstance(value, bool) or (isinstance(value, str) and len(value) <= MAX_VALUE and "\x00" not in value)
        if not _is_id(key) or not ok:
            raise UsageError(f"values.{key} ist ungültig")
    return {"action": action, "args": args, "values": values}


# --- Antwort -------------------------------------------------------------------


class _Bad(Exception):
    pass


def _text(value, limit: int, what: str, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise _Bad(f"{what} muss Text sein")
    return value[:limit]


def _target(raw: dict, what: str) -> dict:
    args = raw.get("args", {})
    if not isinstance(args, dict) or len(args) > MAX_ARGS or not all(_is_id(k) and isinstance(v, str) for k, v in args.items()):
        raise _Bad(f"{what}: args ist ungültig")
    return {key: value[:MAX_ARG] for key, value in args.items()}


def _button(raw, what: str) -> dict:
    if not isinstance(raw, dict):
        raise _Bad(f"{what} muss ein Objekt sein")
    button = {"label": _text(raw.get("label"), 60, f"{what}.label", True), "args": _target(raw, what)}
    if not button["label"]:
        raise _Bad(f"{what}.label fehlt")
    if ("view" in raw) == ("action" in raw):
        raise _Bad(f"{what}: genau eins von view und action ist nötig")
    if "view" in raw:
        if raw["view"] != "" and not _is_id(raw["view"]):
            raise _Bad(f"{what}.view ist ungültig")
        button["view"] = raw["view"]
    else:
        if not _is_id(raw["action"]):
            raise _Bad(f"{what}.action ist ungültig")
        button["action"] = raw["action"]
        confirm = _text(raw.get("confirm"), 500, f"{what}.confirm")
        if confirm:
            button["confirm"] = confirm
    if raw.get("style") in STYLES:
        button["style"] = raw["style"]
    return button


def _buttons(raw, what: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_BUTTONS:
        raise _Bad(f"{what} muss eine Liste mit höchstens {MAX_BUTTONS} Einträgen sein")
    return [_button(item, f"{what}[{index}]") for index, item in enumerate(raw)]


def _field(raw, what: str) -> dict:
    if not isinstance(raw, dict) or not _is_id(raw.get("name")) or raw.get("type") not in FIELD_TYPES:
        raise _Bad(f"{what}: name oder type ist ungültig")
    field = {"name": raw["name"], "type": raw["type"], "label": _text(raw.get("label"), 80, f"{what}.label") or raw["name"],
             "hint": _text(raw.get("hint"), 300, f"{what}.hint"), "required": raw.get("required") is True}
    if field["type"] == "bool":
        field["value"] = raw.get("value") is True
    elif field["type"] == "choice":
        options = raw.get("options")
        if not isinstance(options, list) or not options or len(options) > 100 or not all(isinstance(o, str) for o in options):
            raise _Bad(f"{what}.options muss eine Liste von Texten sein")
        field["options"] = [o[:200] for o in options]
        field["value"] = raw["value"] if raw.get("value") in field["options"] else field["options"][0]
    else:
        field["value"] = _text(raw.get("value"), MAX_VALUE, f"{what}.value")
    return field


def _block(raw, what: str) -> dict:
    if not isinstance(raw, dict):
        raise _Bad(f"{what} muss ein Objekt sein")
    kind = raw.get("type")
    block = {"type": kind, "title": _text(raw.get("title"), 120, f"{what}.title")}
    if kind in ("text", "code"):
        block["text"] = _text(raw.get("text"), MAX_TEXT, f"{what}.text", True)
    elif kind == "table":
        columns, rows = raw.get("columns"), raw.get("rows")
        if not isinstance(columns, list) or not columns or len(columns) > MAX_COLUMNS or not all(isinstance(c, str) for c in columns):
            raise _Bad(f"{what}.columns muss eine Liste von Texten sein")
        if not isinstance(rows, list):
            raise _Bad(f"{what}.rows muss eine Liste sein")
        block["columns"] = [c[:80] for c in columns]
        block["truncated"] = len(rows) > MAX_ROWS
        block["empty"] = _text(raw.get("empty"), 200, f"{what}.empty")
        block["rows"] = []
        for index, row in enumerate(rows[:MAX_ROWS]):
            cells = row.get("cells") if isinstance(row, dict) else None
            if not isinstance(cells, list) or len(cells) != len(columns) or not all(isinstance(c, str) for c in cells):
                raise _Bad(f"{what}.rows[{index}].cells passt nicht zu columns")
            block["rows"].append({"cells": [c[:1000] for c in cells], "actions": _buttons(row.get("actions"), f"{what}.rows[{index}].actions")})
    elif kind == "actions":
        block["actions"] = _buttons(raw.get("actions"), f"{what}.actions")
    elif kind == "form":
        fields = raw.get("fields")
        if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
            raise _Bad(f"{what}.fields muss eine Liste mit höchstens {MAX_FIELDS} Einträgen sein")
        block["fields"] = [_field(item, f"{what}.fields[{index}]") for index, item in enumerate(fields)]
        if len({f["name"] for f in block["fields"]}) != len(block["fields"]):
            raise _Bad(f"{what}.fields: ein Name ist doppelt")
        block["submit"] = _button(raw.get("submit"), f"{what}.submit")
        if "action" not in block["submit"]:
            raise _Bad(f"{what}.submit braucht action")
    else:
        raise _Bad(f"{what}.type ist unbekannt: {kind!r}")
    return block


def page(raw) -> dict:
    """Antwort auf eine Ansicht: nur bekannte Bausteine, mit Grenzen."""
    if not isinstance(raw, dict) or not isinstance(raw.get("blocks"), list):
        raise _Bad("blocks fehlt")
    if len(raw["blocks"]) > MAX_BLOCKS:
        raise _Bad(f"mehr als {MAX_BLOCKS} Bausteine")
    return {"title": _text(raw.get("title"), 120, "title"), "note": _text(raw.get("note"), 500, "note"),
            "blocks": [_block(item, f"blocks[{index}]") for index, item in enumerate(raw["blocks"])]}


def outcome(raw) -> dict:
    """Antwort auf eine Aktion: Erfolg, Meldung, optional Ausgabe und die Ansicht danach."""
    if not isinstance(raw, dict) or not isinstance(raw.get("ok"), bool):
        raise _Bad("ok fehlt")
    result = {"ok": raw["ok"], "message": _text(raw.get("message"), 500, "message"), "output": _text(raw.get("output"), MAX_TEXT, "output")}
    then = raw.get("view")
    if then is not None:
        if not isinstance(then, dict) or not (then.get("view") == "" or _is_id(then.get("view"))):
            raise _Bad("view ist ungültig")
        result["view"] = {"view": then["view"], "args": _target(then, "view")}
    return result


def call(provider, container: str, addon, tile: dict, given: dict, ip: str | None, asked: dict) -> dict:
    """Führt den Befehl der Kachel mit der Anfrage aus und liefert die geprüfte Antwort."""
    name = tiles.ref(addon, tile)
    try:
        # Die Anfrage geht über stdin: Eingaben des Nutzers stehen so in keiner Prozessliste und keinem Log.
        result = provider.exec(container, ["bash", "-lc", tiles.command(addon, tile, given, container, ip)],
                               user="dev", cwd=actions.WORKDIR, stdin=json.dumps(asked, ensure_ascii=False) + "\n", timeout=tile["timeout"])
    except EngineError as exc:
        if exc.details.get("timeout"):
            raise JobFailedError(f"Kachel {name}: Zeitüberschreitung nach {tile['timeout']} s") from None
        raise
    if result.rc != 0:
        last = (result.stderr.strip().splitlines() or [""])[-1][:200]
        raise JobFailedError(f"Kachel {name} endete mit Exit {result.rc}" + (f": {last}" if last else ""))
    if len(result.stdout) > MAX_OUTPUT:
        raise JobFailedError(f"Kachel {name}: Die Antwort ist größer als {MAX_OUTPUT // 1024} KiB")
    try:
        raw = json.loads(result.stdout)
        answer = outcome(raw) if "action" in asked else page(raw)
    except ValueError:
        raise JobFailedError(f"Kachel {name}: Die Antwort ist kein JSON") from None
    except _Bad as exc:
        raise JobFailedError(f"Kachel {name}: Die Antwort ist ungültig: {exc}") from None
    return {"tile": name, "title": tile["title"], **answer}
