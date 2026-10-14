"""Agent-Doku: CLAUDE.md, AGENTS.md und das Infoverzeichnis .devc/ (SPEC 6.5)."""

import re

from . import placeholders
from .errors import ValidationError

BEGIN = "<!-- devc:begin -->"
END = "<!-- devc:end -->"
WORKDIR = "/work"
INFO_DIR = ".devc"  # Infoverzeichnis unter /work, vollständig von devc verwaltet
RULES = (
    "Keine Secrets ins Repository.",
    "Secrets liegen nur unter /run/devc/secrets/.",
    "Addon-Aktionen werden vom Nutzer über devc gestartet, nicht selbst nachgebaut.",
)


def system_values(container: str, ip: str | None = None) -> dict:
    return {"container": container, "container_ip": ip, "workdir": WORKDIR, "user": "dev"}


def _facts(data: dict, image: str) -> list[str]:
    facts = [f"- Plattform: {data.get('platform', 'lxd')}", f"- Image: {image}"]
    if data.get("template"):
        facts.append(f"- Template: {data['template']}")
    facts.append(f"- Arbeitsverzeichnis: {WORKDIR}")
    area = data.get("data", {})
    if area.get("shared"):
        facts.append(f"- Shared Folder: {area['shared']}")
    remote = (data.get("git") or {}).get("remote", "")
    if remote:
        facts.append(f"- Git-Remote: {hide_credentials(remote)}")
    return facts


def block(data: dict, image: str, entries: list[tuple], ip: str | None = None) -> str:
    """Baut den devc-Block. entries: (Addon, AI.md-Text, Parameterwerte) in Installationsreihenfolge."""
    name = data["name"]
    parts = [f"# Dev-Container {name}\n\n" + "\n".join(_facts(data, image))]
    if entries:
        # Der Wegweiser ins Infoverzeichnis: Ohne ihn weiß ein Agent nicht, welche Addons es gibt und wo ihre Anleitung liegt.
        listed = [f"- `{addon.id}` {addon.version} – {addon.description or addon.title} → `{INFO_DIR}/addons/{addon.id}.md`" for addon, _, _ in entries]
        parts.append("## Addons in diesem Container\n\n" + "\n".join(listed) + "\n\n"
                     f"Lies die Datei eines Addons, bevor du es benutzt oder dem Nutzer dazu etwas rätst: Dort stehen Einstellungen, "
                     f"Aktionen, Links und die Anleitung. Übersicht und weitere Addons, die der Nutzer ergänzen kann: `{INFO_DIR}/README.md`. "
                     f"Die Konfiguration dieses Containers: `{INFO_DIR}/config.toml` (Kopie; ändern kann sie nur der Nutzer über devc).")
    for addon, text, values in entries:
        merged = dict(values)
        merged.update(system_values(name, ip or "(Container läuft nicht)"))
        try:
            rendered = placeholders.render(text, merged, "plain")
        except placeholders.PlaceholderError as exc:
            raise ValidationError.single("L7", f"{addon.id}/AI.md", f"AI.md von {addon.id}: {exc}") from None
        if rendered.strip():
            parts.append(rendered.strip())
    ai = (data.get("docs") or {}).get("ai", "")
    if ai.strip():
        parts.append(ai.strip())
    parts.append("## Regeln\n\n" + "\n".join(f"- {rule}" for rule in RULES))
    return BEGIN + "\n" + "\n\n".join(parts) + "\n" + END + "\n"


def merge(existing: str | None, new_block: str) -> str:
    """Setzt den Block in eine vorhandene CLAUDE.md ein; Text außerhalb der Marker bleibt.

    Fehlt die Datei, entsteht sie nur mit dem Block. Fehlt ein Marker oder stimmt die
    Reihenfolge nicht: E_VALIDATION, nichts wird geändert.
    """
    if existing is None:
        return new_block
    # Eine vorhandene CLAUDE.md ganz ohne Marker gilt nach SPEC 6.5 als Fehler.
    begins, ends = existing.count(BEGIN), existing.count(END)
    start, stop = existing.find(BEGIN), existing.find(END)
    if begins != 1 or ends != 1 or start > stop:
        raise ValidationError.single(
            "L10",
            "/work/CLAUDE.md",
            "CLAUDE.md: devc-Marker fehlen, sind doppelt oder in falscher Reihenfolge; "
            f"erwartet werden je einmal {BEGIN} und {END}",
        )
    after = existing[stop + len(END):]
    if after.startswith("\n"):
        after = after[1:]
    return existing[:start] + new_block + after


