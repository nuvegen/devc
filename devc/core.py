"""Use-Cases: eine Funktion je CLI-Befehl (SPEC 4, 8.9).

CLI und HTTP-Server rufen ausschließlich diese Funktionen. Hier wird nichts auf
stdout geschrieben; Ergebnisse gehen über Rückgabewerte, Events und Logger.
"""

import datetime
import grp
import gzip
import io
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from importlib import resources
from urllib.parse import urlsplit

from pathlib import Path

from . import addons as addons_module
from . import config as config_module
from . import instance as instance_module
from . import actions as actions_module
from . import agentdocs, locks, names, providers, security, tomlw
from . import backup as backup_module  # `backup` ist unten der Use-Case des CLI-Befehls
from . import jobs as jobs_module  # `jobs` ist unten der Use-Case des CLI-Befehls
from . import secrets as secrets_module
from . import store as store_module
from . import templates as templates_module
from . import panel as panel_module
from . import tiles as tiles_module
from . import vm as vm_module
from .errors import (
    ConflictError,
    DevcError,
    EngineError,
    Finding,
    JobFailedError,
    MissingSecretError,
    NotFoundError,
    SecurityError,
    UsageError,
    ValidationError,
    has_errors,
)
from .providers.base import CreateSpec

LOG_MAX_AGE_DAYS = 30


def clock() -> datetime.datetime:
    """Injizierbare Uhr (SPEC 12); Tests ersetzen core.clock."""
    return datetime.datetime.now(datetime.timezone.utc)


# Injizierbar für Tests (SPEC 12): Wartezeiten und Job-IDs
sleep = time.sleep
new_job_id = jobs_module.new_id


def _provider(config):
    return providers.get_provider(config)


def _store(config) -> store_module.Store:
    return store_module.Store(config.paths.db)


def _now() -> str:
    return store_module.iso(clock())


def web_file(*parts: str):
    """Datei aus devc/web/, auch aus dem zipapp heraus."""
    return resources.files("devc").joinpath("web", *parts)


# --- init, doctor, token -------------------------------------------------


def init(config) -> dict:
    """Config, Token, Verzeichnisse und LXD-Projekt anlegen (idempotent)."""
    paths = config.paths
    changed = []
    directories = [
        (paths.config_file.parent, 0o755),
        (paths.addons, 0o755),
        (paths.templates, 0o755),
        (paths.secrets, 0o700),
        (paths.shared_secrets, 0o700),
        (paths.state, 0o755),
        (paths.containers, 0o755),
        (paths.logs, 0o755),
        (paths.snapshots, 0o755),
        (paths.backups, 0o755),
        (paths.locks, 0o755),
    ]
    for directory, mode in directories:
        if not directory.is_dir():
            directory.mkdir(parents=True, mode=mode)
            changed.append(f"Verzeichnis {directory} angelegt")
        if mode == 0o700 and (directory.stat().st_mode & 0o777) != 0o700:
            directory.chmod(0o700)
            changed.append(f"Verzeichnis {directory} auf 0700 gesetzt")
    if not paths.config_file.exists():
        paths.config_file.write_text(config_module.default_text(), encoding="utf-8")
        changed.append(f"Konfiguration {paths.config_file} geschrieben")
    _, created = security.ensure_token(paths.token)
    if created:
        changed.append(f"Token {paths.token} erzeugt")
    changed += _provider(config).init_project()
    return {"changed": changed}


def _point(point: str, severity: str, message: str, check: str = "doctor") -> Finding:
    return Finding(check, point, message, severity)


def _in_lxd_group() -> bool:
    if os.geteuid() == 0:
        return True
    try:
        gid = grp.getgrnam("lxd").gr_gid
    except KeyError:
        return False
    return gid in os.getgroups() or gid == os.getegid()


def _orphans(config, provider) -> list[Finding]:
    """W3: Instanz-Verzeichnisse oder Volumes ohne Container. devc löscht sie nicht."""
    existing = {state.name for state in provider.list()}
    found = []
    if config.paths.containers.is_dir():
        for entry in sorted(config.paths.containers.iterdir()):
            if entry.name not in existing:
                found.append(_point("orphans", "warning", f"Verzeichnis ohne Container: {entry}", "W3"))
    for volume in provider.volumes():
        for area in ("work", "home"):
            suffix = f"-{area}"
            if volume.name.endswith(suffix) and volume.name[len("devc-"):-len(suffix)] not in existing:
                found.append(_point("orphans", "warning", f"Volume ohne Container: {volume.name}", "W3"))
    return found


def _cleanup_logs(config) -> list[str]:
    cutoff = clock().timestamp() - LOG_MAX_AGE_DAYS * 86400
    removed = []
    if config.paths.logs.is_dir():
        for file in sorted(config.paths.logs.glob("*.log")):
            if file.is_file() and file.stat().st_mtime < cutoff:
                file.unlink()
                removed.append(file.name)
    return removed


def _proxy_points(config) -> list[Finding]:
    """Einstellungen für den Betrieb hinter einem Proxy (L14) und ob Caddy da ist."""
    server = config.server
    problems = security.proxy_problems(server)
    points = [_point("proxy", p.severity, p.message, "L14") for p in problems]
    if not server["base_domain"]:
        return points or [_point("proxy", "ok", "Kein Proxy eingerichtet (server.base_domain ist leer)")]
    if not problems:
        points.append(_point("proxy", "ok", f"devc hinter dem Proxy unter {security.public_url(server)}"))
    if shutil.which("caddy"):
        points.append(_point("caddy", "ok", "Caddy vorhanden"))
    else:
        points.append(_point("caddy", "warning", "Caddy fehlt; ohne Proxy ist devc unter server.base_domain nicht erreichbar"))
    return points


def doctor(config, cleanup: bool = False) -> dict:
    points = []
    version = ".".join(str(n) for n in sys.version_info[:3])
    if sys.version_info >= (3, 11):
        points.append(_point("python", "ok", f"Python {version}"))
    else:
        points.append(_point("python", "error", f"Python {version} ist zu alt, nötig ist 3.11"))

    provider = None
    try:
        provider = _provider(config)
        points += provider.check()
    except EngineError as exc:
        points.append(_point("lxc", "error", exc.message))

    if _in_lxd_group():
        points.append(_point("group", "ok", "Nutzer ist in der Gruppe lxd"))
    else:
        points.append(_point("group", "warning", "Nutzer ist nicht in der Gruppe lxd"))

    roots = config.allowed_roots
    if roots:
        points.append(_point("allowed_roots", "ok", "Shared Folders erlaubt unter: " + ", ".join(roots)))
    else:
        points.append(_point("allowed_roots", "ok", "Keine allowed_roots, Shared Folders sind verboten"))

    if config.lxd["shared_mode"] == "shift" and os.getuid() != 1000:
        points.append(
            _point(
                "shift",
                "warning",
                f'Host-UID ist {os.getuid()}, nicht 1000: dev kann in Shared Folders nicht schreiben; lxd.shared_mode = "idmap" verwenden',
                "W5",
            )
        )

    if provider is not None and not any(p.severity == "error" for p in points):
        try:
            orphans = _orphans(config, provider)
            points += orphans or [_point("orphans", "ok", "Keine Waisen")]
        except DevcError as exc:
            points.append(_point("orphans", "warning", f"Waisen nicht prüfbar: {exc.message}"))

    points += _proxy_points(config)

    if web_file("vendor", "xterm.js").is_file():
        points.append(_point("xterm", "ok", "xterm.js vorhanden"))
    else:
        points.append(_point("xterm", "warning", "xterm.js fehlt, Web-Terminal zeigt nur den Befehl devc shell"))

    result = {
        "points": [p.to_dict() for p in points],
        "errors": sum(p.severity == "error" for p in points),
        "warnings": sum(p.severity == "warning" for p in points),
    }
    if cleanup:
        result["cleaned"] = _cleanup_logs(config)
    if result["errors"]:
        raise EngineError(f"doctor meldet {result['errors']} Fehler", result)
    return result


# --- Hilfe (SPEC 8.12) ---------------------------------------------------------

# Gepflegt wird unter docs/; devc/help/ trägt Kopien (ein Test prüft die Gleichheit).
HELP_TOPICS = (
    ("user", "Erste Schritte", "Einfache Anleitung für die Arbeit in der Oberfläche"),
    ("technical", "Handbuch", "Alle Befehle, Konfiguration, Addons, Proxy, Fehlercodes"),
    ("ai", "Für KI-Agenten", "Regeln und Arbeitsweise für einen KI-Agenten, der mit devc arbeitet"),
)


def help_list(config) -> dict:
    return {"topics": [{"id": topic, "title": title, "summary": summary} for topic, title, summary in HELP_TOPICS]}


def help_show(config, topic: str) -> dict:
    for topic_id, title, summary in HELP_TOPICS:
        if topic_id == topic:
            text = resources.files("devc").joinpath("help", f"{topic_id}.md").read_text(encoding="utf-8")
            return {"id": topic_id, "title": title, "summary": summary, "text": text}
    raise NotFoundError(f"Hilfethema fehlt: {topic}")


# --- Proxy (SPEC 8.11) ---------------------------------------------------------


def proxy_target(config, name: str, port: int) -> dict:
    """Ziel eines Container-Ports für den Proxy: nur verwaltete, laufende Container, nur Ports ab 1024.

    `public` sagt, ob ein aktives Addon den Port ohne Anmeldung freigibt (Link mit `public = true`).
    Fremde, fehlende und gestoppte Container sind nicht unterscheidbar (alle E_NOT_FOUND).
    """
    missing = NotFoundError("Container fehlt oder läuft nicht")
    if not names.is_container(name) or not isinstance(port, int) or not (1 <= port <= 65535):
        raise missing
    low, high = security.PROXY_PORTS
    if not (low <= port <= high):
        raise SecurityError(f"Port {port} ist über den Proxy nicht erreichbar (erlaubt: {low}–{high})")
    if _store(config).container(name) is None:
        raise missing
    try:
        state = _provider(config).state(name)
    except NotFoundError:
        raise missing from None
    if state.status != "running" or not state.ip:
        raise missing
    return {"container": name, "port": port, "upstream": f"{state.ip}:{port}", "public": port in _public_ports(config, name)}


def _public_ports(config, name: str) -> set[int]:
    """Ports des Containers, die aktive Addons ohne Anmeldung freigeben. Im Zweifel keiner."""
    try:
        _, data = _instance(config, name)
        cat = catalog(config)
        params = instance_module.effective_params(data, cat)
        order, _ = addons_module.resolve_order(data.get("addons", []), cat)
        return set().union(*(actions_module.public_ports(cat.get(addon_id), params[addon_id]) for addon_id in order))
    except (DevcError, KeyError, AttributeError):
        return set()


_CADDY_HEAD = """\
# Caddyfile für devc – erzeugt mit `devc proxy config`.
# Ablegen als /etc/caddy/Caddyfile, danach: sudo systemctl reload caddy
# Basisdomain: {base}

# Container-Ports: devc prüft Anmeldung, Container und Port und nennt das Ziel.
(devc_ports) {{
	forward_auth {devc} {{
		uri /api/proxy/auth
		copy_headers {header}
	}}
	reverse_proxy {{http.request.header.{header}}} {{
		header_up -{header}
		header_up Cookie "devc_(proxy|session)=[^;]*(; *)?" ""
	}}
	# Ohne das käme ein leerer Fehler 502, wenn der Dienst im Container nicht läuft.
	handle_errors 502 503 504 {{
		header Content-Type "text/plain; charset=utf-8"
		respond "devc: Unter {{host}} antwortet kein Dienst. Im Container lauscht nichts auf diesem Port, oder nur auf 127.0.0.1 statt auf 0.0.0.0." {{err.status_code}}
	}}
}}
"""

_CADDY_SITES = """
{scheme}devc.{base}{port} {{{tls}
	reverse_proxy {devc}
}}

{scheme}*.{base}{port} {{{tls}
	import devc_ports
}}
"""

# Hinweisseite für alles, was auf einem unverschlüsselten Port keinen der Namen oben trifft, vor allem den Aufruf
# über die IP-Adresse. Die Adresse im Text ist die, auf der die Verbindung ankam; der Host-Kopf des Aufrufers
# kommt nicht in die Seite. Kein CSS mit geschweiften Klammern: Caddy läse sie als Platzhalter.
_CADDY_INFO = """
http://:{port} {{
	header Content-Type "text/html; charset=utf-8"
	header Content-Security-Policy "default-src 'none'; style-src 'unsafe-inline'"
	header Cache-Control "no-store"
	respond <<DEVC_INFO
	<!doctype html>
	<html lang="de">
	<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>devc</title></head>
	<body style="font-family: system-ui, sans-serif; max-width: 44rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5">
	<h1>devc</h1>
	<p>Hier läuft devc. Die Oberfläche ist aber nur unter ihrem Namen erreichbar, nicht unter dieser Adresse:</p>
	<p><a href="{url}">{url}</a></p>
{steps}
	<h2>Der Token</h2>
	<p>Für die Anmeldung brauchst du den Token. Du bekommst ihn nur auf der Konsole, in der devc läuft:</p>
	<pre>devc token show</pre>
	<p>Läuft devc in einer LXD-VM, geht es von außen so:</p>
	<pre>lxc exec &lt;name-der-vm&gt; -- devc token show</pre>
	</body>
	</html>
	DEVC_INFO 404
}}
"""

