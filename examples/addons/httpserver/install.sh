#!/usr/bin/env bash
# httpserver: nur das Ausgabeverzeichnis anlegen; der Server läuft über die Aktion start.
set -euo pipefail

dir="${DEVC_PARAM_DIR:-/work/out}"
install -d -o 1000 -g 1000 -m 0755 "$dir"
