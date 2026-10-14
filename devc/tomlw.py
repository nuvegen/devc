"""TOML-Writer für die Teilmenge aus SPEC 7.1.

Unterstützt: str, int, bool, Listen aus Skalaren, verschachtelte Tabellen und
Listen von Tabellen. Alles andere (Float, Datum, None, gemischte Listen) → ValueError.
"""

import re

_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def dumps(data: dict) -> str:
    if not isinstance(data, dict):
        raise ValueError("tomlw.dumps erwartet ein dict")
    out: list[str] = []
    _emit_table(data, [], out)
    while out and out[0] == "":
        out.pop(0)
    return "\n".join(out) + "\n" if out else ""


def _emit_table(table: dict, path: list[str], out: list[str]) -> None:
    tables = []
    arrays = []
    for key, value in table.items():
        if not isinstance(key, str):
            raise ValueError(f"Schlüssel muss str sein: {key!r}")
        if isinstance(value, dict):
            tables.append((key, value))
        elif _is_table_array(value, path + [key]):
            arrays.append((key, value))
        else:
            out.append(f"{_key(key)} = {_value(value, path + [key])}")
    for key, value in tables:
        out.append("")
        out.append("[" + _dotted(path + [key]) + "]")
        _emit_table(value, path + [key], out)
    for key, items in arrays:
        for item in items:
            out.append("")
            out.append("[[" + _dotted(path + [key]) + "]]")
            _emit_table(item, path + [key], out)


def _is_table_array(value, path: list[str]) -> bool:
    if not isinstance(value, list) or not value:
        return False
    dicts = [isinstance(item, dict) for item in value]
    if all(dicts):
        return True
    if any(dicts):
        raise ValueError(f"Liste mischt Tabellen und Skalare: {_dotted(path)}")
    return False


def _dotted(path: list[str]) -> str:
    return ".".join(_key(part) for part in path)


def _key(key: str) -> str:
    if _BARE_KEY_RE.match(key):
        return key
    return _basic(key)


def _value(value, path: list[str]) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(item, path) for item in value) + "]"
    return _scalar(value, path)


def _scalar(value, path: list[str]) -> str:
    # bool vor int prüfen: bool ist eine Unterklasse von int
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _multiline(value) if "\n" in value else _basic(value)
    raise ValueError(f"Typ {type(value).__name__} wird nicht unterstützt: {_dotted(path)}")


def _escape_char(char: str, keep_newline: bool) -> str:
    if char == "\n" and keep_newline:
        return char
    if char in _ESCAPES:
        return _ESCAPES[char]
    if ord(char) < 0x20 or ord(char) == 0x7F:
        return f"\\u{ord(char):04x}"
    return char


def _basic(text: str) -> str:
    return '"' + "".join(_escape_char(c, False) for c in text) + '"'


def _multiline(text: str) -> str:
    # Der Zeilenumbruch direkt nach den öffnenden Anführungszeichen wird beim Lesen verworfen.
    return '"""\n' + "".join(_escape_char(c, True) for c in text) + '"""'
