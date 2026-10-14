"""HTTP-API, SSE und statische GUI (SPEC 8.6). Ruft ausschließlich Use-Cases in core."""

import json
import logging
import mimetypes
import re
import secrets as _random
import shutil
import signal
import ssl
import sys
import tempfile
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__, core, security
from . import chat as chat_module
from . import terminal as terminal_module
from . import vm as vm_module
from .errors import AuthError, DevcError, InternalError, NotFoundError, SecurityError, UsageError, ValidationError

log = logging.getLogger("devc.server")

MAX_UPLOAD = 2 * 1024 * 1024 * 1024
MAX_TEXT = 1024 * 1024
UPSTREAM_SECONDS = 5
PROXY_AUTH_PATH = "/api/proxy/auth"
_STATIC_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
_UPLOAD_RE = re.compile(r"^u-[0-9a-f]{16}$")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'"

N = r"(?P<name>[^/]+)"
ROUTES: list[tuple[str, re.Pattern, str, bool]] = []


def route(method: str, pattern: str, public: bool = False):
    def register(func):
        ROUTES.append((method, re.compile("^" + pattern + "$"), func.__name__, public))
        return func

    return register


class App:
    """Zustand des Servers: Konfiguration, Token, Sessions, Bind-Modus."""

    def __init__(self, config, mode: security.BindMode, bind: str, dev: bool = False):
        self.config = config
        self.mode = mode
        self.bind = bind
        self.dev = dev
        self.sessions = security.Sessions()
        self.terminals = terminal_module.Sessions()
        self.chats = chat_module.Chats()
        self.proxy = security.ProxySessions()
        self.targets: dict = {}  # (container, port) -> (gültig bis, Ziel); erspart einen lxc-Aufruf je Anfrage
        self.allowed_hosts = security.allowed_hosts(config.server["allowed_hosts"])

    def target(self, name: str, port: int) -> dict:
        """Ziel eines Container-Ports, für UPSTREAM_SECONDS gemerkt; Fehler werden nicht gemerkt."""
        now = time.monotonic()
        cached = self.targets.get((name, port))
        if cached and cached[0] > now:
            return cached[1]
        target = core.proxy_target(self.config, name, port)
        if len(self.targets) > 1024:
            self.targets.clear()
        self.targets[(name, port)] = (now + UPSTREAM_SECONDS, target)
        return target

    def token(self) -> str | None:
        # bei jeder Anfrage von der Platte: `devc token rotate` wirkt sofort
        return security.read_token(self.config.paths.token)


