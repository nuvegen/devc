"""Einstieg der CLI: argparse, Aufruf der Use-Cases in core, Ausgabe (SPEC 8.9)."""

import argparse
import getpass
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from . import __version__, config, core
from .errors import DevcError, InternalError, MissingSecretError, NotFoundError, UsageError, ValidationError

# "<befehl> [<unterbefehl>]" -> Name der Funktion in core; wird von build_parser gefüllt
COMMANDS: dict[str, str] = {}

_GLOBALS = ("config", "json", "verbose", "quiet", "command", "subcommand", "func")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(f"Ungültiger Aufruf: {message}")


def _common() -> argparse.ArgumentParser:
    # SUPPRESS, damit die Optionen vor und nach dem Befehl stehen dürfen,
    # ohne dass der Unterparser den Wert des Hauptparsers überschreibt.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    group = common.add_argument_group("Globale Optionen")
    group.add_argument("--config", metavar="<pfad>", help="Pfad der config.toml")
    group.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    group.add_argument("-v", "--verbose", action="count", help="ausführlicher")
    group.add_argument("-q", "--quiet", action="store_true", help="nur Fehler ausgeben")
    return common


def build_parser() -> argparse.ArgumentParser:
    COMMANDS.clear()
    common = _common()
    parser = _Parser(
        prog="devc",
        description="devc – Dev-Container-Manager für LXD",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"devc {__version__}")
    top = parser.add_subparsers(dest="command", metavar="<befehl>", title="Befehle")

    def cmd(name: str, func: str, text: str, parent=None) -> argparse.ArgumentParser:
        target = parent if parent is not None else top
        prefix = f"{parent.devc_group} " if parent is not None else ""
        COMMANDS[prefix + name] = func
        sub = target.add_parser(name, help=text, description=text, parents=[common])
        sub.set_defaults(func=func)
        return sub

    def group(name: str, text: str):
        sub = top.add_parser(name, help=text, description=text, parents=[common])
        children = sub.add_subparsers(dest="subcommand", metavar="<unterbefehl>", required=True)
        children.devc_group = name
        return children

    def multi(sub, flag: str, metavar: str, text: str, dest: str | None = None) -> None:
        sub.add_argument(flag, action="append", default=[], metavar=metavar, help=text, dest=dest)

    cmd("init", "init", "Config, Token, Verzeichnisse und LXD-Projekt anlegen (idempotent)")
    p = cmd("doctor", "doctor", "Umgebung prüfen")
    p.add_argument("--cleanup", action="store_true", help="Logs älter als 30 Tage löschen")

    g = group("token", "API-Token anzeigen oder neu erzeugen")
    cmd("show", "token_show", "Token anzeigen", g)
    cmd("rotate", "token_rotate", "Token neu erzeugen (beendet alle Sessions)", g)

    g = group("addon", "Addon-Katalog und eigene Addons")
    cmd("list", "addon_list", "Addons auflisten", g)
    cmd("show", "addon_show", "Addon anzeigen", g).add_argument("id")
    cmd("validate", "addon_validate", "Addon-Verzeichnis prüfen (L3–L8)", g).add_argument("dir")
    cmd("import", "addon_import", "Addon-Archiv importieren", g).add_argument("archive", metavar="archiv")
    p = cmd("export", "addon_export", "Addon als Archiv exportieren", g)
    p.add_argument("id")
    p.add_argument("-o", "--output", metavar="<datei>", help="Zieldatei")
    cmd("rm", "addon_rm", "Eigenes Addon entfernen", g).add_argument("id")

    g = group("template", "Templates")
    cmd("list", "template_list", "Templates auflisten", g)
    cmd("show", "template_show", "Template anzeigen", g).add_argument("id")
    cmd("edit", "template_edit", "Template im Editor bearbeiten", g).add_argument("id")
    cmd("validate", "template_validate", "Template-Datei prüfen (L9)", g).add_argument("file", metavar="datei")
    p = cmd("save", "template_save", "Konfiguration eines Containers als Template speichern", g)
    p.add_argument("container")
    p.add_argument("id")
    cmd("rm", "template_rm", "Eigenes Template entfernen", g).add_argument("id")

    p = cmd("create", "create", "Container anlegen")
    p.add_argument("name")
    source = p.add_mutually_exclusive_group()
    source.add_argument("--template", metavar="<id>", help="Template als Vorlage")
    source.add_argument("--from", dest="from_file", metavar="<datei>", help="fertige devc.toml verwenden")
    p.add_argument("--edit", action="store_true", help="Entwurf vor dem Anlegen im Editor öffnen")
    multi(p, "--addon", "<id>", "Addon ergänzen", "addons")
    multi(p, "--param", "<addon>.<param>=<wert>", "Addon-Parameter setzen", "params")
    multi(p, "--secret", "<slot>[=@datei|=-|=shared:<store>]", "Secret-Slot befüllen", "secrets")
    p.add_argument("--shared", metavar="<host-pfad>", help="Shared Folder statt Code-Volume")
    p.add_argument("--git", metavar="<url>", help="Git-Remote")
    p.add_argument("--image", metavar="<image>", help="LXD-Image")
    p.add_argument("--cpu", type=int, metavar="<n>", help="Anzahl CPUs")
    p.add_argument("--memory", metavar="<größe>", help="Arbeitsspeicher, z. B. 8GiB")
    p.add_argument("--disk", metavar="<größe>", help="Root-Disk, z. B. 30GiB")
    p.add_argument("--no-start", action="store_true", help="Container nicht starten")
    p.add_argument("--yes", action="store_true", help="nicht nachfragen")

    cmd("list", "list_containers", "Container auflisten")
    cmd("info", "info", "Details eines Containers").add_argument("name")
    cmd("start", "start", "Container starten").add_argument("name")
    cmd("stop", "stop", "Container stoppen").add_argument("name")
    cmd("restart", "restart", "Container neu starten").add_argument("name")
    p = cmd("rm", "rm", "Container löschen")
    p.add_argument("name")
    p.add_argument("--keep-data", action="store_true", help="Volumes behalten")
    p.add_argument("--yes", action="store_true", help="nicht nachfragen")
    p = cmd("shell", "shell", "Interaktive Shell im Container")
    p.add_argument("name")
    p.add_argument("--root", action="store_true", help="als root statt dev")
    p = cmd("exec", "exec_command", "Einzelbefehl im Container: exec <name> -- <argv…>")
    p.add_argument("name")
    p.add_argument("--root", action="store_true", help="als root statt dev")
    p.add_argument("argv", nargs=argparse.REMAINDER, metavar="-- <argv…>")

    p = cmd("install", "install", "Addons in einem Container ergänzen und anwenden")
    p.add_argument("name")
    p.add_argument("addons", nargs="+", metavar="addon")
    multi(p, "--param", "<addon>.<param>=<wert>", "Addon-Parameter setzen", "params")
    p = cmd("uninstall", "uninstall", "Addon aus einem Container entfernen und anwenden")
    p.add_argument("name")
    p.add_argument("addon")
    cmd("docs", "docs", "Agent-Doku neu erzeugen").add_argument("name")

    g = group("config", "Instanz-Konfiguration (devc.toml)")
    cmd("show", "config_show", "devc.toml anzeigen", g).add_argument("name")
    cmd("edit", "config_edit", "devc.toml im Editor bearbeiten", g).add_argument("name")
    p = cmd("set", "params_set", "Parameter aktiver Addons setzen und anwenden", g)
    p.add_argument("name")
    p.add_argument("params", nargs="+", metavar="<addon>.<param>=<wert>")
    p = cmd("apply", "config_apply", "devc.toml anwenden", g)
    p.add_argument("name")
    p.add_argument("--dry-run", action="store_true", help="nur Unterschiede zeigen")

    g = group("action", "Addon-Aktionen")
    cmd("list", "action_list", "Aktionen und Links eines Containers", g).add_argument("name")
    p = cmd("run", "action_run", "Aktion ausführen", g)
    p.add_argument("name")
    p.add_argument("action", metavar="<addon>.<id>")

    g = group("tile", "Kacheln aus Addons")
    cmd("list", "tile_list", "Kacheln eines Containers", g).add_argument("name")
    p = cmd("show", "tile_show", "Inhalt einer Kachel der Art view ausgeben (Markdown)", g)
    p.add_argument("name")
    p.add_argument("tile", metavar="<addon>.<id>")

    p = cmd("panel", "tile_panel", "Kachel der Art panel: Ansicht holen oder Aktion ausführen (JSON)", g)
    p.add_argument("name")
    p.add_argument("tile", metavar="<addon>.<id>")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--view", metavar="<id>", help="Ansicht; ohne Angabe die Startansicht")
    which.add_argument("--action", metavar="<id>", help="Aktion ausführen")
    p.add_argument("--arg", dest="args", action="append", default=[], metavar="<name>=<wert>", help="Angabe zur Ansicht oder Aktion")
    p.add_argument("--value", dest="values", action="append", default=[], metavar="<name>=<wert>", help="Eingabe eines Formulars (true/false für Schalter)")

    g = group("vm", "Die VM, in der devc läuft: beschränkte Shell und Update von devc")
    cmd("info", "vm_info", "zeigt, was eingerichtet ist, und die laufende Fassung", g)
    cmd("shell", "vm_shell", "beschränkte Shell der VM: devc, lxc und einige Systemwerkzeuge", g)
    cmd("update", "vm_update", "neues devc.pyz einrichten; der Dienst startet neu", g).add_argument("file", metavar="<devc.pyz>")

    g = group("secret", "Secrets eines Containers oder des geteilten Stores")
    p = cmd("set", "secret_set", "set <name> <slot>[=…] bzw. set --shared <store>[=…]", g)
    p.add_argument("--shared", action="store_true", help="geteilter Store")
    p.add_argument("target", nargs="+", metavar="ziel", help="=@datei, =-, =shared:<store>; ohne Wert verdeckte Abfrage")
    p = cmd("list", "secret_list", "list <name> bzw. list --shared", g)
    p.add_argument("--shared", action="store_true", help="geteilter Store")
    p.add_argument("target", nargs="*", metavar="name")
    p = cmd("rm", "secret_rm", "rm <name> <slot> bzw. rm --shared <store>", g)
    p.add_argument("--shared", action="store_true", help="geteilter Store")
    p.add_argument("target", nargs="+", metavar="ziel")

    g = group("snapshot", "Snapshots der Datenbereiche")
    p = cmd("create", "snapshot_create", "Snapshot erstellen", g)
    p.add_argument("name")
    p.add_argument("--label", metavar="<name>", help="Snapshot-Name statt auto-…")
    p.add_argument("--full", action="store_true", help="ganzen Container sichern")
    cmd("list", "snapshot_list", "Snapshots auflisten", g).add_argument("name")
    p = cmd("restore", "snapshot_restore", "Auf Snapshot zurücksetzen", g)
    p.add_argument("name")
    p.add_argument("snapshot", metavar="snap")
    p.add_argument("--yes", action="store_true", help="nicht nachfragen")
    p = cmd("rm", "snapshot_rm", "Snapshot löschen", g)
    p.add_argument("name")
    p.add_argument("snapshot", metavar="snap")

    p = cmd("backup", "backup", "Backup-Archiv erstellen")
    p.add_argument("name")
    p.add_argument("-o", "--output", metavar="<verzeichnis>", help="Zielverzeichnis")
    p.add_argument("--label", metavar="<label>", help="Zusatz im Dateinamen")
    p.add_argument("--no-home", action="store_true", help="Datenbereich Home auslassen")
    p.add_argument("--no-work", action="store_true", help="Datenbereich Code auslassen")
    p = cmd("restore", "restore", "Backup wiederherstellen")
    p.add_argument("archive", metavar="archiv")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", dest="new_name", metavar="<neu>", help="als neuen Container anlegen")
    target.add_argument("--replace", metavar="<name>", help="vorhandenen Container ersetzen")
    p.add_argument("--edit", action="store_true", help="devc.toml vorher im Editor öffnen")
    p.add_argument("--yes", action="store_true", help="nicht nachfragen")

    p = cmd("push", "push", "Datei in den Container kopieren (Ziel relativ zu /work)")
    p.add_argument("name")
    p.add_argument("local", metavar="lokal")
    p.add_argument("dest", nargs="?", metavar="ziel")
    p = cmd("pull", "pull", "Datei aus dem Container holen (Quelle relativ zu /work)")
    p.add_argument("name")
    p.add_argument("src", metavar="quelle")
    p.add_argument("local", nargs="?", metavar="lokal")

    g = group("file", "Dateien unter /work: löschen, Archive ansehen und entpacken")
    p = cmd("rm", "file_rm", "Datei oder Ordner im Container löschen (Pfad relativ zu /work)", g)
    p.add_argument("name")
    p.add_argument("path", metavar="pfad")
    p.add_argument("--yes", action="store_true", help="nicht nachfragen")
    p = cmd("archive", "archive_list", "Inhalt eines tar- oder zip-Archivs im Container zeigen", g)
    p.add_argument("name")
    p.add_argument("path", metavar="archiv")
    p = cmd("unpack", "archive_unpack", "tar- oder zip-Archiv im Container entpacken", g)
    p.add_argument("name")
    p.add_argument("path", metavar="archiv")
    p.add_argument("--into", metavar="<ordner>", help="Zielordner relativ zu /work; Standard: der Ordner des Archivs")

    p = cmd("serve", "serve", "HTTP-Server mit API und GUI starten")
    p.add_argument("--bind", metavar="<adresse>", help="Bind-Adresse")
    p.add_argument("--port", type=int, metavar="<port>", help="Port")
    p.add_argument("--allow-insecure-bind", action="store_true", help="Nicht-Loopback ohne TLS zulassen")
    p.add_argument("--allow-insecure-terminal", action="store_true", help="Web-Terminal trotz unsicherem Bind")
    p.add_argument("--tls-cert", metavar="<pem>", help="TLS-Zertifikat")
    p.add_argument("--tls-key", metavar="<pem>", help="TLS-Schlüssel")
    p.add_argument("--dev", action="store_true", help="GUI-Dateien bei jedem Request neu laden")
    g = group("help", "Hilfe: erste Schritte, Handbuch, Anweisungen für KI-Agenten")
    cmd("list", "help_list", "Hilfethemen auflisten", g)
    cmd("show", "help_show", "Hilfethema als Markdown ausgeben", g).add_argument("topic", metavar="thema")
    g = group("proxy", "Reverse-Proxy vor devc (Caddy)")
    p = cmd("config", "proxy_config", "Caddyfile aus der Konfiguration ausgeben", g)
    p.add_argument("--http-port", type=int, metavar="<port>", help="zusätzlich unverschlüsselt auf diesem Port (nur localhost)")
    p = cmd("jobs", "jobs", "Jobs anzeigen")
    p.add_argument("--container", metavar="<name>", help="nur Jobs dieses Containers")
    p.add_argument("--follow", metavar="<id>", help="Log eines Jobs verfolgen")

    return parser


