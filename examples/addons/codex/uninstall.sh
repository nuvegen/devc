#!/usr/bin/env bash
# codex entfernen: Programm und Startskript. Unterhaltungen und Einstellungen in ~/.codex und ~/.local/state bleiben.
set -euo pipefail
rm -f /usr/local/bin/codex
rm -rf /usr/local/lib/devc-codex