_CADDY_INFO_TUNNEL = """\
	<h2>So kommst du hin</h2>
	<p>Der Name <code>devc.{base}</code> zeigt immer auf deinen eigenen Rechner. Du brauchst darum eine Weiterleitung von deinem Rechner hierher. Danach geht der Link oben.</p>
	<p>Von einem anderen Rechner, mit einem SSH-Tunnel:</p>
	<pre>ssh -N -L {local}:{{http.request.local.host}}:{remote} &lt;nutzer&gt;@&lt;rechner&gt;</pre>
	<p>Auf dem Rechner, auf dem diese VM läuft:</p>
	<pre>socat TCP-LISTEN:{local},bind=127.0.0.1,fork,reuseaddr TCP:{{http.request.local.host}}:{remote}</pre>"""

_CADDY_INFO_DOMAIN = """\
	<h2>So kommst du hin</h2>
	<p>Die Namen <code>devc.{base}</code> und <code>*.{base}</code> müssen im DNS auf diese Adresse zeigen.</p>"""


def _caddy_info(server: dict, http_port: int | None) -> str:
    base = server["base_domain"]
    if http_port is not None:
        url, local, remote = f"http://{security.PROXY_LABEL}.{base}:{http_port}/", http_port, http_port
    else:
        url, local, remote = security.public_url(server), server["public_port"], 443
    if security.is_local_domain(base):
        steps = _CADDY_INFO_TUNNEL.format(base=base, local=local, remote=remote)
    else:
        steps = _CADDY_INFO_DOMAIN.format(base=base)
    text = "\n# Alles andere, zum Beispiel der Aufruf über die IP-Adresse: Hinweisseite statt leerer Antwort."
    for port in dict.fromkeys((80, http_port) if http_port is not None else (80,)):
        text += _CADDY_INFO.format(port=port, url=url, steps=steps)
    return text


def proxy_config(config, http_port: int | None = None) -> dict:
    """Caddyfile aus der Konfiguration. devc schreibt die Datei nicht selbst nach /etc."""
    server = config.server
    base = server["base_domain"]
    if not base:
        raise ValidationError.single("L14", "server.base_domain", "server.base_domain ist leer; ohne Basisdomain gibt es keinen Proxy")
    if http_port is not None:  # Option statt Einstellung in der config.toml
        if not (1 <= http_port <= 65535):
            raise UsageError("--http-port muss zwischen 1 und 65535 liegen")
        if not security.is_local_domain(base):
            raise UsageError("--http-port gibt es nur für Basisdomains unter localhost; sonst ist nur HTTPS erlaubt")
    bind = server["bind"]
    host = bind if security.is_loopback(bind) and bind != "localhost" else "127.0.0.1"
    devc = f"[{host}]:{server['port']}" if ":" in host else f"{host}:{server['port']}"
    problems = [f for f in security.proxy_problems(dict(server, behind_proxy=True)) if f.path == "server.tls_cert"]
    cert, key = server["tls_cert"], server["tls_key"]
    if any(c.isspace() or c in "{}\"" for c in cert + key):
        problems.append(Finding("L14", "server.tls_cert", "tls_cert und tls_key dürfen im Caddyfile keine Leerzeichen, Klammern oder Anführungszeichen enthalten"))
    if problems:
        raise ValidationError(problems)
    tls = f"\n\ttls {cert} {key}" if cert else "\n\ttls internal"
    text = _CADDY_HEAD.format(base=base, devc=devc, header=security.UPSTREAM_HEADER)
    text += _CADDY_SITES.format(scheme="", base=base, port="", tls=tls, devc=devc)
    if http_port is not None:
        text += "\n# Unverschlüsselt, nur für den Zugriff über einen SSH-Tunnel (localhost)."
        text += _CADDY_SITES.format(scheme="http://", base=base, port=f":{http_port}", tls="", devc=devc)
    text += _caddy_info(server, http_port)
    return {"caddyfile": text}


def token_show(config) -> dict:
    token = security.read_token(config.paths.token)
    if token is None:
        raise NotFoundError("Token fehlt; devc init ausführen")
    return {"token": token}


def token_rotate(config) -> dict:
    return {"token": security.write_token(config.paths.token)}


# --- Addons und Templates (Katalog) ---------------------------------------


def catalog(config) -> addons_module.Catalog:
    return addons_module.catalog(config.paths.addons)


def store_names(config) -> set[str]:
    """Namen der Einträge im geteilten Secret-Store (nie Werte)."""
    directory = config.paths.shared_secrets
    if not directory.is_dir():
        return set()
    return {p.name for p in directory.iterdir() if p.is_file() and names.is_store(p.name)}


def _findings(findings) -> list[dict]:
    return [f.to_dict() for f in findings]


def addon_list(config) -> dict:
    cat = catalog(config)
    return {
        "addons": [addon.summary() for addon in cat.sorted()],
        "broken": [{"id": key, "findings": _findings(value)} for key, value in sorted(cat.broken.items())],
    }


def _addon(config, addon_id: str) -> addons_module.Addon:
    cat = catalog(config)
    addon = cat.get(addon_id)
    if addon is None:
        if addon_id in cat.broken:
            raise ValidationError(cat.broken[addon_id])
        raise NotFoundError(f"Addon fehlt: {addon_id}")
    return addon


def addon_show(config, id: str) -> dict:
    addon = _addon(config, id)
    data = addon.to_dict()
    data["readme"] = (addon.path / "README.md").read_text(encoding="utf-8")
    return data


def addon_validate(config, dir: str) -> dict:
    directory = Path(dir)
    if not directory.is_dir():
        raise NotFoundError(f"Verzeichnis fehlt: {dir}")
    addon, findings = addons_module.load(directory)
    if addon is None:
        raise ValidationError(findings)
    return {"id": addon.id, "version": addon.version, "findings": _findings(findings)}


def _template(config, template_id: str) -> templates_module.Template:
    located = templates_module.locate(config.paths.templates)
    if template_id not in located:
        raise NotFoundError(f"Template fehlt: {template_id}")
    path, origin = located[template_id]
    return templates_module.read(template_id, path, origin)


def template_list(config) -> dict:
    result = []
    for template_id, (path, origin) in sorted(templates_module.locate(config.paths.templates).items()):
        try:
            result.append(templates_module.read(template_id, path, origin).summary())
        except ValidationError as exc:
            result.append({"id": template_id, "origin": origin, "error": exc.message,
                           "origin_label": addons_module.ORIGIN_LABELS[origin]})
    return {"templates": result}


def template_show(config, id: str) -> dict:
    template = _template(config, id)
    return {"meta": template.summary(), "text": template.text}


def _check_template(config, text: str, template_id: str) -> list[Finding]:
    _, findings = templates_module.check_text(text, template_id, catalog(config), store_names(config))
    if has_errors(findings):
        raise ValidationError(findings)
    return findings


def template_validate(config, file: str) -> dict:
    path = Path(file)
    if not path.is_file():
        raise NotFoundError(f"Datei fehlt: {file}")
    if path.suffix != ".toml":
        raise UsageError("Template-Datei muss auf .toml enden")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValidationError.single("L9", path.name, "Datei ist nicht UTF-8") from None
    return {"findings": _findings(_check_template(config, text, path.stem))}


def template_put(config, id: str, text: str) -> dict:
    """Speichert ein eigenes Template als Text, nachdem L9 bestanden ist (SPEC 7.1)."""
    finding = names.check("template", id, "id")
    if finding:
        raise ValidationError([finding])
    findings = _check_template(config, text, id)
    config.paths.templates.mkdir(parents=True, exist_ok=True)
    target = config.paths.templates / f"{id}.toml"
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    return {"id": id, "findings": _findings(findings)}


def draft(config, name: str, template: str | None = None) -> dict:
    """Entwurf einer devc.toml (SPEC 7.4); noch nichts wird angelegt."""
    finding = names.check("container", name, "name")
    if finding:
        raise ValidationError([finding])
    cat = catalog(config)
    if template:
        text = templates_module.draft(_template(config, template).text, name, cat, template)
    else:
        text = templates_module.blank_draft(name, config.defaults)
    _, findings = instance_module.check_text(text, cat, store_names(config))
    return {"text": text, "findings": _findings(findings)}


# --- Container: Hilfen ---------------------------------------------------

ADDON_ROOT = "/opt/devc/addons"
READY_TIMEOUT = 90
CLOUD_INIT_TIMEOUT = 600
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_SSH_BEGIN = "# devc:begin"
_SSH_END = "# devc:end"


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def _exec(provider, name, argv, what, user="root", cwd="/", stdin=None, timeout=None, ok=(0,)):
    """Führt einen festen Befehl im Container aus; Exit außerhalb von `ok` → E_JOB_FAILED.

    Nur für Befehle ohne Secret-Daten in der Ausgabe: stderr geht in die Fehlermeldung.
    """
    result = provider.exec(name, argv, user=user, cwd=cwd, stdin=stdin, timeout=timeout)
    if result.rc not in ok:
        detail = _last_line(result.stderr)
        raise JobFailedError(f"{what} fehlgeschlagen (Exit {result.rc})" + (f": {detail}" if detail else ""))
    return result


def _instance(config, name: str) -> tuple[str, dict]:
    file = config.paths.instance_file(name)
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise NotFoundError(f"Instanz-Konfiguration fehlt: {file}") from None
    return text, templates_module.parse_toml(text, "L10", "devc.toml")


def _managed(store, provider, name: str, running: bool = False, allow_missing: bool = False):
    """Liefert (Zeile, Zustand) eines von devc verwalteten Containers.

    devc ändert nur Container mit Eintrag in `containers` (SPEC 8.3); fremde sind gesperrt.
    """
    finding = names.check("container", name, "name")
    if finding:
        raise ValidationError([finding])
    row = store.container(name)
    try:
        state = provider.state(name)
    except NotFoundError:
        state = None
    if row is None:
        if state is not None:
            raise ConflictError(f"Container {name} wird nicht von devc verwaltet (foreign)")
        raise NotFoundError(f"Container fehlt: {name}")
    if state is None and not allow_missing:
        raise NotFoundError(f"Container {name} fehlt in LXD (missing); devc rm {name} räumt auf")
    if running and (state is None or state.status != "running"):
        raise ConflictError("Container läuft nicht", {"container": name})
    return row, state


# Der Server setzt je Thread einen Rückruf, um die Job-ID zu erfahren, sobald der Job angelegt ist.
_context = threading.local()


def set_job_hook(hook) -> None:
    _context.on_job = hook


def set_progress_hook(hook) -> None:
    """Rückruf für den Stand langer Schritte in diesem Thread (CLI): `hook(stand)` oder `hook(None)` am Ende."""
    _context.on_progress = hook


def _job(config, store, kind, container, func, on_log):
    return jobs_module.run(config, store, kind, container, func, clock, on_log, new_job_id(),
                           on_start=getattr(_context, "on_job", None), on_progress=getattr(_context, "on_progress", None))


def _wait_ready(provider, job, name: str) -> None:
    """Wartet auf Netz und das Ende von cloud-init, damit apt nicht mit dem ersten Boot kollidiert."""
    deadline = READY_TIMEOUT
    while deadline > 0 and provider.state(name).ip is None:
        sleep(1)
        deadline -= 1
    if deadline <= 0:
        job.warn("create", "Container hat nach 90 s noch keine IPv4-Adresse")
    try:
        provider.exec(
            name,
            ["sh", "-c", "command -v cloud-init >/dev/null 2>&1 && cloud-init status --wait >/dev/null 2>&1; true"],
            user="root", cwd="/", timeout=CLOUD_INIT_TIMEOUT,
        )
    except EngineError:
        job.warn("create", "cloud-init war nach 10 Minuten nicht fertig; es geht trotzdem weiter")


def _runtime(config, provider, job, name: str) -> None:
    """SPEC 8.4 Schritte 5 und 6: Laufzeitverzeichnisse und Secrets."""
    secrets_module.prepare_runtime(provider, name)
    slots = secrets_module.push_all(provider, config.paths, name)
    if job is not None:
        job.log("secrets", f"{len(slots)} Secret(s) nach /run/devc/secrets: " + (", ".join(slots) or "keine"))