def hide_credentials(text: str) -> str:
    """Zugangsdaten in http(s)-Adressen ausblenden (`https://nutzer:token@host` → `https://***@host`)."""
    return re.sub(r"(https?://)[^/@\s\"']+@", r"\1***@", text)


def _cell(value) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _code(value) -> str:
    return f"`{_cell(value)}`"


def _table(head: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in rows])


def _render(text: str, values: dict, mode: str = "plain") -> str:
    """Setzt Werte ein; fehlt einer (Addon nicht aktiv, Pflichtwert ohne Standard), bleibt der Text, wie er ist."""
    try:
        return placeholders.render(text, values, mode)
    except placeholders.PlaceholderError:
        return text


TILE_KINDS = {"view": "Ansicht", "chat": "Chat", "link": "Link"}


def addon_page(addon, ai_text: str, readme_text: str, values: dict, container: str, ip: str | None, active: bool, secrets: dict) -> str:
    """Eine Datei je Addon für Mensch und KI: Stand, Einstellungen, Aktionen, Links, Kacheln, Hinweise, Anleitung.
    `secrets`: Slot -> gesetzt? Werte von Secrets stehen nie darin."""
    merged = dict(values)
    merged.update(system_values(container, ip or "(Container läuft nicht)"))
    facts = [f"- Version: {addon.version}"]
    if active:
        facts.append("- Stand: aktiv in diesem Container")
    else:
        facts.append(f"- Stand: nicht in diesem Container. Der Nutzer ergänzt es mit `devc install {container} {addon.id}` "
                     "oder in devc auf der Seite »Konfiguration«.")
    needs = [other for other in addon.all_requires if other != addon.id]
    if needs:
        facts.append("- Braucht: " + ", ".join(f"`{other}`" for other in needs))
    parts = [f"# {addon.title} (`{addon.id}`)\n\n" + (addon.description + "\n\n" if addon.description else "") + "\n".join(facts)]

    rows = []
    for param in addon.params:
        if param.type == "secret":
            slot = f"{addon.id}.{param.name}"
            value = f"Secret, gesetzt: Datei `/run/devc/secrets/{slot}`" if secrets.get(slot) else "Secret, nicht gesetzt"
        elif param.name in values:
            value = _code(placeholders._text(values[param.name])) if placeholders._text(values[param.name]) != "" else "(leer)"
        else:
            value = "–"
        rows.append([_code(param.name), value, _cell(param.description)])
    if rows:
        change = (f"Ändern kann sie der Nutzer: `devc config set {container} {addon.id}.<name>=<wert>` oder in devc auf der Seite »Addons«."
                  if active else "Die Werte gelten, wenn der Nutzer das Addon ergänzt.")
        parts.append("## Einstellungen\n\n" + _table(["Name", "Wert" if active else "Standard", "Bedeutung"], rows) + "\n\n" + change)

    rows = []
    for action in addon.actions:
        if "run" in action:
            command = _code(_render(action["run"], merged, "run"))
            kind = "läuft im Hintergrund" if action.get("detach") else "läuft bis zum Ende"
        else:
            command, kind = "–", f"stoppt `{addon.id}.{action['stops']}`"
        rows.append([_code(f"{addon.id}.{action['id']}"), _cell(action["label"]), command, kind])
    if rows:
        parts.append("## Aktionen\n\n" + _table(["Aktion", "Name in devc", "Befehl", "Art"], rows) + "\n\n"
                     f"Aktionen startet der Nutzer in devc auf der Seite »Addons« oder mit `devc action run {container} <aktion>`. "
                     "Starte sie nicht selbst nach.")

    rows = []
    for link in addon.links:
        if "url" in link:
            target = _code(hide_credentials(_render(link["url"], merged, "url")))
        else:
            target = "Port " + _code(_render(str(link.get("port") or link.get("ports")), merged)) + " im Container" + (", Pfad " + _code(link["path"]) if link.get("path") not in (None, "", "/") else "")
        rows.append([_cell(link["label"]), target, "nur solange `" + f"{addon.id}.{link['when']}" + "` läuft" if link.get("when") else "immer"])
    if rows:
        parts.append("## Links\n\n" + _table(["Link", "Ziel", "Aktiv"], rows) + "\n\nDie Adresse für den Browser zeigt devc dem Nutzer auf der Seite »Addons«.")

    rows = [[_code(f"{addon.id}.{tile['id']}"), _cell(tile["title"]), TILE_KINDS.get(tile["kind"], tile["kind"]), _cell(tile.get("description", ""))]
            for tile in addon.tiles]
    if rows:
        parts.append("## Kacheln in devc\n\n" + _table(["Kachel", "Titel", "Art", "Zweck"], rows) + "\n\nDie Kacheln sieht der Nutzer auf dem Dashboard des Containers.")

    if ai_text.strip():
        # Die AI.md beginnt meist mit dem Titel des Addons als Überschrift; der steht hier schon oben.
        hints = re.sub(r"\A#{1,3} [^\n]*\n+", "", _render(ai_text, merged).strip())
        parts.append("## Hinweise für die KI\n\n" + hints)
    if readme_text.strip():
        parts.append("## Anleitung\n\n" + readme_text.strip())
    return "\n\n".join(parts) + "\n"