def _params(args: argparse.Namespace) -> dict:
    """Argumente für den Use-Case: Namespace ohne globale Optionen, normalisiert."""
    params = {k: v for k, v in vars(args).items() if k not in _GLOBALS}
    func = args.func
    if func == "exec_command":
        # argparse reicht ab dem Namen alles durch; Optionen vor "--" gehören noch zu devc.
        argv = params["argv"]
        own = argv[: argv.index("--")] if "--" in argv else []
        for option in own:
            if option != "--root":
                raise UsageError(f"Ungültiger Aufruf: unbekannte Option {option}; exec <name> [--root] -- <argv…>")
            params["root"] = True
        argv = argv[len(own):]
        if argv[:1] == ["--"]:
            argv = argv[1:]
        if not argv:
            raise UsageError("Ungültiger Aufruf: exec <name> -- <argv…> verlangt einen Befehl")
        params["argv"] = argv
    elif func.startswith("secret_"):
        target = params.pop("target")
        shared = params["shared"]
        if func == "secret_list":
            expected = 0 if shared else 1
        else:
            expected = 1 if shared else 2
        if len(target) != expected:
            form = {
                "secret_set": "secret set <name> <slot>[=…] bzw. secret set --shared <store>[=…]",
                "secret_list": "secret list <name> bzw. secret list --shared",
                "secret_rm": "secret rm <name> <slot> bzw. secret rm --shared <store>",
            }[func]
            raise UsageError(f"Ungültiger Aufruf: {form}")
        params["name"] = None if shared else target[0]
        if func != "secret_list":
            params["spec"] = target[-1]
    return params


