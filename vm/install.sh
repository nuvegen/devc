#!/usr/bin/env bash
# devc in einer frischen Ubuntu-24.04-VM einrichten: LXD, Caddy, Nutzer devc, devc als Dienst.
# Als root ausführen. Wiederholbar: ein zweiter Lauf ändert nur, was fehlt oder neu ist
# (neues devc.pyz, neues Caddyfile), und löscht nichts.
#
# Einstellungen (Umgebung oder /etc/devc/install.conf, Umgebung gewinnt):
#   DEVC_BASE_DOMAIN   Basisdomain, Standard "localhost". Gilt nur, solange es noch keine config.toml gibt.
#   DEVC_PUBLIC_PORT   Port, den der Browser benutzt, Standard 443. Gilt nur für eine neue config.toml.
#   DEVC_HTTP_PORT     zusätzlich unverschlüsselt auf diesem Port (nur Basisdomains unter localhost), Standard leer.
#   DEVC_POOL_DRIVER   Art des LXD-Speicherpools für die Container: "dir" (Standard) legt sie direkt im Dateisystem
#                      der VM ab; "btrfs" legt eine Pool-Datei fester Größe an. Gilt nur, solange es den Pool nicht gibt.
#   DEVC_POOL_SIZE     nur mit btrfs: Größe des Pools, z. B. "30GiB". Standard: 70 % des freien Platzes auf /.
#   DEVC_PYZ           Pfad zu devc.pyz. Standard: neben diesem Skript, sonst ../dist/devc.pyz.
set -euo pipefail

# Caddy kommt als .deb von den GitHub-Releases (Ubuntu 24.04 liefert nur 2.6). Bei einer neuen Version beide Zeilen ändern.
CADDY_VERSION="2.11.7"
LXD_CHANNEL="6/stable"

say() { printf '== %s\n' "$*"; }
die() { printf 'FEHLER: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "install.sh muss als root laufen (sudo)."
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Einstellungen ---------------------------------------------------------------
conf=/etc/devc/install.conf
env_base="${DEVC_BASE_DOMAIN-}" env_port="${DEVC_PUBLIC_PORT-}" env_http="${DEVC_HTTP_PORT-}" env_pool="${DEVC_POOL_SIZE-}" env_driver="${DEVC_POOL_DRIVER-}"
if [ -f "$conf" ]; then
  # nur KEY=VALUE-Zeilen mit bekannten Namen übernehmen, nichts ausführen
  while IFS='=' read -r key value; do
    case "$key" in
      DEVC_BASE_DOMAIN|DEVC_PUBLIC_PORT|DEVC_HTTP_PORT|DEVC_POOL_SIZE|DEVC_POOL_DRIVER) printf -v "$key" '%s' "$value" ;;
    esac
  done < "$conf"
