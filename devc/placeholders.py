"""Platzhalter in Aktionen, Links und AI.md (SPEC 7.3).

    vorlage     = { text | platzhalter | escape }
    platzhalter = "{" name "}"
    name        = [a-z] [a-z0-9_]{0,31}
    escape      = "{{" | "}}"
"""

import re
import shlex
from urllib.parse import quote

SYSTEM_NAMES = ("container", "container_ip", "workdir", "user")
MODES = ("run", "url", "plain")

_TOKEN_RE = re.compile(r"\{\{|\}\}|\{([a-z][a-z0-9_]{0,31})\}|[{}]")


class PlaceholderError(ValueError):
    """Syntaxfehler in einer Vorlage oder fehlender Wert."""


def parse(template: str) -> list[tuple[str, str]]:
    """Zerlegt eine Vorlage in ("text", …) und ("name", …)."""
    parts = []
    position = 0
    for match in _TOKEN_RE.finditer(template):
        if match.start() > position:
            parts.append(("text", template[position:match.start()]))
        token = match.group(0)
        if token == "{{":
            parts.append(("text", "{"))
        elif token == "}}":
            parts.append(("text", "}"))
        elif match.group(1):
            parts.append(("name", match.group(1)))
        else:
            raise PlaceholderError(
                f"Einzelne Klammer {token} an Position {match.start() + 1}; als {token}{token} schreiben"
            )
        position = match.end()
    if position < len(template):
        parts.append(("text", template[position:]))
    return parts


def names_in(template: str) -> list[str]:
    """Alle Platzhalternamen in Reihenfolge des Vorkommens, ohne Duplikate."""
    seen = []
    for kind, value in parse(template):
        if kind == "name" and value not in seen:
            seen.append(value)
    return seen


def _text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render(template: str, values: dict, mode: str = "plain") -> str:
    """Setzt Werte ein: `run` mit shlex.quote, `url` mit URL-Quote, `plain` unverändert."""
    if mode not in MODES:
        raise ValueError(f"Unbekannter Modus: {mode}")
    out = []
    for kind, value in parse(template):
        if kind == "text":
            out.append(value)
            continue
        if values.get(value) is None:
            raise PlaceholderError(f"Kein Wert für Platzhalter {{{value}}}")
        text = _text(values[value])
        if mode == "run":
            text = shlex.quote(text)
        elif mode == "url" and value != "container_ip":
            text = quote(text, safe="")
        out.append(text)
    return "".join(out)
