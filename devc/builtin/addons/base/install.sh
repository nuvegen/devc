#!/usr/bin/env bash
# base: Grundsystem für devc-Container. Läuft als root, MUSS idempotent sein.
set -euo pipefail

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git openssh-client locales tzdata less nano unzip xz-utils bash-completion ripgrep

# Locale
if ! locale -a | grep -qi '^de_DE\.utf8$'; then
  sed -i 's/^# *de_DE.UTF-8/de_DE.UTF-8/' /etc/locale.gen
  locale-gen
fi

# Zeitzone
tz="${DEVC_PARAM_TIMEZONE:-}"
if [ -n "$tz" ] && [ -f "/usr/share/zoneinfo/$tz" ]; then
  ln -sf "/usr/share/zoneinfo/$tz" /etc/localtime
  echo "$tz" > /etc/timezone
fi

# Nutzer dev mit UID/GID 1000. Ein vorhandener Nutzer mit UID 1000 (Ubuntu-Image: ubuntu) wird entfernt.
existing_user="$(getent passwd 1000 | cut -d: -f1 || true)"
if [ -n "$existing_user" ] && [ "$existing_user" != "dev" ]; then
  userdel "$existing_user" || true
fi
existing_group="$(getent group 1000 | cut -d: -f1 || true)"
if [ -n "$existing_group" ] && [ "$existing_group" != "dev" ]; then
  groupdel "$existing_group" || true
fi
getent group dev >/dev/null || groupadd -g 1000 dev
id dev >/dev/null 2>&1 || useradd -M -u 1000 -g 1000 -d /home/dev -s /bin/bash dev

# /home/dev ist das Home-Volume. Skeleton nur beim ersten Mal kopieren.
mkdir -p /home/dev
if [ ! -f /home/dev/.bashrc ]; then
  cp -rT /etc/skel /home/dev
fi
chown 1000:1000 /home/dev
chmod 0750 /home/dev
install -d -o 1000 -g 1000 -m 0755 /home/dev/.local /home/dev/.local/bin /home/dev/.config

# /work: nur übernehmen, wenn es noch root gehört (frisches Volume).
# Ein Shared Folder behält seinen Besitzer vom Host.
mkdir -p /work
if [ "$(stat -c %u /work)" = "0" ]; then
  chown 1000:1000 /work
  chmod 0755 /work
fi

# PATH für Nutzer-Tools
cat > /etc/profile.d/devc-base.sh <<'EOF'
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) PATH="$HOME/.local/bin:$PATH" ;;
esac
export PATH
EOF
chmod 0644 /etc/profile.d/devc-base.sh

# sudo
if [ "${DEVC_PARAM_SUDO:-false}" = "true" ]; then
  apt-get install -y --no-install-recommends sudo
  echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/devc-dev
  chmod 0440 /etc/sudoers.d/devc-dev
else
  rm -f /etc/sudoers.d/devc-dev
fi

# git-Identität
if [ -n "${DEVC_PARAM_GIT_NAME:-}" ]; then
  runuser -u dev -- env HOME=/home/dev git config --global user.name "$DEVC_PARAM_GIT_NAME"
fi
if [ -n "${DEVC_PARAM_GIT_EMAIL:-}" ]; then
  runuser -u dev -- env HOME=/home/dev git config --global user.email "$DEVC_PARAM_GIT_EMAIL"
fi