def _addon_archive(addon) -> bytes:
    """Das Addon-Verzeichnis als tar.gz im Speicher."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", dereference=True) as archive:
        for file in sorted(addon.path.rglob("*")):
            relative = file.relative_to(addon.path)
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            if file.is_dir() or file.is_file():
                info = archive.gettarinfo(str(file), str(relative))
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                if file.is_file():
                    with open(file, "rb") as handle:
                        archive.addfile(info, handle)
                else:
                    archive.addfile(info)
    return buffer.getvalue()


def _text_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def addon_env(config, name: str, addon, values: dict) -> dict:
    """Umgebung für install-Skripte (SPEC 8.5). Secrets nur als Pfad, nie als Wert."""
    env = {
        "DEVC_CONTAINER": name,
        "DEVC_ADDON": addon.id,
        "DEVC_ADDON_DIR": f"{ADDON_ROOT}/{addon.id}",
        "DEVC_WORKDIR": "/work",
        "DEVC_USER": "dev",
        "DEBIAN_FRONTEND": "noninteractive",
    }
    for key, value in values.items():
        env[f"DEVC_PARAM_{key.upper()}"] = _text_value(value)
    filled = set(secrets_module.local_slots(config.paths, name))
    for param in addon.params:
        slot = f"{addon.id}.{param.name}"
        if param.type == "secret" and slot in filled:
            env[f"DEVC_SECRET_{param.name.upper()}"] = secrets_module.container_path(slot)
    return env


def _run_scripts(config, provider, job, name, addon, scripts, values) -> None:
    env = addon_env(config, name, addon, values)
    directory = f"{ADDON_ROOT}/{addon.id}"
    for script in scripts:
        job.log("addon", f"{addon.id}: {script} …")
        rc = provider.exec_stream(
            name, ["bash", "-euo", "pipefail", script], user="root", cwd=directory, env=env,
            on_line=lambda line: job.log("addon", f"{addon.id}: {line}"),
        )
        if rc != 0:
            raise JobFailedError(f"Addon {addon.id}: {script} endete mit Exit {rc}")
        job.log("addon", f"{addon.id}: {script} ok")


_INSTALL_FILE = (
    'if [ "$5" = false ] && [ -e "$2" ]; then exit 0; fi; d=$(dirname "$2"); '
    '[ -d "$d" ] || install -d -o "$4" -g "$4" "$d"; install -m "$3" -o "$4" -g "$4" "$1" "$2"'
)


def _install_addon(config, provider, job, name: str, addon, values: dict) -> None:
    """SPEC 8.4 Schritt 7 für ein Addon: kopieren, Archive, install-Skripte, Dateien."""
    directory = f"{ADDON_ROOT}/{addon.id}"
    _exec(
        provider, name,
        ["sh", "-c", 'rm -rf "$1" && mkdir -p "$1" && tar -xzf - -C "$1" --no-same-owner && chmod 0755 "$1"', "sh", directory],
        f"Addon {addon.id} kopieren", stdin=_addon_archive(addon),
    )
    for archive in addon.archives:
        _exec(
            provider, name,
            ["sh", "-c", 'mkdir -p "$2" && tar -xzf "$1" -C "$2" --no-same-owner', "sh", f"{directory}/{archive['src']}", archive["dest"]],
            f"Addon {addon.id}: Archiv {archive['src']} entpacken",
        )
        job.log("addon", f"{addon.id}: {archive['src']} nach {archive['dest']} entpackt")
    _run_scripts(config, provider, job, name, addon, addon.install, values)
    for item in addon.files:
        owner = "1000" if item["owner"] == "dev" else "0"
        _exec(
            provider, name,
            ["sh", "-c", _INSTALL_FILE, "sh", f"{directory}/{item['src']}", item["dest"], item["mode"], owner,
             "true" if item["overwrite"] else "false"],
            f"Addon {addon.id}: Datei {item['dest']} schreiben",
        )


def _record_addons(store, provider, name: str, installed: dict) -> None:
    text = "# von devc geschrieben\n" + tomlw.dumps(dict(sorted(installed.items())))
    _exec(
        provider, name,
        ["sh", "-c", "mkdir -p /etc/devc && cat > /etc/devc/addons.toml && chmod 0644 /etc/devc/addons.toml"],
        "/etc/devc/addons.toml schreiben", stdin=text.encode("utf-8"),
    )
    store.container_set_addons(name, installed)


_WRITE_FILE = (
    'if [ "$3" = false ] && [ -e "$1" ]; then exit 0; fi; '
    'mkdir -p "$(dirname "$1")" && cat > "$1" && chmod "$2" "$1"'
)


# Entfernt im Ordner $1 jede *.md, deren Name nicht unter den übrigen Argumenten steht.
_PRUNE_PAGES = (
    'cd "$1" 2>/dev/null || exit 0; shift; '
    'for f in *.md; do [ -f "$f" ] || continue; keep=0; for k in "$@"; do [ "$f" = "$k" ] && keep=1; done; '
    '[ "$keep" = 1 ] || rm -f -- "$f"; done'
)


def _write_file(provider, name, dest: str, content: str, mode="0644", overwrite=True, user="dev") -> None:
    _exec(
        provider, name,
        ["sh", "-c", _WRITE_FILE, "sh", dest, mode, "true" if overwrite else "false"],
        f"Datei {dest} schreiben", user=user, cwd="/", stdin=content.encode("utf-8"),
    )


def _read_file(provider, name, path: str, user="dev") -> str | None:
    result = _exec(
        provider, name, ["sh", "-c", 'if [ -e "$1" ]; then cat "$1"; else exit 44; fi', "sh", path],
        f"Datei {path} lesen", user=user, cwd="/", ok=(0, 44),
    )
    return None if result.rc == 44 else result.stdout


def git_host(remote: str) -> str | None:
    """Host eines SSH-Remotes (ssh://… oder user@host:pfad); None bei HTTPS oder ungültigem Host."""
    if remote.startswith("ssh://"):
        try:
            host = urlsplit(remote).hostname
        except ValueError:
            return None
    elif "://" not in remote and "@" in remote and ":" in remote:
        host = remote.split("@", 1)[1].split(":", 1)[0]
    else:
        return None
    return host if host and _HOST_RE.match(host) else None


def _setup_git(provider, job, name: str, data: dict) -> None:
    """SPEC 8.4 Schritt 9. Kein git init, kein Push."""
    remote = (data.get("git") or {}).get("remote", "")
    if not remote:
        return
    # Nur ~/.ssh/config; user.name und user.email setzt das Addon base aus seinen Parametern.
    host = git_host(remote)
    if host is None:
        if not remote.startswith("https://"):
            job.warn("git", f"Kein gültiger Host im Git-Remote, ~/.ssh/config bleibt unverändert: {remote}")
        else:
            job.log("git", "HTTPS-Remote, kein SSH-Schlüssel nötig")
        return
    path = "/home/dev/.ssh/config"
    existing = _read_file(provider, name, path) or ""
    entry = f"{_SSH_BEGIN}\nHost {host}\n  IdentityFile /run/devc/secrets/git.key\n  IdentitiesOnly yes\n{_SSH_END}\n"
    start, stop = existing.find(_SSH_BEGIN), existing.find(_SSH_END)
    if 0 <= start < stop:
        rest = existing[stop + len(_SSH_END):]
        text = existing[:start] + entry + (rest[1:] if rest.startswith("\n") else rest)
    else:
        text = existing + ("" if not existing or existing.endswith("\n") else "\n") + entry
    _exec(provider, name, ["install", "-d", "-m", "0700", "/home/dev/.ssh"], "~/.ssh anlegen", user="dev")
    _write_file(provider, name, path, text, mode="0600")
    job.log("git", f"~/.ssh/config: Schlüssel /run/devc/secrets/git.key für {host}")


def _write_docs(config, provider, job, name: str, data: dict, cat) -> None:
    """SPEC 6.5: CLAUDE.md-Block, AGENTS.md und das Infoverzeichnis .devc/."""
    order, _ = addons_module.resolve_order(data.get("addons", []), cat)
    params = instance_module.effective_params(data, cat)
    ai = []
    for addon_id in order:
        addon = cat.get(addon_id)
        ai.append((addon, (addon.path / "AI.md").read_text(encoding="utf-8"), params[addon_id]))
    image = data.get("image", config.defaults["image"])
    ip = provider.state(name).ip
    block = agentdocs.block(data, image, ai, ip)
    merged = agentdocs.merge(_read_file(provider, name, "/work/CLAUDE.md"), block)
    _write_file(provider, name, "/work/CLAUDE.md", merged)
    link = provider.exec(
        name,
        ["sh", "-c", "if [ -L AGENTS.md ] || [ ! -e AGENTS.md ]; then ln -sfn CLAUDE.md AGENTS.md; else exit 45; fi"],
        user="dev", cwd="/work",
    )
    if link.rc == 45:
        job.warn("docs", "W4 /work/AGENTS.md ist kein Symlink und bleibt unangetastet")
    elif link.rc != 0:
        raise JobFailedError(f"AGENTS.md verlinken fehlgeschlagen (Exit {link.rc})")
    active, others = _write_info(config, provider, name, data, cat, ip)
    job.log("docs", f"CLAUDE.md, AGENTS.md und {agentdocs.INFO_DIR}/ geschrieben ({active} aktive, {others} weitere Addons)")


def _write_info(config, provider, name: str, data: dict, cat, ip, only=None) -> tuple[int, int]:
    """Infoverzeichnis /work/.devc/ (SPEC 6.5): Übersicht, Kopie der Konfiguration ohne Geheimnisse, eine Seite je Addon
    des Katalogs. `only`: nur die Übersicht und die Seiten dieser Addons schreiben (nach einer Änderung an einem Secret)."""
    order, _ = addons_module.resolve_order(data.get("addons", []), cat)
    params = instance_module.effective_params(data, cat)
    image = data.get("image", config.defaults["image"])
    stored = {entry["slot"] for entry in secrets_module.local_list(config.paths, name)}
    secrets = {slot: slot in stored for slot in instance_module.secret_slots(data, cat)}
    active = [cat.get(addon_id) for addon_id in order]
    others = [addon for addon in cat.sorted() if addon.id not in order and data.get("platform", "lxd") in addon.platforms]
    info = "/work/" + agentdocs.INFO_DIR
    _write_file(provider, name, f"{info}/README.md", agentdocs.index(data, image, active, others, ip, secrets))
    for addon in active + others:
        if only is not None and addon.id not in only:
            continue
        texts = [(addon.path / file).read_text(encoding="utf-8") for file in ("AI.md", "README.md")]
        page = agentdocs.addon_page(addon, texts[0], texts[1], params.get(addon.id, addon.values()), name, ip, addon.id in order, secrets)
        _write_file(provider, name, f"{info}/addons/{addon.id}.md", page)
    if only is None:
        try:
            source = config.paths.instance_file(name).read_text(encoding="utf-8")
        except OSError:
            source = ""
        _write_file(provider, name, f"{info}/config.toml", agentdocs.config_copy(source))
        # Seiten von Addons, die es im Katalog nicht mehr gibt, entfernen: Der Ordner gehört devc (SPEC 6.5).
        _exec(provider, name, ["sh", "-c", _PRUNE_PAGES, "sh", f"{info}/addons", *[f"{addon.id}.md" for addon in active + others]],
              "alte Addon-Seiten entfernen", user="dev", cwd="/")
    return len(active), len(others)


def _secret_info(config, provider, name: str, data: dict, slot: str) -> bool:
    """Nach einer Änderung an einem Secret: Zustand »gesetzt / nicht gesetzt« im Infoverzeichnis nachziehen.
    Das Secret selbst ist da schon gesetzt oder entfernt; scheitert das Schreiben, bleibt es dabei."""
    try:
        _write_info(config, provider, name, data, catalog(config), provider.state(name).ip, only={slot.split(".", 1)[0]})
    except DevcError:
        return False
    return True


def _write_instance_files(provider, job, name: str, data: dict) -> None:
    files = data.get("files", [])
    for item in files:
        _write_file(provider, name, item["dest"], item["content"], item.get("mode", "0644"), item.get("overwrite", True))
    if files:
        job.log("files", f"{len(files)} Datei(en) der Instanz geschrieben")


def _provision(config, store, provider, job, name: str, data: dict, cat, only=None, text=None) -> None:
    """SPEC 8.4 Schritte 7–11. `only`: nur diese Addons (neu) installieren; None = alle.

    `text` ist der Text der devc.toml; er wird am Ende als angewendeter Stand abgelegt.
    """
    order, _ = addons_module.resolve_order(data.get("addons", []), cat)
    params = instance_module.effective_params(data, cat)
    installed = dict(store.container(name)["addons"])
    for addon_id in order:
        if only is not None and addon_id not in only:
            continue
        addon = cat.get(addon_id)
        job.log("addon", f"{addon_id} {addon.version} wird installiert")
        _install_addon(config, provider, job, name, addon, params[addon_id])
        installed[addon_id] = addon.version
        _record_addons(store, provider, name, installed)
    _write_instance_files(provider, job, name, data)
    _setup_git(provider, job, name, data)
    _write_docs(config, provider, job, name, data, cat)
    if text is not None:
        config.paths.applied_file(name).write_text(text, encoding="utf-8")
    store.container_set_applied(name, _now())


# --- Container: Use-Cases -------------------------------------------------


def build_draft(config, name, template=None, from_file=None, addons=(), params=(), secret_stores=None,
                shared=None, git=None, image=None, cpu=None, memory=None, disk=None) -> str:
    """Entwurf erzeugen und CLI-Optionen zusammenführen (SPEC 7.4, 7.5)."""
    if template and from_file:
        raise UsageError("--from schließt --template aus")
    cat = catalog(config)
    if from_file:
        try:
            text = Path(from_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise NotFoundError(f"Datei fehlt: {from_file}") from None
        except UnicodeDecodeError:
            raise ValidationError.single("L10", str(from_file), "Datei ist nicht UTF-8") from None
    else:
        text = draft(config, name, template)["text"]
    return instance_module.merge(
        text, cat, addons=addons, params=params, secret_stores=secret_stores, shared=shared, git=git,
        image=image, cpu=cpu, memory=memory, disk=disk,
    )


def validate_instance(config, text: str, name: str | None = None, provided=()) -> dict:
    """Prüft den Text einer devc.toml (L2, L5–L12, L15); ändert nichts."""
    cat = catalog(config)
    stores = store_names(config)
    data, findings = instance_module.check_text(text, cat, stores)
    if name is not None and data.get("name") not in (None, name):
        findings.append(Finding("L10", "name", f"name in der devc.toml ({data.get('name')}) passt nicht zu {name}"))
    if not has_errors(findings):
        findings += instance_module.check_shared(data, config.allowed_roots)
    missing = [] if has_errors(findings) else instance_module.missing_secrets(data, cat, stores, provided)
    return {"data": data, "findings": findings, "missing_secrets": missing}


def create(config, name, text=None, template=None, from_file=None, addons=(), params=(), secret_stores=None,
           secret_values=None, shared=None, git=None, image=None, cpu=None, memory=None, disk=None,
           no_start=False, ask_secret=None, on_log=None, _kind="create", _before_provision=None, _locked=False) -> dict:
    """Container anlegen (SPEC 8.4).

    secret_values: Slot -> Rohbytes. secret_stores: Slot -> Store-Name.
    ask_secret: Rückfrage der CLI für fehlende Secrets (Slot -> Rohbytes); None = E_MISSING_SECRET.
    """
    secret_values = dict(secret_values or {})
    secret_stores = dict(secret_stores or {})
    for slot in list(secret_values) + list(secret_stores):
        if not names.is_slot(slot):
            raise UsageError(f"Secret-Slot ungültig: {slot}")
    if text is None:
        text = build_draft(config, name, template, from_file, addons, params, secret_stores, shared, git, image, cpu, memory, disk)
    kept = set(secrets_module.local_slots(config.paths, name)) if names.is_container(name) else set()
    checked = validate_instance(config, text, name, provided=set(secret_values) | kept)
    data, findings = checked["data"], checked["findings"]
    if has_errors(findings):
        raise ValidationError(findings)
    cat = catalog(config)
    slots = instance_module.secret_slots(data, cat)
    unknown = [slot for slot in secret_values if slot not in slots and slot != names.GIT_KEY_SLOT]
    if unknown:
        raise UsageError("Kein Secret-Slot dieser Instanz: " + ", ".join(unknown))
    for slot in checked["missing_secrets"]:
        if ask_secret is None:
            raise MissingSecretError(checked["missing_secrets"])
        secret_values[slot] = ask_secret(slot)

    provider = _provider(config)
    store = _store(config)
    if store.container(name) is not None or any(state.name == name for state in provider.list()):
        raise ConflictError(f"Name ist belegt: {name}", {"check": "L10", "path": "name"})
    resources = instance_module.resources_of(data, config.defaults)
    spec = CreateSpec(
        name=name,
        image=data.get("image", config.defaults["image"]),
        cpu=resources["cpu"], memory=resources["memory"], disk=resources["disk"],
        shared=(data.get("data") or {}).get("shared"),
    )

    def work(job):
        if data.get("template"):
            job.log("template", f"Entwurf aus {data['template']}")
        config.paths.container_dir(name).mkdir(parents=True, exist_ok=True)
        config.paths.instance_file(name).write_text(text, encoding="utf-8")
        store.container_add(name, "lxd", _now())
        for slot, value in secret_values.items():
            secrets_module.local_set(config.paths, name, slot, value, "local")
        for slot, info in slots.items():
            if slot not in secret_values and slot not in kept and info["store"] is not None:
                secrets_module.local_set_from_store(config.paths, name, slot, info["store"])
        provider.create(spec)
        job.log("create", f"Container {name} aus {spec.image} ({spec.cpu} CPU, {spec.memory}, {spec.disk})")
        job.log("mount", f"/work: {spec.shared or names.volume_name(name, 'work')}, /home/dev: {names.volume_name(name, 'home')}")
        provider.start(name)
        _wait_ready(provider, job, name)
        _runtime(config, provider, job, name)
        if _before_provision is not None:
            _before_provision(provider, job)
        _provision(config, store, provider, job, name, data, cat, text=text)
        # `--no-start` richtet den Container vollständig ein und stoppt ihn danach.
        if no_start:
            provider.stop(name)
        return {"name": name, "status": "stopped" if no_start else "running"}

    if _locked:
        result, job = _job(config, store, _kind, name, work, on_log)
    else:
        with locks.container_lock(config.paths, name):
            result, job = _job(config, store, _kind, name, work, on_log)
    result.update(job=job.id, warnings=job.warnings, findings=_findings(findings))
    return result


def _installing(config, row) -> bool:
    """Unvollständig und der Lock ist belegt: Die Einrichtung läuft noch, sie ist nicht gescheitert."""
    return row["applied_at"] is None and locks.is_locked(config.paths, row["name"])


def list_containers(config) -> dict:
    """Gleicht die Datenbank mit LXD ab: fehlende Container sind `missing`, fremde `foreign`."""
    provider = _provider(config)
    store = _store(config)
    states = {state.name: state for state in provider.list()}
    result = []
    for row in store.containers():
        state = states.pop(row["name"], None)
        template = ""
        try:
            template = _instance(config, row["name"])[1].get("template", "")
        except DevcError:
            pass
        result.append({
            "name": row["name"],
            "status": state.status if state else "missing",
            "ip": state.ip if state else None,
            "template": template,
            "addons": row["addons"],
            "created_at": row["created_at"],
            "applied_at": row["applied_at"],
            "incomplete": row["applied_at"] is None,
            "installing": _installing(config, row),
        })
    for state in states.values():
        result.append({"name": state.name, "status": "foreign", "ip": state.ip, "template": "", "addons": {},
                       "created_at": state.created, "applied_at": None, "incomplete": False, "installing": False})
    return {"containers": sorted(result, key=lambda item: item["name"])}


def info(config, name: str) -> dict:
    provider = _provider(config)
    store = _store(config)
    row, state = _managed(store, provider, name, allow_missing=True)
    _, data = _instance(config, name)
    cat = catalog(config)
    warnings = []
    for addon_id, version in sorted(row["addons"].items()):
        addon = cat.get(addon_id)
        if addon is not None and addon.version != version:
            warnings.append(Finding("W2", "addons", f"Addon {addon_id}: installiert {version}, verfügbar {addon.version}", "warning"))
    resources = instance_module.resources_of(data, config.defaults)
    return {
        "name": name,
        "status": state.status if state else "missing",
        "ip": state.ip if state else None,
        "platform": row["platform"],
        "image": data.get("image", config.defaults["image"]),
        "template": data.get("template", ""),
        "resources": resources,
        "data": data.get("data", {}),
        "git": (data.get("git") or {}).get("remote", ""),
        "addons": row["addons"],
        "created_at": row["created_at"],
        "applied_at": row["applied_at"],
        "incomplete": row["applied_at"] is None,
        "installing": _installing(config, row),
        "findings": _findings(warnings),
    }


def start(config, name: str) -> dict:
    """lxc start, danach Laufzeitverzeichnisse und Secrets (SPEC 8.4)."""
    provider = _provider(config)
    store = _store(config)
    with locks.container_lock(config.paths, name):
        _, state = _managed(store, provider, name)
        if state.status != "running":
            provider.start(name)
        _runtime(config, provider, None, name)
    return {"name": name, "status": "running"}


def stop(config, name: str) -> dict:
    provider = _provider(config)
    store = _store(config)
    with locks.container_lock(config.paths, name):
        _, state = _managed(store, provider, name)
        if state.status == "running":
            provider.stop(name)
    return {"name": name, "status": "stopped"}


def restart(config, name: str) -> dict:
    stop(config, name)
    return start(config, name)


def rm(config, name: str, keep_data: bool = False, on_log=None) -> dict:
    """Container löschen. Die CLI fragt vorher nach (SPEC 11.4)."""
    provider = _provider(config)
    store = _store(config)

    def work(job):
        provider.delete(name, keep_data)
        job.log("done", f"Container {name} gelöscht" + (", Volumes behalten" if keep_data else " samt Volumes"))
        secrets_module.local_remove_all(config.paths, name)
        for file in (config.paths.instance_file(name), config.paths.applied_file(name)):
            if file.exists():
                file.unlink()
        try:
            config.paths.container_dir(name).rmdir()
        except OSError:
            pass  # fehlt oder enthält fremde Dateien: stehen lassen
        store.container_remove(name)
        return {"name": name, "keep_data": keep_data}

    with locks.container_lock(config.paths, name):
        _managed(store, provider, name, allow_missing=True)
        result, job = _job(config, store, "delete", name, work, on_log)
    result["job"] = job.id
    return result


def shell(config, name: str, root: bool = False) -> None:
    """Ersetzt den Prozess durch eine interaktive Shell im Container."""
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    argv = provider.shell_argv(name, "root" if root else "dev")
    os.execvp(argv[0], argv)


def exec_command(config, name: str, argv, root: bool = False) -> dict:
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    result = provider.exec(name, list(argv), user="root" if root else "dev", cwd="/work")
    return {"rc": result.rc, "stdout": result.stdout, "stderr": result.stderr}


def docs(config, name: str, on_log=None) -> dict:
    """Agent-Doku neu erzeugen."""
    provider = _provider(config)
    store = _store(config)
    _managed(store, provider, name, running=True)
    _, data = _instance(config, name)

    def work(job):
        _write_docs(config, provider, job, name, data, catalog(config))
        return {"name": name}

    result, job = _job(config, store, "apply", name, work, on_log)
    result.update(job=job.id, warnings=job.warnings)
    return result


# --- Konfiguration anwenden (SPEC 8.4 »config apply«) ----------------------


def _applied(config, name: str) -> tuple[str | None, dict]:
    try:
        text = config.paths.applied_file(name).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, {}
    return text, templates_module.parse_toml(text, "L10", "applied.toml")


def config_show(config, name: str) -> dict:
    if _store(config).container(name) is None:
        raise NotFoundError(f"Container fehlt: {name}")
    text, _ = _instance(config, name)
    return {"name": name, "text": text, "applied": text == _applied(config, name)[0]}


def config_put(config, name: str, text: str) -> dict:
    """Speichert eine bearbeitete devc.toml als Text, nachdem die Prüfungen bestanden sind."""
    if _store(config).container(name) is None:
        raise NotFoundError(f"Container fehlt: {name}")
    provided = set(secrets_module.local_slots(config.paths, name))
    checked = validate_instance(config, text, name, provided)
    if has_errors(checked["findings"]):
        raise ValidationError(checked["findings"])
    if checked["data"].get("name") != name:
        raise ValidationError.single("L10", "name", "name darf nicht geändert werden")
    file = config.paths.instance_file(name)
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(file)
    return {"name": name, "findings": _findings(checked["findings"]), "missing_secrets": checked["missing_secrets"]}


def config_discard(config, name: str) -> dict:
    """Verwirft nicht angewendete Änderungen: devc.toml wird wieder der angewendete Stand."""
    if _store(config).container(name) is None:
        raise NotFoundError(f"Container fehlt: {name}")
    text, _ = _applied(config, name)
    if text is None:
        raise ConflictError("Es gibt noch keinen angewendeten Stand")
    config.paths.instance_file(name).write_text(text, encoding="utf-8")
    return {"name": name}


def _apply_plan(config, row, name: str, old: dict, new: dict, cat) -> dict:
    """Ermittelt, was `config apply` tun muss; meldet nicht anwendbare Änderungen (E_VALIDATION)."""
    defaults = config.defaults
    installed = row["addons"]
    order, _ = addons_module.resolve_order(new.get("addons", []), cat)
    if old:
        diff = instance_module.diff(old, new, cat, defaults)
        if diff["data"]:
            raise ValidationError.single(
                "L10", "data", "Der Datenbereich (data.*) lässt sich nicht per config apply ändern; Backup und Restore mit geänderter devc.toml verwenden")
        if diff["image"]:
            raise ValidationError.single("L10", "image", "Das Image lässt sich nicht per config apply ändern; Container neu anlegen")
    else:
        # noch nie vollständig angewendet: an Schritt 7 wieder aufsetzen
        diff = {"params_changed": [], "resources": True, "secrets": True, "git": True, "files": True, "docs": True}
    reasons = {}
    for addon_id in order:
        addon = cat.get(addon_id)
        if addon_id not in installed:
            reasons[addon_id] = "neu"
        elif installed[addon_id] != addon.version:
            reasons[addon_id] = f"Version {installed[addon_id]} → {addon.version}"
        elif addon_id in diff["params_changed"]:
            reasons[addon_id] = "Parameter geändert"
    removed = [a for a in installed if a not in order]
    for addon_id in removed:
        addon = cat.get(addon_id)
        if addon is None or not addon.uninstall:
            raise ValidationError.single(
                "L10", "addons", f"Addon {addon_id} hat kein uninstall; Entfernen geht nur durch Neuanlage des Containers")
    return {
        "install": [{"addon": a, "reason": r} for a, r in reasons.items()],
        "uninstall": removed,
        "resources": bool(diff["resources"]),
        "secrets": bool(diff["secrets"]),
        "git": bool(diff["git"]),
        "files": bool(diff["files"]),
        "docs": bool(diff["docs"]),
    }


def _sync_store_secrets(config, name: str, old: dict, new: dict) -> None:
    """Übernimmt Store-Einträge für Slots, deren Verweis in [secrets] neu ist oder sich geändert hat.

    Ein unveränderter Verweis fasst die Kopie nicht an: ein lokal gesetzter Wert bleibt, und eine
    Store-Änderung wirkt erst nach `secret set <name> <slot>=shared:<store>` (SPEC 11.3).
    """
    before = old.get("secrets") if isinstance(old.get("secrets"), dict) else {}
    after = new.get("secrets") if isinstance(new.get("secrets"), dict) else {}
    filled = set(secrets_module.local_slots(config.paths, name))
    for slot, store in after.items():
        if before.get(slot) != store or slot not in filled:
            if slot in filled and store not in secrets_module.shared_names(config.paths):
                continue  # Verweis geändert, Store-Eintrag fehlt: vorhandenen Wert behalten
            secrets_module.local_set_from_store(config.paths, name, slot, store)


def config_apply(config, name: str, dry_run: bool = False, on_log=None) -> dict:
    provider = _provider(config)
    store = _store(config)
    row, _ = _managed(store, provider, name, allow_missing=dry_run)
    text, new = _instance(config, name)
    provided = set(secrets_module.local_slots(config.paths, name))
    checked = validate_instance(config, text, name, provided)
    if has_errors(checked["findings"]):
        raise ValidationError(checked["findings"])
    cat = catalog(config)
    applied_text, old = _applied(config, name)
    plan = _apply_plan(config, row, name, old, new, cat)
    plan["changed"] = applied_text != text or bool(plan["install"] or plan["uninstall"])
    result = {"name": name, "plan": plan, "findings": _findings(checked["findings"]), "dry_run": dry_run}
    if dry_run:
        return result
    if checked["missing_secrets"]:
        raise MissingSecretError(checked["missing_secrets"])
    resources = instance_module.resources_of(new, config.defaults)
    old_params = instance_module.effective_params(old, cat) if old else {}
    new_params = instance_module.effective_params(new, cat)

    def work(job):
        # Hintergrund-Aktionen eines Addons, das neu eingerichtet wird, liefen sonst mit den alten
        # Einstellungen weiter: vorher stoppen, danach mit den neuen starten. Andere Prozesse im
        # Container (Terminal-Sitzungen, Aktionen anderer Addons) bleiben unberührt.
        restart = []
        for item in plan["install"]:
            addon = cat.get(item["addon"])
            for action in addon.actions if item["reason"] != "neu" else ():
                if action["detach"] and actions_module.running_pid(provider, name, addon.id, action) is not None:
                    actions_module.stop(provider, job, name, addon, action)
                    store.action_run_remove(name, f"{addon.id}.{action['id']}")
                    restart.append((addon, action))
        if plan["resources"]:
            provider.set_limits(name, resources["cpu"], resources["memory"], resources["disk"])
            job.log("create", f"Ressourcen: {resources['cpu']} CPU, {resources['memory']}, {resources['disk']}")
        installed = dict(store.container(name)["addons"])
        for addon_id in plan["uninstall"]:
            addon = cat.get(addon_id)
            job.log("addon", f"{addon_id} wird entfernt")
            _run_scripts(config, provider, job, name, addon, addon.uninstall, old_params.get(addon_id, addon.values()))
            _exec(provider, name, ["rm", "-rf", f"{ADDON_ROOT}/{addon_id}"], f"Addon {addon_id}: Kopie entfernen")
            installed.pop(addon_id, None)
            _record_addons(store, provider, name, installed)
        _sync_store_secrets(config, name, old, new)
        _runtime(config, provider, job, name)
        _provision(config, store, provider, job, name, new, cat, only={i["addon"] for i in plan["install"]}, text=text)
        for addon, action in restart:
            label = f"{addon.id}.{action['id']}"
            try:
                started = actions_module.run(provider, job, name, addon, action, new_params[addon.id], live.ip)
            except DevcError as error:
                job.warn("action", f"{label} ließ sich nicht neu starten: {error.message}")
                continue
            if started.get("pid"):
                store.action_run_set(name, label, _now(), started["pid"])
            job.log("action", f"{label} mit den neuen Einstellungen neu gestartet")
        return result

    with locks.container_lock(config.paths, name):
        _, live = _managed(store, provider, name, running=True)
        done, job = _job(config, store, "apply", name, work, on_log)
    done.update(job=job.id, warnings=job.warnings)
    return done


def params_set(config, name: str, params, on_log=None) -> dict:
    """Setzt Parameter aktiver Addons (`<addon>.<param>=<wert>`) in der devc.toml und wendet an.

    Neu eingerichtet werden nur die Addons, deren Parameter sich geändert haben; ihre laufenden
    Hintergrund-Aktionen starten mit den neuen Werten neu (siehe config_apply).
    """
    params = list(params)
    if not params:
        raise UsageError("Mindestens ein <addon>.<param>=<wert> angeben")
    text, data = _instance(config, name)
    cat = catalog(config)
    active = set(addons_module.active_ids(data.get("addons", [])))
    for spec in params:
        addon_id = instance_module.parse_param(spec, cat)[0]
        if addon_id not in active:
            raise NotFoundError(f"Addon {addon_id} ist in {name} nicht aktiv")
    new_text = instance_module.merge(text, cat, params=params)
    checked = validate_instance(config, new_text, name, set(secrets_module.local_slots(config.paths, name)))
    if has_errors(checked["findings"]):
        raise ValidationError(checked["findings"])
    config_put(config, name, new_text)
    return config_apply(config, name, on_log=on_log)


def install(config, name: str, addons, params=(), on_log=None, secret_values=None) -> dict:
    """Ergänzt Addons in der devc.toml und wendet an.

    `secret_values` (Slot -> Bytes) füllt Secrets der neuen Addons, bevor sie eingerichtet werden.
    """
    text, _ = _instance(config, name)
    cat = catalog(config)
    secret_values = secret_values or {}
    for slot in secret_values:
        # Vor dem Speichern prüfen: Ein falscher Slot soll keine halb geänderte Konfiguration hinterlassen.
        addon_id, _, param_name = slot.partition(".")
        addon = cat.addons.get(addon_id) if addon_id in addons else None
        param = addon.param(param_name) if addon else None
        if param is None or param.type != "secret":
            raise UsageError(f"Kein Secret der neuen Addons: {slot}")
    config_put(config, name, instance_module.merge(text, cat, addons=list(addons), params=list(params)))
    for slot, value in secret_values.items():
        secret_set(config, name=name, target=slot, value=value)
    return config_apply(config, name, on_log=on_log)


def uninstall(config, name: str, addon: str, on_log=None) -> dict:
    """Nimmt ein Addon aus der devc.toml und wendet an."""
    text, _ = _instance(config, name)
    new_text = instance_module.remove_addon(text, addon)
    # erst prüfen, ob das Entfernen überhaupt anwendbar ist, dann speichern
    checked = validate_instance(config, new_text, name, set(secrets_module.local_slots(config.paths, name)))
    if has_errors(checked["findings"]):
        raise ValidationError(checked["findings"])
    row = _store(config).container(name)
    if row is None:
        raise NotFoundError(f"Container fehlt: {name}")
    _apply_plan(config, row, name, _applied(config, name)[1], checked["data"], catalog(config))
    config_put(config, name, new_text)
    return config_apply(config, name, on_log=on_log)


# --- Addon-Aktionen -------------------------------------------------------


def action_list(config, name: str) -> dict:
    """Aktive Addons mit Hilfe (README), Aktionen samt Status und aufgelösten Links."""
    provider = _provider(config)
    store = _store(config)
    _, state = _managed(store, provider, name, allow_missing=True)
    _, data = _instance(config, name)
    cat = catalog(config)
    params = instance_module.effective_params(data, cat)
    order, _ = addons_module.resolve_order(data.get("addons", []), cat)
    running = state is not None and state.status == "running"
    result = []
    for addon_id in order:
        addon = cat.get(addon_id)
        entry = {"id": addon.id, "title": addon.title, "version": addon.version, "description": addon.description,
                 "readme": (addon.path / "README.md").read_text(encoding="utf-8"), "params": []}
        for param in addon.params:
            item = {"name": param.name, "type": param.type, "description": param.description}
            if param.type != "secret":  # Secret-Werte gibt es hier nie; gesetzt werden sie unter Secrets
                item.update(value=params[addon_id].get(param.name), default=param.default if param.has_default else None,
                            options=list(param.options), min=param.min, max=param.max)
                if param.type == "string":
                    item["max_length"] = param.max_length
            item.update(param.view())
            entry["params"].append(item)
        entry.update(actions_module.describe(provider, name, addon, params[addon_id], state.ip if state else None, running, config.server))
        result.append(entry)
    return {"name": name, "running": running, "addons": result}


def action_run(config, name: str, action: str, on_log=None) -> dict:
    provider = _provider(config)
    store = _store(config)
    _, state = _managed(store, provider, name, running=True)
    _, data = _instance(config, name)
    cat = catalog(config)
    addon, spec = actions_module.find(cat, addons_module.active_ids(data.get("addons", [])), action)
    given = instance_module.effective_params(data, cat)[addon.id]
    detached = "stops" in spec or spec["detach"]
    # Doppelstart schon vor dem Job erkennen: die API antwortet dann direkt mit E_CONFLICT.
    if spec["detach"] and actions_module.running_pid(provider, name, addon.id, spec) is not None:
        raise ConflictError(f"Aktion {action} läuft bereits")

    def work(job):
        result = actions_module.run(provider, job, name, addon, spec, given, state.ip)
        if spec["detach"] and result.get("pid"):
            store.action_run_set(name, action, _now(), result["pid"])
        if "stopped" in result:
            store.action_run_remove(name, result["stopped"])
        return result

    if detached:
        with locks.container_lock(config.paths, name):
            result, job = _job(config, store, "action", name, work, on_log)
    else:
        result, job = _job(config, store, "action", name, work, on_log)
    result["job"] = job.id
    return result


def _tile_context(config, name: str, running: bool = False):
    provider = _provider(config)
    _, state = _managed(_store(config), provider, name, running=running, allow_missing=not running)
    _, data = _instance(config, name)
    cat = catalog(config)
    return provider, state, data, cat, instance_module.effective_params(data, cat)


def tile_list(config, name: str) -> dict:
    """Kacheln der aktiven Addons für das Container-Dashboard (SPEC 8.13)."""
    provider, state, data, cat, params = _tile_context(config, name)
    order, _ = addons_module.resolve_order(data.get("addons", []), cat)
    running = state is not None and state.status == "running"
    found = []
    for addon_id in order:
        found += tiles_module.describe(cat.get(addon_id), params[addon_id], name, state.ip if state else None, running, config.server)
    return {"name": name, "running": running, "tiles": found}


def tile_show(config, name: str, tile: str) -> dict:
    """Inhalt einer Kachel der Art `view`: die Ausgabe ihres Befehls im Container."""
    provider, state, data, cat, params = _tile_context(config, name, running=True)
    addon, spec = tiles_module.find(cat, addons_module.active_ids(data.get("addons", [])), tile, "view")
    return {"name": name, **tiles_module.view(provider, name, addon, spec, params[addon.id], state.ip)}


def tile_panel(config, name: str, tile: str, view=None, action=None, args=None, values=None) -> dict:
    """Kachel der Art `panel`: eine Ansicht holen oder eine Aktion ausführen (SPEC 8.13)."""
    asked = panel_module.request(view, action, args, values)
    provider, state, data, cat, params = _tile_context(config, name, running=True)
    addon, spec = tiles_module.find(cat, addons_module.active_ids(data.get("addons", [])), tile, "panel")
    result = {"name": name, **panel_module.call(provider, name, addon, spec, params[addon.id], state.ip, asked)}
    if "action" in asked:
        # Im Log steht nur, welche Aktion lief, nie die Eingaben.
        text = f"Kachel {tile}: Aktion {asked['action']} " + ("ausgeführt" if result["ok"] else "fehlgeschlagen")
        jobs_module.run(config, _store(config), "tile", name, lambda job: job.log("action", text), clock, None, new_job_id())
    return result


def _chat_command(config, name: str, tile: str):
    provider, state, data, cat, params = _tile_context(config, name, running=True)
    addon, spec = tiles_module.find(cat, addons_module.active_ids(data.get("addons", [])), tile, "chat")
    return provider, spec, tiles_module.command(addon, spec, params[addon.id], name, state.ip)


def chat_open(config, name: str, tile: str):
    """Startet die Bridge einer Kachel der Art `chat` im Container; liefert (Prozess, Titel der Kachel)."""
    provider, spec, command = _chat_command(config, name, tile)
    proc = provider.exec_pipe(name, ["bash", "-lc", command], user="dev", cwd="/work")
    chat_event(config, name, f"Unterhaltung {tile} gestartet")
    return proc, spec["title"]


def chat_event(config, name: str, text: str) -> None:
    """Vermerkt Start oder Ende einer Unterhaltung als Job (kind=chat), ohne Inhalte."""
    store = _store(config)
    jobs_module.run(config, store, "chat", name, lambda job: job.log("action", text), clock, None, new_job_id())


def chat_show(config, name: str, tile: str, session: str) -> str:
    """Verlauf einer früheren Unterhaltung als Zeilen der Bridge (`--show <kennung>`)."""
    provider, spec, command = _chat_command(config, name, tile)
    try:
        result = provider.exec(name, ["bash", "-lc", f"{command} --show {shlex.quote(session)}"], user="dev", cwd="/work", timeout=15)
    except EngineError as exc:
        if exc.details.get("timeout"):
            raise JobFailedError(f"Kachel {tile}: Zeitüberschreitung beim Lesen der Unterhaltung") from None
        raise
    if result.rc != 0:
        raise JobFailedError(f"Kachel {tile}: Die Bridge liefert die Unterhaltung nicht (Exit {result.rc})")
    return result.stdout


def chat_stats(config, name: str, tile: str, session: str) -> str:
    """Statistik einer Unterhaltung als Markdown: Die Bridge liefert sie mit `--stats <kennung>` (SPEC 8.13)."""
    provider, spec, command = _chat_command(config, name, tile)
    missing = JobFailedError(f"Kachel {tile}: Die Bridge liefert keine Statistik")
    try:
        result = provider.exec(name, ["bash", "-lc", f"{command} --stats {shlex.quote(session)}"], user="dev", cwd="/work", timeout=30)
    except EngineError as exc:
        if exc.details.get("timeout"):
            raise JobFailedError(f"Kachel {tile}: Zeitüberschreitung beim Lesen der Statistik") from None
        raise
    try:
        text = json.loads(result.stdout).get("stats") if result.rc == 0 else None
    except (ValueError, AttributeError):
        text = None
    if not isinstance(text, str) or not text.strip():
        raise missing
    return text[:64 * 1024]


def chat_history(config, name: str, tile: str) -> dict:
    """Frühere Unterhaltungen einer Kachel der Art `chat`: Die Bridge nennt sie mit `--sessions`."""
    provider, spec, command = _chat_command(config, name, tile)
    try:
        result = provider.exec(name, ["bash", "-lc", command + " --sessions"], user="dev", cwd="/work", timeout=15)
    except EngineError as exc:
        if exc.details.get("timeout"):
            raise JobFailedError(f"Kachel {tile}: Zeitüberschreitung beim Lesen der Unterhaltungen") from None
        raise
    found = []
    try:
        listed = json.loads(result.stdout).get("sessions") if result.rc == 0 else None
    except (ValueError, AttributeError):
        listed = None
    if not isinstance(listed, list):
        raise JobFailedError(f"Kachel {tile}: Die Bridge nennt keine Unterhaltungen (Exit {result.rc})")
    for item in listed[:100]:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            updated = item.get("updated")
            found.append({"id": item["id"], "title": str(item.get("title") or "")[:200],
                          "updated": updated if isinstance(updated, (int, float)) and not isinstance(updated, bool) else None})
    return {"name": name, "tile": tile, "sessions": found}


# --- Eigene Addons und Templates verwalten --------------------------------


def addon_archive(config, id: str) -> tuple[str, bytes]:
    """Dateiname und Inhalt eines Addon-Archivs (tar.gz)."""
    addon = _addon(config, id)
    return f"{addon.id}-{addon.version}.tar.gz", addons_module.pack(addon)


def addon_export(config, id: str, output: str | None = None) -> dict:
    filename, data = addon_archive(config, id)
    target = Path(output) if output else Path(filename)
    if target.is_dir():
        target = target / filename
    target.write_bytes(data)
    return {"id": id, "file": str(target)}


def addon_import_data(config, data: bytes, on_log=None) -> dict:
    """Prüft ein Addon-Archiv (L16, danach L3–L8) und legt es unter den eigenen Addons ab.

    Ein vorhandenes eigenes Addon mit gleicher ID wird ersetzt (SPEC 10).
    """
    store = _store(config)

    def work(job):
        config.paths.addons.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=config.paths.addons))
        try:
            directory = addons_module.unpack(data, staging)
            addon, findings = addons_module.load(directory)
            if addon is None:
                raise ValidationError(findings)
            target = config.paths.addons / addon.id
            replaced = target.exists()
            if replaced:
                target.rename(staging / ".alt")
            directory.rename(target)
            job.log("done", f"Addon {addon.id} {addon.version} " + ("ersetzt" if replaced else "importiert"))
            return {"id": addon.id, "version": addon.version, "replaced": replaced}
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    result, job = _job(config, store, "addon-import", None, work, on_log)
    result["job"] = job.id
    return result


