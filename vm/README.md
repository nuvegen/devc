# vm/ – devc in einer eigenen VM

Eine VM ist ein Mandant: eigenes LXD, eigenes devc, eigener Token. In der VM laufen LXD, devc (als Dienst) und Caddy; nach außen zeigt die VM nur Caddy.

Die Kurzfassung in fünf Schritten steht im [`README.md`](../README.md) der Projektwurzel (Schnellstart). Das Bauen der Dateien erklärt [`docs/BAUEN.md`](../docs/BAUEN.md). Hier stehen die Einzelheiten.

| Datei | Zweck |
|---|---|
| `install.sh` | Richtet eine frische Ubuntu-24.04-VM ein. Wiederholbar, löscht nichts. |
| `cloud-init.yaml` | Vorlage für den ersten Start: legt `install.sh` und `devc.pyz` in die VM und führt `install.sh` aus. |
| `make-user-data.py` | Füllt die Vorlage und schreibt die fertige cloud-init-Datei (`user-data`). |

## Was die VM braucht

| | Mindestens | Empfohlen |
|---|---|---|
| CPU | 2 | 4 |
| RAM | 4 GiB | 8 GiB oder mehr |
| Platte | 20 GiB | 60 GiB oder mehr |

- Ubuntu 24.04 (Cloud-Image), Netzzugang beim ersten Start (LXD, Caddy, Container-Images).
- Die Templates von devc verlangen je Container 4 CPUs und 8 GiB RAM. Das sind Obergrenzen; ein Container bekommt nie mehr, als die VM hat.
- Die Container liegen direkt auf der Platte der VM (Speicherpool vom Typ `dir`) und teilen sich deren freien Platz. Mit `--pool-driver btrfs` entsteht stattdessen eine Pool-Datei fester Größe (70 % des freien Platzes, anders mit `--pool-size`); Snapshots sind dann schneller, kleine Schreibvorgänge langsamer. Vergleich: Handbuch, Abschnitt 16.

## 1. cloud-init-Datei erzeugen

Auf dem Rechner mit dem devc-Quelltext:

```bash
python3 build.py                                  # erzeugt dist/devc.pyz
python3 vm/make-user-data.py --ssh-key ~/.ssh/id_ed25519.pub -o user-data
```

| Option | Bedeutung | Standard |
|---|---|---|
| `--base-domain` | Basisdomain. GUI unter `devc.<basisdomain>`, Container-Ports unter `<port>-<container>.<basisdomain>` | `localhost` |
| `--public-port` | Port, den der Browser benutzt, wenn es nicht 443 ist (zum Beispiel 8443 bei einem Tunnel) | `443` |
| `--http-port` | Zusätzlich unverschlüsselt auf diesem Port. Nur für Basisdomains unter `localhost`, gedacht für den Zugriff über einen SSH-Tunnel | aus |
| `--pool-driver` | Speicherpool für die Container: `dir` oder `btrfs` | `dir` |
| `--pool-size` | Nur mit `--pool-driver btrfs`: Größe des Pools, zum Beispiel `40GiB` | 70 % des freien Platzes |
| `--ssh-key` | Öffentlicher SSH-Schlüssel für den Nutzer `ubuntu` (mehrfach möglich) | keiner |

Für den Zugriff über einen SSH-Tunnel (Entwicklung, Heimnetz):

```bash
python3 vm/make-user-data.py --public-port 8443 --http-port 8080 --ssh-key ~/.ssh/id_ed25519.pub -o user-data
```

Die Datei enthält `install.sh` und `devc.pyz` und ist etwa 300 KiB groß. Sie ist eine Kopie vom Zeitpunkt des Erzeugens: Nach jeder Änderung an devc (`python3 build.py`), an `install.sh` oder an `cloud-init.yaml` muss sie neu erzeugt werden, sonst bekommen neue VMs den alten Stand. Sie enthält kein Geheimnis; der Token entsteht erst in der VM.

## 2. VM anlegen

### Mit LXD (VM auf einem Rechner mit LXD)

