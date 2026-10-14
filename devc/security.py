"""Token, Sessions, Bind-Regeln, Pfadprüfung (SPEC 11)."""

import os
import secrets
from pathlib import Path


def read_token(file: Path) -> str | None:
    try:
        token = file.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    return token or None


def write_token(file: Path) -> str:
    """Erzeugt ein neues Token und schreibt es mit Modus 0600 (ersetzt ein vorhandenes)."""
    token = secrets.token_urlsafe(32)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(token + "\n")
    os.replace(tmp, file)
    return token


def ensure_token(file: Path) -> tuple[str, bool]:
    """Liefert (token, neu_angelegt)."""
    token = read_token(file)
    if token is not None:
        return token, False
    return write_token(file), True


# --- Bind-Regeln (SPEC 11.2, L14) ------------------------------------------

import hmac
import ipaddress
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import AuthError, Finding, SecurityError, UsageError, ValidationError

SESSION_SECONDS = 12 * 3600
SESSION_COOKIE = "devc_session"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def is_loopback(bind: str) -> bool:
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind.strip("[]")).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class BindMode:
    loopback: bool
    tls: bool
    insecure: bool  # Nicht-Loopback ohne TLS, ausdrücklich erlaubt
    terminal: bool  # Web-Terminal wird angeboten
    check_host: bool  # Host-Header gegen allowed_hosts prüfen
    proxy: bool = False  # hinter einem Proxy: Host, Origin und Schema kommen je Anfrage aus `forwarded`


def bind_mode(bind: str, tls: bool, allow_insecure_bind: bool = False, allow_insecure_terminal: bool = False,
              terminal: bool = True, proxy: bool = False) -> BindMode:
    """Wendet die Tabelle aus SPEC 11.2 an; ein unsicherer Bind ohne Flag ist E_SECURITY."""
    if proxy:
        if not is_loopback(bind):
            raise SecurityError(
                f"Mit server.behind_proxy lauscht devc nur auf Loopback, nicht auf {bind}",
                {"check": "L14", "path": "server.bind"},
            )
        return BindMode(True, False, False, terminal, False, True)
    if is_loopback(bind):
        return BindMode(True, tls, False, terminal, True)
    if tls:
        return BindMode(False, True, False, terminal, False)
    if not allow_insecure_bind:
        raise SecurityError(
            f"Bind an {bind} ohne TLS ist unsicher; --tls-cert/--tls-key setzen oder --allow-insecure-bind angeben",
            {"check": "L14", "path": "server.bind"},
        )
    return BindMode(False, False, True, terminal and allow_insecure_terminal, False)


def check_tls_files(cert: str, key: str) -> bool:
    """L14: beide gesetzt = TLS an; die Dateien müssen lesbar sein."""
    if not cert and not key:
        return False
    if not cert or not key:
        raise ValidationError.single("L14", "server.tls_cert", "tls_cert und tls_key müssen beide gesetzt sein")
    for label, file in (("server.tls_cert", cert), ("server.tls_key", key)):
        if not (os.path.isfile(file) and os.access(file, os.R_OK)):
            raise ValidationError.single("L14", label, f"TLS-Datei ist nicht lesbar: {file}")
    return True


def insecure_warning(bind: str, port: int, terminal: bool) -> str:
    lines = [
        f"WARNUNG: devc lauscht ohne TLS auf {bind}:{port}.",
        "  Token und alle Daten gehen unverschlüsselt über das Netz.",
        "  Wer mitlesen kann, kann die Container und damit faktisch den Host steuern.",
        "  Das Web-Terminal ist " + ("EINGESCHALTET (--allow-insecure-terminal)." if terminal else "ausgeschaltet."),
    ]
    return "\n".join(lines)


def allowed_hosts(configured) -> set[str]:
    return {host.lower() for host in configured} | set(LOOPBACK_HOSTS)


def host_of(header: str | None) -> str:
    """Host-Header ohne Port, kleingeschrieben; IPv6 bleibt in eckigen Klammern."""
    value = (header or "").strip().lower()
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.rsplit(":", 1)[0] if ":" in value else value


def origin_matches(origin: str | None, host: str | None, tls: bool) -> bool:
    """Der Origin muss in Schema, Host und Port zum Host-Header passen."""
    if not origin or not host:
        return False
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        return False
    scheme = "https" if tls else "http"
    if parts.scheme != scheme or not parts.hostname:
        return False
    default = 443 if tls else 80
    host_name = host_of(host).strip("[]")
    tail = host.strip().lower()[len(host_of(host)):]
    try:
        host_port = int(tail[1:]) if tail.startswith(":") else default
    except ValueError:
        return False
    return parts.hostname.lower() == host_name and (port or default) == host_port


