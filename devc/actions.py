"""Addon-Aktionen und Links (SPEC 8.4 »action run«, 7.3)."""

import shlex

from . import addons as addons_module
from . import placeholders, security
from .errors import ConflictError, EngineError, JobFailedError, NotFoundError

ACTIONS_DIR = "/run/devc/actions"
WORKDIR = "/work"

_STATUS = (
    'p=$(cat "$1" 2>/dev/null) || exit 3; case "$p" in ""|*[!0-9]*) exit 3;; esac; '
    'kill -0 "$p" 2>/dev/null || exit 3; echo "$p"'
)
# TERM an die Prozessgruppe, 5 s warten, dann KILL; Exit 3 = lief nicht
_STOP = (
    'p=$(cat "$1" 2>/dev/null) || exit 3; case "$p" in ""|*[!0-9]*) rm -f "$1"; exit 3;; esac; '
    'kill -0 "$p" 2>/dev/null || { rm -f "$1"; exit 3; }; '
    'kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null; '
    'i=0; while [ "$i" -lt 50 ] && kill -0 "$p" 2>/dev/null; do sleep 0.1; i=$((i+1)); done; '
    'if kill -0 "$p" 2>/dev/null; then kill -KILL -- "-$p" 2>/dev/null || kill -KILL "$p" 2>/dev/null; fi; '
    'rm -f "$1"; exit 0'
)


def pid_file(addon_id: str, action_id: str) -> str:
    return f"{ACTIONS_DIR}/{addon_id}-{action_id}.pid"


def log_file(addon_id: str, action_id: str) -> str:
    return f"{ACTIONS_DIR}/{addon_id}-{action_id}.log"


def values_for(addon, given: dict, container: str, ip: str | None) -> dict:
    values = addon.values(given)
    values.update({"container": container, "container_ip": ip, "workdir": WORKDIR, "user": "dev"})
    return values


def _render(template: str, values: dict, mode: str) -> str:
    try:
        used = placeholders.names_in(template)
    except placeholders.PlaceholderError as exc:
        raise JobFailedError(f"Vorlage ist ungültig: {exc}") from None
    if "container_ip" in used and not values.get("container_ip"):
        raise ConflictError("Container läuft nicht")
    try:
        return placeholders.render(template, values, mode)
    except placeholders.PlaceholderError as exc:
        raise ConflictError(str(exc)) from None


def running_pid(provider, container: str, addon_id: str, action: dict) -> int | None:
    """PID einer detach-Aktion, wenn die PID-Datei existiert und der Prozess lebt."""
    result = provider.exec(
        container, ["sh", "-c", _STATUS, "sh", pid_file(addon_id, action["id"])], user=action["user"], cwd="/"
    )
    if result.rc != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def status(provider, container: str, addon_id: str, action: dict) -> str:
    return "running" if running_pid(provider, container, addon_id, action) is not None else "stopped"


def _port_link(link: dict, values: dict, container: str, server: dict | None, port=None) -> dict:
    """Link mit `port`: hinter einem Proxy dessen Adresse (SPEC 8.11), sonst die des Containers."""
    if port is None:
        port = link["port"] if isinstance(link["port"], int) else values.get(link["port"][1:-1])
    low, high = addons_module.LINK_PORTS
    url = None
    try:
        path = _render(link["path"], values, "url")
    except ConflictError:
        path = None
    if isinstance(port, int) and not isinstance(port, bool) and path is not None:
        if server and server["base_domain"]:
            if low <= port <= high:
                url = security.public_url(server, f"{port}-{container}", path)
        elif values.get("container_ip"):
            url = f"http://{values['container_ip']}:{port}{path}"
    return {"url": url, "port": port, "path": path, **({"public": True} if link.get("public") else {})}


def public_ports(addon, given: dict) -> set[int]:
    """Ports, die ein Addon über Links mit `public = true` ohne Anmeldung freigibt (SPEC 8.11)."""
    values = addon.values(given)
    ports = set()
    for link in addon.links:
        if not link.get("public"):
            continue
        if "ports" in link:
            try:
                ports.update(port for port, _ in addons_module.parse_ports(values.get(link["ports"][1:-1])))
            except ValueError:
                pass
        else:
            port = link["port"] if isinstance(link["port"], int) else values.get(link["port"][1:-1])
            if isinstance(port, int) and not isinstance(port, bool):
                ports.add(port)
    return ports