```bash
lxc init ubuntu:24.04 devc-acme --vm -c limits.cpu=4 -c limits.memory=8GiB -d root,size=60GiB < /dev/null
lxc config set devc-acme user.user-data - < user-data
lxc start devc-acme
```

Die Datei ist zu groß für die Befehlszeile; darum `lxc init` und danach `lxc config set … -`, das von der Standardeingabe liest, statt `lxc launch -c user.user-data=…`.

Schneller läuft die VM auf einem eigenen Speicherpool vom Typ `dir` (einmal anlegen, dann `-s devc-vms` bei `lxc init`):

```bash
lxc storage create devc-vms dir
lxc init ubuntu:24.04 devc-acme --vm -s devc-vms -c limits.cpu=4 -c limits.memory=8GiB -d root,size=60GiB < /dev/null
```

Warum, und was das für Snapshots der VM heißt: Handbuch, Abschnitt 15, »Empfohlen: ein eigener Speicherpool für die VMs«.

### Mit virt-install (Linux mit KVM und libvirt)

```bash
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
qemu-img create -f qcow2 -F qcow2 -b "$PWD/noble-server-cloudimg-amd64.img" devc-acme.qcow2 60G
virt-install --name devc-acme --memory 8192 --vcpus 4 \
  --disk path=devc-acme.qcow2,format=qcow2 \
  --cloud-init user-data=user-data \
  --os-variant ubuntu24.04 --network network=default --import --noautoconsole
```

## 3. Warten und Token holen

Der erste Start dauert einige Minuten. Fertig ist die VM, wenn cloud-init es meldet:

```bash
lxc exec devc-acme -- cloud-init status --wait        # oder per SSH: ssh ubuntu@<vm> cloud-init status --wait
lxc exec devc-acme -- tail -5 /var/log/cloud-init-output.log
```

Die letzte Zeile nennt die Adresse der GUI. Den Token gibt es nur auf der Konsole der VM:

```bash
lxc exec devc-acme -- devc token show                 # oder per SSH: ssh ubuntu@<vm> devc token show
```

`devc` läuft in der VM immer als Nutzer `devc`, egal wer es aufruft. Alle Befehle aus der Anleitung (`docs/ANLEITUNG.md`) gehen damit.

## 4. Im Browser öffnen

Mit `--base-domain localhost` über einen SSH-Tunnel zum Rechner, auf dem die VM läuft (`<vm>` ist die Adresse der VM aus `lxc list`):

```bash
ssh -N -L 8080:<vm>:8080 <nutzer>@<rechner>           # dann http://devc.localhost:8080/
ssh -N -L 8443:<vm>:443 <nutzer>@<rechner>            # dann https://devc.localhost:8443/
```

Läuft die VM auf deinem eigenen Rechner, brauchst du ebenfalls eine Weiterleitung, weil `devc.localhost` immer auf den Rechner selbst zeigt:

```bash
socat TCP-LISTEN:8080,bind=127.0.0.1,fork,reuseaddr TCP:<vm>:8080      # dann http://devc.localhost:8080/
```

Wer die Adresse der VM direkt aufruft (`http://<vm>/` oder `http://<vm>:8080/`), sieht eine Hinweisseite mit dem Link, dem passenden Befehl und dem Weg zum Token. Mehr dazu im Handbuch, Abschnitt 15, Schritt 4.

Bei HTTPS warnt der Browser, weil Caddy das Zertifikat aus einer eigenen CA ausstellt. Die CA liegt in der VM unter `/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`.

## Echtes Zertifikat statt eigener CA

In der VM in `/home/devc/.config/devc/config.toml`:

```toml
[server]
base_domain = "acme.dev.example.com"
public_port = 443
behind_proxy = true
tls_cert = "/etc/devc/tls/wildcard.pem"      # gilt für *.acme.dev.example.com
tls_key = "/etc/devc/tls/wildcard.key"       # lesbar für den Nutzer caddy
```