def addon_import(config, archive: str, on_log=None) -> dict:
    try:
        data = Path(archive).read_bytes()
    except FileNotFoundError:
        raise NotFoundError(f"Datei fehlt: {archive}") from None
    return addon_import_data(config, data, on_log)


def addon_rm(config, id: str) -> dict:
    """Entfernt ein eigenes Addon. Mitgelieferte bleiben; benutzte Addons sind gesperrt."""
    finding = names.check("addon", id, "id")
    if finding:
        raise ValidationError([finding])
    target = config.paths.addons / id
    builtin = (addons_module.BUILTIN / "addons" / id).is_dir()
    if not target.is_dir():
        if builtin:
            raise ConflictError(f"Addon {id} ist mitgeliefert und lässt sich nicht entfernen")
        raise NotFoundError(f"Addon fehlt: {id}")
    if not builtin:
        users = [row["name"] for row in _store(config).containers() if id in row["addons"]]
        if users:
            raise ConflictError(f"Addon {id} ist in Benutzung: " + ", ".join(users), {"containers": users})
    shutil.rmtree(target)
    return {"id": id}


_INSTANCE_HEAD_RE = re.compile(r"^\s*(name|template|platform)\s*=")


def template_save(config, container: str, id: str) -> dict:
    """Speichert die Konfiguration eines Containers als eigenes Template."""
    if _store(config).container(container) is None:
        raise NotFoundError(f"Container fehlt: {container}")
    finding = names.check("template", id, "id")
    if finding:
        raise ValidationError([finding])
    text, _ = _instance(config, container)
    kept = []
    in_head = True
    for line in text.splitlines():
        if in_head and re.match(r"^\s*\[", line):
            in_head = False
        if in_head and (_INSTANCE_HEAD_RE.match(line) or line.startswith("# Entwurf aus ")):
            continue
        kept.append(line)
    head = [f'id = "{id}"', f'title = "Vorlage aus {container}"', 'version = "1.0.0"',
            f'description = "Aus dem Container {container} gespeichert"']
    return template_put(config, id, "\n".join(head + kept) + "\n")


