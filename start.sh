#!/usr/bin/env bash
# devc starten: als Dienst, wenn es einen gibt, sonst als Prozess im Hintergrund.
# Aufruf: ./start.sh [Optionen für "devc serve", zum Beispiel --config <pfad>]
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/devc-common.sh"

if has_service; then
  [ $# -eq 0 ] || die "devc läuft hier als Dienst; Optionen gehören in die config.toml."
  if service_active; then
    say "devc läuft schon als Dienst (devc.service)."
  else
    as_root systemctl start devc.service
    say "devc als Dienst gestartet (devc.service). Zustand: systemctl status devc"
  fi
  exit 0
fi

pids="$(serve_pids)"
if [ -n "$pids" ]; then
  say "devc läuft schon als Prozess (PID $(echo $pids))."
  exit 0
fi

if [ -f "$here/devc/__main__.py" ]; then
  cmd=(python3 -m devc)
elif [ -f "$here/devc.pyz" ]; then
  cmd=(python3 "$here/devc.pyz")
elif [ -f "$here/dist/devc.pyz" ]; then
  cmd=(python3 "$here/dist/devc.pyz")
else
  die "Weder der Quelltext (devc/) noch devc.pyz liegt in $here."
fi

mkdir -p "$state"
cd "$here"
nohup "${cmd[@]}" serve "$@" > "$logfile" 2>&1 < /dev/null &
pid=$!
echo "$pid" > "$pidfile"

# Kurz warten: Ein Fehler beim Start (Port belegt, Konfiguration falsch) zeigt sich sofort.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  kill -0 "$pid" 2>/dev/null || break
  grep -q 'läuft auf' "$logfile" 2>/dev/null && break
  sleep 0.3
done
if ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$pidfile"
  say "devc ist nicht gestartet. Die Meldung:"
  cat "$logfile" >&2
  exit 1
fi

say "devc gestartet (PID $pid)."
grep -m1 'läuft auf' "$logfile" || true
say "Log: $logfile"
say "Token: ${cmd[*]} token show"
