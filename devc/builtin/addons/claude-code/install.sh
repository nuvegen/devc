#!/usr/bin/env bash
# claude-code: offizieller nativer Installer als Nutzer dev. Läuft als root, idempotent.
# Quelle: https://code.claude.com/docs/en/setup (Stand 10/2026)
set -euo pipefail

channel="${DEVC_PARAM_CHANNEL:-stable}"
provider="${DEVC_PARAM_PROVIDER:-anthropic}"
base_url="${DEVC_PARAM_BASE_URL:-}"
model="${DEVC_PARAM_MODEL:-}"
anthropic_model="${DEVC_PARAM_ANTHROPIC_MODEL:-}"

# Die Werte landen in einem Skript: hier noch einmal prüfen, nicht nur in devc.
[[ "$base_url" =~ ^(https?://[A-Za-z0-9._:/%+~-]+)?$ ]] || { echo "base_url ist ungültig" >&2; exit 1; }
[[ "$model" =~ ^([A-Za-z0-9][]A-Za-z0-9._:/@+[-]*)?$ ]] || { echo "model ist ungültig" >&2; exit 1; }
[[ "$anthropic_model" =~ ^(sonnet|opus|haiku)?$ ]] || { echo "anthropic_model ist ungültig" >&2; exit 1; }
case "$provider" in
  anthropic) base_url=""; model="$anthropic_model" ;;  # Felder des anderen Dienstes gelten hier nicht
  custom) [ -n "$base_url" ] || { echo "Anderer Dienst gewählt, aber keine Adresse (base_url) eingetragen" >&2; exit 1; } ;;
  *) echo "provider ist ungültig" >&2; exit 1 ;;
esac

if [ ! -x /home/dev/.local/bin/claude ]; then
  runuser -u dev -- env HOME=/home/dev bash -c \
    'curl -fsSL https://claude.ai/install.sh | bash -s "$1"' _ "$channel"
fi

# Zugangsdaten nur aus den Secret-Dateien, nie als Wert in dieses Skript.
# Der Abo-Token hat Vorrang: Es wird immer nur eines von beiden gesetzt, damit klar ist, worüber abgerechnet wird.
# Mit einem anderen Dienst gilt dessen Schlüssel auth_token (früher api_key, das wird weiter gelesen).
# Der Abo-Token geht nie an einen anderen Dienst.
{
  if [ -n "$base_url" ]; then
    printf 'export ANTHROPIC_BASE_URL=%q\n' "$base_url"
    echo 'export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1'
    cat <<'EOF'
if [ -r /run/devc/secrets/claude-code.auth_token ]; then
  ANTHROPIC_AUTH_TOKEN="$(cat /run/devc/secrets/claude-code.auth_token)"
  export ANTHROPIC_AUTH_TOKEN
elif [ -r /run/devc/secrets/claude-code.api_key ]; then
  ANTHROPIC_AUTH_TOKEN="$(cat /run/devc/secrets/claude-code.api_key)"
  export ANTHROPIC_AUTH_TOKEN
fi
EOF
  else
    cat <<'EOF'
if [ -r /run/devc/secrets/claude-code.oauth_token ]; then
  CLAUDE_CODE_OAUTH_TOKEN="$(cat /run/devc/secrets/claude-code.oauth_token)"
  export CLAUDE_CODE_OAUTH_TOKEN
elif [ -r /run/devc/secrets/claude-code.api_key ]; then
  ANTHROPIC_API_KEY="$(cat /run/devc/secrets/claude-code.api_key)"
  export ANTHROPIC_API_KEY
fi
EOF
  fi
  if [ "$provider" = anthropic ] && [ -n "$model" ]; then
    printf 'export ANTHROPIC_MODEL=%q\n' "$model"
  elif [ -n "$model" ]; then
    # Claude Code fragt intern nach mehreren Modellen (klein, mittel, groß, Unter-Agenten): alle auf dieses eine legen.
    for name in ANTHROPIC_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL CLAUDE_CODE_SUBAGENT_MODEL; do
      printf 'export %s=%q\n' "$name" "$model"
    done
  fi
} > /etc/profile.d/devc-claude-code.sh
chmod 0644 /etc/profile.d/devc-claude-code.sh

# Mit einem anderen Dienst gibt es keine Anmeldung im Browser: die Begrüßung von Claude Code im Terminal überspringen.
if [ -n "$base_url" ]; then
  runuser -u dev -- env HOME=/home/dev python3 - <<'EOF'
import json, os
path = os.path.expanduser("~/.claude.json")
try:
    data = json.load(open(path, encoding="utf-8"))
except (OSError, ValueError):
    data = {}
if isinstance(data, dict) and data.get("hasCompletedOnboarding") is not True:
    data["hasCompletedOnboarding"] = True
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
EOF
fi
