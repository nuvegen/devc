# Gemeinsamer Teil von start.sh und stop.sh. Wird eingelesen, nicht ausgeführt.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
state="${XDG_STATE_HOME:-$HOME/.local/state}/devc"
pidfile="$state/serve.pid"
logfile="$state/serve.log"

say() { printf '%s\n' "$*"; }
die() { printf 'Fehler: %s\n' "$*" >&2; exit 1; }

# Gibt es devc als Dienst (eingerichtet von vm/install.sh)?
has_service() { command -v systemctl >/dev/null 2>&1 && systemctl cat devc.service >/dev/null 2>&1; }
service_active() { systemctl is-active --quiet devc.service; }

# Als root direkt, sonst mit sudo.
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

# PIDs von "devc serve", die als Prozess dieses Nutzers laufen (ohne Dienst).
serve_pids() {
  local pid
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q 'devc.* serve'; then
      echo "$pid"
      return
    fi
  fi
  pgrep -u "$(id -u)" -f '^python3[0-9.]* (-m devc|[^ ]*devc\.pyz) serve( |$)' || true
}