fi
DEVC_BASE_DOMAIN="${env_base:-${DEVC_BASE_DOMAIN:-localhost}}"
DEVC_PUBLIC_PORT="${env_port:-${DEVC_PUBLIC_PORT:-443}}"
DEVC_HTTP_PORT="${env_http:-${DEVC_HTTP_PORT:-}}"
DEVC_POOL_SIZE="${env_pool:-${DEVC_POOL_SIZE:-}}"
DEVC_POOL_DRIVER="${env_driver:-${DEVC_POOL_DRIVER:-dir}}"
[[ "$DEVC_BASE_DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "DEVC_BASE_DOMAIN ist kein DNS-Name: $DEVC_BASE_DOMAIN"
[[ "$DEVC_PUBLIC_PORT" =~ ^[0-9]{1,5}$ ]] || die "DEVC_PUBLIC_PORT ist keine Portnummer: $DEVC_PUBLIC_PORT"
[[ -z "$DEVC_HTTP_PORT" || "$DEVC_HTTP_PORT" =~ ^[0-9]{1,5}$ ]] || die "DEVC_HTTP_PORT ist keine Portnummer: $DEVC_HTTP_PORT"
[[ -z "$DEVC_POOL_SIZE" || "$DEVC_POOL_SIZE" =~ ^[0-9]+GiB$ ]] || die "DEVC_POOL_SIZE braucht die Form 30GiB: $DEVC_POOL_SIZE"
[[ "$DEVC_POOL_DRIVER" = dir || "$DEVC_POOL_DRIVER" = btrfs ]] || die "DEVC_POOL_DRIVER muss dir oder btrfs sein: $DEVC_POOL_DRIVER"
[[ -z "$DEVC_POOL_SIZE" || "$DEVC_POOL_DRIVER" = btrfs ]] || die "DEVC_POOL_SIZE gibt es nur mit DEVC_POOL_DRIVER=btrfs; ein dir-Pool hat keine eigene Größe."

pyz="${DEVC_PYZ:-}"
if [ -z "$pyz" ]; then
  for candidate in "$here/devc.pyz" "$here/../dist/devc.pyz"; do
    [ -f "$candidate" ] && pyz="$candidate" && break
  done
fi
[ -n "$pyz" ] && [ -f "$pyz" ] || die "devc.pyz nicht gefunden; DEVC_PYZ setzen oder neben install.sh legen."

. /etc/os-release
[ "${ID:-}" = "ubuntu" ] && [ "${VERSION_ID:-}" = "24.04" ] || say "Hinweis: geprüft ist nur Ubuntu 24.04, hier läuft ${PRETTY_NAME:-unbekannt}."

install -d -m 0755 /etc/devc
printf 'DEVC_BASE_DOMAIN=%s\nDEVC_PUBLIC_PORT=%s\nDEVC_HTTP_PORT=%s\nDEVC_POOL_DRIVER=%s\nDEVC_POOL_SIZE=%s\n' \
  "$DEVC_BASE_DOMAIN" "$DEVC_PUBLIC_PORT" "$DEVC_HTTP_PORT" "$DEVC_POOL_DRIVER" "$DEVC_POOL_SIZE" > "$conf"

# --- Pakete ----------------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
need=()
for package in python3 curl ca-certificates snapd; do
  dpkg -s "$package" >/dev/null 2>&1 || need+=("$package")
done
if [ "${#need[@]}" -gt 0 ]; then
  say "Pakete installieren: ${need[*]}"
  apt-get update -qq
  apt-get install -y -qq "${need[@]}" >/dev/null
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "devc braucht Python 3.11 oder neuer."

# --- LXD -------------------------------------------------------------------------
if ! snap list lxd >/dev/null 2>&1; then
  say "LXD installieren (Snap, Kanal $LXD_CHANNEL)"
  snap install lxd --channel="$LXD_CHANNEL" >/dev/null
fi
lxc=/snap/bin/lxc
/snap/bin/lxd waitready --timeout 180
if ! "$lxc" storage show default >/dev/null 2>&1; then
  free_gib=$(( $(df --output=avail -B1G / | tail -1) ))
  if [ "$DEVC_POOL_DRIVER" = btrfs ]; then
    [ -n "$DEVC_POOL_SIZE" ] || DEVC_POOL_SIZE="$(( free_gib * 70 / 100 ))GiB"
    [ "${DEVC_POOL_SIZE%GiB}" -ge 5 ] || die "Zu wenig Platz für den Speicherpool ($DEVC_POOL_SIZE); die Platte der VM vergrößern."
    pool_config="  config:
    size: $DEVC_POOL_SIZE"
    say "LXD einrichten: Pool default (btrfs, $DEVC_POOL_SIZE), Netz lxdbr0"
  else
    # dir: die Container liegen direkt im Dateisystem der VM und teilen sich deren freien Platz
    [ "$free_gib" -ge 7 ] || die "Zu wenig Platz für die Container (frei: $free_gib GiB); die Platte der VM vergrößern."
    pool_config="  config: {}"
    say "LXD einrichten: Pool default (dir), Netz lxdbr0"
  fi
  /snap/bin/lxd init --preseed <<PRESEED
networks:
- name: lxdbr0
  type: bridge
  config:
    ipv4.address: auto
    ipv4.nat: "true"
    ipv6.address: none
storage_pools:
- name: default
  driver: $DEVC_POOL_DRIVER
$pool_config
profiles:
- name: default
  devices:
    root:
      path: /
      pool: default
      type: disk
    eth0:
      name: eth0
      network: lxdbr0
      type: nic
PRESEED
fi

# --- Caddy -----------------------------------------------------------------------
have_caddy="$(caddy version 2>/dev/null | sed -n 's/^v\([0-9.]*\).*/\1/p' || true)"
if [ "$have_caddy" != "$CADDY_VERSION" ]; then
  say "Caddy $CADDY_VERSION installieren (vorhanden: ${have_caddy:-keins})"
  arch="$(dpkg --print-architecture)"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  base="https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION"
  deb="caddy_${CADDY_VERSION}_linux_${arch}.deb"
  curl -fsSL -o "$tmp/$deb" "$base/$deb"
  curl -fsSL -o "$tmp/sums" "$base/caddy_${CADDY_VERSION}_checksums.txt"
  (cd "$tmp" && grep " $deb\$" sums | sha512sum -c - >/dev/null) || die "Prüfsumme von $deb stimmt nicht."
  dpkg -i "$tmp/$deb" >/dev/null
fi

# --- Nutzer und Programm ---------------------------------------------------------
if ! id devc >/dev/null 2>&1; then
  say "Nutzer devc anlegen"
  useradd --create-home --shell /bin/bash devc
fi
# lxd: Container verwalten. systemd-journal: Logs lesen (journalctl im VM-Terminal), ändern kann devc damit nichts.
usermod -aG lxd,systemd-journal devc
install -d -m 0755 /opt/devc
if ! cmp -s "$pyz" /opt/devc/devc.pyz; then
  say "devc nach /opt/devc/devc.pyz kopieren"
  install -m 0644 "$pyz" /opt/devc/devc.pyz
  restart_devc=1
fi
# `devc` auf der Konsole der VM läuft immer als Nutzer devc, egal wer es aufruft.
cat > /usr/local/bin/devc <<'WRAPPER'
#!/bin/sh
export PATH="/snap/bin:$PATH"
if [ "$(id -un)" = "devc" ]; then
  exec /usr/bin/python3 /opt/devc/devc.pyz "$@"
fi
# Im Verzeichnis des Aufrufers bleiben, wenn devc es lesen darf (für push, pull, addon import); sonst nach /.
# /home/devc geht nicht: Andere Nutzer dürfen es nicht betreten.
sudo -H -u devc sh -c 'test -r . && test -x .' 2>/dev/null || cd /
exec sudo -H -u devc env PATH="$PATH" /usr/bin/python3 /opt/devc/devc.pyz "$@"
WRAPPER
chmod 0755 /usr/local/bin/devc

# --- Quelle für spätere Updates --------------------------------------------------
# Unter /opt/devc/install liegen immer dieses Skript und das eingerichtete devc.pyz. Das Update aus der
# Oberfläche (»devc aktualisieren«, `devc vm update`) tauscht dort devc.pyz aus und ruft das Skript erneut auf.
install -d -m 0755 /opt/devc/install
if [ "$here" != /opt/devc/install ]; then
  cmp -s "${BASH_SOURCE[0]}" /opt/devc/install/install.sh || install -m 0755 "${BASH_SOURCE[0]}" /opt/devc/install/install.sh
  cmp -s "$pyz" /opt/devc/install/devc.pyz || install -m 0644 "$pyz" /opt/devc/install/devc.pyz
fi
install -d -m 0755 /var/lib/devc
install -d -m 0700 -o devc -g devc /var/lib/devc/update
cat > /opt/devc/install/devc-update <<'UPDATE'
#!/bin/sh
# Richtet ein neues devc.pyz ein. devc startet dieses Skript per sudo (eine Regel in /etc/sudoers.d/devc-update),
# nachdem es die Datei geprüft und unter /var/lib/devc/update bereitgelegt hat. Ohne Argumente.
set -eu
stage=/var/lib/devc/update/devc.pyz
target=/opt/devc/install/devc.pyz
# Gelesen wird als Nutzer devc: So kommt hier nie eine Datei an, die devc nicht selbst lesen darf.
runuser -u devc -- test -s "$stage" || { echo "Die bereitgelegte Datei fehlt oder ist leer: $stage" >&2; exit 1; }
runuser -u devc -- cat "$stage" > "$target.new"
chmod 0644 "$target.new"
mv "$target.new" "$target"
# install.sh startet den Dienst devc neu und darf nicht mit ihm enden: als eigene Einheit von systemd.
# Ausgabe: journalctl -u 'devc-update-*'
exec systemd-run --quiet --collect --unit "devc-update-$(date +%s)" /opt/devc/install/install.sh
UPDATE
chmod 0755 /opt/devc/install/devc-update
new="$(mktemp)"
printf '%s\n' '# devc darf genau dieses eine Skript als root starten: das Update von devc (vm/install.sh).' \
  'devc ALL=(root) NOPASSWD: /opt/devc/install/devc-update ""' > "$new"
visudo -cf "$new" >/dev/null || die "Die sudo-Regel für das Update ist ungültig."
cmp -s "$new" /etc/sudoers.d/devc-update || install -m 0440 "$new" /etc/sudoers.d/devc-update
rm -f "$new"

# --- VM-Terminal -----------------------------------------------------------------
# Beschränkte Shell für die Kachel »VM« und `devc vm shell`: Bash im Modus --restricted mit einem Ordner,
# der nur die erlaubten Befehle enthält. Das ist ein Geländer gegen Versehen, keine Sicherheitsgrenze:
# Wer lxc ausführen darf, kann in der VM alles.
vmshell=/opt/devc/vmshell
VM_TOOLS="devc lxc systemctl journalctl df du free ps top uptime ip ss ping curl ls cat less grep head tail wc sort date hostname id clear"
# Das Skript löscht nichts: Fällt ein Befehl später aus der Liste, bleibt sein Verweis, bis ihn jemand von Hand entfernt.
install -d -m 0755 "$vmshell" "$vmshell/bin"
for tool in $VM_TOOLS; do
  case "$tool" in
    devc) target=/usr/local/bin/devc ;;
    lxc) target="$lxc" ;;
    *) target="$(command -v "$tool" || true)" ;;
  esac
  if [ -n "$target" ] && [ -x "$target" ]; then ln -sfn "$target" "$vmshell/bin/$tool"; fi