_MARKS = {"ok": "ok      ", "warning": "WARNUNG ", "error": "FEHLER  "}


def _doctor_lines(result: dict, quiet: bool = False) -> list[str]:
    lines = []
    for point in result.get("points", []):
        if quiet and point["severity"] == "ok":
            continue
        check = "" if point["check"] == "doctor" else point["check"] + " "
        lines.append(f"{_MARKS[point['severity']]}{check}{point['path']}: {point['message']}")
    for name in result.get("cleaned", []):
        lines.append(f"gelöscht {name}")
    return lines


def _format_init(result: dict, quiet: bool) -> list[str]:
    if quiet:
        return []
    return result["changed"] or ["Alles vorhanden, nichts geändert"]


def _table(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows]


def _finding_lines(findings: list[dict]) -> list[str]:
    return [f"{f['check']} {f['path']}: {f['message']}" if f["path"] else f"{f['check']}: {f['message']}" for f in findings]


def _format_addon_list(result: dict, quiet: bool) -> list[str]:
    rows = [["ID", "VERSION", "HERKUNFT", "TITEL"]]
    rows += [[a["id"], a["version"], a["origin_label"], a["title"]] for a in result["addons"]]
    lines = _table(rows)
    for broken in result["broken"]:
        lines.append(f"fehlerhaft: {broken['id']}")
        lines += ["  " + line for line in _finding_lines(broken["findings"])]
    return lines


