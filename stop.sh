#!/usr/bin/env bash
# devc stoppen: den Dienst, wenn es einen gibt, sonst den Prozess im Hintergrund.
# Offene Terminal-Sitzungen in der Oberfläche enden dabei. Die Container laufen weiter.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/devc-common.sh"

if has_service; then
  if service_active; then
    as_root systemctl stop devc.service
    say "devc gestoppt (devc.service). Die Container laufen weiter."
  else
    say "devc läuft nicht (devc.service ist gestoppt)."
  fi
  exit 0
fi

pids="$(serve_pids)"
if [ -z "$pids" ]; then
  rm -f "$pidfile"
  say "devc läuft nicht."
  exit 0
fi

kill $pids
for _ in $(seq 50); do
  alive=""
  for pid in $pids; do kill -0 "$pid" 2>/dev/null && alive="$alive $pid"; done
  [ -z "$alive" ] && break
  sleep 0.2
done
[ -z "$alive" ] || die "devc beendet sich nicht (PID$alive). Mit Gewalt: kill -9$alive"
rm -f "$pidfile"
say "devc gestoppt (PID $(echo $pids)). Die Container laufen weiter."
