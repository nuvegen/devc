#!/usr/bin/env bash
# abap: sapcli (pipx), abaplint und Transpiler (npm). Läuft als root, idempotent.
# sapcli: https://github.com/jfilak/sapcli  (Verbindung über SAP_* Umgebungsvariablen)
set -euo pipefail

apt-get install -y --no-install-recommends python3 pipx git

if ! runuser -u dev -- env HOME=/home/dev bash -lc 'command -v sapcli' >/dev/null 2>&1; then
  runuser -u dev -- env HOME=/home/dev pipx install git+https://github.com/jfilak/sapcli.git
fi

npm install -g @abaplint/cli @abaplint/transpiler-cli

# Verbindungsdaten ohne Passwort; Werte maskiert
install -d -o 1000 -g 1000 -m 0700 /home/dev/.config/devc
env_file=/home/dev/.config/devc/sap.env
{
  printf 'export SAP_ASHOST=%q\n' "${DEVC_PARAM_SAP_HOST}"
  printf 'export SAP_PORT=%q\n' "${DEVC_PARAM_SAP_PORT}"
  if [ "${DEVC_PARAM_SAP_SSL:-true}" = "true" ]; then
    echo 'export SAP_SSL=yes'
  else
    echo 'export SAP_SSL=no'
  fi
  printf 'export SAP_CLIENT=%q\n' "${DEVC_PARAM_SAP_CLIENT}"
  printf 'export SAP_USER=%q\n' "${DEVC_PARAM_SAP_USER}"
} > "$env_file"
chown 1000:1000 "$env_file"
chmod 0600 "$env_file"

# Login-Shells: Verbindungsdaten laden, Passwort nur aus der Secret-Datei
cat > /etc/profile.d/devc-abap.sh <<'EOF'
if [ -r "$HOME/.config/devc/sap.env" ]; then
  . "$HOME/.config/devc/sap.env"
fi
if [ -r /run/devc/secrets/abap.sap_password ]; then
  SAP_PASSWORD="$(cat /run/devc/secrets/abap.sap_password)"
  export SAP_PASSWORD
fi
EOF
chmod 0644 /etc/profile.d/devc-abap.sh

# FAQ: devc hat files/faq.tar.gz vor diesem Skript nach /FAQ entpackt ([[archives]]).
# /FAQ gehört root und ist für dev nur lesbar. Eigene Dateien der KI liegen in /FAQ/ki,
# einem Symlink ins Code-Volume, damit sie den Neuaufbau des Containers überleben.
install -d -o 0 -g 0 -m 0755 /FAQ
if [ -L /FAQ/ki ]; then
  rm -f /FAQ/ki
fi
chown -R root:root /FAQ
chmod -R u=rwX,go=rX /FAQ
install -d -o 1000 -g 1000 -m 0755 /work/.devc /work/.devc/faq-ki
if [ ! -e /FAQ/ki ]; then
  ln -s /work/.devc/faq-ki /FAQ/ki
fi