done
cat > "$vmshell/bashrc" <<'BASHRC'
# Beschränkte Shell der VM für devc (Kachel »VM«, `devc vm shell`). Von vm/install.sh geschrieben.
PATH=/opt/devc/vmshell/bin
export LESSSECURE=1 SYSTEMD_PAGERSECURE=1 PAGER=less
unset -f command_not_found_handle 2>/dev/null
unalias -a
HISTFILE="$HOME/.devc_vm_history"
HISTSIZE=2000
PS1='\[\e[1m\]vm\[\e[0m\]:\h\$ '
commands() { echo "Erlaubte Befehle:"; ls /opt/devc/vmshell/bin; }
echo "Terminal der VM (beschränkt). »commands« zeigt die erlaubten Befehle."
BASHRC
chmod 0644 "$vmshell/bashrc"

config=/home/devc/.config/devc/config.toml
if [ ! -f "$config" ]; then
  say "Konfiguration schreiben: devc.$DEVC_BASE_DOMAIN"
  sudo -H -u devc install -d -m 0755 /home/devc/.config /home/devc/.config/devc
  sudo -H -u devc tee "$config" >/dev/null <<CONFIG
# devc-Konfiguration – von vm/install.sh angelegt. Fehlende Werte sind Defaults (devc doctor, Anleitung Abschnitt 3).
[server]
base_domain = "$DEVC_BASE_DOMAIN"
public_port = $DEVC_PUBLIC_PORT
behind_proxy = true
CONFIG
else
  say "Konfiguration bleibt: $config"