class Handler(BaseHTTPRequestHandler):
    server_version = f"devc/{__version__}"
    protocol_version = "HTTP/1.1"
    app: App = None

    # --- Rahmen ---------------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002 - Signatur der Basisklasse
        log.info("%s %s", self.address_string(), format % args)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        self._responded = False
        self.cookie_auth = False
        self.tls = self.app.mode.tls
        self.public_host = None  # nur hinter einem Proxy: der Name, den der Browser sieht
        try:
            url = urlsplit(self.path)
            self.query = {key: values[-1] for key, values in parse_qs(url.query).items()}
            path = unquote(url.path)
            if self.app.mode.proxy and path == PROXY_AUTH_PATH:
                security.require_local(self._peer())  # der Name ist hier der eines Container-Ports, siehe proxy_auth
            elif self.app.mode.proxy:
                request = security.forwarded(self._peer(), self.headers, self.app.config.server["base_domain"])
                security.check_origin(self.headers.get("Origin"), request)
                self.tls, self.public_host = request.tls, request.host
            elif self.app.mode.check_host:
                security.check_host(self.headers.get("Host"), self.app.allowed_hosts)
            for route_method, pattern, handler, public in ROUTES:
                match = pattern.match(path)
                if match and route_method == method:
                    if not public:
                        self._authenticate(method)
                    getattr(self, handler)(**match.groupdict())
                    return
            if method == "GET" and not path.startswith("/api/"):
                self._static(path)
                return
            if any(pattern.match(path) for _, pattern, _, _ in ROUTES):
                raise UsageError(f"Methode {method} ist für {path} nicht erlaubt")
            raise NotFoundError(f"Unbekannter Pfad: {path}")
        except DevcError as error:
            self._error(error)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:  # letzte Instanz
            log.exception("Unerwarteter Fehler") if self.app.dev else log.error("Unerwarteter Fehler: %s", type(error).__name__)
            # Nur der Typ: die Meldung könnte Secret-Werte enthalten.
            self._error(InternalError(f"Unerwarteter Fehler ({type(error).__name__})"))

    def _peer(self) -> str:
        return self.client_address[0]

    def _authenticate(self, method: str) -> None:
        """Bearer-Token oder Session-Cookie; ändernde Anfragen mit Cookie brauchen `X-Devc: 1`."""
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            security.require(security.token_matches(header[len("Bearer "):].strip(), self.app.token()))
            return
        security.require(self.app.sessions.valid(self._session()))
        self.cookie_auth = True
        if method != "GET" and self.headers.get("X-Devc") != "1":
            raise AuthError("Ändernde Anfragen mit Cookie brauchen den Header X-Devc: 1")

    def _cookie(self, name: str) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(name)
        return morsel.value if morsel else None

    def _session(self) -> str | None:
        return self._cookie(security.SESSION_COOKIE)

    # --- Antworten ------------------------------------------------------

    def _head(self, status: int, content_type: str | None, length: int | None, extra: dict | None = None) -> None:
        self._responded = True
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._head(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _text(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self._head(status, "text/plain; charset=utf-8", len(body))
        self.wfile.write(body)

    def _empty(self, extra: dict | None = None) -> None:
        self._head(204, None, 0, extra)

    def _redirect(self, location: str, extra: dict | None = None) -> None:
        self._head(302, None, 0, {"Location": location, **(extra or {})})

    def _file(self, handle, length: int, content_type: str, filename: str | None = None) -> None:
        extra = {}
        if filename:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
            extra["Content-Disposition"] = f'attachment; filename="{safe}"'
        self._head(200, content_type, length, extra)
        shutil.copyfileobj(handle, self.wfile)

    def _error(self, error: DevcError) -> None:
        if self._responded:
            return
        self.close_connection = True  # ein evtl. ungelesener Body darf die Verbindung nicht verwirren
        self._json(error.to_dict(), error.http_status)

    # --- Eingaben -------------------------------------------------------

    def _length(self, limit: int) -> int:
        header = self.headers.get("Content-Length")
        if header is None or not header.isdigit():
            raise UsageError("Content-Length fehlt")
        length = int(header)
        if length > limit:
            raise UsageError(f"Body ist größer als erlaubt ({limit} Bytes)")
        return length

    def _body(self, limit: int | None = None) -> bytes:
        return self.rfile.read(self._length(MAX_TEXT if limit is None else limit))

    def _body_text(self) -> str:
        try:
            return self._body().decode("utf-8")
        except UnicodeDecodeError:
            raise UsageError("Body ist nicht UTF-8") from None

    def _body_json(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise UsageError("Body ist kein gültiges JSON") from None
        if not isinstance(data, dict):
            raise UsageError("Body muss ein JSON-Objekt sein")
        return data

    def _body_file(self, target) -> int:
        """Streamt einen Upload (max. 2 GiB) in ein Dateiobjekt."""
        remaining = length = self._length(MAX_UPLOAD)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise UsageError("Upload ist unvollständig")
            target.write(chunk)
            remaining -= len(chunk)
        return length

    def _flag(self, key: str) -> bool:
        return self.query.get(key, "") in ("1", "true")

    def _job(self, func, *args, status: int = 202, **kwargs) -> None:
        """Startet einen Use-Case im Thread und antwortet 202 mit der Job-ID, sobald der Job angelegt ist.

        Scheitert der Use-Case vor dem Anlegen des Jobs (Prüfung, Konflikt), kommt der Fehler direkt zurück.
        """
        started = threading.Event()
        box: dict = {}

        def hook(job_id: str) -> None:
            box.setdefault("job", job_id)
            started.set()

        def run() -> None:
            core.set_job_hook(hook)
            try:
                box["result"] = func(*args, **kwargs)
            except BaseException as error:
                box["error"] = error
            finally:
                core.set_job_hook(None)
                started.set()

        threading.Thread(target=run, daemon=True, name="devc-job").start()
        started.wait()
        if "job" in box:
            self._json({"job": box["job"]}, status)
        elif "error" in box:
            raise box["error"]
        else:
            self._json(box.get("result") or {})

    # --- Statische GUI --------------------------------------------------

    def _static(self, path: str) -> None:
        relative = path.lstrip("/") or "index.html"
        if not _STATIC_RE.match(relative) or ".." in relative.split("/"):
            raise NotFoundError("Datei fehlt")
        resource = core.web_file(*relative.split("/"))
        if not resource.is_file():
            raise NotFoundError(f"Datei fehlt: /{relative}")
        body = resource.read_bytes()
        suffix = "." + relative.rsplit(".", 1)[-1] if "." in relative else ""
        content_type = TYPES.get(suffix) or mimetypes.guess_type(relative)[0] or "application/octet-stream"
        self._head(200, content_type, len(body))
        self.wfile.write(body)

    # --- Anmeldung und Status -------------------------------------------

    @route("POST", "/api/login", public=True)
    def login(self):
        token = self._body_json().get("token")
        security.require(isinstance(token, str) and security.token_matches(token, self.app.token()), "Token ist falsch")
        session = self.app.sessions.create()
        cookie = f"{security.SESSION_COOKIE}={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age={security.SESSION_SECONDS}"
        if self.tls:
            cookie += "; Secure"
        self._empty({"Set-Cookie": cookie})

    @route("POST", "/api/logout")
    def logout(self):
        self.app.sessions.remove(self._session())
        self.app.proxy.clear()  # ein Token, ein Nutzer: Abmelden beendet auch die Sitzungen der Container-Ports
        self._empty({"Set-Cookie": f"{security.SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})

    @route("GET", "/api/status")
    def status(self):
        mode = self.app.mode
        self._json({
            "version": __version__,
            "platform": "lxd",
            "bind": self.app.bind,
            "insecure": mode.insecure,
            "behind_proxy": mode.proxy,
            "terminal": mode.terminal and terminal_module.available(),
            "terminal_allowed": mode.terminal,
        })

    # --- Hilfe (SPEC 8.12) ----------------------------------------------

    @route("GET", "/api/help")
    def help_list(self):
        self._json(core.help_list(self.app.config))

    @route("GET", "/api/help/(?P<id>[^/]+)")
    def help_show(self, id):
        self._json(core.help_show(self.app.config, id))

    # --- Proxy: Container-Ports (SPEC 8.11) -----------------------------

    def _proxy_mode(self) -> str:
        if not self.app.mode.proxy:
            raise NotFoundError("Kein Proxy eingerichtet (server.behind_proxy)")
        return self.app.config.server["base_domain"]

    @route("GET", PROXY_AUTH_PATH, public=True)
    def proxy_auth(self):
        """Fragt Caddy per `forward_auth` für jede Anfrage an `<port>-<container>.<base_domain>`."""
        base = self._proxy_mode()
        headers = self.headers
        host = (headers.get("X-Forwarded-Host") or "").split(",", 1)[0].strip().lower()
        target = security.proxy_name(host, base)
        if target is None:
            raise NotFoundError("Unbekannter Name")
        port, name = target
        tls = security.forwarded_tls(headers, base)
        origin = f"{'https' if tls else 'http'}://{host}"
        hostname = security.host_of(host)
        uri = headers.get("X-Forwarded-Uri") or "/"
        if not uri.startswith("/"):
            raise UsageError("Ungültiger Pfad")
        clean, ticket = security.strip_ticket(uri)
        if ticket is not None:
            # Das Ticket erreicht den Container nie: erst einlösen, dann ohne den Parameter neu aufrufen.
            session = self.app.proxy.redeem(ticket, hostname)
            extra = {}
            if session:
                cookie = f"{security.PROXY_COOKIE}={session}; HttpOnly; SameSite=Lax; Path=/; Max-Age={security.SESSION_SECONDS}"
                extra["Set-Cookie"] = cookie + ("; Secure" if tls else "")
            self._redirect(origin + clean, extra)
            return
        if not self.app.proxy.valid(self._cookie(security.PROXY_COOKIE), hostname) and not self._public_port(name, port):
            if headers.get("X-Forwarded-Method", "GET").upper() not in ("GET", "HEAD"):
                raise AuthError("Anmeldung fehlt; die Seite im Browser neu laden")
            devc = security.PROXY_LABEL + "." + base + host[len(hostname):]  # gleicher Port wie die Anfrage
            self._redirect(f"{origin.split('://')[0]}://{devc}/api/proxy/login?to={quote(origin + uri, safe='')}")
            return
        self._head(200, None, 0, {security.UPSTREAM_HEADER: self.app.target(name, port)["upstream"]})

    def _public_port(self, name: str, port: int) -> bool:
        """Gibt ein Addon den Port ohne Anmeldung frei? Fehler (Container fehlt, läuft nicht) heißen nein."""
        try:
            return self.app.target(name, port)["public"]
        except DevcError:
            return False

    @route("GET", "/api/proxy/login", public=True)
    def proxy_login(self):
        """Stellt dem angemeldeten Browser ein Ticket für einen Container-Port aus und leitet dorthin zurück."""
        base = self._proxy_mode()
        to = security.proxy_return_url(self.query.get("to"), base)
        if not self.app.sessions.valid(self._session()):
            self._redirect("/?to=" + quote(to, safe=""))  # die GUI meldet an und kommt mit `to` wieder
            return
        ticket = self.app.proxy.ticket(security.host_of(urlsplit(to).netloc))
        self._redirect(f"{to}{'&' if '?' in to else '?'}{security.TICKET_PARAM}={ticket}")

    @route("GET", "/api/proxy/config")
    def proxy_config(self):
        self._text(core.proxy_config(self.app.config, self._port_param("http_port"))["caddyfile"])

    def _port_param(self, key: str) -> int | None:
        value = self.query.get(key)
        if value is None:
            return None
        if not value.isdigit():
            raise UsageError(f"{key} muss eine Zahl sein")
        return int(value)

    # --- Addons ---------------------------------------------------------

    @route("GET", "/api/addons")
    def addons(self):
        self._json(core.addon_list(self.app.config))

    @route("PUT", "/api/addons")
    def addon_import(self):
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as handle:
            if self._body_file(handle) > 256 * 1024 * 1024:
                raise UsageError("Addon-Archiv ist zu groß")
            handle.seek(0)
            data = handle.read()
        result = core.addon_import_data(self.app.config, data)
        self._json({"id": result["id"], "version": result["version"]}, 201)

    @route("GET", "/api/addons/(?P<id>[^/]+)")
    def addon_show(self, id):
        self._json(core.addon_show(self.app.config, id))

    @route("GET", "/api/addons/(?P<id>[^/]+)/archive")
    def addon_archive(self, id):
        filename, data = core.addon_archive(self.app.config, id)
        self._head(200, "application/gzip", len(data), {"Content-Disposition": f'attachment; filename="{filename}"'})
        self.wfile.write(data)

    @route("DELETE", "/api/addons/(?P<id>[^/]+)")
    def addon_rm(self, id):
        core.addon_rm(self.app.config, id)
        self._empty()

    # --- Templates und Entwürfe -----------------------------------------

    @route("GET", "/api/templates")
    def templates(self):
        self._json(core.template_list(self.app.config))

    @route("GET", "/api/templates/(?P<id>[^/]+)")
    def template_show(self, id):
        self._json(core.template_show(self.app.config, id))

    @route("PUT", "/api/templates/(?P<id>[^/]+)")
    def template_put(self, id):
        self._json({"findings": core.template_put(self.app.config, id, self._body_text())["findings"]})

    @route("DELETE", "/api/templates/(?P<id>[^/]+)")
    def template_rm(self, id):
        core.template_rm(self.app.config, id)
        self._empty()

    @route("POST", "/api/drafts")
    def drafts(self):
        body = self._body_json()
        name = body.get("name")
        if not isinstance(name, str):
            raise UsageError("name fehlt")
        template = body.get("template") or None
        self._json(core.draft(self.app.config, name, template))

    @route("POST", "/api/validate/instance")
    def validate_instance(self):
        checked = core.validate_instance(self.app.config, self._body_text(), provided=())
        self._json({"findings": [f.to_dict() for f in checked["findings"]], "missing_secrets": checked["missing_secrets"]})

    # --- Container ------------------------------------------------------

    @route("GET", "/api/containers")
    def containers(self):
        self._json(core.list_containers(self.app.config))

    @route("POST", "/api/containers")
    def container_create(self):
        text = self._body_text()
        checked = core.validate_instance(self.app.config, text)
        name = checked["data"].get("name")
        if not isinstance(name, str):
            from .errors import ValidationError

            raise ValidationError(checked["findings"] or [])
        self._job(core.create, self.app.config, name, text=text)

    @route("GET", f"/api/containers/{N}")
    def container_info(self, name):
        self._json(core.info(self.app.config, name))

    @route("DELETE", f"/api/containers/{N}")
    def container_rm(self, name):
        self._job(core.rm, self.app.config, name, keep_data=self._flag("keep_data"))

    @route("POST", f"/api/containers/{N}/(?P<action>start|stop|restart)")
    def container_lifecycle(self, name, action):
        result = getattr(core, action)(self.app.config, name)
        self._json({"status": result["status"]})

    @route("GET", f"/api/containers/{N}/config")
    def config_show(self, name):
        self._text(core.config_show(self.app.config, name)["text"])

    @route("PUT", f"/api/containers/{N}/config")
    def config_put(self, name):
        result = core.config_put(self.app.config, name, self._body_text())
        self._json({"findings": result["findings"], "missing_secrets": result["missing_secrets"]})

    @route("POST", f"/api/containers/{N}/config/apply")
    def config_apply(self, name):
        if self._flag("dry_run"):
            self._json(core.config_apply(self.app.config, name, dry_run=True))
        else:
            self._job(core.config_apply, self.app.config, name)

    @route("GET", f"/api/containers/{N}/addons")
    def container_addons(self, name):
        self._json(core.action_list(self.app.config, name))

    @route("POST", f"/api/containers/{N}/addons")
    def addon_install(self, name):
        body = self._body_json()
        addons, params = body.get("addons"), body.get("params", {})
        if not (isinstance(addons, list) and addons and all(isinstance(a, str) for a in addons)):
            raise UsageError("addons muss eine Liste mit mindestens einem Addon sein")
        if not (isinstance(params, dict) and all(isinstance(values, dict) for values in params.values())):
            raise UsageError("params muss je Addon ein Objekt mit Parametern sein")
        text = lambda value: ("true" if value else "false") if isinstance(value, bool) else str(value)
        specs = [f"{addon}.{key}={text(value)}" for addon, values in params.items() for key, value in values.items()]
        secrets = body.get("secrets", {})
        if not (isinstance(secrets, dict) and all(isinstance(value, str) and value for value in secrets.values())):
            raise UsageError("secrets muss je Slot einen nicht leeren Text tragen")
        self._job(core.install, self.app.config, name, addons, specs,
                  secret_values={slot: value.encode("utf-8") for slot, value in secrets.items()})

    @route("PUT", f"/api/containers/{N}/addons/(?P<id>[^/]+)/params")
    def addon_params(self, name, id):
        values = self._body_json().get("params")
        if not isinstance(values, dict) or not values:
            raise UsageError("params muss ein Objekt mit mindestens einem Parameter sein")
        text = lambda value: ("true" if value else "false") if isinstance(value, bool) else str(value)
        self._job(core.params_set, self.app.config, name, [f"{id}.{key}={text(value)}" for key, value in values.items()])

    @route("POST", f"/api/containers/{N}/actions/(?P<action>[^/]+)")
    def action_run(self, name, action):
        self._job(core.action_run, self.app.config, name, action)

    @route("GET", f"/api/containers/{N}/tiles")
    def tile_list(self, name):
        self._json(core.tile_list(self.app.config, name))

    @route("GET", f"/api/containers/{N}/tiles/(?P<tile>[^/]+)/view")
    def tile_show(self, name, tile):
        self._json(core.tile_show(self.app.config, name, tile))

    @route("POST", f"/api/containers/{N}/tiles/(?P<tile>[^/]+)/panel")
    def tile_panel(self, name, tile):
        body = self._body_json()
        self._json(core.tile_panel(self.app.config, name, tile, view=body.get("view"), action=body.get("action"),
                                   args=body.get("args"), values=body.get("values")))

    def _terminal_allowed(self) -> None:
        if not self.app.mode.terminal:
            raise SecurityError("Das Web-Terminal ist für diesen Bind nicht freigegeben")

    @route("GET", f"/api/containers/{N}/terminals")
    def terminal_list(self, name):
        self._terminal_allowed()
        self._json({"sessions": self.app.terminals.list(name)})

    @route("POST", f"/api/containers/{N}/terminals")
    def terminal_create(self, name):
        self._terminal_allowed()
        user = self._body_json().get("user", "dev")
        if user not in ("dev", "root"):
            raise UsageError("user muss dev oder root sein")
        self._json(self.app.terminals.create(self.app.config, name, user).describe(), 201)

    @route("DELETE", f"/api/containers/{N}/terminals/(?P<sid>[0-9a-f]+)")
    def terminal_close(self, name, sid):
        self._terminal_allowed()
        self.app.terminals.get(name, sid).close()
        self._json({"closed": sid})

    @route("GET", f"/api/containers/{N}/terminal")
    def terminal(self, name):
        self._terminal_allowed()
        terminal_module.serve(self, self.app, name, self.query.get("session", ""))

    # --- VM: beschränktes Terminal und Update (SPEC 8.14) ----------------

    @route("GET", "/api/vm")
    def vm_info(self):
        info = core.vm_info(self.app.config)
        allowed = self.app.mode.terminal
        self._json({**info, "terminal": info["terminal"] and allowed, "update": info["update"] and allowed, "allowed": allowed})

    @route("GET", "/api/vm/terminals")
    def vm_terminal_list(self):
        self._terminal_allowed()
        self._json({"sessions": self.app.terminals.list(vm_module.NAME)})

    @route("POST", "/api/vm/terminals")
    def vm_terminal_create(self):
        self._terminal_allowed()
        self._body_json()  # ein ungelesener Body bliebe in der Verbindung stehen und verdürbe die nächste Anfrage
        self._json(self.app.terminals.create(self.app.config, vm_module.NAME, vm_module.user()).describe(), 201)

    @route("DELETE", "/api/vm/terminals/(?P<sid>[0-9a-f]+)")
    def vm_terminal_close(self, sid):
        self._terminal_allowed()
        self.app.terminals.get(vm_module.NAME, sid).close()
        self._json({"closed": sid})

    @route("GET", "/api/vm/terminal")
    def vm_terminal(self):
        self._terminal_allowed()
        terminal_module.serve(self, self.app, vm_module.NAME, self.query.get("session", ""))

    @route("POST", "/api/vm/update")
    def vm_update(self):
        # Ein neues devc einzuspielen ist so mächtig wie eine Shell: dieselbe Regel wie für das Terminal.
        self._terminal_allowed()
        config = self.app.config
        config.paths.uploads.mkdir(parents=True, exist_ok=True)
        path = config.paths.uploads / f"u-{_random.token_hex(8)}.devc.pyz"
        try:
            with open(path, "wb") as handle:
                self._length(vm_module.MAX_UPDATE)
                self._body_file(handle)
            self._json(core.vm_update(config, str(path)), 202)
        finally:
            path.unlink(missing_ok=True)  # eigener, gerade angelegter Upload

    # --- Chat (Kacheln der Art chat, SPEC 8.13) -------------------------

    @route("GET", f"/api/containers/{N}/chats")
    def chat_list(self, name):
        self._terminal_allowed()
        self._json({"sessions": self.app.chats.list(name)})

    @route("GET", f"/api/containers/{N}/chats/history")
    def chat_history(self, name):
        self._terminal_allowed()
        self._json(core.chat_history(self.app.config, name, self.query.get("tile", "")))

    @route("GET", f"/api/containers/{N}/chats/transcript")
    def chat_transcript(self, name):
        self._terminal_allowed()
        self._json(chat_module.transcript(self.app.config, name, self.query.get("tile", ""), self.query.get("session")))

    @route("GET", f"/api/containers/{N}/chats/stats")
    def chat_stats(self, name):
        self._terminal_allowed()
        self._json(chat_module.stats(self.app.config, name, self.query.get("tile", ""), self.query.get("session")))

    @route("POST", f"/api/containers/{N}/chats")
    def chat_create(self, name):
        self._terminal_allowed()
        body = self._body_json()
        tile = body.get("tile")
        if not isinstance(tile, str):
            raise UsageError("tile fehlt")
        chat = self.app.chats.create(self.app.config, name, tile, body.get("resume"))
        self._json(chat.describe(), 201)

    @route("DELETE", f"/api/containers/{N}/chats/(?P<sid>[0-9a-f]+)")
    def chat_close(self, name, sid):
        self._terminal_allowed()
        self.app.chats.get(name, sid).end()
        self._json({"closed": sid})

    @route("GET", f"/api/containers/{N}/chat")
    def chat(self, name):
        self._terminal_allowed()
        chat_module.serve(self, self.app, name, self.query.get("session", ""))

    # --- Secrets --------------------------------------------------------

    @route("GET", f"/api/containers/{N}/secrets")
    def container_secrets(self, name):
        self._json(core.secret_list(self.app.config, name=name))

    @route("PUT", f"/api/containers/{N}/secrets/(?P<slot>[^/]+)")
    def container_secret_set(self, name, slot):
        body = self._body_json()
        value, store = body.get("value"), body.get("store")
        if (value is None) == (store is None) or not isinstance(value if store is None else store, str):
            raise UsageError('Genau eins von "value" und "store" angeben')
        core.secret_set(self.app.config, name=name, target=slot,
                        value=value.encode("utf-8") if value is not None else None, from_store=store)
        self._empty()

    @route("DELETE", f"/api/containers/{N}/secrets/(?P<slot>[^/]+)")
    def container_secret_rm(self, name, slot):
        core.secret_rm(self.app.config, name=name, target=slot)
        self._empty()

    @route("GET", "/api/secrets")
    def secrets(self):
        self._json(core.secret_list(self.app.config, shared=True))

    @route("PUT", "/api/secrets/(?P<store>[^/]+)")
    def secret_set(self, store):
        value = self._body()
        if not value:
            raise UsageError("Leerer Wert")
        core.secret_set(self.app.config, shared=True, target=store, value=value)
        self._empty()

    @route("DELETE", "/api/secrets/(?P<store>[^/]+)")
    def secret_rm(self, store):
        core.secret_rm(self.app.config, shared=True, target=store)
        self._empty()

    # --- Snapshots und Backups ------------------------------------------

    @route("GET", f"/api/containers/{N}/snapshots")
    def snapshots(self, name):
        self._json(core.snapshot_list(self.app.config, name))

    @route("POST", f"/api/containers/{N}/snapshots")
    def snapshot_create(self, name):
        body = self._body_json()
        self._job(core.snapshot_create, self.app.config, name, label=body.get("label") or None, full=bool(body.get("full")))

    @route("POST", f"/api/containers/{N}/snapshots/(?P<snap>[^/]+)/restore")
    def snapshot_restore(self, name, snap):
        self._job(core.snapshot_restore, self.app.config, name, snap)

    @route("DELETE", f"/api/containers/{N}/snapshots/(?P<snap>[^/]+)")
    def snapshot_rm(self, name, snap):
        core.snapshot_rm(self.app.config, name, snap)
        self._empty()

    @route("GET", f"/api/containers/{N}/backups")
    def backups(self, name):
        self._json(core.backup_list(self.app.config, name))

    @route("POST", f"/api/containers/{N}/backups")
    def backup_create(self, name):
        body = self._body_json()
        self._job(core.backup, self.app.config, name, label=body.get("label") or None,
                  no_home=body.get("include_home") is False, no_work=body.get("include_work") is False)

    @route("GET", "/api/backups/(?P<file>[^/]+)")
    def backup_download(self, file):
        path = core.backup_file(self.app.config, file)
        with open(path, "rb") as handle:
            self._file(handle, path.stat().st_size, "application/x-tar", path.name)

    @route("DELETE", "/api/backups/(?P<file>[^/]+)")
    def backup_rm(self, file):
        core.backup_rm(self.app.config, file)
        self._empty()

    def _upload_path(self, upload_id: str) -> Path:
        path = self.app.config.paths.uploads / f"{upload_id}.devc.tar"
        if not _UPLOAD_RE.match(upload_id or "") or not path.is_file():
            raise NotFoundError("Upload fehlt")
        return path

    @route("POST", "/api/restore/inspect")
    def restore_inspect(self):
        config = self.app.config
        if self.headers.get("Content-Type", "").startswith("application/json"):
            path = core.backup_file(config, str(self._body_json().get("backup", "")))
            result = core.restore_inspect(config, str(path))
        else:
            config.paths.uploads.mkdir(parents=True, exist_ok=True)
            upload_id = "u-" + _random.token_hex(8)
            path = config.paths.uploads / f"{upload_id}.devc.tar"
            try:
                with open(path, "wb") as handle:
                    self._body_file(handle)
                result = core.restore_inspect(config, str(path))
            except BaseException:
                path.unlink(missing_ok=True)  # eigener, gerade angelegter Upload
                raise
            result["upload_id"] = upload_id
        result.pop("includes", None)
        self._json(result)

    @route("POST", "/api/restore")
    def restore(self):
        body = self._body_json()
        source = body.get("source") if isinstance(body.get("source"), dict) else {}
        upload = None
        if "upload_id" in source:
            upload = path = self._upload_path(str(source["upload_id"]))
        else:
            path = core.backup_file(self.app.config, str(source.get("backup", "")))
        target, name, text = body.get("target"), body.get("name"), body.get("devc_toml")
        if target not in ("new", "replace") or not isinstance(name, str):
            raise UsageError('target muss "new" oder "replace" sein, name ist Pflicht')
        if text is not None and not isinstance(text, str):
            raise UsageError("devc_toml muss Text sein")

        def run():
            try:
                return core.restore(self.app.config, str(path), new_name=name if target == "new" else None,
                                    replace=name if target == "replace" else None, text=text)
            finally:
                if upload is not None:
                    upload.unlink(missing_ok=True)

        self._job(run)

    # --- Dateien --------------------------------------------------------

    @route("GET", f"/api/containers/{N}/files")
    def files(self, name):
        self._json(core.files_list(self.app.config, name, self.query.get("path", "/work")))

    @route("GET", f"/api/containers/{N}/files/raw")
    def file_get(self, name):
        with tempfile.TemporaryFile() as handle:
            meta = core.file_read(self.app.config, name, self.query.get("path", ""), handle)
            length = handle.tell()
            handle.seek(0)
            self._file(handle, length, meta["content_type"], meta["filename"])

    @route("PUT", f"/api/containers/{N}/files/raw")
    def file_put(self, name):
        with tempfile.TemporaryFile() as handle:
            self._body_file(handle)
            handle.seek(0)
            core.file_write(self.app.config, name, self.query.get("path", ""), handle)
        self._empty()

    @route("DELETE", f"/api/containers/{N}/files/raw")
    def file_delete(self, name):
        core.file_rm(self.app.config, name, self.query.get("path", ""))
        self._empty()

    @route("GET", f"/api/containers/{N}/files/archive")
    def file_archive(self, name):
        self._json(core.archive_list(self.app.config, name, self.query.get("path", "")))

    @route("POST", f"/api/containers/{N}/files/unpack")
    def file_unpack(self, name):
        body = self._body_json()
        self._json(core.archive_unpack(self.app.config, name, str(body.get("path", "")), str(body["into"]) if body.get("into") else None))

    # --- Jobs -----------------------------------------------------------

    @route("GET", "/api/jobs")
    def jobs(self):
        self._json(core.jobs(self.app.config, self.query.get("container") or None))

    @route("GET", "/api/jobs/(?P<id>[^/]+)")
    def job(self, id):
        self._json(core.job_get(self.app.config, id))

    @route("GET", "/api/jobs/(?P<id>[^/]+)/events")
    def job_events(self, id):
        events = core.job_events(self.app.config, id)
        self._head(200, "text/event-stream; charset=utf-8", None, {"Connection": "close", "X-Accel-Buffering": "no"})
        self.close_connection = True
        for event, data in events:
            if event == "log":
                payload = data
            elif event in ("state", "progress"):
                payload = json.dumps(data, ensure_ascii=False)
            else:
                payload = ""
            self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, app: App):
        handler = type("BoundHandler", (Handler,), {"app": app})
        if ":" in address[0]:
            import socket

            self.address_family = socket.AF_INET6
        super().__init__(address, handler)
        self.app = app

    def server_close(self) -> None:
        self.app.terminals.close_all()  # sonst liefen die Shells ohne Server weiter
        self.app.chats.close_all()
        super().server_close()


def make_server(config, bind=None, port=None, allow_insecure_bind=False, allow_insecure_terminal=False,
                tls_cert=None, tls_key=None, dev=False) -> Server:
    """Prüft L14 (Bind-Regeln, TLS-Dateien) und baut den Server, ohne ihn zu starten."""
    bind = bind or config.server["bind"]
    port = config.server["port"] if port is None else port
    if not (0 <= port <= 65535):
        raise UsageError("Port muss zwischen 1 und 65535 liegen")
    cert = tls_cert if tls_cert is not None else config.server["tls_cert"]
    key = tls_key if tls_key is not None else config.server["tls_key"]
    proxy = config.server["behind_proxy"]
    # Hinter dem Proxy gehören tls_cert/tls_key ins Caddyfile (SPEC 8.11); devc selbst macht dann kein TLS.
    tls = False if proxy else security.check_tls_files(cert, key)
    mode = security.bind_mode(bind, tls, allow_insecure_bind, allow_insecure_terminal, config.server["terminal"], proxy)
    problems = [f for f in security.proxy_problems(config.server, bind) if f.severity == "error"]
    if problems:
        raise ValidationError(problems)
    if security.read_token(config.paths.token) is None:
        raise NotFoundError("Token fehlt; devc init ausführen")
    try:
        server = Server((bind, port), App(config, mode, bind, dev))
    except OSError as exc:
        raise UsageError(f"Server ließ sich nicht an {bind}:{port} binden: {exc.strerror}") from None
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def serve(config, **options) -> None:
    """Startet den Server und läuft bis Strg+C."""
    server = make_server(config, **options)
    mode = server.app.mode
    host, port = server.server_address[:2]
    if mode.insecure:
        warning = security.insecure_warning(server.app.bind, port, mode.terminal)
        print(warning, file=sys.stderr)
        log.warning("%s", warning.splitlines()[0])
    scheme = "https" if mode.tls else "http"
    shown = f"[{host}]" if ":" in str(host) else host
    where = f"{scheme}://{shown}:{port}/"
    if mode.proxy:
        where = f"{security.public_url(config.server)} (hinter dem Proxy, lauscht auf {shown}:{port})"
    print(f"devc {__version__} läuft auf {where} – Token: devc token show – Ende mit Strg+C", flush=True)
    def stop(*_):
        raise KeyboardInterrupt

    # Auch `kill` und systemd beenden den Server geordnet, sonst blieben die Terminal-Sitzungen offen.
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.server_close()
