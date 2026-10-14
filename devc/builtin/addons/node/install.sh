#!/usr/bin/env bash
# node: Node.js aus NodeSource. Läuft als root, idempotent.
set -euo pipefail

v="${DEVC_PARAM_VERSION:-22}"

if ! command -v node >/dev/null 2>&1 || ! node --version | grep -q "^v${v}\."; then
  curl -fsSL "https://deb.nodesource.com/setup_${v}.x" | bash -
  apt-get install -y nodejs
fi
