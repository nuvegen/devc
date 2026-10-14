#!/usr/bin/env bash
# packages: Pakete aus der Liste des Containers. Läuft als root, idempotent.
set -euo pipefail

# devc prüft die Liste schon beim Speichern. Hier noch einmal: Nur Paketnamen erreichen apt-get, nie eine Option.
names=()
for name in ${DEVC_PARAM_APT:-}; do
  if [[ ! "$name" =~ ^[a-z0-9][a-z0-9+.-]+$ ]]; then
    echo "packages: »$name« ist kein Paketname" >&2
    exit 1
  fi
  names+=("$name")
done

if [ "${#names[@]}" -eq 0 ]; then
  echo "packages: Die Liste ist leer, nichts zu tun."
  exit 0
fi

options=()
if [ "${DEVC_PARAM_RECOMMENDS:-false}" != "true" ]; then
  options+=(--no-install-recommends)
fi

apt-get update
apt-get install -y "${options[@]}" -- "${names[@]}"