def _format_addon_show(result: dict, quiet: bool) -> list[str]:
    lines = [f"{result['id']} {result['version']} – {result['title']}", result["description"], "Herkunft: " + result["origin_label"]]
    if result["requires"]:
        lines.append("Benötigt: " + ", ".join(result["requires"]))
    for param in result["params"]:
        default = f" (Standard: {json.dumps(param['default'], ensure_ascii=False)})" if "default" in param else ""
        required = ", Pflicht" if param["required"] else ""
        lines.append(f"Parameter {param['name']}: {param['type']}{required}{default} – {param['description']}")
    for action in result["actions"]:
        lines.append(f"Aktion {result['id']}.{action['id']}: {action['label']}")
    for link in result["links"]:
        target = link["url"] if "url" in link else f"Port {link.get('port', link.get('ports'))}, Pfad {link['path']}"
        lines.append(f"Link: {link['label']} – {target}")
    return lines + ["", result["readme"].rstrip()]


def _format_template_list(result: dict, quiet: bool) -> list[str]:
    rows = [["ID", "VERSION", "HERKUNFT", "TITEL"]]
    for t in result["templates"]:
        rows.append([t["id"], t.get("version", "?"), t["origin_label"], t.get("title") or "fehlerhaft: " + t.get("error", "")])
    return _table(rows)


def _incomplete_note(container: dict) -> str:
    if container["installing"]:
        return " (wird eingerichtet)"
    return " (unvollständig)" if container["incomplete"] else ""


def _format_list(result: dict, quiet: bool) -> list[str]:
    rows = [["NAME", "STATUS", "IP", "TEMPLATE", "ADDONS"]]
    for c in result["containers"]:
        status = c["status"] + _incomplete_note(c)
        rows.append([c["name"], status, c["ip"] or "-", c["template"] or "-", ", ".join(sorted(c["addons"])) or "-"])
    return _table(rows)


def _format_info(result: dict, quiet: bool) -> list[str]:
    area = result["data"]
    lines = [
        f"Name:       {result['name']}",
        f"Status:     {result['status']}" + _incomplete_note(result),
        f"IP:         {result['ip'] or '-'}",
        f"Plattform:  {result['platform']}",
        f"Image:      {result['image']}",
        f"Template:   {result['template'] or '-'}",
        f"Ressourcen: {result['resources']['cpu']} CPU, {result['resources']['memory']} RAM, {result['resources']['disk']} Disk",
        f"Code:       {area.get('shared') or 'Volume'}",
        f"Git-Remote: {result['git'] or '-'}",
        "Addons:     " + (", ".join(f"{k} {v}" for k, v in sorted(result["addons"].items())) or "-"),
        f"Angelegt:   {result['created_at']}",
        f"Angewendet: {result['applied_at'] or '-'}",
    ]
    return lines + _finding_lines(result["findings"])


def _format_job(text: str):
    def format_result(result: dict, quiet: bool) -> list[str]:
        lines = _finding_lines([f for f in result.get("findings", []) if f["severity"] == "warning"])
        if quiet:
            return lines
        return lines + [text.format(**result) + f" (Job {result['job']})"]

    return format_result


def _format_secret_list(result: dict, quiet: bool) -> list[str]:
    if "container" in result:
        rows = [["SLOT", "QUELLE", "GEÄNDERT", "PFLICHT"]]
        rows += [[s["slot"], s["source"], s["modified"] or "-", "ja" if s["required"] else "nein"] for s in result["secrets"]]
    else:
        rows = [["NAME", "GEÄNDERT", "GENUTZT VON"]]
        rows += [[s["name"], s["modified"], ", ".join(s["used_by"]) or "-"] for s in result["secrets"]]
    return _table(rows)