def index(data: dict, image: str, active: list, others: list, ip: str | None, secrets: dict) -> str:
    """.devc/README.md: docs.readme der Instanz, danach die Übersicht über Container, Addons, Secrets und Konfiguration."""
    name = data["name"]
    parts = []
    text = (data.get("docs") or {}).get("readme", "")
    if text.strip():
        parts.append(text.strip())
    facts = _facts(data, image) + [f"- Adresse im Netz der Container: {ip or '(Container läuft nicht)'}",
                                   "- Konfiguration: [config.toml](config.toml) (Kopie zur Information, ohne Geheimnisse)"]
    parts.append(f"# Container {name}\n\nDiesen Ordner schreibt devc bei jedem »Anwenden« neu. Eigene Änderungen hier gehen verloren.\n\n" + "\n".join(facts))
    row = lambda addon: [_code(addon.id), addon.version, _cell(addon.description or addon.title), f"[addons/{addon.id}.md](addons/{addon.id}.md)"]
    if active:
        parts.append("## Aktive Addons\n\n" + _table(["Addon", "Version", "Wofür", "Mehr"], [row(addon) for addon in active]))
    if others:
        parts.append("## Weitere Addons\n\nNicht in diesem Container. Der Nutzer ergänzt eins mit "
                     f"`devc install {name} <addon>` oder in devc auf der Seite »Konfiguration«.\n\n"
                     + _table(["Addon", "Version", "Wofür", "Mehr"], [row(addon) for addon in others]))
    if secrets:
        rows = [[_code(slot), "gesetzt" if found else "nicht gesetzt", f"`/run/devc/secrets/{slot}`" if found else "–"] for slot, found in sorted(secrets.items())]
        parts.append("## Secrets\n\nWerte stehen nie in diesem Ordner. Der Nutzer setzt sie in devc auf der Seite »Secrets«.\n\n"
                     + _table(["Slot", "Zustand", "Datei"], rows))
    return "\n\n".join(parts) + "\n"


CONFIG_HEAD = ("# Kopie der Konfiguration dieses Containers (devc.toml), von devc geschrieben.\n"
               "# Nur zur Information: Änderungen hier wirken nicht. Ändern kann sie der Nutzer in devc (Seite »Konfiguration«).\n"
               "# Namen aus dem Secret-Store und Zugangsdaten in Adressen sind durch *** ersetzt.\n\n")
_SECTION_RE = re.compile(r"^\s*\[+\s*([^\]]+?)\s*\]+")
_ASSIGN_RE = re.compile(r"^(\s*[^=#\s][^=]*=\s*).*$")


def config_copy(text: str) -> str:
    """Die devc.toml für das Infoverzeichnis: Werte unter [secrets] und Zugangsdaten in Adressen ausgeblendet."""
    out, section = [], ""
    for line in text.splitlines():
        found = _SECTION_RE.match(line)
        if found:
            section = found[1]
        elif section == "secrets" and _ASSIGN_RE.match(line):
            line = _ASSIGN_RE.sub(r'\1"***"', line)
        out.append(hide_credentials(line))
    return CONFIG_HEAD + "\n".join(out).rstrip("\n") + "\n"
