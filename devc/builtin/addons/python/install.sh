#!/usr/bin/env bash
# python: Python mit venv, pip, pipx und ruff. Läuft als root, idempotent.
set -euo pipefail

v="${DEVC_PARAM_VERSION:-3.12}"

apt-get install -y --no-install-recommends python3 python3-venv python3-pip pipx

if ! command -v "python$v" >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends software-properties-common
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update
  apt-get install -y --no-install-recommends "python$v" "python$v-venv"
fi

if ! runuser -u dev -- env HOME=/home/dev pipx list --short 2>/dev/null | grep -q '^ruff '; then
  runuser -u dev -- env HOME=/home/dev pipx install ruff
fi
