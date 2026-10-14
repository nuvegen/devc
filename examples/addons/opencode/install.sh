#!/usr/bin/env bash
# opencode: offizielles Installationsskript als Nutzer dev. Läuft als root, idempotent.
# Quelle: https://opencode.ai/docs/ (Stand 10/2026)
set -euo pipefail

if ! runuser -u dev -- env HOME=/home/dev bash -lc 'command -v opencode' >/dev/null 2>&1; then
  runuser -u dev -- env HOME=/home/dev bash -c 'curl -fsSL https://opencode.ai/install | bash'
fi

# Installationsort des Skripts in den PATH aufnehmen, falls es nicht ~/.local/bin nutzt
cat > /etc/profile.d/devc-opencode.sh <<'EOF'
if [ -d "$HOME/.opencode/bin" ]; then
  case ":$PATH:" in
    *":$HOME/.opencode/bin:"*) ;;
    *) PATH="$HOME/.opencode/bin:$PATH" ;;
  esac
  export PATH
fi
EOF
chmod 0644 /etc/profile.d/devc-opencode.sh
