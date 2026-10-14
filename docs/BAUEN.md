# devc selbst bauen

Diese Seite zeigt Schritt für Schritt, wie aus dem Quelltext die fertigen Dateien werden. Du brauchst dafür kein Vorwissen über das Projekt.

Am Ende hast du zwei Dateien im Ordner `dist/`:

| Datei | Was ist das? | Wofür? |
|---|---|---|
| `devc.pyz` | devc als eine einzige Datei | devc direkt auf einem Rechner benutzen oder eine VM aktualisieren |
| `user-data` | Startdatei für eine neue VM, enthält `devc.pyz` | eine neue VM mit devc anlegen (Schnellstart im `README.md`) |

## 1. Was du brauchst

- einen Rechner mit Linux, zum Beispiel Ubuntu 24.04
- Python 3.11 oder neuer
- `git`

Mehr nicht. Es wird nichts nachgeladen und nichts installiert: devc benutzt nur, was Python mitbringt.

Prüfen:

```bash
python3 --version        # muss 3.11 oder höher zeigen
git --version
```

## 2. Den Quelltext holen

Der Quelltext liegt im Repository von devc auf github.com. Die Adresse zum Klonen zeigt dort der Knopf »Code«:

```bash
git clone <adresse-des-repositorys>
cd devc
```

Alle weiteren Befehle laufen in diesem Ordner.

## 3. Die Tests laufen lassen

```bash
python3 -m unittest discover -s tests
```

Das dauert ein bis zwei Minuten. Die Tests brauchen kein LXD und kein Netz. Am Ende muss `OK` stehen:

```
Ran 538 tests in 88.7s

OK
```

Steht dort `FAILED`, bau nicht weiter. Die Zeilen darüber nennen den Test, der gescheitert ist.

## 4. devc bauen

```bash
python3 build.py
```

Das erzeugt `dist/devc.pyz`. Kurz ausprobieren:

```bash
python3 dist/devc.pyz --version      # zeigt: devc 1.0.0
python3 dist/devc.pyz --help
```

Die Datei läuft von jedem Ort, auch ohne den Quelltext. Du kannst sie auf einen anderen Rechner kopieren.

## 5. Die Startdatei für VMs bauen

```bash
python3 vm/make-user-data.py --public-port 8443 --http-port 8080 -o dist/user-data
```

Das erzeugt `dist/user-data`, etwa 300 KiB groß. Mit dieser Datei startest du eine neue VM, wie im Schnellstart im `README.md` beschrieben. Ab Schritt 2 dort, nur mit `dist/user-data` statt `user-data`:

```bash
lxc init ubuntu:24.04 devc-vm --vm -c limits.cpu=4 -c limits.memory=8GiB -d root,size=60GiB < /dev/null
lxc config set devc-vm user.user-data - < dist/user-data
lxc start devc-vm
```

Die Optionen im Befehl oben passen zum Schnellstart: Die Oberfläche ist über einen SSH-Tunnel unter `http://devc.localhost:8080/` erreichbar. Andere Fälle:

```bash
# mit SSH-Schlüssel, damit du dich als Nutzer ubuntu in der VM anmelden kannst
python3 vm/make-user-data.py --public-port 8443 --http-port 8080 --ssh-key ~/.ssh/id_ed25519.pub -o dist/user-data

# Speicher für Container als btrfs-Pool mit 40 GiB statt direkt auf der Platte der VM
python3 vm/make-user-data.py --public-port 8443 --http-port 8080 --pool-driver btrfs --pool-size 40GiB -o dist/user-data

# eigene Domain statt localhost
python3 vm/make-user-data.py --base-domain acme.dev.example.com -o dist/user-data
```

Alle Optionen zeigt `python3 vm/make-user-data.py --help`; erklärt sind sie im Handbuch, Abschnitt 15.

**Wichtig:** `user-data` enthält eine Kopie von `devc.pyz`. Baue sie nach jedem `python3 build.py` neu. Sonst bekommen neue VMs den alten Stand.

## 6. Alles auf einmal

Nach einer Änderung am Quelltext sind es immer dieselben drei Befehle:

```bash
python3 -m unittest discover -s tests
python3 build.py
python3 vm/make-user-data.py --public-port 8443 --http-port 8080 -o dist/user-data
```

## 7. Eine vorhandene VM aktualisieren

Eine VM, die schon läuft, bekommt einen neuen Build nicht von selbst. So bringst du ihn hinein, hier für eine LXD-VM mit dem Namen `devc-vm`:

```bash
lxc file push dist/devc.pyz devc-vm/opt/devc/install/devc.pyz
lxc exec devc-vm -- /opt/devc/install/install.sh
lxc exec devc-vm -- devc --version
```

Der Dienst startet dabei neu. Container und Daten bleiben, offene Terminal-Sitzungen enden.

Einfacher geht es in der Oberfläche: Kachel »VM«, »devc aktualisieren …«, dann `dist/devc.pyz` hochladen. Oder in der VM: `devc vm update devc.pyz`.

## 8. Einen Build veröffentlichen

Für ein Release gehören genau die zwei Dateien aus `dist/` als Anhang dazu, mit diesen Namen:

- `devc.pyz`
- `user-data`

`README.md` und Handbuch sagen nur: »aus dem aktuellen Release von devc auf github.com herunterladen«. Dort steht mit Absicht keine feste Adresse, damit die Texte auch dann stimmen, wenn sich die Adresse ändert. Wer mag, trägt später die genaue Adresse nach. Der Ordner `dist/` selbst wird nicht ins Git aufgenommen.

## Wenn etwas nicht klappt

| Was du siehst | Was hilft |
|---|---|
| `python3: command not found` oder eine Version unter 3.11 | Neueres Python installieren, zum Beispiel `sudo apt install python3.12`, und in allen Befehlen `python3.12` statt `python3` schreiben. |
| Die Tests enden mit `FAILED` | Nicht weiterbauen. Die Meldung nennt den Test und die Stelle. |
| `make-user-data.py` meldet `No such file or directory: …/dist/devc.pyz` | Erst `python3 build.py` ausführen. |
| `lxc launch -c user.user-data=…` meldet »Argument list too long« | Die Datei ist zu groß für die Befehlszeile. Nimm die drei Befehle aus Schritt 5. |
| `lxc init` bleibt stehen | `< /dev/null` am Ende des Befehls fehlt. |
| Die VM meldet »Zu wenig Platz für die Container« oder »… für den Speicherpool« | Die Platte der VM ist zu klein. Größere Platte nehmen; bei `--pool-driver btrfs` hilft auch ein kleineres `--pool-size`. |

## Mehr für Entwickler

- Echte Tests gegen LXD: `DEVC_IT=1 python3 -m unittest tests.it_e2e -v`. Sie legen die Container `it-e2e` und `it-e2e2` an und löschen sie wieder.
- Die Oberfläche im Browser prüfen: Skripte in `build/tools/`.
- Spezifikation, Plan und Regeln für die Arbeit am Code: `build/README.md`.