def _format_status(result: dict, quiet: bool) -> list[str]:
    return [] if quiet else [f"{result['name']}: {result['status']}"]


def _format_secret_change(verb: str):
    def format_result(result: dict, quiet: bool) -> list[str]:
        if quiet:
            return []
        if "store" in result:
            return [f"Store-Eintrag {result['store']} {verb}"]
        extra = " und in den laufenden Container geschrieben" if result.get("pushed") else ""
        return [f"Secret {result['slot']} für {result['container']} {verb}{extra}"]

    return format_result


def _format_apply(result: dict, quiet: bool) -> list[str]:
    lines = _finding_lines([f for f in result["findings"] if f["severity"] == "warning"])
    plan = result["plan"]
    if result["dry_run"] or not quiet:
        for item in plan["install"]:
            lines.append(f"Addon installieren: {item['addon']} ({item['reason']})")
        for addon in plan["uninstall"]:
            lines.append(f"Addon entfernen: {addon}")
        labels = {"resources": "Ressourcen", "secrets": "Secrets", "git": "Git", "files": "Dateien", "docs": "Doku-Texte"}
        changed = [label for key, label in labels.items() if plan[key]]
        if changed:
            lines.append("Geändert: " + ", ".join(changed))
        if not plan["changed"]:
            lines.append("Keine Unterschiede zum angewendeten Stand")
    if result["dry_run"]:
        return lines + ["Probelauf, nichts geändert"]
    return lines if quiet else lines + [f"Konfiguration von {result['name']} angewendet (Job {result['job']})"]


def _format_action_list(result: dict, quiet: bool) -> list[str]:
    lines = []
    for addon in result["addons"]:
        for action in addon["actions"]:
            status = f" [{action['status']}]" if "status" in action else ""
            lines.append(f"{addon['id']}.{action['id']}{status}  {action['label']}")
        for link in addon["links"]:
            state = "aktiv" if link["active"] else "inaktiv"
            note = " (ohne Anmeldung)" if link.get("public") else ""
            lines.append(f"{addon['id']} Link [{state}]  {link['label']}: {link['url'] or '-'}{note}")
    return lines or ["Keine Aktionen"]


def _format_tile_list(result: dict, quiet: bool) -> list[str]:
    rows = [["KACHEL", "ART", "TITEL", "ADRESSE"]]
    rows += [[t["ref"], t["kind"], t["title"], t.get("url") or ""] for t in result["tiles"]]
    return _table(rows) if result["tiles"] else ["Keine Kacheln"]


def _format_vm_info(result: dict, quiet: bool) -> list[str]:
    yes = lambda value: "eingerichtet" if value else "nicht eingerichtet"
    return [f"devc {result['version']}" + (f" ({result['build']})" if result["build"] else " (aus dem Quelltext)"),
            f"VM-Terminal: {yes(result['terminal'])}" + (" – " + " ".join(result["commands"]) if result["commands"] else ""),
            f"Update: {yes(result['update'])}"]


def _format_vm_update(result: dict, quiet: bool) -> list[str]:
    if quiet:
        return []
    return [f"Update gestartet: devc {result['to']['version']} ({result['to']['build']}). Der Dienst startet gleich neu."]


def _format_tile_panel(result: dict, quiet: bool) -> list[str]:
    return [json.dumps(result, ensure_ascii=False, indent=2)]


def _format_tile_show(result: dict, quiet: bool) -> list[str]:
    return [result["text"].rstrip("\n")]


def _format_action_run(result: dict, quiet: bool) -> list[str]:
    if quiet:
        return []
    if result["status"] == "running":
        return [f"{result['action']} läuft (PID {result['pid']})"]
    if result.get("was_running") is False:
        return [f"{result['action']}: lief nicht"]
    return [f"{result['action']}: {result['status']}"]


def _format_snapshot_list(result: dict, quiet: bool) -> list[str]:
    rows = [["NAME", "ERSTELLT", "UMFANG"]]
    rows += [[s["name"], s["created_at"], "voll" if s["scope"] == "full" else "Daten"] for s in result["snapshots"]]
    return _table(rows)


def _format_jobs(result: dict, quiet: bool) -> list[str]:
    rows = [["JOB", "CONTAINER", "ART", "STATUS", "FEHLER", "START"]]
    rows += [[j["id"], j["container"] or "-", j["kind"], j["state"], j["error_code"] or "-", j["started_at"] or "-"] for j in result["jobs"]]
    return _table(rows)


def _format_archive(result: dict, quiet: bool) -> list[str]:
    lines = [e["name"] + ("/" if e["type"] == "dir" else "") for e in result["entries"]]
    if result["truncated"]:
        lines.append(f"… und {result['count'] - len(result['entries'])} weitere")
    lines += [f"Übersprungen: {s}" for s in result["skipped"]]
    return lines if quiet else lines + [f"{result['count']} Einträge, entpackt {result['size']} Bytes; oberste Ebene: {', '.join(result['top']) or '-'}"]


