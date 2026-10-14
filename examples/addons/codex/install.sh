#!/usr/bin/env bash
# codex: Codex CLI aus dem Release von github.com/openai/codex und ein Startskript mit den Einstellungen des Addons.
# Läuft als root, idempotent.
set -euo pipefail

base_url="${DEVC_PARAM_BASE_URL:-}"
model="${DEVC_PARAM_MODEL:-}"
release="${DEVC_PARAM_RELEASE:-latest}"

# Die Werte landen in einem Skript: hier noch einmal prüfen, nicht nur in devc.
[[ "$base_url" =~ ^(https?://[A-Za-z0-9._:/%+~-]+)?$ ]] || { echo "base_url ist ungültig" >&2; exit 1; }
[[ "$model" =~ ^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$ ]] || { echo "model ist ungültig" >&2; exit 1; }
[[ "$release" =~ ^(latest|[0-9]+\.[0-9]+\.[0-9]+)$ ]] || { echo "release ist ungültig" >&2; exit 1; }

case "$(uname -m)" in
  x86_64) arch=x86_64 ;;
  aarch64 | arm64) arch=aarch64 ;;
  *) echo "Für $(uname -m) gibt es kein Codex CLI" >&2; exit 1 ;;
esac

dir=/usr/local/lib/devc-codex
if [ ! -x "$dir/codex" ] || [ "$(cat "$dir/release" 2>/dev/null || true)" != "$release" ]; then
  file="codex-$arch-unknown-linux-musl"
  if [ "$release" = latest ]; then
    url="https://github.com/openai/codex/releases/latest/download/$file.tar.gz"
  else
    url="https://github.com/openai/codex/releases/download/rust-v$release/$file.tar.gz"
  fi
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL --retry 3 -o "$tmp/codex.tar.gz" "$url"
  tar -xzf "$tmp/codex.tar.gz" -C "$tmp"
  install -D -m 0755 "$tmp/$file" "$dir/codex"
  echo "$release" > "$dir/release"
fi

# Startskript: `codex` im Terminal und die Kachel benutzen dieselben Einstellungen.
# Der API-Key kommt nur aus der Secret-Datei, nie als Wert in dieses Skript.
flags=(-c "model=\"$model\"")
key=OPENAI_API_KEY
if [ -n "$base_url" ]; then
  key=DEVC_CODEX_KEY
  flags+=(-c 'model_provider="devc"' -c 'model_providers.devc.name="devc"' -c "model_providers.devc.base_url=\"$base_url\""
          -c 'model_providers.devc.wire_api="responses"' -c "model_providers.devc.env_key=\"$key\"")
fi
{
  echo '#!/bin/bash'
  echo '# Von devc erzeugt (Addon codex). Änderungen gehen beim nächsten Einrichten verloren.'
  echo "if [ -z \"\${$key:-}\" ] && [ -r /run/devc/secrets/codex.api_key ]; then"
  echo "  $key=\"\$(cat /run/devc/secrets/codex.api_key)\""
  echo "  export $key"
  echo 'fi'
  printf 'exec %q' "$dir/codex"
  printf ' %q' "${flags[@]}"
  echo ' "$@"'
} > /usr/local/bin/codex
chmod 0755 /usr/local/bin/codex