fi
say "devc init"
devc init >/dev/null   # legt Verzeichnisse, Token und das LXD-Projekt an; gibt den Token nicht aus

# --- Caddyfile -------------------------------------------------------------------
new="$(mktemp)"
if [ -n "$DEVC_HTTP_PORT" ]; then
  devc proxy config --http-port "$DEVC_HTTP_PORT" > "$new"
else
  devc proxy config > "$new"
fi
caddy validate --adapter caddyfile --config "$new" >/dev/null 2>&1 || die "Das erzeugte Caddyfile ist ungültig: $new"
if ! cmp -s "$new" /etc/caddy/Caddyfile; then
  if [ -f /etc/caddy/Caddyfile ] && ! head -1 /etc/caddy/Caddyfile | grep -q '^# Caddyfile für devc'; then
    cp --update=none /etc/caddy/Caddyfile /etc/caddy/Caddyfile.vor-devc
  fi
  say "Caddyfile einspielen"
  install -m 0644 "$new" /etc/caddy/Caddyfile
  systemctl enable caddy >/dev/null 2>&1
  systemctl reload-or-restart caddy
fi
rm -f "$new"

# --- Dienst ----------------------------------------------------------------------
unit=/etc/systemd/system/devc.service
new="$(mktemp)"
cat > "$new" <<'UNIT'
[Unit]
Description=devc – Dev-Container-Manager (GUI und API hinter Caddy)
After=network-online.target snap.lxd.daemon.service
Wants=network-online.target

[Service]
User=devc
SupplementaryGroups=lxd
WorkingDirectory=/home/devc
Environment=PATH=/snap/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/python3 /opt/devc/devc.pyz serve
Restart=on-failure
RestartSec=3
# devc beendet auf SIGTERM seine Terminal-Sitzungen geordnet.
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
UNIT
if ! cmp -s "$new" "$unit"; then
  say "Dienst devc.service einrichten"
  install -m 0644 "$new" "$unit"
  systemctl daemon-reload
  restart_devc=1
fi
rm -f "$new"
systemctl enable devc.service >/dev/null 2>&1
if [ "${restart_devc:-0}" = 1 ] || ! systemctl is-active --quiet devc.service; then
  systemctl restart devc.service
fi

# --- Abschluss -------------------------------------------------------------------
for _ in $(seq 1 20); do
  systemctl is-active --quiet devc.service && break
  sleep 0.5
done
systemctl is-active --quiet devc.service || die "devc.service läuft nicht; siehe: journalctl -u devc.service"
port=""; [ "$DEVC_PUBLIC_PORT" = 443 ] || port=":$DEVC_PUBLIC_PORT"
say "Fertig. GUI: https://devc.$DEVC_BASE_DOMAIN$port/"
[ -z "$DEVC_HTTP_PORT" ] || say "Unverschlüsselt (nur über einen Tunnel): http://devc.$DEVC_BASE_DOMAIN:$DEVC_HTTP_PORT/"
say "Token auf der Konsole der VM anzeigen: devc token show"