FORMATTERS = {
    "jobs": _format_jobs,
    "snapshot_create": lambda result, quiet: [] if quiet else [f"Snapshot {result['snapshot']} erstellt (Job {result['job']})"],
    "snapshot_list": _format_snapshot_list,
    "snapshot_restore": lambda result, quiet: [] if quiet else [f"{result['name']} auf Snapshot {result['snapshot']} zurückgesetzt (Job {result['job']})"],
    "snapshot_rm": lambda result, quiet: [] if quiet else [f"Snapshot {result['snapshot']} gelöscht"],
    "backup": lambda result, quiet: [result["file"]],
    "restore": lambda result, quiet: _finding_lines([f for f in result.get("findings", []) if f["severity"] == "warning"]) + ([] if quiet else [f"Container {result['name']} aus Backup hergestellt: {result['status']} (Job {result['job']})"]),
    "push": lambda result, quiet: [] if quiet else [result["dest"]],
    "pull": lambda result, quiet: [] if quiet else [result["file"]],
    "file_rm": lambda result, quiet: [] if quiet else [f"{result['path']} gelöscht"],
    "archive_list": _format_archive,
    "archive_unpack": lambda result, quiet: [f"Übersprungen: {s}" for s in result["skipped"]] + ([] if quiet else [
        f"{result['files']} Dateien, {result['dirs']} Ordner und {result['links']} Links nach {result['into']} entpackt"]),
    "config_show": lambda result, quiet: [result["text"].rstrip("\n")],
    "config_edit": lambda result, quiet: _finding_lines(result["findings"]) + ([] if quiet else [f"devc.toml von {result['name']} gespeichert; anwenden mit: devc config apply {result['name']}"]),
    "config_apply": _format_apply,
    "params_set": _format_apply,
    "install": _format_apply,
    "uninstall": _format_apply,
    "action_list": _format_action_list,
    "tile_list": _format_tile_list,
    "tile_show": _format_tile_show,
    "tile_panel": _format_tile_panel,
    "vm_info": _format_vm_info,
    "vm_update": _format_vm_update,
    "action_run": _format_action_run,
    "addon_import": lambda result, quiet: [] if quiet else [f"Addon {result['id']} {result['version']} " + ("ersetzt" if result["replaced"] else "importiert")],
    "addon_export": lambda result, quiet: [result["file"]],
    "addon_rm": lambda result, quiet: [] if quiet else [f"Addon {result['id']} entfernt"],
    "template_save": lambda result, quiet: _finding_lines(result["findings"]) + ([] if quiet else [f"Template {result['id']} gespeichert"]),
    "template_rm": lambda result, quiet: [] if quiet else [f"Template {result['id']} entfernt"],
    "create": _format_job("Container {name} ist angelegt: {status}"),
    "list_containers": _format_list,
    "info": _format_info,
    "start": _format_status,
    "stop": _format_status,
    "restart": _format_status,
    "rm": _format_job("Container {name} gelöscht"),
    "docs": _format_job("Agent-Doku für {name} geschrieben"),
    "secret_set": _format_secret_change("gesetzt"),
    "secret_rm": _format_secret_change("entfernt"),
    "secret_list": _format_secret_list,
    "init": _format_init,
    "doctor": _doctor_lines,
    "addon_list": _format_addon_list,
    "addon_show": _format_addon_show,
    "addon_validate": lambda result, quiet: _finding_lines(result["findings"]),
    "template_list": _format_template_list,
    "template_show": lambda result, quiet: [result["text"].rstrip("\n")],
    "template_validate": lambda result, quiet: _finding_lines(result["findings"]),
    "template_edit": lambda result, quiet: _finding_lines(result["findings"]),
    "token_show": lambda result, quiet: [result["token"]],
    "help_list": lambda result, quiet: [f"{t['id']:<10} {t['title']} – {t['summary']}" for t in result["topics"]],
    "help_show": lambda result, quiet: [result["text"].rstrip("\n")],
    "proxy_config": lambda result, quiet: [result["caddyfile"].rstrip("\n")],
    "token_rotate": lambda result, quiet: [result["token"]],
}


def _print_error(error: DevcError, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(error.to_dict(), ensure_ascii=False))
        return
    if "points" in error.details:  # doctor: Befunde auch im Fehlerfall zeigen
        for line in _doctor_lines(error.details):
            print(line)
    if isinstance(error, ValidationError) and error.findings:
        for finding in error.findings:
            print(finding.as_line(), file=sys.stderr)
        return
    print(f"Fehler [{error.code}]: {error.message}", file=sys.stderr)


def _print_result(func: str, result, json_mode: bool, quiet: bool) -> None:
    if json_mode:
        print(json.dumps({} if result is None else result, ensure_ascii=False))
    elif func in FORMATTERS:
        for line in FORMATTERS[func](result, quiet):
            print(line)


def edit_text(text: str, filename: str) -> str:
    """Öffnet den Text im Editor des Nutzers ($VISUAL, $EDITOR, sonst vi) und liefert das Ergebnis."""
    editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi")
    with tempfile.TemporaryDirectory(prefix="devc-edit-") as directory:
        file = Path(directory) / filename
        file.write_text(text, encoding="utf-8")
        try:
            done = subprocess.run([*editor, str(file)])
        except OSError:
            raise UsageError(f"Editor ließ sich nicht starten: {editor[0]}") from None
        if done.returncode != 0:
            raise UsageError(f"Editor endete mit Exit {done.returncode}; nichts gespeichert")
        return file.read_text(encoding="utf-8")


def _mib(count: int) -> str:
    return f"{count / 1024 / 1024:.0f} MiB"


def _show_progress(step) -> None:
    """Eine Zeile auf stderr, die sich selbst überschreibt; `None` räumt sie weg."""
    if step is None:
        sys.stderr.write("\r\033[K")
    else:
        percent = "" if step["percent"] is None else f"{step['percent']:3d} %  "
        total = "" if not step["total"] else f" von {'' if step['exact'] else 'etwa '}{_mib(step['total'])}"
        sys.stderr.write(f"\r{step['label']}: {percent}{_mib(step['done'])}{total}\033[K")
    sys.stderr.flush()