def template_rm(config, id: str) -> dict:
    """Entfernt ein eigenes Template; mitgelieferte bleiben."""
    finding = names.check("template", id, "id")
    if finding:
        raise ValidationError([finding])
    target = config.paths.templates / f"{id}.toml"
    if not target.is_file():
        if (templates_module.BUILTIN / f"{id}.toml").is_file():
            raise ConflictError(f"Template {id} ist mitgeliefert und lässt sich nicht entfernen")
        raise NotFoundError(f"Template fehlt: {id}")
    target.unlink()
    return {"id": id}


# --- Snapshots, Backups, Dateien (SPEC 12.1, 11.5) -------------------------

_RESTORE_AREA = 'find "$1" -mindepth 1 -delete && tar -xzf - -C "$1"'


def _stamp() -> str:
    """YYYYMMDD-HHMMSS in Ortszeit."""
    return clock().astimezone().strftime("%Y%m%d-%H%M%S")


def _shared_dir(config, data: dict) -> Path | None:
    """Host-Ordner eines Shared Folders, erneut gegen allowed_roots geprüft (L11)."""
    shared = (data.get("data") or {}).get("shared")
    if not shared:
        return None
    findings = instance_module.check_shared(data, config.allowed_roots)
    if findings:
        raise ValidationError(findings)
    return Path(shared)