def describe(provider, container: str, addon, given: dict, ip: str | None, running: bool, server: dict | None = None) -> dict:
    """Aktionen mit Status und Links mit aufgelöster URL für ein aktives Addon."""
    values = values_for(addon, given, container, ip)
    states = {}
    actions = []
    for action in addon.actions:
        entry = {"id": action["id"], "label": action["label"], "description": action["description"],
                 "detach": action["detach"], "kind": "stops" if "stops" in action else "run"}
        if action["detach"]:
            states[action["id"]] = status(provider, container, addon.id, action) if running else "stopped"
            entry["status"] = states[action["id"]]
        actions.append(entry)
    links = []
    for link in addon.links:
        if "ports" in link:
            # ein Link je Eintrag des ports-Parameters (SPEC 7.8)
            try:
                entries = addons_module.parse_ports(values.get(link["ports"][1:-1]))
            except ValueError:
                entries = []
            for port, label in entries:
                extra = _port_link(link, values, container, server, port)
                url = extra.pop("url")
                active = running and url is not None and (not link["when"] or states.get(link["when"]) == "running")
                links.append({"label": label or f"{link['label']} {port}", "url": url, "active": active, **extra})
            continue
        extra = {}
        if "port" in link:
            extra = _port_link(link, values, container, server)
            url = extra.pop("url")
        else:
            try:
                url = _render(link["url"], values, "url")
            except ConflictError:
                url = None
        active = running and url is not None and (not link["when"] or states.get(link["when"]) == "running")
        links.append({"label": link["label"], "url": url, "active": active, **extra})
    return {"actions": actions, "links": links}


def stop(provider, job, container: str, addon, target: dict, name: str | None = None) -> bool:
    """Beendet die Hintergrund-Aktion `target`; False, wenn sie nicht lief."""
    name = name or f"{addon.id}.{target['id']}"
    result = provider.exec(
        container, ["sh", "-c", _STOP, "sh", pid_file(addon.id, target["id"])], user=target["user"], cwd="/"
    )
    if result.rc == 3:
        job.log("action", f"{addon.id}.{target['id']} lief nicht")
        return False
    if result.rc != 0:
        raise JobFailedError(f"Aktion {name}: Stoppen fehlgeschlagen (Exit {result.rc})")
    job.log("action", f"{addon.id}.{target['id']} gestoppt")
    return True


def run(provider, job, container: str, addon, action: dict, given: dict, ip: str | None) -> dict:
    """Führt eine Aktion aus. Ausgabe geht ins Job-Log."""
    name = f"{addon.id}.{action['id']}"
    if "stops" in action:
        target = addon.action(action["stops"])
        if not stop(provider, job, container, addon, target, name):
            return {"action": name, "status": "stopped", "was_running": False}
        return {"action": name, "status": "stopped", "was_running": True, "stopped": f"{addon.id}.{target['id']}"}

    command = _render(action["run"], values_for(addon, given, container, ip), "run")
    if action["detach"]:
        if running_pid(provider, container, addon.id, action) is not None:
            raise ConflictError(f"Aktion {name} läuft bereits")
        script = (
            f"umask 077; setsid nohup bash -lc {shlex.quote(command)} "
            f"> {log_file(addon.id, action['id'])} 2>&1 & echo $! > {pid_file(addon.id, action['id'])}"
        )
        result = provider.exec(container, ["bash", "-lc", script], user=action["user"], cwd=WORKDIR)
        if result.rc != 0:
            raise JobFailedError(f"Aktion {name}: Start fehlgeschlagen (Exit {result.rc})")
        pid = running_pid(provider, container, addon.id, action)
        job.log("action", f"{name} läuft im Hintergrund" + (f" (PID {pid})" if pid else ""))
        return {"action": name, "status": "running" if pid else "stopped", "pid": pid}

    job.log("action", f"{name} …")
    try:
        result = provider.exec(
            container, ["bash", "-lc", command], user=action["user"], cwd=WORKDIR, timeout=action["timeout"]
        )
    except EngineError as exc:
        if exc.details.get("timeout"):
            raise JobFailedError(f"Aktion {name}: Zeitüberschreitung nach {action['timeout']} s") from None
        raise
    for line in (result.stdout + result.stderr).splitlines():
        job.log("action", line)
    if result.rc != 0:
        raise JobFailedError(f"Aktion {name} endete mit Exit {result.rc}")
    job.log("action", f"{name} ok")
    return {"action": name, "status": "ok"}


def find(catalog, active_ids, spec: str):
    """Löst `<addon>.<id>` zu (Addon, Aktion) auf; das Addon muss im Container aktiv sein."""
    from . import names

    parts = names.split_action(spec)
    if parts is None:
        raise NotFoundError(f"Aktion ungültig, erwartet <addon>.<id>: {spec}")
    addon = catalog.get(parts[0]) if parts[0] in active_ids else None
    action = addon.action(parts[1]) if addon else None
    if action is None:
        raise NotFoundError(f"Aktion fehlt: {spec}")
    return addon, action