class Context:
    """Was ein Adapter über den Aufruf wissen muss."""

    def __init__(self, json_mode: bool, quiet: bool):
        self.json = json_mode
        self.quiet = quiet
        self.on_log = None if (json_mode or quiet) else print
        # Stand langer Schritte (Backup, Restore): nur im Terminal, als eine Zeile, die sich erneuert.
        core.set_progress_hook(_show_progress if self.on_log and sys.stderr.isatty() else None)


def _template_edit(cfg, ctx, id: str) -> dict:
    shown = core.template_show(cfg, id)
    text = edit_text(shown["text"], f"{id}.toml")
    if text == shown["text"] and shown["meta"]["origin"] != "builtin":
        return {"id": id, "findings": [], "changed": False}
    return core.template_put(cfg, id, text)


def _interactive() -> bool:
    return sys.stdin.isatty()


def _ask_secret(slot: str) -> bytes:
    value = getpass.getpass(f"Secret {slot}: ")
    if not value:
        raise UsageError(f"Kein Wert für {slot} eingegeben")
    return value.encode("utf-8")


def read_secret_spec(spec: str, prompt: bool = True) -> tuple[str, bytes | None, str | None]:
    """Zerlegt `<ziel>[=@datei|=-|=shared:<store>]`; liefert (ziel, wert, store).

    Ein Wert direkt auf der Kommandozeile ist nicht erlaubt: Prozessargumente sind sichtbar.
    """
    target, sep, source = spec.partition("=")
    if not sep:
        if not prompt or not _interactive():
            raise MissingSecretError([target])
        return target, _ask_secret(target), None
    if source == "-":
        return target, sys.stdin.buffer.read(), None
    if source.startswith("@"):
        try:
            return target, Path(source[1:]).expanduser().read_bytes(), None
        except FileNotFoundError:
            raise NotFoundError(f"Datei fehlt: {source[1:]}") from None
        except OSError as exc:
            raise UsageError(f"Datei nicht lesbar: {source[1:]} ({exc.strerror})") from None
    if source.startswith("shared:") and source[len("shared:"):]:
        return target, None, source[len("shared:"):]
    raise UsageError("Secret-Werte nie auf der Kommandozeile angeben; erlaubt: =@datei, =- oder =shared:<store>")


def _create(cfg, ctx, name, template, from_file, edit, addons, params, secrets, shared, git, image, cpu, memory, disk, no_start, yes) -> dict:
    values, stores = {}, {}
    for spec in secrets:
        target, value, store = read_secret_spec(spec, prompt=not yes)
        if store is not None:
            stores[target] = store
        else:
            values[target] = value
    options = dict(template=template, from_file=from_file, addons=addons, params=params, secret_stores=stores,
                   shared=shared, git=git, image=image, cpu=cpu, memory=memory, disk=disk)
    text = None
    if edit:
        text = edit_text(core.build_draft(cfg, name, **options), "devc.toml")
    ask = _ask_secret if (not yes and _interactive()) else None
    return core.create(cfg, name, text=text, secret_values=values, no_start=no_start, ask_secret=ask,
                       on_log=ctx.on_log, **options)


def _rm(cfg, ctx, name, keep_data, yes) -> dict:
    if not yes:
        if not _interactive():
            raise UsageError("Löschen verlangt eine Bestätigung: --yes angeben")
        what = "die Volumes bleiben erhalten" if keep_data else "samt aller Daten in den Volumes"
        answer = input(f"Container {name} löschen, {what}? [j/N] ")
        if answer.strip().lower() not in ("j", "ja"):
            raise UsageError("Abgebrochen, nichts gelöscht")
    return core.rm(cfg, name, keep_data=keep_data, on_log=ctx.on_log)


def _secret_set(cfg, ctx, shared, name, spec) -> dict:
    target, value, store = read_secret_spec(spec)
    return core.secret_set(cfg, shared=shared, name=name, target=target, value=value, from_store=store)


def _secret_rm(cfg, ctx, shared, name, spec) -> dict:
    return core.secret_rm(cfg, shared=shared, name=name, target=spec)


def _exec(cfg, ctx, name, argv, root):
    result = core.exec_command(cfg, name, argv, root=root)
    if ctx.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        sys.stdout.write(result["stdout"])
        sys.stderr.write(result["stderr"])
    # Der Exit-Code des Befehls im Container wird durchgereicht.
    raise SystemExit(result["rc"])


def _docs(cfg, ctx, name) -> dict:
    return core.docs(cfg, name, on_log=ctx.on_log)


def _serve(cfg, ctx, bind, port, allow_insecure_bind, allow_insecure_terminal, tls_cert, tls_key, dev):
    from . import server

    if dev:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(message)s", force=True)
    server.serve(cfg, bind=bind, port=port, allow_insecure_bind=allow_insecure_bind,
                 allow_insecure_terminal=allow_insecure_terminal, tls_cert=tls_cert, tls_key=tls_key, dev=dev)


def _jobs(cfg, ctx, container, follow):
    if follow is None:
        return core.jobs(cfg, container)
    state = {}
    for event, data in core.job_events(cfg, follow):
        if event == "log" and not ctx.json:
            print(data, flush=True)
        elif event == "state":
            state = data
    if ctx.json:
        print(json.dumps(core.job_get(cfg, follow), ensure_ascii=False))
    # Ein fehlgeschlagener Job endet mit dem Exit-Code seines Fehlers.
    from .errors import CODES

    raise SystemExit(CODES.get(state.get("error") or "", (0, 0))[0] if state.get("state") == "failed" else 0)