def _areas(data: dict) -> list[str]:
    """Datenbereiche, die als LXD-Volume vorliegen."""
    return ["home"] if (data.get("data") or {}).get("shared") else ["work", "home"]


def snapshot_create(config, name: str, label: str | None = None, full: bool = False, on_log=None) -> dict:
    provider = _provider(config)
    store = _store(config)
    snap = label or f"auto-{_stamp()}"
    finding = names.check("snapshot", snap, "label")
    if finding:
        raise ValidationError([finding])

    def work(job):
        for area in _areas(data):
            provider.volume_snapshot(name, area, snap)
        shared = _shared_dir(config, data)
        if shared is not None:
            directory = config.paths.snapshot_dir(name, snap)
            directory.mkdir(parents=True, exist_ok=True)
            backup_module.pack_directory(shared, directory / "work.tar.gz")
            job.log("snapshot", f"Shared Folder {shared} als tar gesichert")
        if full:
            provider.full_snapshot(name, snap)
        store.snapshot_add(name, snap, _now(), "full" if full else "data")
        job.log("snapshot", f"Snapshot {snap} ({'voll' if full else 'Daten'}) erstellt")
        return {"name": name, "snapshot": snap, "scope": "full" if full else "data"}

    with locks.container_lock(config.paths, name):
        _managed(store, provider, name)
        _, data = _instance(config, name)
        if store.snapshot(name, snap) is not None:
            raise ConflictError(f"Snapshot existiert schon: {snap}")
        result, job = _job(config, store, "snapshot", name, work, on_log)
    result["job"] = job.id
    return result


def snapshot_list(config, name: str) -> dict:
    store = _store(config)
    if store.container(name) is None:
        raise NotFoundError(f"Container fehlt: {name}")
    return {"name": name, "snapshots": [
        {"name": row["name"], "created_at": row["created_at"], "scope": row["scope"],
         "auto": names.is_auto_snapshot(row["name"])}
        for row in store.snapshots(name)
    ]}


def _snapshot(store, name: str, snapshot: str) -> dict:
    row = store.snapshot(name, snapshot) if names.is_snapshot(snapshot) else None
    if row is None:
        raise NotFoundError(f"Snapshot fehlt: {snapshot}")
    return row


def snapshot_restore(config, name: str, snapshot: str, on_log=None) -> dict:
    """Setzt die Datenbereiche auf einen Snapshot zurück. Die CLI fragt vorher nach.

    Bei einem Shared Folder wird der Inhalt des Host-Ordners vollständig ersetzt (SPEC 11.5).
    """
    provider = _provider(config)
    store = _store(config)

    def work(job):
        running = state.status == "running"
        if running:
            provider.stop(name)
        try:
            if row["scope"] == "full":
                provider.full_restore(name, snapshot)
            for area in _areas(data):
                provider.volume_restore(name, area, snapshot)
            shared = _shared_dir(config, data)
            if shared is not None:
                archive = config.paths.snapshot_dir(name, snapshot) / "work.tar.gz"
                if not archive.is_file():
                    raise NotFoundError(f"tar-Snapshot fehlt: {archive}")
                backup_module.clear_directory(shared)
                backup_module.unpack_directory(archive, shared)
                job.log("snapshot", f"Shared Folder {shared} aus tar ersetzt")
        finally:
            if running:
                provider.start(name)
                _runtime(config, provider, job, name)
        job.log("snapshot", f"Auf Snapshot {snapshot} zurückgesetzt")
        return {"name": name, "snapshot": snapshot}

    with locks.container_lock(config.paths, name):
        _, state = _managed(store, provider, name)
        row = _snapshot(store, name, snapshot)
        _, data = _instance(config, name)
        result, job = _job(config, store, "snapshot", name, work, on_log)
    result["job"] = job.id
    return result


def snapshot_rm(config, name: str, snapshot: str) -> dict:
    provider = _provider(config)
    store = _store(config)
    with locks.container_lock(config.paths, name):
        _managed(store, provider, name, allow_missing=True)
        row = _snapshot(store, name, snapshot)
        _, data = _instance(config, name)
        for area in _areas(data):
            try:
                provider.volume_snapshot_delete(name, area, snapshot)
            except NotFoundError:
                pass
        if row["scope"] == "full":
            try:
                provider.full_snapshot_delete(name, snapshot)
            except NotFoundError:
                pass
        directory = config.paths.snapshot_dir(name, snapshot)
        if directory.is_dir():
            archive = directory / "work.tar.gz"
            if archive.is_file():
                archive.unlink()
            try:
                directory.rmdir()
            except OSError:
                pass
        store.snapshot_remove(name, snapshot)
    return {"name": name, "snapshot": snapshot}


AREA_LABELS = {"work": "Code (/work)", "home": "Home (/home/dev)"}

# Größe des unkomprimierten tar eines Verzeichnisses, geschätzt wie backup.tar_size: je Eintrag ein Kopf,
# Dateien auf 512 Bytes gerundet. Nur für die Prozentzahl; scheitert es, gibt es eben keine.
_AREA_SIZE = (
    'find "$1" -mindepth 1 -printf "%y %s\\n" | '
    "awk '{ n += 512 } $1 == \"f\" { n += int(($2 + 511) / 512) * 512 } END { printf \"%d\\n\", n + 10240 }'"
)


def _area_size(provider, name: str, area: str) -> int | None:
    result = provider.exec(name, ["sh", "-c", _AREA_SIZE, "sh", backup_module.AREAS[area]], user="root", cwd="/")
    text = result.stdout.strip()
    return int(text) if result.rc == 0 and text.isdigit() and int(text) > 0 else None


def _dump_area(provider, job, name: str, area: str, target: Path) -> None:
    """Sichert einen Datenbereich als tar.gz nach `target` und meldet den Fortschritt.

    Der Container liefert das tar unkomprimiert; gepackt wird hier. So lässt sich der Stand an den
    ungepackten Daten zählen und gegen die vorab geschätzte Größe stellen.
    """
    label = f"{AREA_LABELS[area]} sichern"
    total = _area_size(provider, name, area)
    job.progress(label, 0, total)
    failure = []
    read_fd, write_fd = os.pipe()
    source, sink = os.fdopen(read_fd, "rb"), os.fdopen(write_fd, "wb")

    def pack():
        done = 0
        try:
            with open(target, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=6, mtime=0) as packed:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    packed.write(chunk)
                    done += len(chunk)
                    job.progress(label, done, total)
        except BaseException as error:  # noqa: BLE001 – wird unten im Job gemeldet
            failure.append(error)
            for _ in iter(lambda: source.read(1024 * 1024), b""):
                pass  # weiterlesen, sonst bliebe tar im Container an der vollen Leitung hängen

    worker = threading.Thread(target=pack, name=f"devc-backup-{area}", daemon=True)
    worker.start()
    try:
        result = provider.exec(name, ["tar", "-C", backup_module.AREAS[area], "-cf", "-", "."], user="root", cwd="/", stdout=sink)
    finally:
        sink.close()
        worker.join()
        source.close()
        job.progress_end()
    if result.rc != 0:
        raise JobFailedError(f"Sichern von {backup_module.AREAS[area]} fehlgeschlagen (Exit {result.rc}): {_last_line(result.stderr)}")
    if failure:
        if isinstance(failure[0], OSError):
            raise JobFailedError(f"Sichern von {backup_module.AREAS[area]} fehlgeschlagen: Schreiben nach {target.parent} scheiterte ({failure[0].strerror})")
        raise failure[0]