Danach `sudo /opt/devc/install/install.sh`: Das Skript erzeugt das Caddyfile neu und lädt Caddy. Das Zertifikat selbst bringst du mit; devc erzeugt es nicht.

## Später

| Aufgabe | Befehl in der VM |
|---|---|
| devc aktualisieren | In der Oberfläche: Kachel »VM«, »devc aktualisieren …«, neues `devc.pyz` hochladen. Auf der Konsole: `devc vm update <devc.pyz>`. Von Hand: neues `devc.pyz` nach `/opt/devc/install/devc.pyz` kopieren, dann `sudo /opt/devc/install/install.sh`. Der Dienst startet neu; offene Terminal-Sitzungen enden. |
| Auch `vm/install.sh` ist neu | Die neue Fassung in der VM einmal mit `sudo` aufrufen. Sie legt sich selbst nach `/opt/devc/install/install.sh`. Das Update aus der Oberfläche tauscht nur `devc.pyz`. |
| Einstellungen ändern | `config.toml` bearbeiten, dann `sudo /opt/devc/install/install.sh` |
| Unverschlüsselten Port ändern oder abschalten | `DEVC_HTTP_PORT` in `/etc/devc/install.conf` ändern oder leeren, dann `install.sh` |
| Zustand prüfen | `devc doctor`, `systemctl status devc caddy`, `journalctl -u devc` |
| Neuen Token | `devc token rotate` |

`install.sh` übernimmt `DEVC_BASE_DOMAIN` und `DEVC_PUBLIC_PORT` nur, solange es noch keine `config.toml` gibt. Danach gilt die `config.toml`.

## Was install.sh einrichtet

1. LXD als Snap (Kanal `6/stable`), Speicherpool `default` (`dir`, auf Wunsch `btrfs`), Netz `lxdbr0`. Einen vorhandenen Pool lässt das Skript, wie er ist.
2. Caddy in einer festen Version als `.deb` von GitHub, mit geprüfter SHA-512-Summe. Warum nicht aus einer Paketquelle: Die Paketquelle von Caddy war nicht erreichbar, und Ubuntu 24.04 liefert nur Caddy 2.6. Neue Version: `CADDY_VERSION` in `install.sh` ändern.
3. Nutzer `devc` (Gruppe `lxd`), `devc.pyz` unter `/opt/devc/`, Aufruf `devc` unter `/usr/local/bin/`.
4. `config.toml` mit `base_domain`, `public_port` und `behind_proxy = true`; `devc init`.
5. `/etc/caddy/Caddyfile` aus `devc proxy config`. Ein vorhandenes fremdes Caddyfile bleibt als `Caddyfile.vor-devc` erhalten.
6. Dienst `devc.service` (Nutzer `devc`, lauscht auf `127.0.0.1:8740`, startet nach einem Absturz neu).
7. `/opt/devc/install/` mit `install.sh` und dem eingerichteten `devc.pyz`: die Quelle für spätere Updates.
8. Terminal der VM (Kachel »VM«, `devc vm shell`): `/opt/devc/vmshell/bin` verweist auf die erlaubten Befehle (Liste `VM_TOOLS` in `install.sh`), `/opt/devc/vmshell/bashrc` richtet die beschränkte Bash ein. Der Nutzer `devc` kommt in die Gruppe `systemd-journal`, damit `journalctl` etwas zeigt.
9. Update aus der Oberfläche: `/opt/devc/install/devc-update` (root) und `/etc/sudoers.d/devc-update`. Die Regel erlaubt `devc` genau dieses Skript, ohne Argumente. Es holt das geprüfte `devc.pyz` aus `/var/lib/devc/update/` und startet `install.sh` als eigene Einheit von systemd; Ausgabe: `journalctl -u 'devc-update-*'`.

Nach einem Neustart der VM laufen LXD, Caddy und devc von selbst wieder. Die Container bleiben gestoppt, bis du sie startest (GUI oder `devc start <name>`).

Von außen erreichbar ist nur Caddy (Ports 80 und 443, dazu der unverschlüsselte Port, falls gesetzt). Port 8740 lauscht nur in der VM.
