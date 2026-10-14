# devc – Dev-Container-Manager für LXD

devc gibt Coding-Agents wie Claude Code und opencode abgeschlossene Arbeitsumgebungen: Container in LXD. Du legst sie aus Vorlagen an, richtest sie über Addons ein, sicherst sie und bedienst sie im Browser oder auf der Kommandozeile. devc ist ein Python-Programm und braucht nur die Standardbibliothek.

| Ich will … | Lies |
|---|---|
| devc schnell ausprobieren | [Schnellstart](#schnellstart-devc-in-einer-vm) gleich hier |
| die Oberfläche verstehen | [`docs/HILFE.md`](docs/HILFE.md) – Erste Schritte in einfacher Sprache |
| alles nachschlagen | [`docs/ANLEITUNG.md`](docs/ANLEITUNG.md) – das Handbuch |
| devc selbst bauen | [`docs/BAUEN.md`](docs/BAUEN.md) – vom Quelltext zur fertigen Datei |
| einen KI-Agenten mit devc arbeiten lassen | [`docs/KI.md`](docs/KI.md) |

## Schnellstart: devc in einer VM

Der einfachste Weg: Du startest eine frische Ubuntu-VM mit einer fertigen Datei. Die VM richtet sich beim ersten Start selbst ein, mit LXD, devc und allem, was dazugehört. Auf deinem Rechner wird sonst nichts installiert.

Du brauchst einen Rechner mit LXD, der VMs starten kann, und dort 4 CPUs, 8 GiB RAM und 60 GiB Platte für die VM. Kleiner geht auch: mindestens 2 CPUs, 4 GiB RAM und 20 GiB Platte.

**1. Die fertige Datei laden**

Lade die Datei `user-data` aus dem aktuellen Release von devc auf github.com herunter und lege sie auf den Rechner mit LXD.

Gibt es noch kein Release, baust du die Datei in zwei Befehlen selbst: [`docs/BAUEN.md`](docs/BAUEN.md).

**2. Die VM starten**

```bash
lxc init ubuntu:24.04 devc-vm --vm -c limits.cpu=4 -c limits.memory=8GiB -d root,size=60GiB < /dev/null
lxc config set devc-vm user.user-data - < user-data
lxc start devc-vm
```

**3. Warten, bis die VM fertig ist**

```bash
lxc exec devc-vm -- cloud-init status --wait
```

Das dauert einige Minuten. Am Ende steht dort `status: done`.

**4. Den Token holen**

```bash
lxc exec devc-vm -- devc token show
```

Der Token ist das Passwort für die Oberfläche.

**5. Im Browser öffnen**

Die Adresse der VM zeigt `lxc list devc-vm`. Von deinem Arbeitsplatz baust du einen SSH-Tunnel zu dem Rechner, auf dem die VM läuft:

```bash
ssh -N -L 8080:<adresse-der-vm>:8080 <nutzer>@<rechner>
```

Läuft die VM auf deinem eigenen Rechner, nimmst du statt SSH eine Weiterleitung mit `socat`:

```bash
socat TCP-LISTEN:8080,bind=127.0.0.1,fork,reuseaddr TCP:<adresse-der-vm>:8080
```

Dann im Browser <http://devc.localhost:8080/> öffnen und mit dem Token anmelden. Fertig.

Die Adresse der VM direkt im Browser (`http://<adresse-der-vm>/`) zeigt eine Hinweisseite mit genau diesen Befehlen.

Wie es weitergeht, steht in [`docs/HILFE.md`](docs/HILFE.md): auf »Neuer Container« klicken, eine Vorlage wählen, einen Namen vergeben.

Alle Einzelheiten zur VM (andere Größen, eigene Domain, mehrere VMs nebeneinander, aktualisieren) stehen im Handbuch, Abschnitt 15, und in [`vm/README.md`](vm/README.md).

**Tipp für mehr Tempo:** Lege die VM auf einen eigenen Speicherpool vom Typ `dir`. Das ist ein Befehl mehr und steht im Handbuch, Abschnitt 15, »Empfohlen: ein eigener Speicherpool für die VMs«.

## Die Kommandozeile

Alles, was die Oberfläche kann, kann auch der Befehl `devc`. In der VM aus dem Schnellstart rufst du ihn so auf:

```bash
lxc exec devc-vm -- devc list
```

Ein erster Container auf der Kommandozeile:

```bash
devc template list                            # welche Vorlagen gibt es?
devc create py-tools --template pydev         # Container anlegen
devc list                                     # alle Container
devc shell py-tools                           # Shell im Container, als Nutzer dev in /work
```

Im Container liegt der Code unter `/work` und das Home unter `/home/dev`. Beides bleibt erhalten, wenn der Container neu aufgebaut wird.

| Zweck | Befehl |
|---|---|
| Konfiguration ändern | `devc config edit py-tools`, dann `devc config apply py-tools` (`--dry-run` zeigt den Plan) |
| Addon ergänzen | `devc install py-tools node --param node.version=22` |
| Addon-Aktion | `devc action list py-tools`, `devc action run py-tools httpserver.start` |
| Kacheln aus Addons | `devc tile list py-tools`, `devc tile show py-tools info.status` |
| Secret hinterlegen | `devc secret set --shared anthropic=@~/anthropic.key` |
| Snapshot | `devc snapshot create py-tools --label vor-umbau`, `devc snapshot restore py-tools vor-umbau` |
| Backup und Restore | `devc backup py-tools`, `devc restore <archiv> --name py-tools2 --edit` |
| Dateien | `devc push py-tools datei.txt src/`, `devc pull py-tools src/datei.txt` |
| Jobs und Logs | `devc jobs --container py-tools`, `devc jobs --follow <job-id>` |
| Löschen | `devc rm py-tools` (fragt nach; `--keep-data` behält die Daten) |
| Hilfe | `devc help show user`, `devc help show technical` |

Secret-Werte stehen nie auf der Kommandozeile: `devc secret set <name> <slot>` fragt verdeckt, `=@datei` liest eine Datei, `=-` liest die Standardeingabe.

## Ohne VM: direkt auf einem Linux-Rechner

Du kannst devc auch direkt auf einem Rechner mit LXD betreiben. Das ist mehr Handarbeit: LXD richtest du selbst ein, und devc läuft nicht als Dienst.

Du brauchst:

- Linux mit LXD (Snap), dazu einen Speicherpool und ein Netz (Standard: `default` und `lxdbr0`)
- deinen Nutzer in der Gruppe `lxd`
- Python 3.11 oder neuer
- Netzzugang, damit die Container ihre Werkzeuge laden können

devc ist eine einzige Datei, `devc.pyz`. Lade sie aus dem aktuellen Release von devc auf github.com herunter. Gibt es noch kein Release, baust du sie selbst: [`docs/BAUEN.md`](docs/BAUEN.md). Dann in dem Ordner, in dem die Datei liegt:

```bash
alias devc="python3 $PWD/devc.pyz"      # dauerhaft: die Zeile in ~/.bashrc eintragen

devc init       # legt Konfiguration, Token und das LXD-Projekt devcontainers an
devc doctor     # prüft, ob alles passt
```

Die Oberfläche startest du von Hand:

```bash
devc serve                # http://127.0.0.1:8740/
devc token show           # Token für die Anmeldung
```

Im Quelltext liegen dafür zwei Skripte. Sie starten devc im Hintergrund und stoppen es wieder; gibt es devc als Dienst, bedienen sie den Dienst:

```bash
./start.sh                # startet devc, zeigt Adresse und Log
./stop.sh                 # stoppt devc; die Container laufen weiter
```

Sie ist so nur vom Rechner selbst erreichbar. Von einem anderen Rechner hilft ein SSH-Tunnel: `ssh -L 8740:127.0.0.1:8740 <rechner>`. Mehr dazu (TLS, Betrieb hinter einem Proxy, Shared Folders, älteres Python) im Handbuch, Abschnitte 2, 3 und 12.

## Entwicklung

Bauen und Testen in einfachen Schritten: [`docs/BAUEN.md`](docs/BAUEN.md). Die Kurzfassung:

```bash
python3 -m unittest discover -s tests                 # Unit-Tests, ohne LXD und ohne Netz
python3 build.py                                      # erzeugt dist/devc.pyz
python3 vm/make-user-data.py --public-port 8443 --http-port 8080 -o dist/user-data
DEVC_IT=1 python3 -m unittest tests.it_e2e -v         # End-to-End gegen echtes LXD, Container it-e2e*
```

Der End-to-End-Test legt die Container `it-e2e` und `it-e2e2` an und löscht sie am Ende wieder; `DEVC_IT_KEEP=1` lässt sie stehen. `DEVC_IT_PYZ=dist/devc.pyz` prüft die gebaute Datei statt des Quelltexts.

Spezifikation, Plan, Entscheidungen aus dem Bau und die Arbeitsanweisung für Claude Code liegen unter [`build/`](build/README.md).

Aufbau: `devc/core.py` enthält die Use-Cases; CLI (`devc/__main__.py`) und HTTP-Server (`devc/server.py`) rufen nur diese. Allein `devc/providers/lxd.py` ruft `lxc` auf. Mitgelieferte Addons und Templates liegen in `devc/builtin/` und entsprechen `examples/`. Die GUI (`devc/web/`) nutzt ausschließlich die HTTP-API; `xterm.js` 5.3.0 (MIT) liegt in `devc/web/vendor/`. Die Einrichtung einer VM liegt in `vm/`.

Ein Apple-Provider (apple/container) ist nicht Teil von v1; die Schnittstelle dafür steht in `build/SPEC-devc.md`, Abschnitt 8.2.

## Lizenz

devc steht unter der [Apache License 2.0](LICENSE). Du darfst devc frei nutzen, ändern und weitergeben, auch kommerziell. Bei jeder Weitergabe gehören die Datei `LICENSE` und der Hinweis aus [`NOTICE`](NOTICE) dazu (»devc – Copyright 2026 Alexander Ramm«). Geänderte Dateien kennzeichnest du als geändert.

Mitgeliefert ist `xterm.js` unter der MIT-Lizenz (`devc/web/vendor/LICENSE-xterm.txt`).