def backup(config, name: str, output: str | None = None, label: str | None = None,
           no_home: bool = False, no_work: bool = False, on_log=None) -> dict:
    """Portables Archiv eines Containers (SPEC 12.1). Secret-Werte sind nie enthalten."""
    provider = _provider(config)
    store = _store(config)
    if label is not None:
        finding = names.check("snapshot", label, "label")
        if finding:
            raise ValidationError([finding])
    includes = [area for area, skip in (("work", no_work), ("home", no_home)) if not skip]
    directory = Path(output) if output else config.paths.backups
    if output and not directory.is_dir():
        raise NotFoundError(f"Verzeichnis fehlt: {output}")

    def work(job):
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / backup_module.file_name(name, _stamp(), label)
        if target.exists():
            raise ConflictError(f"Backup existiert schon: {target.name}")
        running = state.status == "running"
        staging = Path(tempfile.mkdtemp(prefix=".devc-backup-", dir=directory))
        try:
            (staging / "data").mkdir()
            if not running:
                provider.start(name)
                job.log("backup", "Container für das Backup gestartet")
            try:
                shared = _shared_dir(config, data)
                for area in includes:
                    file = staging / backup_module.DATA[area]
                    if area == "work" and shared is not None:
                        step = f"{AREA_LABELS[area]} sichern"
                        try:
                            backup_module.pack_directory(shared, file, lambda done, total: job.progress(step, done, total))
                        finally:
                            job.progress_end()
                    else:
                        _dump_area(provider, job, name, area, file)
                    job.log("backup", f"{backup_module.AREAS[area]} gesichert ({file.stat().st_size} Bytes)")
            finally:
                if not running:
                    provider.stop(name)
            manifest = backup_module.manifest_text(name, data.get("template", ""), includes, _now())
            backup_module.build(target, staging, manifest, text, includes)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        job.log("backup", f"Backup geschrieben: {target}")
        return {"name": name, "file": str(target), "includes": includes, "size": target.stat().st_size}

    with locks.container_lock(config.paths, name):
        _, state = _managed(store, provider, name)
        text, data = _instance(config, name)
        result, job = _job(config, store, "backup", name, work, on_log)
    result["job"] = job.id
    return result


def backup_file(config, filename: str) -> Path:
    """Löst einen Backup-Dateinamen im Backup-Verzeichnis auf."""
    if not names.is_backup_file(filename):
        raise NotFoundError(f"Backup fehlt: {filename}")
    path = config.paths.backups / filename
    if not path.is_file():
        raise NotFoundError(f"Backup fehlt: {filename}")
    return path


def backup_list(config, name: str | None = None) -> dict:
    result = []
    if config.paths.backups.is_dir():
        for file in sorted(config.paths.backups.iterdir()):
            match = names.BACKUP_FILE_RE.fullmatch(file.name)
            if not match or not file.is_file() or (name is not None and match.group(1) != name):
                continue
            # Der Containername kann selbst Bindestriche tragen; maßgeblich ist das Manifest erst beim Restore.
            result.append({"file": file.name, "container": match.group(1), "stamp": match.group(2),
                           "label": match.group(3) or "", "size": file.stat().st_size})
    return {"backups": result}


def backup_rm(config, filename: str) -> dict:
    backup_file(config, filename).unlink()
    return {"file": filename}


def restore_inspect(config, archive: str) -> dict:
    path = Path(archive)
    if not path.is_file():
        raise NotFoundError(f"Datei fehlt: {archive}")
    return backup_module.inspect(path)


def _feed_area(provider, job, name: str, area: str, archive: Path):
    """Schickt data/<area>.tar.gz aus dem Backup an tar im Container und meldet den Fortschritt."""
    label = f"{AREA_LABELS[area]} wiederherstellen"
    total = backup_module.area_size(archive, area)
    job.progress(label, 0, total, exact=True)
    read_fd, write_fd = os.pipe()
    source, sink = os.fdopen(read_fd, "rb"), os.fdopen(write_fd, "wb")

    def send():
        try:
            backup_module.extract_area(archive, area, sink, lambda done: job.progress(label, done, total, exact=True))
        except (OSError, ValueError):
            pass  # tar im Container hat aufgehört zu lesen; den Grund nennt sein Exit-Code
        finally:
            try:
                sink.close()
            except OSError:
                pass

    worker = threading.Thread(target=send, name=f"devc-restore-{area}", daemon=True)
    worker.start()
    try:
        return provider.exec(name, ["sh", "-c", _RESTORE_AREA, "sh", backup_module.AREAS[area]], user="root", cwd="/", stdin=source)
    finally:
        source.close()
        worker.join()
        job.progress_end()


def restore(config, archive: str, new_name: str | None = None, replace: str | None = None,
            text: str | None = None, secret_values=None, ask_secret=None, on_log=None) -> dict:
    """Stellt ein Backup als neuen Container her oder ersetzt einen vorhandenen.

    Genau eins von `new_name` und `replace`. `text` ist eine bearbeitete devc.toml.
    Bei `replace` fragt die CLI vorher nach; die Daten des Containers werden ersetzt.
    """
    if (new_name is None) == (replace is None):
        raise UsageError("Genau eins von --name und --replace angeben")
    path = Path(archive)
    if not path.is_file():
        raise NotFoundError(f"Datei fehlt: {archive}")
    inspected = backup_module.inspect(path)
    name = new_name or replace
    if text is None:
        text = backup_module.set_name(inspected["devc_toml"], name)

    def data_restore(provider, job):
        for area in inspected["includes"]:
            job.log("restore", f"{backup_module.AREAS[area]} wird aus dem Backup ersetzt")
            result = _feed_area(provider, job, name, area, path)
            if result.rc != 0:
                raise JobFailedError(f"Restore von {backup_module.AREAS[area]} fehlgeschlagen (Exit {result.rc}): {_last_line(result.stderr)}")

    options = dict(text=text, secret_values=secret_values, ask_secret=ask_secret, on_log=on_log,
                   _kind="restore", _before_provision=data_restore)
    if new_name is not None:
        result = create(config, name, **options)
    else:
        provider = _provider(config)
        store = _store(config)
        # erst prüfen, dann löschen: scheitert die Prüfung, bleibt der Container, wie er ist
        checked = validate_instance(config, text, name, set(secrets_module.local_slots(config.paths, name)) | set(secret_values or {}))
        if has_errors(checked["findings"]):
            raise ValidationError(checked["findings"])
        with locks.container_lock(config.paths, name):
            _managed(store, provider, name, allow_missing=True)
            # Nur den Container entfernen; die Volumes bleiben und werden wiederverwendet. Bereiche,
            # die das Backup enthält, werden danach geleert und neu gefüllt.
            provider.delete(name, keep_data=True)
            store.container_remove(name)
            result = create(config, name, _locked=True, **options)
    result.update(archive=str(path), includes=inspected["includes"], replaced=replace is not None)
    return result


def _work_path(provider, name: str, relative: str) -> str:
    """L13: Pfad relativ zu /work, im Container aufgelöst; außerhalb von /work → E_SECURITY."""
    candidate = relative if relative.startswith("/") else f"/work/{relative}"
    result = provider.exec(name, ["realpath", "-m", candidate], user="dev", cwd="/work")
    resolved = result.stdout.strip()
    if result.rc != 0 or not (resolved == "/work" or resolved.startswith("/work/")):
        raise SecurityError(f"Pfad liegt nicht unter /work: {relative}", {"check": "L13", "path": relative})
    return resolved


def push(config, name: str, local: str, dest: str | None = None) -> dict:
    """Kopiert eine lokale Datei in den Container (Ziel relativ zu /work)."""
    source = Path(local)
    if not source.is_file():
        raise NotFoundError(f"Datei fehlt: {local}")
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    target = _work_path(provider, name, dest or source.name)
    if target == "/work" or (dest or "").endswith("/"):
        target = _work_path(provider, name, f"{target}/{source.name}")
    mode = f"0{source.stat().st_mode & 0o777:03o}"
    provider.push(name, source, target, mode=mode, uid=1000, gid=1000)
    return {"name": name, "dest": target}


def pull(config, name: str, src: str, local: str | None = None) -> dict:
    """Holt eine Datei aus dem Container; ein Verzeichnis kommt als tar.gz."""
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    source = _work_path(provider, name, src)
    kind = provider.exec(name, ["sh", "-c", 'if [ -d "$1" ]; then echo d; elif [ -f "$1" ]; then echo f; else exit 44; fi', "sh", source],
                         user="dev", cwd="/work")
    if kind.rc != 0:
        raise NotFoundError(f"Datei fehlt im Container: {source}")
    base = source.rsplit("/", 1)[1] or "work"
    if kind.stdout.strip() == "d":
        target = Path(local) if local else Path(f"{base}.tar.gz")
        if target.is_dir():
            target = target / f"{base}.tar.gz"
        with open(target, "wb") as handle:
            result = provider.exec(name, ["tar", "-C", source, "-czf", "-", "."], user="dev", cwd="/work", stdout=handle)
        if result.rc != 0:
            raise JobFailedError(f"Verzeichnis {source} ließ sich nicht packen (Exit {result.rc})")
        return {"name": name, "src": source, "file": str(target), "kind": "directory"}
    target = Path(local) if local else Path(base)
    if target.is_dir():
        target = target / base
    provider.pull(name, source, target)
    return {"name": name, "src": source, "file": str(target), "kind": "file"}


# --- Jobs und Dateien für API und GUI -------------------------------------


def jobs(config, container: str | None = None) -> dict:
    """Jobs, neueste zuerst. Ein Job im Zustand `running` trägt `active`: Hält noch jemand den Lock seines Containers?

    Ohne Lock ist der Job liegen geblieben (devc wurde währenddessen beendet) und läuft nicht mehr.
    """
    rows = _store(config).jobs(container)
    for row in rows:
        if row["state"] == "running":
            row["active"] = bool(row["container"]) and locks.is_locked(config.paths, row["container"])
    return {"jobs": rows}


def job_get(config, job_id: str) -> dict:
    row = _store(config).job(job_id) if names.is_job(job_id) else None
    if row is None:
        raise NotFoundError(f"Job fehlt: {job_id}")
    try:
        row["log"] = config.paths.job_log(job_id).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        row["log"] = ""
    return row


def job_events(config, job_id: str):
    """Generator über (ereignis, daten) eines Jobs, siehe jobs.events."""
    store = _store(config)
    if not names.is_job(job_id) or store.job(job_id) is None:
        raise NotFoundError(f"Job fehlt: {job_id}")
    return jobs_module.events(config, store, job_id, sleep)


def terminal_open(config, name: str, user: str = "dev"):
    """Startet `bash -l` an einem PTY im Container; liefert (provider, pid, master_fd)."""
    if user not in ("dev", "root"):
        raise UsageError("user muss dev oder root sein")
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    pid, master = provider.exec_pty(name, ["bash", "-l"], user=user, cwd="/work")
    terminal_event(config, name, f"Terminal-Sitzung als {user} gestartet")
    return provider, pid, master


def terminal_event(config, name: str, text: str) -> None:
    """Vermerkt Start oder Ende einer Terminal-Sitzung als Job (kind=terminal), ohne Inhalte."""
    store = _store(config)
    jobs_module.run(config, store, "terminal", name, lambda job: job.log("action", text), clock, None, new_job_id())


# --- VM: beschränktes Terminal und Update von devc (SPEC 8.14) -----------------


def vm_info(config) -> dict:
    return vm_module.info()


def vm_event(config, text: str, kind: str = "terminal") -> None:
    """Vermerkt eine Sitzung im VM-Terminal oder ein Update als Job ohne Container, ohne Inhalte."""
    jobs_module.run(config, _store(config), kind, None, lambda job: job.log("action", text), clock, None, new_job_id())


def vm_terminal_open(config):
    """Startet die beschränkte Shell der VM an einem PTY; liefert (shells, pid, master_fd)."""
    pid, master = vm_module.shells.exec_pty()
    vm_event(config, f"VM-Terminal als {vm_module.user()} gestartet")
    return vm_module.shells, pid, master


def vm_shell(config) -> None:
    """Ersetzt den Prozess durch die beschränkte Shell der VM."""
    vm_module.shell()


def vm_update(config, file: str) -> dict:
    """Richtet ein neues devc.pyz ein. Der Dienst startet danach neu; Sitzungen und Unterhaltungen enden."""
    path = Path(file).expanduser()
    if not path.is_file():
        raise UsageError(f"Datei fehlt: {file}")
    result = vm_module.update(path)
    vm_event(config, f"Update von devc gestartet: {result['from']['version']} ({result['from']['build'] or 'Quelltext'}) "
                     f"→ {result['to']['version']} ({result['to']['build']})", "update")
    return result


