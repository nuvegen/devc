#!/usr/bin/env bash
# info: schreibt Daten zum Container als Markdown auf die Standardausgabe.
# devc zeigt die Ausgabe als Seite der Kachel »Info«. Aufruf: info.sh <prozesse>
set -u
export LC_ALL=C

count="${1:-10}"

# Zahl in KiB als lesbare Größe
size() {
  awk -v k="$1" 'BEGIN {
    if (k >= 1048576) printf "%.1f GiB", k / 1048576
    else if (k >= 1024) printf "%.0f MiB", k / 1024
    else printf "%d KiB", k
  }'
}

# Sekunden als Dauer
duration() {
  awk -v s="$1" 'BEGIN {
    d = int(s / 86400); h = int(s % 86400 / 3600); m = int(s % 3600 / 60)
    if (d > 0) printf "%d Tage %d Std.", d, h
    else if (h > 0) printf "%d Std. %d Min.", h, m
    else printf "%d Min.", m
  }'
}

# Zeichen, die eine Tabellenzeile stören würden
clean() { tr -d '\n' | tr '|`' '/ ' | cut -c1-"${1:-80}"; }

system="$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-}")"
up="$(cut -d. -f1 /proc/uptime 2>/dev/null || echo 0)"
ips="$(hostname -I 2>/dev/null | xargs)"

echo "## Container"
echo
echo "| Was | Wert |"
echo "|---|---|"
echo "| Name | $(hostname | clean) |"
echo "| System | $(printf '%s' "${system:-unbekannt}" | clean) |"
echo "| Kernel | $(uname -r | clean) |"
echo "| Läuft seit | $(duration "$up") |"
echo "| IP-Adresse | ${ips:-keine} |"
echo "| Zeitzone | $(date +%Z) |"
echo "| Stand | $(date '+%d.%m.%Y %H:%M:%S') |"
echo

total="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
avail="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
used=$((total - avail))
echo "## Auslastung"
echo
echo "| Was | Wert |"
echo "|---|---|"
echo "| CPUs | $(nproc) |"
echo "| Last (1, 5, 15 Minuten) | $(cut -d' ' -f1-3 /proc/loadavg) |"
echo "| Arbeitsspeicher | $(size "$used") von $(size "$total") belegt ($((used * 100 / (total > 0 ? total : 1))) %) |"
echo "| Prozesse | $(ps -e --no-headers | wc -l) |"
echo

echo "## Speicherplatz"
echo
echo "| Ort | Belegt | Größe | Frei | Anteil |"
echo "|---|---|---|---|---|"
for place in / /work /home/dev; do
  [ -d "$place" ] || continue
  df -Pk "$place" 2>/dev/null | awk -v p="$place" 'NR == 2 { print $3, $2, $4, $5, p }' | while read -r u s f percent p; do
    echo "| \`$p\` | $(size "$u") | $(size "$s") | $(size "$f") | $percent |"
  done
done
echo

if [ "$count" -gt 0 ] 2>/dev/null; then
  echo "## Prozesse"
  echo
  echo "Die $count Prozesse mit der meisten Rechenzeit."
  echo
  echo "| PID | Nutzer | CPU % | RAM % | Befehl |"
  echo "|---|---|---|---|---|"
  ps -eo pid=,user=,pcpu=,pmem=,args= --sort=-pcpu | head -n "$count" | while read -r pid user cpu mem args; do
    echo "| $pid | $user | $cpu | $mem | \`$(printf '%s' "$args" | clean 70)\` |"
  done
  echo
fi

if [ -r /etc/devc/addons.toml ]; then
  echo "## Addons"
  echo
  echo "| Addon | Version |"
  echo "|---|---|"
  sed -n 's/^"\{0,1\}\([a-z0-9-]*\)"\{0,1\} *= *"\([^"]*\)".*/| \1 | \2 |/p' /etc/devc/addons.toml
fi