def check_host(header: str | None, allowed: set[str]) -> None:
    if host_of(header) not in allowed:
        raise SecurityError("Host-Header ist nicht erlaubt (server.allowed_hosts)")


def token_matches(given: str | None, expected: str | None) -> bool:
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


class Sessions:
    """Sessions liegen nur im Speicher und gelten 12 Stunden."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._sessions: dict[str, float] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        session = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[session] = self._clock() + SESSION_SECONDS
        return session

    def valid(self, session: str | None) -> bool:
        if not session:
            return False
        with self._lock:
            expires = self._sessions.get(session)
            if expires is None:
                return False
            if expires <= self._clock():
                del self._sessions[session]
                return False
            return True

    def remove(self, session: str | None) -> None:
        with self._lock:
            self._sessions.pop(session or "", None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


def require(condition: bool, message: str = "Anmeldung fehlt oder ist ungültig") -> None:
    if not condition:
        raise AuthError(message)


# --- Betrieb hinter einem Proxy (SPEC 11.2) -----------------------------------

PROXY_LABEL = "devc"


def is_local_domain(base_domain: str) -> bool:
    """`localhost` und `*.localhost` löst der Browser selbst auf und behandelt sie als sicheren Kontext."""
    return base_domain == "localhost" or base_domain.endswith(".localhost")


def proxy_host(server: dict) -> str:
    """Name von GUI und API hinter dem Proxy: `devc.<base_domain>`."""
    return f"{PROXY_LABEL}.{server['base_domain']}"


def public_url(server: dict, label: str = PROXY_LABEL, path: str = "/") -> str:
    """Von außen sichtbare Adresse eines Namens unter `base_domain`; `public_port` hängt an, wenn er nicht 443 ist."""
    port = "" if server["public_port"] == 443 else f":{server['public_port']}"
    return f"https://{label}.{server['base_domain']}{port}{path}"


def proxy_problems(server: dict, bind: str | None = None) -> list[Finding]:
    """L14: unpassende Kombinationen von `behind_proxy`, `base_domain`, `bind` und den TLS-Dateien."""
    bind = server["bind"] if bind is None else bind
    found = []
    if not server["behind_proxy"]:
        if server["base_domain"]:
            found.append(Finding("L14", "server.behind_proxy",
                                 "server.base_domain ist gesetzt, aber server.behind_proxy nicht: devc prüft den Namen nicht", "warning"))
        return found
    if not server["base_domain"]:
        found.append(Finding("L14", "server.base_domain", "server.behind_proxy braucht server.base_domain"))
    if not is_loopback(bind):
        found.append(Finding("L14", "server.bind", f"Mit server.behind_proxy lauscht devc nur auf Loopback, nicht auf {bind}"))
    if bool(server["tls_cert"]) != bool(server["tls_key"]):
        found.append(Finding("L14", "server.tls_cert", "tls_cert und tls_key müssen beide gesetzt sein (Zertifikat des Proxys) oder beide leer (eigene CA)"))
    return found


@dataclass(frozen=True)
class Forwarded:
    host: str  # wie der Browser ihn sieht, kleingeschrieben, mit Port falls angegeben
    tls: bool


def _first(value: str | None) -> str:
    return (value or "").split(",", 1)[0].strip().lower()


def require_local(peer: str) -> None:
    """Hinter einem Proxy spricht nur dieser mit devc, und er läuft auf demselben Rechner."""
    try:
        address = ipaddress.ip_address(peer)
        local = (getattr(address, "ipv4_mapped", None) or address).is_loopback
    except ValueError:
        local = False
    if not local:
        raise SecurityError("devc läuft hinter einem Proxy und nimmt Anfragen nur von diesem an")


def forwarded_tls(headers, base_domain: str) -> bool:
    """Schema laut Proxy. Unverschlüsselt ist nur `*.localhost` erlaubt (Zugang über einen verschlüsselten SSH-Tunnel)."""
    proto = _first(headers.get("X-Forwarded-Proto"))
    if proto != "https" and not (proto == "http" and is_local_domain(base_domain)):
        raise SecurityError("devc ist hinter dem Proxy nur über HTTPS erreichbar")
    return proto == "https"


def forwarded(peer: str, headers, base_domain: str) -> Forwarded:
    """Wertet `X-Forwarded-Host` und `X-Forwarded-Proto` aus und prüft den Namen gegen `devc.<base_domain>`.

    Die Köpfe gelten nur vom Proxy auf demselben Rechner.
    """
    require_local(peer)
    host = _first(headers.get("X-Forwarded-Host")) or _first(headers.get("Host"))
    if host_of(host) != f"{PROXY_LABEL}.{base_domain}":
        raise SecurityError(f"Host ist nicht erlaubt; devc ist unter {PROXY_LABEL}.{base_domain} erreichbar")
    return Forwarded(host, forwarded_tls(headers, base_domain))


def check_origin(origin: str | None, request: Forwarded) -> None:
    """Ein mitgeschickter Origin muss in Schema, Host und Port die Adresse von devc sein."""
    if origin is None:
        return
    if not origin_matches(origin, request.host, request.tls):
        raise SecurityError("Origin passt nicht zum Host")


# --- Container-Ports über den Proxy (SPEC 8.11) ----------------------------

PROXY_COOKIE = "devc_proxy"
TICKET_PARAM = "devc_ticket"
TICKET_SECONDS = 30
PROXY_PORTS = (1024, 65535)
UPSTREAM_HEADER = "X-Devc-Upstream"
_PROXY_LABEL_RE = re.compile(r"([1-9][0-9]{0,4})-([a-z][a-z0-9-]{1,30})")
MAX_RETURN_URL = 2048


def proxy_name(host: str | None, base_domain: str) -> tuple[int, str] | None:
    """Zerlegt `<port>-<container>.<base_domain>[:port]` in (Port, Containername); None, wenn der Name nicht passt."""
    name = host_of(host)
    suffix = "." + base_domain
    if not base_domain or not name.endswith(suffix):
        return None
    match = _PROXY_LABEL_RE.fullmatch(name[: -len(suffix)])
    if not match or match.group(2).endswith("-"):
        return None
    port = int(match.group(1))
    return (port, match.group(2)) if port <= 65535 else None


def strip_ticket(uri: str) -> tuple[str, str | None]:
    """Entfernt den Ticket-Parameter aus Pfad und Query; liefert (bereinigt, Ticket oder None)."""
    path, mark, query = uri.partition("?")
    ticket, kept = None, []
    for pair in query.split("&") if mark else []:
        key, _, value = pair.partition("=")
        if key == TICKET_PARAM:
            ticket = value
        elif pair:
            kept.append(pair)
    return path + ("?" + "&".join(kept) if kept else ""), ticket


def proxy_return_url(to: str | None, base_domain: str) -> str:
    """Prüft das Ziel des Anmeldewegs streng (sonst offene Weiterleitung) und liefert es bereinigt zurück."""
    problem = UsageError("Ziel der Anmeldung ist keine Adresse eines Container-Ports")
    if not isinstance(to, str) or not to or len(to) > MAX_RETURN_URL or "\\" in to or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in to):
        raise problem
    try:
        parts = urlsplit(to)
        parts.port  # wirft bei einem ungültigen Port
    except ValueError:
        raise problem from None
    if parts.scheme != "https" and not (parts.scheme == "http" and is_local_domain(base_domain)):
        raise problem
    netloc = parts.netloc.lower()
    if "@" in netloc or proxy_name(netloc, base_domain) is None:
        raise problem
    clean, _ = strip_ticket((parts.path or "/") + ("?" + parts.query if parts.query else ""))
    return f"{parts.scheme}://{netloc}{clean}"


class ProxySessions:
    """Tickets und Sitzungen der Proxy-Namen; nur im Speicher. Beide gelten für genau einen Hostnamen."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._tickets: dict[str, tuple[str, float]] = {}
        self._sessions: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def _sweep(self, table: dict) -> None:
        now = self._clock()
        for key in [key for key, (_, expires) in table.items() if expires <= now]:
            del table[key]

    def ticket(self, host: str) -> str:
        """Einmaliges, kurzlebiges Ticket für einen Proxy-Namen (ohne Port)."""
        ticket = secrets.token_urlsafe(32)
        with self._lock:
            self._sweep(self._tickets)
            self._tickets[ticket] = (host, self._clock() + TICKET_SECONDS)
        return ticket

    def redeem(self, ticket: str | None, host: str) -> str | None:
        """Tauscht ein Ticket gegen eine Sitzung. Jedes vorgelegte Ticket ist danach verbraucht."""
        with self._lock:
            entry = self._tickets.pop(ticket or "", None)
            if entry is None or entry[0] != host or entry[1] <= self._clock():
                return None
            self._sweep(self._sessions)
            session = secrets.token_urlsafe(32)
            self._sessions[session] = (host, self._clock() + SESSION_SECONDS)
            return session

    def valid(self, session: str | None, host: str) -> bool:
        with self._lock:
            entry = self._sessions.get(session or "")
            return entry is not None and entry[0] == host and entry[1] > self._clock()

    def clear(self) -> None:
        with self._lock:
            self._tickets.clear()
            self._sessions.clear()
