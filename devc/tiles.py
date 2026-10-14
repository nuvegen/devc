"""Kacheln aus Addons auf dem Container-Dashboard (SPEC 6.2 `[[tiles]]`, 8.13)."""

from . import actions, names
from .errors import ConflictError, EngineError, JobFailedError, NotFoundError, UsageError

MAX_VIEW = 256 * 1024  # Zeichen; mehr zeigt die Ansicht nicht


def ref(addon, tile: dict) -> str:
    return f"{addon.id}.{tile['id']}"


def describe(addon, given: dict, container: str, ip: str | None, running: bool, server: dict | None = None) -> list[dict]:
    """Kacheln eines aktiven Addons; `link` mit aufgelöster Adresse."""
    values = actions.values_for(addon, given, container, ip)
    result = []
    for tile in addon.tiles:
        entry = {"ref": ref(addon, tile), "addon": addon.id, "id": tile["id"], "title": tile["title"],
                 "kind": tile["kind"], "description": tile["description"], "active": running}
        if tile["kind"] == "view":
            entry["refresh"] = tile["refresh"]
        elif tile["kind"] == "link":
            if "port" in tile:
                entry.update(actions._port_link(tile, values, container, server))
            else:
                try:
                    entry["url"] = actions._render(tile["url"], values, "url")
                except ConflictError:
                    entry["url"] = None
            entry["active"] = running and entry["url"] is not None
        result.append(entry)
    return result


def find(catalog, active_ids, spec: str, kind: str | None = None):
    """Löst `<addon>.<id>` zu (Addon, Kachel) auf; das Addon muss im Container aktiv sein."""
    parts = names.split_action(spec)
    if parts is None:
        raise NotFoundError(f"Kachel ungültig, erwartet <addon>.<id>: {spec}")
    addon = catalog.get(parts[0]) if parts[0] in active_ids else None
    tile = addon.tile(parts[1]) if addon else None
    if tile is None:
        raise NotFoundError(f"Kachel fehlt: {spec}")
    if kind is not None and tile["kind"] != kind:
        raise UsageError(f"Kachel {spec} ist nicht von der Art {kind}")
    return addon, tile


def command(addon, tile: dict, given: dict, container: str, ip: str | None) -> str:
    """Befehl einer Kachel mit `run`, Platzhalter eingesetzt (SPEC 7.3)."""
    return actions._render(tile["run"], actions.values_for(addon, given, container, ip), "run")


def view(provider, container: str, addon, tile: dict, given: dict, ip: str | None) -> dict:
    """Führt den Befehl einer `view`-Kachel aus; seine Ausgabe ist der Text der Seite (Markdown)."""
    name = ref(addon, tile)
    try:
        result = provider.exec(container, ["bash", "-lc", command(addon, tile, given, container, ip)],
                               user="dev", cwd=actions.WORKDIR, timeout=tile["timeout"])
    except EngineError as exc:
        if exc.details.get("timeout"):
            raise JobFailedError(f"Kachel {name}: Zeitüberschreitung nach {tile['timeout']} s") from None
        raise
    if result.rc != 0:
        last = (result.stderr.strip().splitlines() or [""])[-1][:200]
        raise JobFailedError(f"Kachel {name} endete mit Exit {result.rc}" + (f": {last}" if last else ""))
    text = result.stdout
    truncated = len(text) > MAX_VIEW
    return {"tile": name, "title": tile["title"], "refresh": tile["refresh"], "text": text[:MAX_VIEW], "truncated": truncated}