def _confirm(yes: bool, question: str) -> None:
    if yes:
        return
    if not _interactive():
        raise UsageError("Diese Aktion verlangt eine Bestätigung: --yes angeben")
    if input(question + " [j/N] ").strip().lower() not in ("j", "ja"):
        raise UsageError("Abgebrochen, nichts geändert")


def _file_rm(cfg, ctx, name, path, yes) -> dict:
    _confirm(yes, f"{path} in {name} löschen? Ordner werden samt Inhalt gelöscht.")
    return core.file_rm(cfg, name, path)


def _snapshot_restore(cfg, ctx, name, snapshot, yes) -> dict:
    _confirm(yes, f"Daten von {name} auf den Snapshot {snapshot} zurücksetzen? Neuere Änderungen gehen verloren.")
    return core.snapshot_restore(cfg, name, snapshot, on_log=ctx.on_log)


def _restore(cfg, ctx, archive, new_name, replace, edit, yes) -> dict:
    text = None
    if edit:
        inspected = core.restore_inspect(cfg, archive)
        text = edit_text(core.backup_module.set_name(inspected["devc_toml"], new_name or replace), "devc.toml")
    if replace:
        _confirm(yes, f"Container {replace} durch das Backup ersetzen? Seine Daten werden überschrieben.")
    ask = _ask_secret if (not yes and _interactive()) else None
    return core.restore(cfg, archive, new_name=new_name, replace=replace, text=text, ask_secret=ask, on_log=ctx.on_log)


def _config_edit(cfg, ctx, name) -> dict:
    shown = core.config_show(cfg, name)
    return core.config_put(cfg, name, edit_text(shown["text"], "devc.toml"))


def _with_log(func: str):
    def adapter(cfg, ctx, **params):
        return getattr(core, func)(cfg, on_log=ctx.on_log, **params)

    return adapter


# Befehle, die vor dem Use-Case Ein-/Ausgabe brauchen (Editor, Rückfragen, Secrets, Live-Log)
def _tile_panel(cfg, ctx, name, tile, view=None, action=None, args=(), values=()):
    def pairs(specs, option):
        found = {}
        for spec in specs:
            key, sep, value = spec.partition("=")
            if not sep or not key:
                raise UsageError(f"{option} erwartet <name>=<wert>: {spec}")
            found[key] = value
        return found

    given = {key: {"true": True, "false": False}.get(value, value) for key, value in pairs(values, "--value").items()}
    return core.tile_panel(cfg, name, tile, view=view, action=action, args=pairs(args, "--arg"), values=given or None)


ADAPTERS = {
    "tile_panel": _tile_panel,
    "template_edit": _template_edit,
    "create": _create,
    "rm": _rm,
    "secret_set": _secret_set,
    "secret_rm": _secret_rm,
    "exec_command": _exec,
    "docs": _docs,
    "config_edit": _config_edit,
    "config_apply": _with_log("config_apply"),
    "params_set": _with_log("params_set"),
    "install": _with_log("install"),
    "uninstall": _with_log("uninstall"),
    "action_run": _with_log("action_run"),
    "addon_import": _with_log("addon_import"),
    "snapshot_create": _with_log("snapshot_create"),
    "snapshot_restore": _snapshot_restore,
    "file_rm": _file_rm,
    "backup": _with_log("backup"),
    "restore": _restore,
    "serve": _serve,
    "jobs": _jobs,
}


def _run(argv: list[str]) -> tuple[str, object, bool]:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) is None:
        raise UsageError("Ungültiger Aufruf: Befehl fehlt, siehe devc --help")
    params = _params(args)
    if getattr(args, "verbose", 0):
        # zeigt u. a. jeden lxc-Aufruf als Argumentliste (nie stdin)
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s", force=True)
    cfg = config.load(getattr(args, "config", None))
    try:
        if args.func in ADAPTERS:
            ctx = Context(getattr(args, "json", False), getattr(args, "quiet", False))
            result = ADAPTERS[args.func](cfg, ctx, **params)
        else:
            result = getattr(core, args.func)(cfg, **params)
    except NotImplementedError:
        raise InternalError(f"Befehl ist noch nicht umgesetzt: {args.func}") from None
    return args.func, result, getattr(args, "quiet", False)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Nur bis "--" schauen: dahinter stehen Argumente für den Befehl im Container.
    own = argv[: argv.index("--")] if "--" in argv else argv
    json_mode = "--json" in own
    verbose = any(a == "--verbose" or (a.startswith("-v") and set(a[1:]) == {"v"}) for a in own)
    try:
        func, result, quiet = _run(argv)
    except DevcError as error:
        _print_error(error, json_mode)
        return error.exit_code
    except KeyboardInterrupt:
        print("Abgebrochen", file=sys.stderr)
        return 130
    except Exception as error:  # letzte Instanz: alles Unerwartete wird E_INTERNAL
        if verbose:
            traceback.print_exc()
        # Nur der Typ: die Meldung einer fremden Ausnahme könnte Secret-Werte enthalten.
        _print_error(InternalError(f"Unerwarteter Fehler ({type(error).__name__})"), json_mode)
        return InternalError.exit_code
    _print_result(func, result, json_mode, quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