def files_list(config, name: str, path: str = "/work") -> dict:
    """Verzeichnisliste unter /work (L13)."""
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    resolved = _work_path(provider, name, path)
    result = provider.exec(
        name, ["find", resolved, "-mindepth", "1", "-maxdepth", "1", "-printf", "%y\\t%s\\t%T@\\t%f\\n"],
        user="dev", cwd="/work",
    )
    if result.rc != 0:
        raise NotFoundError(f"Verzeichnis fehlt im Container: {resolved}")
    entries = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        kind = {"d": "dir", "f": "file", "l": "link"}.get(parts[0], "other")
        entries.append({"name": parts[3], "type": kind, "size": int(parts[1]) if parts[1].isdigit() else 0,
                        "modified": int(float(parts[2])) if parts[2].replace(".", "", 1).isdigit() else 0})
    entries.sort(key=lambda entry: (entry["type"] != "dir", entry["name"].lower()))
    return {"name": name, "path": resolved, "entries": entries}


def file_read(config, name: str, path: str, target) -> dict:
    """Schreibt eine Datei (roh) oder ein Verzeichnis (tar.gz) aus /work in das Dateiobjekt `target`."""
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    resolved = _work_path(provider, name, path)
    kind = provider.exec(name, ["sh", "-c", 'if [ -d "$1" ]; then echo d; elif [ -f "$1" ]; then echo f; else exit 44; fi', "sh", resolved],
                         user="dev", cwd="/work")
    if kind.rc != 0:
        raise NotFoundError(f"Datei fehlt im Container: {resolved}")
    base = resolved.rsplit("/", 1)[1] or "work"
    if kind.stdout.strip() == "d":
        result = provider.exec(name, ["tar", "-C", resolved, "-czf", "-", "."], user="dev", cwd="/work", stdout=target)
        filename, content_type = f"{base}.tar.gz", "application/gzip"
    else:
        result = provider.exec(name, ["cat", resolved], user="dev", cwd="/work", stdout=target)
        filename, content_type = base, "application/octet-stream"
    if result.rc != 0:
        raise JobFailedError(f"Lesen von {resolved} fehlgeschlagen (Exit {result.rc})")
    return {"filename": filename, "content_type": content_type}


def file_write(config, name: str, path: str, source) -> dict:
    """Schreibt Rohdaten aus dem Dateiobjekt `source` in eine Datei unter /work (L13)."""
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    resolved = _work_path(provider, name, path)
    if resolved == "/work":
        raise UsageError("Ziel muss eine Datei unter /work sein")
    result = provider.exec(name, ["sh", "-c", 'mkdir -p "$(dirname "$1")" && cat > "$1"', "sh", resolved],
                           user="dev", cwd="/work", stdin=source)
    if result.rc != 0:
        raise JobFailedError(f"Schreiben von {resolved} fehlgeschlagen (Exit {result.rc}): {_last_line(result.stderr)}")
    return {"name": name, "path": resolved}


# Entpacken läuft über devc: Es liest das Archiv selbst, lässt unsichere Einträge weg und schickt
# dem Container einen bereinigten tar-Strom. So braucht der Container kein `unzip`.
ARCHIVE_MAX_BYTES = 1024 ** 3   # entpackte Größe und Größe der Archivdatei
ARCHIVE_MAX_ENTRIES = 100_000
ARCHIVE_PREVIEW = 500
_CAT_FILE = 'if [ ! -f "$1" ]; then exit 44; fi; if [ "$(stat -c %s "$1")" -gt "$2" ]; then exit 45; fi; cat "$1"'
_UNPACK = 'mkdir -p "$1" && tar -x -C "$1" --no-same-owner -f -'
_RM_FILE = 'if [ -e "$1" ] || [ -L "$1" ]; then rm -rf -- "$1"; else exit 44; fi'


def file_rm(config, name: str, path: str) -> dict:
    """Löscht eine Datei oder einen Ordner samt Inhalt unter /work (L13). /work selbst bleibt."""
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    resolved = _work_path(provider, name, path)
    if resolved == "/work":
        raise UsageError("/work selbst lässt sich nicht löschen")
    result = provider.exec(name, ["sh", "-c", _RM_FILE, "sh", resolved], user="dev", cwd="/work")
    if result.rc == 44:
        raise NotFoundError(f"Datei fehlt im Container: {resolved}")
    if result.rc != 0:
        raise JobFailedError(f"Löschen von {resolved} fehlgeschlagen (Exit {result.rc}): {_last_line(result.stderr)}")
    return {"name": name, "path": resolved}


def _archive_members(handle) -> tuple[str, list[dict], list[str]]:
    """Liest ein tar- oder zip-Archiv: (Format, Einträge, übersprungene Namen).

    Übersprungen wird, was aus dem Zielordner herausführen könnte (absolute Pfade, `..`,
    Links nach außen) und was keine Datei, kein Ordner und kein symbolischer Link ist.
    """
    entries: list[dict] = []
    skipped: list[str] = []
    total = 0

    def add(raw: str, kind: str | None, size: int, mode: int, mtime: float, link: str, opener) -> None:
        nonlocal total
        clean = posixpath.normpath(raw)
        if clean in (".", ""):
            return
        outside = lambda value: value == ".." or value.startswith("../")
        if kind is None or raw.startswith("/") or outside(clean):
            skipped.append(raw)
            return
        if kind == "link" and (not link or link.startswith("/") or outside(posixpath.normpath(posixpath.join(posixpath.dirname(clean), link)))):
            skipped.append(raw)
            return
        total += size
        if len(entries) >= ARCHIVE_MAX_ENTRIES:
            raise UsageError(f"Archiv hat mehr als {ARCHIVE_MAX_ENTRIES} Einträge")
        if total > ARCHIVE_MAX_BYTES:
            raise UsageError(f"Archiv ist entpackt größer als {ARCHIVE_MAX_BYTES // 1024 ** 2} MiB")
        entries.append({"name": clean, "type": kind, "size": size, "mode": mode, "mtime": mtime, "link": link, "open": opener})

    now = clock().timestamp()
    handle.seek(0)
    try:
        if zipfile.is_zipfile(handle):
            handle.seek(0)
            archive = zipfile.ZipFile(handle)
            for info in archive.infolist():
                if info.flag_bits & 0x1:
                    raise UsageError("Archiv ist verschlüsselt")
                unix = info.external_attr >> 16
                try:
                    mtime = time.mktime(info.date_time + (0, 0, -1))
                except (ValueError, OverflowError):
                    mtime = now
                if info.is_dir():
                    add(info.filename, "dir", 0, 0o755, mtime, "", None)
                elif stat.S_ISLNK(unix):
                    add(info.filename, "link", 0, 0o777, mtime, archive.read(info).decode("utf-8", "replace"), None)
                else:
                    add(info.filename, "file", info.file_size, (unix & 0o777) or 0o644, mtime, "", lambda info=info: archive.open(info))
            return "zip", entries, skipped
        handle.seek(0)
        archive = tarfile.open(fileobj=handle, mode="r:*")
        for member in archive:
            kind = "dir" if member.isdir() else "file" if member.isfile() else "link" if member.issym() else None
            add(member.name, kind, member.size if kind == "file" else 0, (member.mode & 0o777) or (0o755 if kind == "dir" else 0o644),
                member.mtime, member.linkname if kind == "link" else "", lambda member=member: archive.extractfile(member))
        return "tar", entries, skipped
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as error:
        raise UsageError("Datei ist kein lesbares tar- oder zip-Archiv") from error


def _fetch_archive(provider, name: str, resolved: str):
    """Holt die Archivdatei aus dem Container in eine temporäre Datei auf dem Host."""
    handle = tempfile.TemporaryFile()
    try:
        result = provider.exec(name, ["sh", "-c", _CAT_FILE, "sh", resolved, str(ARCHIVE_MAX_BYTES)], user="dev", cwd="/work", stdout=handle)
        if result.rc == 44:
            raise NotFoundError(f"Datei fehlt im Container: {resolved}")
        if result.rc == 45:
            raise UsageError(f"Archivdatei ist größer als {ARCHIVE_MAX_BYTES // 1024 ** 2} MiB")
        if result.rc != 0:
            raise JobFailedError(f"Lesen von {resolved} fehlgeschlagen (Exit {result.rc})")
    except BaseException:
        handle.close()
        raise
    return handle


def archive_list(config, name: str, path: str) -> dict:
    """Vorschau: was ein tar- oder zip-Archiv unter /work enthält und was beim Entpacken wegfiele."""
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    resolved = _work_path(provider, name, path)
    with _fetch_archive(provider, name, resolved) as handle:
        kind, entries, skipped = _archive_members(handle)
    return {
        "name": name, "path": resolved, "format": kind, "count": len(entries), "size": sum(e["size"] for e in entries),
        "top": sorted({e["name"].split("/", 1)[0] for e in entries}),
        "entries": [{"name": e["name"], "type": e["type"], "size": e["size"]} for e in entries[:ARCHIVE_PREVIEW]],
        "truncated": len(entries) > ARCHIVE_PREVIEW, "skipped": skipped,
    }


def archive_unpack(config, name: str, path: str, into: str | None = None) -> dict:
    """Entpackt ein tar- oder zip-Archiv unter /work nach `into` (Standard: der Ordner des Archivs)."""
    provider = _provider(config)
    _managed(_store(config), provider, name, running=True)
    resolved = _work_path(provider, name, path)
    target = _work_path(provider, name, into) if into else (resolved.rsplit("/", 1)[0] or "/work")
    counts = {"file": 0, "dir": 0, "link": 0}
    with _fetch_archive(provider, name, resolved) as handle, tempfile.TemporaryFile() as stream:
        _, entries, skipped = _archive_members(handle)
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as out:
            for entry in entries:
                info = tarfile.TarInfo(entry["name"])
                info.mode, info.mtime = entry["mode"], entry["mtime"]
                counts[entry["type"]] += 1
                if entry["type"] == "dir":
                    info.type = tarfile.DIRTYPE
                    out.addfile(info)
                elif entry["type"] == "link":
                    info.type, info.linkname = tarfile.SYMTYPE, entry["link"]
                    out.addfile(info)
                else:
                    info.size = entry["size"]
                    try:
                        with entry["open"]() as source:
                            out.addfile(info, source)
                    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as error:
                        raise UsageError(f"Archiv ist beschädigt: {entry['name']}") from error
        stream.seek(0)
        result = provider.exec(name, ["sh", "-c", _UNPACK, "sh", target], user="dev", cwd="/work", stdin=stream)
    if result.rc != 0:
        raise JobFailedError(f"Entpacken nach {target} fehlgeschlagen (Exit {result.rc}): {_last_line(result.stderr)}")
    return {"name": name, "path": resolved, "into": target, "files": counts["file"], "dirs": counts["dir"], "links": counts["link"], "skipped": skipped}


# --- Secrets --------------------------------------------------------------


def secret_set(config, shared: bool = False, name: str | None = None, target: str = "",
               value: bytes | None = None, from_store: str | None = None) -> dict:
    """Setzt einen Store-Eintrag (`shared`) oder den Slot eines Containers.

    Genau eins von `value` und `from_store`; im geteilten Store nur `value`.
    """
    if (value is None) == (from_store is None):
        raise UsageError("Genau eine Quelle für das Secret angeben")
    if shared:
        if from_store is not None:
            raise UsageError("Ein Store-Eintrag kann nicht auf den Store verweisen")
        secrets_module.shared_set(config.paths, target, value)
        return {"store": target}
    provider = _provider(config)
    store = _store(config)
    _, state = _managed(store, provider, name, allow_missing=True)
    _, data = _instance(config, name)
    finding = names.check("slot", target, "slot")
    if finding:
        raise ValidationError([finding])
    slots = instance_module.secret_slots(data, catalog(config))
    if target not in slots and target != names.GIT_KEY_SLOT:
        raise NotFoundError(f"Kein Secret-Slot dieses Containers: {target}")
    if from_store is not None:
        secrets_module.local_set_from_store(config.paths, name, target, from_store)
    else:
        secrets_module.local_set(config.paths, name, target, value, "local")
    pushed = False
    if state is not None and state.status == "running":
        secrets_module.prepare_runtime(provider, name)
        secrets_module.push(provider, name, target, secrets_module.local_get(config.paths, name, target))
        pushed = True
        _secret_info(config, provider, name, data, target)
    return {"container": name, "slot": target, "pushed": pushed}


def secret_list(config, shared: bool = False, name: str | None = None) -> dict:
    """Namen, Quelle und Änderungsdatum – nie Werte."""
    if shared:
        return {"secrets": secrets_module.shared_list(config.paths)}
    if _store(config).container(name) is None:
        raise NotFoundError(f"Container fehlt: {name}")
    _, data = _instance(config, name)
    stored = {entry["slot"]: entry for entry in secrets_module.local_list(config.paths, name)}
    result = []
    slots = instance_module.secret_slots(data, catalog(config))
    for slot in sorted(set(slots) | set(stored)):
        entry = stored.get(slot)
        result.append({
            "slot": slot,
            "source": entry["source"] if entry else "none",
            "modified": entry["modified"] if entry else None,
            "required": slots.get(slot, {}).get("required", False),
        })
    return {"container": name, "secrets": result}


def secret_rm(config, shared: bool = False, name: str | None = None, target: str = "") -> dict:
    if shared:
        secrets_module.shared_remove(config.paths, target)
        return {"store": target}
    provider = _provider(config)
    _, state = _managed(_store(config), provider, name, allow_missing=True)
    if not secrets_module.local_remove(config.paths, name, target):
        raise NotFoundError(f"Secret-Slot ist nicht befüllt: {target}")
    if state is not None and state.status == "running":
        secrets_module.remove_from_container(provider, name, target)
        _secret_info(config, provider, name, _instance(config, name)[1], target)
    return {"container": name, "slot": target}
