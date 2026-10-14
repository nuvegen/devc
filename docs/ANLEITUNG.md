# devc – Anleitung für Nutzer

Stand: devc 1.0.0 (LXD). Diese Anleitung beschreibt die Bedienung. Die vollständige Spezifikation steht in `build/SPEC-devc.md`.

Dieses Handbuch steht auch in devc selbst: in der Oberfläche unter »Hilfe«, auf der Kommandozeile mit `devc help show technical`. Dort gibt es außerdem »Erste Schritte« in einfacher Sprache (`devc help show user`) und Anweisungen für KI-Agenten (`devc help show ai`).

## 1. Was devc macht

devc legt auf einem Linux-Host Dev-Container für Coding-Agents (Claude Code, opencode) in LXD an. Ein Container entsteht reproduzierbar aus einem Image, Addons und einer Konfigurationsdatei `devc.toml`. Was nicht reproduzierbar ist, liegt in zwei Datenbereichen, die den Neuaufbau überleben:

| Bereich | Pfad im Container | Inhalt |
|---|---|---|
| Code | `/work` | Projekt, `CLAUDE.md`, Infoverzeichnis `.devc/` |
| Home | `/home/dev` | Einstellungen, Agent-Memory (`~/.claude`), installierte Nutzer-Tools |

Im Container arbeitest du als Nutzer `dev` (UID 1000). Alle Container liegen im eigenen LXD-Projekt `devcontainers`; andere LXD-Projekte fasst devc nicht an.

Begriffe:

- **Addon**: bringt ein Werkzeug in den Container (z. B. `python`, `node`, `claude-code`). `base` ist immer dabei.
- **Template**: fertige Konfiguration als Vorlage für neue Container.
- **Secret-Store**: geteilte Secrets auf dem Host; Templates nennen nur den Namen, nie den Wert.
- **Job**: länger laufende Operation mit eigenem Log (Anlegen, Anwenden, Backup …).

## 2. Installieren

Es gibt zwei Wege. Der erste ist der einfache.

### Schnellstart: devc in einer VM

Du startest eine frische Ubuntu-VM mit einer fertigen Datei. Die VM richtet sich beim ersten Start selbst ein: LXD, devc als Dienst und der Proxy Caddy. Du brauchst einen Rechner mit LXD, der VMs starten kann.

```bash
# 1. Die Datei "user-data" aus dem aktuellen Release von devc auf github.com herunterladen
#    und in diesen Ordner legen

# 2. Die VM starten
lxc init ubuntu:24.04 devc-vm --vm -c limits.cpu=4 -c limits.memory=8GiB -d root,size=60GiB < /dev/null
lxc config set devc-vm user.user-data - < user-data
lxc start devc-vm

# 3. Warten, bis die VM fertig ist (einige Minuten, am Ende steht "status: done")
lxc exec devc-vm -- cloud-init status --wait

# 4. Den Token für die Anmeldung holen
lxc exec devc-vm -- devc token show

# 5. Von deinem Arbeitsplatz einen Tunnel öffnen; die Adresse der VM zeigt "lxc list devc-vm"
ssh -N -L 8080:<adresse-der-vm>:8080 <nutzer>@<rechner>
```

Dann im Browser `http://devc.localhost:8080/` öffnen und mit dem Token anmelden.

Läuft die VM auf deinem eigenen Rechner, ersetzt eine Weiterleitung mit `socat` den Tunnel (Abschnitt 15, Schritt 4). Rufst du die Adresse der VM direkt auf, zeigt eine Hinweisseite die passenden Befehle.

Gibt es noch kein Release, baust du die Datei selbst, das sind zwei Befehle: `docs/BAUEN.md` im Quelltext.

In der VM heißt der Aufruf einfach `devc`, zum Beispiel `lxc exec devc-vm -- devc list`. Alle Befehle aus diesem Handbuch gehen dort unverändert. Abschnitt 3 (Einrichten) ist in der VM schon erledigt; weiter geht es mit Abschnitt 4.

Andere Größen, eine eigene Domain, mehrere VMs nebeneinander und das Aktualisieren beschreibt Abschnitt 15. Dort steht auch, wie die VM mit einem eigenen Speicherpool schneller wird (»Empfohlen: ein eigener Speicherpool für die VMs«).

### Ohne VM: direkt auf einem Linux-Rechner

Das ist mehr Handarbeit: LXD richtest du selbst ein, und devc läuft nicht als Dienst. Du brauchst:

- LXD als Snap, deinen Nutzer in der Gruppe `lxd`
- Python 3.11 oder neuer
- Netzzugang für Installationen in den Containern

devc ist eine einzelne Datei `devc.pyz`. Lade sie aus dem aktuellen Release von devc auf github.com herunter. Sie läuft von jedem Ort. Ein Alias in `~/.bashrc` macht daraus den Befehl `devc`, der in dieser Anleitung verwendet wird. In dem Ordner, in dem die Datei liegt:

```bash
alias devc="python3 $PWD/devc.pyz"
devc --help
```

Gibt es noch kein Release, baust du sie aus dem Quelltext: `python3 build.py` erzeugt `dist/devc.pyz` (`docs/BAUEN.md`).

Ist das System-Python älter als 3.11, nimm im Alias einen neueren Interpreter, zum Beispiel `python3.12` oder den aus einem venv. Es wird nichts hineininstalliert.

Danach weiter mit Abschnitt 3.

### Optionen für jeden Befehl

| Option | Wirkung |
|---|---|
| `--json` | Ausgabe als JSON, auch im Fehlerfall |
| `-v` | zeigt unter anderem jeden `lxc`-Aufruf |
| `-q` | nur Ergebnisse und Fehler |
| `--config <pfad>` | andere `config.toml` |

## 3. Einrichten

```bash
devc init
devc doctor
```

`devc init` legt an, was fehlt, und ändert sonst nichts: `~/.config/devc/config.toml`, das API-Token, die Verzeichnisse unter `~/.config/devc/` und `~/.local/share/devc/` sowie das LXD-Projekt `devcontainers` mit seinem Profil. `devc doctor` prüft die Umgebung und meldet Fehler und Warnungen.

Wichtige Einstellungen in `~/.config/devc/config.toml`:

| Schlüssel | Bedeutung |
|---|---|
| `allowed_roots` | Host-Verzeichnisse, unter denen Shared Folders liegen dürfen. Leer = Shared Folders verboten. |
| `[defaults]` | Image, CPU, RAM, Disk und Addons für Container ohne Template |
| `[lxd]` | Projekt, Storage-Pool, Netz, `shared_mode` |
| `[server]` | Bind-Adresse, Port, TLS, Web-Terminal, Betrieb hinter einem Proxy (`base_domain`, `public_port`, `behind_proxy`) |

Nach einer Änderung an `allowed_roots` oder `shared_mode` **`devc init` erneut ausführen**; devc gleicht dann die Beschränkungen des LXD-Projekts an.

## 4. Den ersten Container anlegen

```bash
devc template list
devc create py-tools --template pydev --edit
```

`--edit` öffnet den Entwurf der `devc.toml` in deinem Editor (`$VISUAL`, `$EDITOR`, sonst `vi`). Der Entwurf gilt nur für diesen Container; das Template bleibt unverändert. Nach dem Speichern prüft devc die Datei und legt den Container an: Volumes, Start, Addons in der richtigen Reihenfolge, Dateien, Agent-Doku. Der Fortschritt läuft live durch.

Weitere Wege:

```bash
devc create web1 --template webdev --cpu 2 --memory 4GiB        # Optionen statt Editor
devc create tools --addon python --addon node                   # ohne Template, mit den Defaults
devc create proj --from meine.devc.toml                         # fertige Datei verwenden
devc create proj --template pydev --shared ~/devc-shared/proj   # Shared Folder statt Code-Volume
```

Mitgelieferte Templates:

| Template | Inhalt |
|---|---|
| `pydev` | Python, Claude Code, einfacher HTTP-Server, Link auf Port 8080 für einen eigenen Dienst (`webproxy`), Kachel »Git« |
| `webdev` | Node.js, Claude Code, Kachel »Git« |
| `abapdev` | ABAP-Werkzeuge (sapcli, abaplint), Claude Code, opencode, Kachel »Git«. Die Platzhalter in `[ECKIGEN KLAMMERN]` vor dem Anlegen ersetzen. |

Solange die Einrichtung läuft, zeigen `devc list` und die GUI **wird eingerichtet**. Schlägt ein Schritt fehl, bleibt der Container bestehen und gilt als **unvollständig**. `devc config apply <name>` setzt die Einrichtung fort, `devc rm <name>` räumt ihn weg.

### Secrets beim Anlegen

Verweist die `devc.toml` unter `[secrets]` auf einen Store-Eintrag, muss es ihn geben, oder du gibst den Wert beim Anlegen mit:

```bash
devc secret set --shared anthropic=@~/anthropic.key
devc create py-tools --template pydev

# oder nur für diesen Container:
devc create py-tools --template pydev --secret claude-code.api_key=@~/anthropic.key
```

Fehlt ein Wert, fragt devc im Terminal verdeckt nach. Mit `--yes` oder ohne Terminal endet der Befehl stattdessen mit `E_MISSING_SECRET`.

Claude Code läuft auch ohne API-Key, mit deinem Claude-Abo. Entferne dafür die Zeile `"claude-code.api_key" = …` aus `[secrets]`. Dann gibt es zwei Wege:

- **Im Container anmelden:** `claude` starten und dem Browser-Flow folgen. Die Anmeldung liegt im Home-Volume und bleibt erhalten.
- **Abo-Token hinterlegen:** Auf einem Rechner, auf dem Claude Code mit dem Abo angemeldet ist, erzeugt `claude setup-token` einen lange gültigen Token. Den hinterlegst du als Secret `claude-code.oauth_token`; im Container steht er dann als `CLAUDE_CODE_OAUTH_TOKEN` bereit, und eine Anmeldung ist nicht nötig. Praktisch für mehrere Container:

  ```bash
  devc secret set --shared claude-abo                          # fragt verdeckt nach dem Token
  devc secret set py-tools claude-code.oauth_token=shared:claude-abo
  ```

  Oder gleich in der Konfiguration unter `[secrets]`: `"claude-code.oauth_token" = "claude-abo"`.

Sind Token und API-Key beide hinterlegt, gilt der Token. Eine Änderung wirkt in neuen Terminal-Sitzungen.

**Modell.** Das Feld »Modell« (`anthropic_model`) gilt für Terminal und Chat: `sonnet`, `opus`, `haiku` oder leer für die Voreinstellung von Claude Code.

**Anderer Dienst statt Anthropic.** Manche Anbieter bilden die Schnittstelle von Anthropic nach, zum Beispiel BytePlus ModelArk. Dann arbeitet Claude Code mit deren Modellen, im Terminal und in der Kachel »Claude«. Auf der Seite **Addons** wählst du bei `claude-code` »Dienst: Anderer Dienst«. Darunter erscheinen drei Felder:

| Feld | Name | Beim Anbieter heißt es |
|---|---|---|
| Adresse | `base_url` | `ANTHROPIC_BASE_URL` |
| Schlüssel (Secret) | `auth_token` | `ANTHROPIC_AUTH_TOKEN` oder API-Key |
| Modell | `model` | `ANTHROPIC_MODEL`, zum Beispiel `ark-code-latest` |

Dasselbe auf der Kommandozeile:

```bash
devc config set py-tools claude-code.provider=custom
devc config set py-tools claude-code.base_url=https://ark.ap-southeast.bytepluses.com/api/coding
devc config set py-tools claude-code.model=ark-code-latest     # Name genau wie beim Dienst
devc secret set py-tools claude-code.auth_token                 # der Schlüssel dieses Dienstes
devc config apply py-tools
```

Der Abo-Token und der API-Key von Anthropic gehen nie an den anderen Dienst. Zurück zu Anthropic: »Dienst: Anthropic« wählen. Die Felder des anderen Dienstes bleiben gespeichert, gelten dann aber nicht. Für Dienste, die nur die Schnittstelle von OpenAI haben, gibt es das Addon `codex`.

## 5. Mit Containern arbeiten

```bash
devc list                     # Übersicht mit Status
devc info py-tools            # Details
devc shell py-tools           # Shell als dev in /work (--root für root)
devc exec py-tools -- ls -la  # einzelner Befehl; der Exit-Code wird durchgereicht
devc stop py-tools
devc start py-tools
devc restart py-tools
devc rm py-tools              # fragt nach; --keep-data behält die Volumes
```

Status in `devc list`:

| Status | Bedeutung |
|---|---|
| `running`, `stopped` | normal |
| `(wird eingerichtet)` | Einrichtung läuft noch; warten, Fortschritt mit `devc jobs --container <name>` |
| `(unvollständig)` | Einrichtung abgebrochen, siehe oben |
| `missing` | devc kennt den Container, in LXD fehlt er. `devc rm` räumt auf. |
| `foreign` | Container im Projekt, den devc nicht angelegt hat. devc ändert ihn nicht. |

Nach `devc rm --keep-data` verwendet ein neues `devc create` mit demselben Namen die vorhandenen Volumes weiter.

### Dateien

```bash
devc push py-tools bericht.md docs/       # nach /work/docs/bericht.md
devc pull py-tools src/main.py            # Datei holen
devc pull py-tools src                    # Verzeichnis kommt als src.tar.gz
devc file archive py-tools paket.zip      # zeigt, was ein tar- oder zip-Archiv enthält
devc file unpack py-tools paket.zip       # entpackt in den Ordner des Archivs
devc file unpack py-tools paket.zip --into paket   # entpackt nach /work/paket
devc file rm py-tools alt.txt             # löscht Datei oder Ordner, fragt nach (--yes: ohne Rückfrage)
```

Ziel und Quelle sind relativ zu `/work`. Pfade außerhalb von `/work` lehnt devc ab.

**Archive entpacken.** devc entpackt `tar` (auch `.tar.gz`, `.tgz`, `.tar.bz2`, `.tar.xz`) und `zip`. Der Container braucht dafür kein `unzip`: devc liest das Archiv selbst und schickt dem Container nur geprüfte Einträge.

- Nicht entpackt werden Einträge, die aus dem Zielordner herausführen (absolute Pfade, `..`, Links nach außen), sowie harte Links und Gerätedateien. devc nennt sie als »Übersprungen«.
- Vorhandene Ordner werden ergänzt, gleichnamige Dateien ersetzt.
- Grenzen: Archivdatei und entpackter Inhalt je höchstens 1 GiB, höchstens 100 000 Einträge. Verschlüsselte zip-Archive gehen nicht.

In der GUI zeigt die Seite **Dateien** den Ordner `/work`. Das Menü **…** am Ende jeder Zeile bietet:

| Eintrag | Wirkung |
|---|---|
| Herunterladen | Datei laden; ein Ordner kommt als `tar.gz` |
| Entpacken … | nur bei Archiven. Öffnet eine Vorschau: Du wählst »Hier« oder »In einen neuen Ordner« und siehst, welche Pfade entstehen und was es schon gibt. Erst der Knopf »Entpacken« schreibt. Bringt das Archiv genau einen Ordner mit, ist »Hier« vorgewählt, sonst ein neuer Ordner mit dem Namen des Archivs. |
| Löschen … | löscht nach einer Rückfrage; Ordner samt Inhalt |

Einen ganzen Ordner lädst du also hoch, indem du ihn als Archiv hochlädst und dort entpackst.

Ein Klick auf eine Datei öffnet sie:

| Datei | Darstellung |
|---|---|
| Text (UTF-8, bis 2 MiB) | Text mit Zeilennummern. Umschaltbar auf **Markdown**; bei `.md` und `.markdown` ist Markdown vorgewählt. |
| Bild (`png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`, `bmp`, `ico`, `avif`) | Bild mit Zoom: −, 100 %, +, Fit. Mit gedrückter Maustaste verschiebst du den Ausschnitt; Strg + Mausrad zoomt. |
| alles andere | Hex-Ansicht: Adresse, Bytes, Zeichen. Je 64 KiB, weitere auf Knopfdruck. |

- **Hex** steht bei jeder Datei zur Wahl, **Text** auch bei SVG-Bildern.
- Bei Text und Markdown wechselst du zwischen **Ansicht** und **Bearbeiten**. »Ansicht« zeigt auch deine ungespeicherte Fassung, bei Markdown also die Vorschau. **Speichern** (oder Strg+S) schreibt die Datei, **Verwerfen** holt den gespeicherten Stand zurück.
- Hat sich die Datei im Container geändert, seit du sie geöffnet hast (etwa durch einen Agenten), fragt devc vor dem Überschreiben nach.
- Windows-Zeilenenden bleiben beim Speichern erhalten.
- Dateien über 20 MiB zeigt die Seite nicht an; »Laden« holt sie auf deinen Rechner.
- Wechselst du mit ungespeicherten Änderungen auf eine andere Seite von devc, sind die Änderungen weg. Nur beim Schließen oder Neuladen des Browserfensters warnt der Browser.

### Software nachinstallieren: root und sudo

Der Nutzer `dev` hat **kein Passwort**, und `sudo` ist für ihn nicht freigeschaltet. Fragt `sudo` nach einem Passwort, gibt es keins, das passt. Das ist Absicht: Der Coding-Agent im Container soll nicht von sich aus root werden. Zum Nachinstallieren gibt es drei Wege.

**Weg 1 – einmalig als root arbeiten.** Ändert nichts an der Konfiguration:

```bash
devc shell py-tools --root
devc exec py-tools --root -- apt-get install -y jq
```

Im Web-Terminal wählst du dafür oben den Benutzer `root` und öffnest eine neue Sitzung.

**Weg 2 – `sudo` ohne Passwort für `dev` freischalten.** Das Addon `base` hat dafür den Parameter `sudo`. Mit `devc config edit py-tools` ergänzen:

```toml
[params.base]
sudo = true
```

und mit `devc config apply py-tools` anwenden. Danach geht im Container `sudo apt-get install …` ohne Passwortabfrage. `sudo = false` und erneutes Anwenden nimmt das Recht wieder weg.

Die Kurzform erledigt beides in einem Schritt, schreibt die `devc.toml` dabei aber neu, sodass Kommentare darin verloren gehen:

```bash
devc install py-tools base --param base.sudo=true
```

Bedenke: Mit `sudo` kann auch der Agent im Container alles als root tun.

**Weg 3 – dauerhaft über ein Addon.** Was du per `apt` nachinstallierst, liegt im Container selbst und ist nach einem Neuaufbau (`devc rm` und `devc create`, Restore) wieder weg; nur `/work` und `/home/dev` bleiben. Pakete, die du immer brauchst, gehören deshalb in ein eigenes Addon mit einem `install.sh` (Abschnitt 7, »Eigene Addons«). `examples/addons/httpserver/` ist eine kleine Vorlage. Das Addon trägst du in `addons` der `devc.toml` oder in ein Template ein, dann ist das Paket in jedem neuen Container dabei.

Faustregel: für »mal eben schnell« Weg 1, für regelmäßig benötigte Pakete Weg 3.

### Agent-Doku

devc schreibt in `/work/CLAUDE.md` einen Block zwischen `<!-- devc:begin -->` und `<!-- devc:end -->`. Darin stehen die aktiven Addons mit ihren Hinweisen. Eigene Regeln gehören außerhalb der Marker und bleiben erhalten. `/work/AGENTS.md` ist ein Symlink auf `CLAUDE.md`.

Damit ein KI-Agent im Container weiß, was es gibt und wie es funktioniert, legt devc das Infoverzeichnis `/work/.devc/` an:

| Datei | Inhalt |
|---|---|
| `README.md` | Übersicht: der Container, aktive Addons, weitere Addons zum Ergänzen, Secrets (nur »gesetzt« oder »nicht gesetzt«) |
| `addons/<addon>.md` | Je Addon eine Seite: Einstellungen mit den aktuellen Werten, Aktionen mit ihrem Befehl, Links, Kacheln, Hinweise für die KI, Anleitung |
| `config.toml` | Kopie der Konfiguration des Containers. Nur zum Lesen; Änderungen dort wirken nicht |

- Geheimnisse stehen dort nie. Namen aus dem Secret-Store und Zugangsdaten in Adressen sind durch `***` ersetzt.
- Der Ordner gehört devc: Eigene Änderungen darin gehen beim nächsten »Anwenden« verloren.
- Jedes »Anwenden« schreibt den Ordner neu, auch nach `devc install` und `devc config set`. Setzt oder entfernst du ein Secret, zieht devc den Zustand sofort nach.
- `devc docs <name>` erzeugt alles neu, zum Beispiel nach einem neuen eigenen Addon.

```bash
devc docs py-tools                              # Agent-Doku und Infoverzeichnis neu schreiben
devc exec py-tools -- cat .devc/README.md       # Übersicht ansehen
```

Liegt in `/work` schon eine `CLAUDE.md` ohne diese Marker, bricht devc ab und ändert nichts. Füge die beiden Markerzeilen an der gewünschten Stelle ein und führe `devc config apply <name>` aus.

## 6. Konfiguration ändern

```bash
devc config show py-tools
devc config edit py-tools             # Editor; gespeichert wird nur eine gültige Datei
devc config apply py-tools --dry-run  # zeigt, was sich ändern würde
devc config apply py-tools
```

`apply` setzt Ressourcen, installiert neue Addons, installiert Addons mit geänderten Parametern oder neuer Version erneut und schreibt Dateien und Agent-Doku. Der Container muss dafür laufen.

Nicht per `apply` änderbar:

- **Datenbereich** (`[data]`, Volume ↔ Shared Folder): Backup erstellen und mit geänderter `devc.toml` wiederherstellen (Abschnitt 9).
- **Image**: Container neu anlegen.
- **Addon entfernen**, das kein `uninstall`-Skript hat: nur durch Neuanlage. Die mitgelieferten Addons haben keins.

Kurzformen für Addons:

```bash
devc install py-tools node --param node.version=22
devc uninstall py-tools <addon>
```

In der Oberfläche: »Addon hinzufügen« auf der Seite Addons oder Konfiguration. Du wählst das Addon aus der Liste aller Addons, trägst die Einstellungen ein und klickst »Hinzufügen und einrichten«. Das entspricht `devc install`; was das Addon braucht (`requires`), kommt dabei mit. Secrets des Addons kannst du im selben Formular eintragen; sie sind dann gesetzt, bevor das Addon eingerichtet wird.

## 7. Addons und Aktionen

```bash
devc addon list
devc addon show httpserver            # Parameter, Aktionen, Hilfe
devc action list py-tools             # Aktionen und Links dieses Containers
devc action run py-tools python.venv
devc action run py-tools httpserver.start
devc action run py-tools httpserver.stop
```

Aktionen sind im Addon fest hinterlegte Befehle; devc setzt nur die Parameter ein. Eine Hintergrund-Aktion wie `httpserver.start` läuft weiter, bis die zugehörige Stopp-Aktion sie beendet. Ein zweiter Start endet mit `E_CONFLICT`. Links öffnen einen Dienst im Container im Browser. Hinter dem Proxy (Abschnitt 12) führen sie auf `https://<port>-<container>.<basisdomain>/`; ohne Proxy auf `http://<container-ip>:<port>/`, das nur vom Host aus erreichbar ist.

**Einstellungen eines Addons im Betrieb ändern:**

```bash
devc config set py-tools httpserver.dir=/work/meinefiles
devc config set py-tools httpserver.port=8010 httpserver.dir=/work/out
```

In der GUI geht dasselbe auf der Seite **Addons** des Containers: Unter jedem Addon stehen seine Einstellungen, »Übernehmen« wendet sie an. Die Hilfe zu einem Addon hat dort eine eigene Seite (Link »Hilfe zu …«).

Was dabei passiert:

1. devc schreibt die neuen Werte in die `devc.toml` des Containers. Kommentare in der Datei gehen dabei verloren.
2. Nur die Addons mit geänderten Werten werden neu eingerichtet: Ihr `install.sh` läuft noch einmal als root im laufenden Container. Diese Skripte sind so geschrieben, dass ein weiterer Lauf nichts doppelt macht. Der Container wird nicht neu gestartet.
3. Läuft gerade eine Hintergrund-Aktion eines dieser Addons (zum Beispiel `httpserver.start`), stoppt devc sie vorher und startet sie danach mit den neuen Werten.
4. Alles andere läuft weiter: Terminal-Sitzungen, ein laufendes `claude`, Aktionen anderer Addons.

Dasselbe gilt, wenn du die Werte unter Konfiguration änderst und »Anwenden« klickst (`devc config apply`). Enthält die `devc.toml` noch weitere, bisher nicht angewendete Änderungen, wendet `config set` sie mit an.

Mitgelieferte Addons:

| Addon | Inhalt | Parameter (Auswahl) |
|---|---|---|
| `base` | Nutzer `dev`, git, curl, ripgrep (`rg`), Locale, Zeitzone | `timezone`, `git_name`, `git_email`, `sudo` |
| `claude-code` | Claude Code CLI, Kachel »Claude« (Chat) | `provider` (Anthropic oder anderer Dienst), `anthropic_model`, `base_url`, `model`, `channel`, `chat_updates`, Secrets `oauth_token` (Abo), `api_key` (API), `auth_token` (anderer Dienst) |
| `codex` | Codex CLI, Kachel »Codex« (Chat), für OpenAI und kompatible Dienste wie Novita | `base_url`, `model`, `release`, Secret `api_key` |
| `info` | Kachel »Info«: System, Auslastung, Speicherplatz, Prozesse (installiert nichts) | `processes` |
| `opencode` | opencode CLI und Weboberfläche, Anmeldung über `/connect` | `port` |
| `python` | Python, venv, pip, pipx, ruff | `version` |
| `node` | Node.js aus NodeSource | `version` |
| `packages` | Zusätzliche Pakete von Ubuntu (`apt`), Liste je Container | `apt`, `recommends` |
| `git` | Kachel »Git«: Repositories finden, Stand, Commit, Push, Pull, Commit rückgängig machen (installiert nichts) | `roots`, `depth`, `username`, Secret `token` |
| `httpserver` | HTTP-Server für `/work/out` | `port`, `dir` |
| `webproxy` | Links auf Dienste im Container über den Proxy (installiert nichts) | `proxy`, `public` |
| `abap` | sapcli, abaplint, Transpiler (`abap_transpile`), FAQ unter `/FAQ` | `sap_host`, `sap_port`, `sap_ssl`, `sap_client`, `sap_user`, Secret `sap_password` |

Zum Addon `packages`: Es ist für Pakete gedacht, die kein eigenes Addon haben. Die Liste nimmt nur Paketnamen an, keine Optionen und keine Versionen.

```bash
devc install py-tools packages
devc config set py-tools packages.apt="jq sqlite3"
```

Ein Paket aus der Liste zu nehmen entfernt es nicht; es fehlt erst nach dem Neuanlegen des Containers. Die Hilfeseite des Addons nennt die Pakete für den Browser von Playwright.

Zum Addon `abap`: `/FAQ` kommt aus dem Archiv `files/faq.tar.gz` des Addons, gehört root und ist für `dev` nur lesbar. Der Agent legt eigene Notizen in `/FAQ/ki` ab; das ist ein Symlink ins Code-Volume und bleibt beim Neuaufbau erhalten. Pro Container gibt es eine SAP-Verbindung; für ein weiteres System einen weiteren Container anlegen.

### Kacheln aus Addons

Ein Addon kann eigene Kacheln auf das Dashboard des Containers legen. Sie stehen hinter den festen Kacheln.

```bash
devc tile list py-tools               # Kacheln dieses Containers
devc tile show py-tools info.status   # Text einer Kachel der Art view
devc tile panel py-tools git.repos    # Daten einer Kachel der Art panel (JSON)
```

| Art | Was die Kachel öffnet | Beispiel |
|---|---|---|
| `view` | Eine Seite in devc. Ihr Text kommt bei jedem Aufruf frisch aus einem Befehl im Container. | »Info« aus dem Addon `info` |
| `chat` | Eine Unterhaltung mit einem Programm im Container | »Claude« aus dem Addon `claude-code` |
| `panel` | Eine bedienbare Seite in devc: Listen, Knöpfe und Formulare, die ein Skript im Container als Daten liefert | »Git« aus dem Addon `git` |
| `link` | Die eigene Oberfläche eines Addons in einem neuen Tab | – |

**Git.** Das Addon `git` sucht Repositories unter `/work` und `/home/dev` und zeigt je Repository Zweig, Server, Änderungen und die letzten 30 Commits. Du kannst Dateien vormerken, einen Commit erstellen, Push und Pull ausführen und einen Commit rückgängig machen (`git revert`, ein neuer Commit). Der Pull führt nichts zusammen (`--ff-only`): Haben du und der Server beide neue Commits, ändert er nichts und meldet das. Zweige wechseln, Zusammenführen und Konflikte bleiben Sache des Terminals.

```bash
devc install py-tools git
devc secret set py-tools git.token             # Token für https (GitHub, GitLab)
```

GitHub und GitLab nehmen über https nur noch ein Token; einen Benutzernamen braucht es damit nicht. Für einen Server mit Benutzername und Passwort setzt du zusätzlich `git.username` und nimmst das Passwort als `git.token`. Das Secret liegt im Container unter `/run/devc/secrets/git.token` und ist dort für `dev` lesbar, also auch für eine KI im Container. Nimm ein Token, das nur darf, was nötig ist. Adressen mit ssh brauchen kein Token. Jede Aktion der Kachel steht mit ihrem Namen im Log des Containers, ohne Eingaben.

**Info.** Das Addon `info` zeigt System, Auslastung, Speicherplatz, Prozesse und installierte Addons. Die Seite lädt alle 5 Sekunden neu, solange sie offen ist.

```bash
devc install py-tools info
devc config set py-tools info.processes=20     # 20 Prozesse zeigen statt 10
```

**Claude.** Mit dem Addon `claude-code` schreibst du auf der Kachel »Claude« mit Claude Code, ohne Terminal.

- »Neue Unterhaltung« startet. Enter macht eine neue Zeile, Strg+Enter sendet.
- Oben stehen die Version von Claude Code und das Modell, schon bevor du schreibst.
- Mit »A−« und »A+« oben rechts stellst du die Textgröße ein. Ein Klick auf die Prozentzahl setzt sie zurück. Der Browser merkt sich die Wahl.
- »Statistik« oben rechts öffnet ein Fenster mit Zahlen zur Unterhaltung: Zeit des Modells, Zeit der Werkzeuge, Wartezeit auf dich, Tokens (Eingabe, davon aus dem Cache, Ausgabe, Kontext beim letzten Aufruf) und die Zeit je Werkzeug. Die Zahlen stammen aus dem Verlauf, den Claude Code im Container unter `~/.claude/projects/` schreibt; die Zeiten sind daraus geschätzt. Auch frühere Unterhaltungen haben den Knopf.
- Tippst du `/`, zeigt ein Popup die Befehle von Claude Code, zum Beispiel `/model sonnet`, `/config` oder `/clear`. Die Pfeiltasten wählen, Enter übernimmt den Befehl ins Feld, Strg+Enter sendet ihn.
- Eine Freigabe beantwortest du mit »Erlauben«, »Immer erlauben« (für dieses Werkzeug, bis zum Ende der Unterhaltung) oder »Ablehnen«.
- Will Claude eine Datei ändern, gibt es zusätzlich »Im Ordner erlauben«. Danach fragt Claude für Dateien in diesem Ordner und seinen Unterordnern nicht mehr, bis die Unterhaltung endet.
- Fragt Claude dir zu oft, klicke oben rechts auf »Freigaben: …«. Das Fenster hat drei Stufen:
  - »Nachfragen«: Claude fragt vor jeder Änderung an einer Datei und vor jedem Befehl.
  - »Dateien im Projekt frei«: Claude ändert Dateien unter `/work` ohne Rückfrage. Vor Befehlen fragt es weiter.
  - »Alles erlauben«: Claude fragt nicht mehr. Es darf im Container alles, was der Nutzer `dev` darf.

  Die Wahl gilt sofort und für alle Unterhaltungen in diesem Container. Der Container bleibt die Grenze: Claude kommt auch mit »Alles erlauben« nicht an andere Container oder an die VM. Es erreicht aber alles im Container, auch die Secrets dort und das Netz.
- Solange Claude arbeitet, steht unter dem Verlauf, was es gerade tut und wie lange schon, zum Beispiel »Bash: npm test · 1:12«.
- Bei längerer Arbeit meldet sich Claude zwischendurch kurz mit dem Stand. Abschalten: `devc config set py-tools claude-code.chat_updates=false`.
- »Abbrechen« stoppt die laufende Antwort. »Beenden« schließt die Unterhaltung. Dabei fasst Claude sie in wenigen Wörtern zusammen; das wird ihr Titel in der Liste und dauert einige Sekunden.

**Codex.** Das Addon `codex` bringt Codex CLI von OpenAI und die Kachel »Codex«. Sie sieht aus wie die Kachel »Claude« und kann dasselbe: schreiben, Dateien ändern, Befehle ausführen, Freigaben, Statistik, frühere Unterhaltungen. Das Modell kommt von OpenAI oder von einem kompatiblen Dienst, zum Beispiel Novita.

```bash
devc install py-tools codex
devc config set py-tools codex.model=deepseek/deepseek-v3.1    # Name genau wie beim Dienst
devc secret set py-tools codex.api_key
```

- `base_url` ist die Adresse des Dienstes; voreingestellt ist Novita (`https://api.novita.ai/openai/v1`). Leer bedeutet OpenAI selbst.
- Der Dienst muss die »Responses API« von OpenAI anbieten. Codex spricht nur diese.
- Befehle, die nur lesen, führt Codex ohne Rückfrage aus. Vor allem anderen fragt es. Unter »Freigaben: …« gibt es zwei Stufen: »Nachfragen« und »Alles erlauben«.
- Im Terminal startet `codex --sandbox danger-full-access` Codex mit denselben Einstellungen.
- Die Unterhaltung läuft weiter, wenn du die Seite verlässt. Sie endet mit »Beenden«, mit dem Stopp des Containers und mit `devc serve`.
- Lange Unterhaltungen: Sichtbar bleiben etwa die letzten 256 KB Text. Ältere Einträge holt »Ältere Einträge anzeigen« oben im Verlauf zurück, je Klick etwa 256 KB. Das gilt auch beim Ansehen früherer Unterhaltungen und für die Kachel »Codex«.
- Unter »Frühere Unterhaltungen« zeigt »Öffnen« eine beendete Unterhaltung zum Ansehen. Erst »Fortsetzen« darunter startet sie wieder. Der Verlauf liegt im Container unter `~/.local/state/devc-chat/claude-code/`.
- Claude arbeitet im Ordner `/work` und braucht eine Anmeldung: Secret `claude-code.oauth_token` oder `claude-code.api_key`, oder einmal `claude` im Terminal starten. Mit einem anderen Dienst: `claude-code.auth_token`.
- Ein anderes Modell wählst du mit `devc config set py-tools claude-code.anthropic_model=sonnet`. Es gilt auch im Terminal.
- Den Chat gibt es nur dort, wo auch das Web-Terminal erlaubt ist (Abschnitt 12). Zum Anmelden nimm weiter das Terminal. Manche `/`-Befehle brauchen auch ein Terminal; Claude meldet das dann im Chat.

### Eigene Addons

Ein Addon ist ein Verzeichnis mit `addon.toml`, `README.md`, `AI.md` und meist `install.sh`. Als Vorlage dienen die Verzeichnisse unter `examples/addons/`; das Schema steht in `build/SPEC-devc.md`, Abschnitt 6.2. Install-Skripte laufen als root im Container und müssen mehrfach ausführbar sein.

```bash
devc addon validate ./mein-addon      # prüft, ohne etwas zu ändern
tar -czf mein-addon.tar.gz mein-addon
devc addon import mein-addon.tar.gz   # nach ~/.config/devc/addons/
devc addon export python -o .         # Archiv eines vorhandenen Addons
devc addon rm mein-addon              # nur eigene, nur wenn kein Container es nutzt
```

Ein eigenes Addon mit der ID eines mitgelieferten überschreibt dieses. Container übernehmen eine neue Version erst mit `devc config apply`.

### Eigene Kacheln

Kacheln stehen als `[[tiles]]` in der `addon.toml`. Jede braucht `id`, `title` und `kind`; `description` ist der Text auf der Kachel.

```toml
# Art view: Der Befehl gibt Markdown aus, devc zeigt es als Seite.
[[tiles]]
id = "status"
title = "Status"
kind = "view"
run = "bash /opt/devc/addons/mein-addon/status.sh {port}"
timeout = 10        # Sekunden, 1 bis 60
refresh = 5         # alle 5 Sekunden neu laden; 0 = nie

# Art link: Das Addon hat eine eigene Oberfläche auf einem Port im Container.
[[tiles]]
id = "ui"
title = "Oberfläche"
kind = "link"
port = "{port}"     # oder: url = "https://…"
path = "/"

# Art panel: Der Befehl bekommt eine Anfrage als JSON und liefert die Seite als JSON.
[[tiles]]
id = "liste"
title = "Meine Liste"
kind = "panel"
run = "python3 /opt/devc/addons/mein-addon/panel.py"
timeout = 30        # Sekunden, 1 bis 300

# Art chat: Der Befehl startet ein Programm, das mit devc JSON-Zeilen tauscht.
[[tiles]]
id = "chat"
title = "Mein Agent"
kind = "chat"
run = "python3 /opt/devc/addons/mein-addon/bridge.py"
```

- `run` läuft als `dev` in `/work`. Platzhalter wie `{port}` setzt devc aus den Parametern des Addons ein. Die Dateien des Addons liegen im Container unter `/opt/devc/addons/<id>/`.
- Eine Seite der Art `view` kann Überschriften, Absätze, Listen, Tabellen, Code-Blöcke, `Code`, fett und Links enthalten. Sie ist höchstens 256 KiB groß. Vorlage: `examples/addons/info/`.
- Ein Addon liefert devc nie HTML. Braucht es eine eigene Oberfläche, startet es einen Dienst im Container und verknüpft ihn mit `link`. Der Dienst muss auf `0.0.0.0` lauschen (Abschnitt 12, »Dienste im Container im Browser öffnen«).

**Das Protokoll der Art `panel`.** devc ruft `run` für jede Ansicht und jede Aktion neu auf und schreibt eine JSON-Zeile auf dessen Standardeingabe. Das Programm antwortet mit einem JSON-Objekt auf der Standardausgabe und endet. Vorlage: `examples/addons/git/gitpanel.py`.

| Anfrage von devc | Antwort des Programms |
|---|---|
| `{"view": "<ansicht>", "args": {…}}`; die erste Ansicht heißt `""` | `{"title": "…", "note": "…", "blocks": [ … ]}` |
| `{"action": "<aktion>", "args": {…}, "values": {…}}` | `{"ok": true, "message": "…", "output": "…", "view": {"view": "…", "args": {…}}}`; `output` und `view` sind freiwillig |

Bausteine in `blocks`:

| `type` | Felder | Was devc zeichnet |
|---|---|---|
| `text` | `text` (Markdown), `title` | Text wie bei `view` |
| `code` | `text`, `title` | Text in fester Schrift, zum Beispiel ein Diff |
| `table` | `columns`, `rows` (je Zeile `cells` und `actions`), `empty`, `title` | Tabelle mit Knöpfen je Zeile |
| `actions` | `actions`, `title` | eine Reihe Knöpfe |
| `form` | `fields`, `submit`, `title` | Formular; die Eingaben kommen als `values` zur Aktion |

- Ein Knopf ist `{"label": "…", "view": "…", "args": {…}}` (wechselt die Ansicht) oder `{"label": "…", "action": "…", "args": {…}}` (führt etwas aus). Bei einer Aktion fragt `confirm` vorher nach; `style` ist `primary` oder `danger`.
- Ein Feld hat `name`, `label`, `type` (`text`, `multiline`, `bool`, `choice` mit `options`), dazu `value`, `required`, `hint`.
- Namen von Ansichten, Aktionen, Feldern und `args` bestehen aus a–z, 0–9, `-` und `_`. Werte in `args` sind Texte.
- Nach einer Aktion zeigt devc `message` und lädt die Ansicht neu, oder die aus `view`.
- devc prüft jede Antwort und zeigt nur diese Bausteine. Die Antwort ist höchstens 1 MiB groß, eine Tabelle zeigt höchstens 500 Zeilen.
- Was der Nutzer eingibt, steht nur in der JSON-Zeile, nie in der Befehlszeile. Prüfe `args` trotzdem selbst: Der Browser des Nutzers kann schicken, was er will.

**Das Protokoll der Art `chat`.** devc startet `run` und schreibt eine JSON-Zeile je Nachricht auf dessen Standardeingabe. Das Programm antwortet mit einer JSON-Zeile je Ereignis auf der Standardausgabe. Vorlage: `examples/addons/claude-code/bridge.py`.

Von devc an das Programm:

| Zeile | Bedeutung |
|---|---|
| `{"type":"start","resume":null}` | Immer die erste Zeile. `resume` nennt eine frühere Unterhaltung. |
| `{"type":"send","text":"…"}` | Nachricht des Nutzers |
| `{"type":"permit","id":"…","allow":true,"always":false,"folder":false}` | Antwort auf eine Freigabe. `always`: dieses Werkzeug nicht mehr fragen. `folder`: den Ordner aus der Anfrage nicht mehr fragen |
| `{"type":"set","name":"…","value":"…"}` | Der Nutzer hat eine Einstellung geändert (siehe `settings` bei `ready`). Das Programm antwortet mit `ready` und dem neuen Stand. |
| `{"type":"interrupt"}` | Laufende Antwort abbrechen |
| `{"type":"end"}` | Der Nutzer hat »Beenden« geklickt. Das Programm hat noch 30 Sekunden, zum Beispiel für einen Titel. |

Vom Programm an devc:

| Ereignis | Felder | Bedeutung |
|---|---|---|
| `ready` | `agent`, `version`, `model`, `session`, `commands` | Wer antwortet; `session` ist die Kennung zum Fortsetzen. Am besten gleich nach dem Start senden. `commands` ist freiwillig: die Befehle für das Popup bei `/`, als Liste `[{"name": "clear", "hint": "", "description": "…"}]`. `settings` ist freiwillig: Einstellungen für das Fenster im Chat, als Liste `[{"name": "permissions", "label": "Freigaben", "hint": "…", "value": "ask", "options": [{"value": "ask", "label": "Nachfragen", "hint": "…"}]}]` |
| `status` | `state`: `idle`, `working`, `waiting`, `retry`, `ending`; `text` | Zustand. `text` ist freiwillig und sagt kurz, was das Programm gerade tut; der Chat zeigt es mit einer Uhr unter dem Verlauf. |
| `user` | `text` | Nachricht des Nutzers, für den Verlauf |
| `text_delta` · `text` | `text` | Stück der Antwort · fertige Antwort (Markdown) |
| `tool_start` | `id`, `name`, `summary`, `input` | Ein Werkzeug beginnt |
| `tool_end` | `id`, `ok`, `output` | Ergebnis des Werkzeugs |
| `permission` | `id`, `tool`, `summary`, `input`, `folder` | Bitte um Freigabe. `folder` ist freiwillig: der Ordner, für den das Programm »Im Ordner erlauben« anbietet |
| `permission_done` | `id`, `allow` | Die Freigabe ist beantwortet |
| `turn_end` | `ok`, `text`, `duration_ms`, `cost_usd` | Die Antwort ist zu Ende |
| `error` | `text` | Fehlermeldung |

- Schließt devc die Standardeingabe, beendet sich das Programm.
- Mit dem Argument `--sessions` gibt das Programm nur `{"sessions":[{"id":"…","title":"…","updated":1790000000}]}` aus und endet. Daraus entsteht die Liste »Frühere Unterhaltungen«.
- Mit `--show <kennung>` gibt das Programm den Verlauf dieser Unterhaltung als Ereignisse aus und endet. Das zeigt »Öffnen«.
- Mit `--stats <kennung>` gibt das Programm `{"stats":"<Markdown>"}` aus und endet. Das zeigt der Knopf »Statistik«. Wer keine Zahlen hat, gibt nichts aus; der Knopf meldet dann, dass es keine Statistik gibt.
- Bei `resume` gibt das Programm zuerst den alten Verlauf als Ereignisse aus (`user`, `text`, …). Den Verlauf legt es selbst unter `/home/dev` ab.
- Jede Zeile, die kein solches Ereignis ist, zeigt devc als Fehlermeldung. So siehst du auch Fehler der Shell.

## 8. Templates

```bash
devc template show pydev
devc template edit pydev              # mitgelieferte werden als eigene Kopie gespeichert
devc template validate ./mein.toml
devc template save py-tools mein-py   # Konfiguration eines Containers als Template
devc template rm mein-py              # nur eigene
```

Eigene Templates liegen als `~/.config/devc/templates/<id>.toml`. In einem Template steht `${name}` für den späteren Containernamen. Änderungen an einem Template wirken nie auf bestehende Container.

## 9. Secrets

```bash
devc secret set --shared anthropic            # fragt verdeckt nach dem Wert
devc secret set --shared git-deploy=@~/.ssh/deploy_key
devc secret list --shared
devc secret rm --shared anthropic

devc secret list py-tools                     # Slots mit Quelle, nie Werte
devc secret set py-tools claude-code.api_key=shared:anthropic
devc secret set py-tools claude-code.api_key=@datei
echo -n "wert" | devc secret set py-tools claude-code.api_key=-
devc secret rm py-tools claude-code.api_key
```

- Werte gibt man **nie** auf der Kommandozeile an; erlaubt sind die verdeckte Abfrage, `=@datei`, `=-` (stdin) und `=shared:<store>`.
- Ein Container bekommt eine **Kopie** des Store-Eintrags. Eine spätere Änderung im Store wirkt erst nach erneutem `devc secret set <name> <slot>=shared:<store>`.
- Im Container liegen Secrets als Dateien unter `/run/devc/secrets/` (nur für `dev` lesbar) und werden bei jedem Start neu geschrieben. Die Addons stellen sie in Login-Shells als Umgebungsvariable bereit, z. B. `ANTHROPIC_API_KEY`.
- Secrets stehen nie in der `devc.toml`, in Backups oder in Logs.

Für ein SSH-Git-Remote (`[git] remote = "ssh://…"`) hinterlegst du den Schlüssel im Slot `git.key`; devc trägt ihn in `~/.ssh/config` des Containers für den Host des Remotes ein. devc führt weder `git init` noch `git push` aus.

## 10. Snapshots und Backups

**Snapshots** sind Zeitpunkte auf demselben Host:

```bash
devc snapshot create py-tools --label vor-umbau
devc snapshot create py-tools --full          # zusätzlich der Container selbst; stoppt ihn kurz
devc snapshot list py-tools
devc snapshot restore py-tools vor-umbau      # fragt nach; neuere Änderungen gehen verloren
devc snapshot rm py-tools vor-umbau
```

Ohne `--label` heißt der Snapshot `auto-<datum>-<zeit>`. Beim Zurücksetzen wird der Container kurz gestoppt. Bei einem Shared Folder sichert devc den Host-Ordner als tar und **ersetzt beim Zurücksetzen seinen gesamten Inhalt**.

**Backups** sind portable Archive, auch für einen anderen Host:

```bash
devc backup py-tools --label vor-update       # nach ~/.local/share/devc/backups/
devc backup py-tools -o /mnt/sicherung --no-home
devc restore <archiv> --name py-tools2 --edit # als neuer Container, devc.toml vorher anpassen
devc restore <archiv> --replace py-tools      # vorhandenen ersetzen; fragt nach
```

Während ein Backup entsteht oder wiederhergestellt wird, zeigt devc den Fortschritt je Datenbereich: in der Oberfläche als Balken unter der Meldung, im Terminal als Zeile mit Prozent und MiB. Beim Backup ist die Gesamtgröße geschätzt (»von etwa …«), beim Wiederherstellen ist sie genau. Du kannst die Seite währenddessen verlassen: Der Vorgang läuft weiter, und zurück auf einer Seite des Containers ist die Anzeige wieder da. In der Oberfläche sichert »Backup jetzt« auf der Seite Backups sofort alles, ohne weitere Angaben.

Die Datei endet auf `.tar`, die Daten darin sind trotzdem gepackt: Sie liegen als `data/work.tar.gz` und `data/home.tar.gz` im Archiv.

Ein Backup enthält `devc.toml`, die Datenbereiche und Prüfsummen, aber keine Secrets. Beim Wiederherstellen als neuer Container müssen die Secrets deshalb im Store liegen. Ein beschädigtes Archiv lehnt devc ab, bevor es etwas ändert. `--replace` behält die Secrets des Containers und ersetzt nur die Datenbereiche, die das Backup enthält.

## 11. Shared Folders

Statt des Code-Volumes kann ein Host-Ordner als `/work` dienen, etwa um mit einer IDE auf dem Host an denselben Dateien zu arbeiten.

1. In `~/.config/devc/config.toml` den übergeordneten Ordner eintragen: `allowed_roots = ["/home/<du>/devc-shared"]`
2. `devc init` ausführen.
3. Den Projektordner anlegen und in der `devc.toml` verwenden:

```toml
[data]
shared = "/home/<du>/devc-shared/py-tools"
```

Der Ordner muss existieren und unter einem Eintrag von `allowed_roots` liegen, sonst endet der Befehl mit `E_SECURITY`. Dateien, die `dev` im Container anlegt, gehören auf dem Host dir. `lxd.shared_mode` bleibt auf dem Standard `idmap`; `shift` lehnt LXD im eingeschränkten Projekt ab.

## 12. Web-GUI

```bash
devc serve                # http://127.0.0.1:8740/
devc token show           # Token für die Anmeldung
devc token rotate         # neues Token, meldet alle Browser ab
```

`devc serve` läuft im Vordergrund, bis du es mit Strg+C beendest. Für den Betrieb im Hintergrund liegen im Quelltext zwei Skripte:

```bash
./start.sh                # startet devc im Hintergrund, zeigt Adresse und Log
./stop.sh                 # stoppt devc; die Container laufen weiter
```

Gibt es devc als Dienst (in einer VM nach Abschnitt 15), starten und stoppen die Skripte den Dienst `devc.service`. Sonst läuft devc als Prozess deines Nutzers; PID und Log liegen unter `~/.local/state/devc/`. Optionen reicht `start.sh` an `devc serve` weiter, zum Beispiel `./start.sh --config <pfad>`. Mit dem Stoppen enden offene Terminal-Sitzungen.

Die GUI bietet dasselbe wie die CLI: Container anlegen und bearbeiten, Addon-Aktionen, Dateien, Secrets, Snapshots, Backups, Job-Logs und ein Terminal im Browser. Dazu kommen die Kacheln aus Addons, zum Beispiel »Info« und der Chat »Claude« (Abschnitt 7). Das Terminal füllt das Browserfenster und passt Spalten und Zeilen an, wenn du das Fenster vergrößerst oder verkleinerst; »Vollbild« nimmt den ganzen Bildschirm. Jede Terminal-Sitzung ist ein Tab; mit + oder »Neue Sitzung« öffnest du eine weitere (als `dev` oder `root`, je nach Auswahl daneben), mit × beendest du sie. Sitzungen laufen weiter, wenn du die Seite neu lädst, woanders hin navigierst oder den Browser schließt: Beim nächsten Öffnen sind die Tabs samt bisheriger Ausgabe wieder da. Sie enden mit `exit`, mit dem Stoppen des Containers und mit `devc serve`; pro Container sind höchstens 8 gleichzeitig möglich. Einen Container löschst du über »Container löschen …« oben rechts auf seinem Dashboard; der Knopf führt zur Seite Konfiguration, wo du wählst, ob die Daten bleiben, und das Löschen bestätigst.

| Aufruf | Verhalten |
|---|---|
| `devc serve` | nur vom Host selbst erreichbar |
| `devc serve --bind 0.0.0.0 --tls-cert c.pem --tls-key k.pem` | aus dem Netz, verschlüsselt |
| `devc serve --bind 0.0.0.0 --allow-insecure-bind` | aus dem Netz ohne TLS: Warnung, rotes Banner, Web-Terminal aus |
| zusätzlich `--allow-insecure-terminal` | Web-Terminal trotzdem an |
| `devc serve --bind 0.0.0.0` | Abbruch mit Exit 6 |

Wer das Token kennt, steuert die Container und damit faktisch den Host. Ohne TLS gehört der Server nicht in ein Netz, dem du nicht vertraust. Für den Zugriff von einem anderen Rechner ist ein SSH-Tunnel die einfachste sichere Lösung: `ssh -L 8740:127.0.0.1:8740 <host>`.

Jobs verfolgst du auch ohne GUI:

```bash
devc jobs --container py-tools
devc jobs --follow <job-id>
```

### Hinter einem Proxy (Caddy)

Läuft devc in einer eigenen VM, nimmt ein Reverse-Proxy in derselben VM die Anfragen an und reicht sie an devc weiter. devc lauscht dann nur auf `127.0.0.1`; TLS macht der Proxy. In der `config.toml`:

```toml
[server]
base_domain = "localhost"     # GUI und API liegen unter devc.<base_domain>
public_port = 8443            # nur nötig, wenn der Browser einen anderen Port als 443 benutzt
behind_proxy = true
```

Das Caddyfile erzeugt devc aus diesen Einstellungen; einspielen musst du es selbst:

```bash
devc proxy config | sudo tee /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Statt der eigenen CA von Caddy kannst du ein eigenes Zertifikat für `*.<base_domain>` eintragen: `tls_cert` und `tls_key` in `[server]` setzen (lesbar für den Nutzer `caddy`) und das Caddyfile neu erzeugen. In einer VM nach Abschnitt 15 macht `sudo /opt/devc/install/install.sh` beides.

`devc doctor` meldet, wenn die Einstellungen nicht zusammenpassen oder Caddy fehlt. In diesem Betrieb gilt:

- devc beantwortet nur Anfragen, die der Proxy unter dem Namen `devc.<base_domain>` weiterreicht. Der direkte Aufruf von Port 8740 wird abgelehnt, auch vom Rechner selbst.
- `devc serve --bind 0.0.0.0` bricht ab; die Schalter `--allow-insecure-bind` und `--tls-cert` gehören zum Betrieb ohne Proxy.
- Erlaubt ist nur HTTPS. Ausnahme sind Namen unter `localhost`: Dort geht auch `http://`, weil der Zugriff über einen SSH-Tunnel läuft.

**Zugriff über einen SSH-Tunnel (`*.localhost`).** Chrome und Firefox lösen jeden Namen unter `localhost` selbst auf `127.0.0.1` auf, ohne DNS. Ein Tunnel vom eigenen Rechner zum Proxy genügt:

```bash
ssh -N -L 8443:<adresse-der-vm>:443 <nutzer>@<rechner>
# dann im Browser: https://devc.localhost:8443/
```

Caddy stellt das Zertifikat aus einer eigenen CA aus; der Browser warnt, bis du die Ausnahme bestätigst oder die CA importierst (`/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt` in der VM). Ohne Warnung geht es unverschlüsselt durch den Tunnel, der selbst verschlüsselt. Dafür das Caddyfile mit `--http-port` erzeugen (nur für Basisdomains unter `localhost`):

```bash
devc proxy config --http-port 8080 | sudo tee /etc/caddy/Caddyfile
ssh -N -L 8080:<adresse-der-vm>:8080 <nutzer>@<rechner>
# dann im Browser: http://devc.localhost:8080/
```

Wer den Proxy unverschlüsselt über eine Adresse aufruft, die keiner dieser Namen ist (zum Beispiel über die IP-Adresse), bekommt eine Hinweisseite statt einer leeren Antwort: mit dem Link zur Oberfläche, bei der Basisdomain `localhost` den Befehlen für die Weiterleitung und dem Befehl für den Token. Sie steht im Caddyfile aus `devc proxy config`; ein älteres Caddyfile erzeugst du dafür neu.

### Dienste im Container im Browser öffnen

Hinter dem Proxy ist jeder Port ab 1024 eines laufenden Containers unter einem eigenen Namen erreichbar:

```
https://<port>-<container>.<base_domain>/        zum Beispiel https://8000-py-tools.localhost:8443/
```

- Der Dienst muss im Container auf `0.0.0.0` lauschen, nicht nur auf `127.0.0.1`.
- Beim ersten Aufruf landest du auf der Anmeldeseite von devc und danach wieder beim Dienst. Bist du bei devc schon angemeldet, merkst du davon nichts.
- Jeder Port hat seine eigene Anmeldung (12 Stunden). »Abmelden« in der GUI beendet alle.
- Die App im Container sieht weder dein devc-Token noch die Cookies von devc.
- Ports unter 1024 sind gesperrt. Ein gestoppter oder unbekannter Container ergibt »nicht gefunden«; lauscht im Container nichts auf dem Port (oder nur auf `127.0.0.1`), meldet der Proxy Fehler 502 mit dem Hinweis »antwortet kein Dienst«.
- WebSocket und Server-Sent Events gehen durch.

Die Links dazu stehen auf der Addon-Seite des Containers:

- **Addons mit eigenem Dienst**, zum Beispiel »Im Browser öffnen« (`httpserver`) und »opencode öffnen«. Ein Link ist aktiv, solange die zugehörige Aktion läuft. Für opencode: »Weboberfläche starten«, dann »opencode öffnen«.
- **Das Addon `webproxy`** für alles andere, etwa einen Entwicklungsserver, den du selbst im Terminal startest. In der Konfiguration des Containers:

  ```toml
  addons = ["python", "webproxy"]

  [params.webproxy]
  proxy = "3000=Vite 8080"      # ergibt die Links »Vite« und »Port 8080«
  ```

  Danach »Anwenden« (oder `devc config apply <name>`). Beim Anlegen geht es auch direkt: `devc create web --addon webproxy --param "webproxy.proxy=3000=Vite 8080"`.

Erreichbar ist ein Port auch ohne Eintrag, wenn du die Adresse von Hand eingibst; das Addon liefert nur die Links.

**Ports ohne Anmeldung (für APIs).** Programme, die eine API im Container aufrufen, können die Anmeldung von devc meist nicht mitmachen: Sie bekämen statt der Antwort die Umleitung zur Anmeldeseite. Für solche Ports hat das Addon `webproxy` eine zweite Liste:

```toml
[params.webproxy]
proxy = "3000=Vite"        # mit Anmeldung
public = "8080=API"        # ohne Anmeldung
```

Ein Port in `public` ist für jeden offen, der den Proxy erreicht, also je nach Aufbau für das ganze Netz oder das Internet. devc prüft dort nichts mehr; die Anwendung muss sich selbst schützen (zum Beispiel mit einem eigenen API-Schlüssel). Die Addon-Seite kennzeichnet solche Links mit »ohne Anmeldung erreichbar«. In einem eigenen Addon bewirkt `public = true` im Link dasselbe.

In einem eigenen Addon meldest du einen Link so an (`port` ist eine Zahl oder der Platzhalter eines `int`-Parameters, `path` ist optional):

```toml
[[links]]
label = "App öffnen"
port = "{port}"
path = "/"
when = "start"        # nur aktiv, solange die Aktion start läuft
```

## 13. Fehler und Hilfe

Fehler erscheinen als `Fehler [E_CODE]: <Meldung>`; Prüfbefunde tragen ihre Nummer, z. B. `L5 addons: Addon abap benötigt node`.

| Code | Exit | Bedeutung | Was tun |
|---|---|---|---|
| `E_USAGE` | 2 | falscher Aufruf | `devc <befehl> --help` |
| `E_ENGINE` | 3 | `lxc` fehlt oder meldet einen Fehler | `devc doctor`, mit `-v` wiederholen |
| `E_NOT_FOUND` | 4 | Container, Addon, Template … fehlt | Namen prüfen |
| `E_CONFLICT` | 5 | Name belegt, Container beschäftigt oder läuft nicht | Zustand mit `devc list` prüfen |
| `E_VALIDATION` | 5 | eine Prüfung schlägt fehl | Meldung nennt Prüfnummer und Stelle |
| `E_MISSING_SECRET` | 5 | ein Secret hat keinen Wert | `devc secret set …` |
| `E_SECURITY` | 6 | Pfad außerhalb des Erlaubten, unsicherer Bind | Abschnitt 11 bzw. 12 |
| `E_JOB_FAILED` | 1 | ein Schritt eines Jobs schlug fehl | Log: `devc jobs --follow <job-id>` |
| `E_INTERNAL` | 1 | Fehler in devc selbst | mit `-v` wiederholen und melden |

Häufige Fälle:

- **„Container ist beschäftigt“**: Es läuft schon ein Job für diesen Container. `devc jobs --container <name>` zeigt ihn.
- **Anlegen bricht in einem Addon ab**: Das Job-Log zeigt die Ausgabe des Install-Skripts. Nach der Korrektur `devc config apply <name>`.
- **`devc doctor` meldet Abweichungen am Projekt**: `devc init` ausführen.
- **Waisen (W3)**: Verzeichnisse oder Volumes ohne Container. devc löscht sie nicht selbst; `doctor` nennt sie.

Job-Logs liegen unter `~/.local/share/devc/logs/`. `devc doctor --cleanup` löscht Logs, die älter als 30 Tage sind; von selbst löscht devc nichts.

## 14. Wo liegt was

| Ort | Inhalt |
|---|---|
| `~/.config/devc/config.toml` | Konfiguration |
| `~/.config/devc/token` | API-Token |
| `~/.config/devc/addons/`, `templates/` | eigene Addons und Templates |
| `~/.config/devc/secrets/` | Secret-Store und Secret-Kopien der Container |
| `~/.local/share/devc/containers/<name>/devc.toml` | Konfiguration eines Containers |
| `~/.local/share/devc/backups/` | Backups |
| `~/.local/share/devc/logs/` | Job-Logs |
| `~/.cache/devc/` | entpackte mitgelieferte Addons aus `devc.pyz` |
| LXD-Projekt `devcontainers` | Container und Volumes `devc-<name>-work`, `devc-<name>-home` (nachsehen mit `lxc`: Abschnitt 16) |

### devc wieder entfernen

1. Alle Container löschen: `devc list`, dann je Container `devc rm <name>`.
2. Das leere LXD-Projekt entfernen: `lxc project delete devcontainers`
3. Die Verzeichnisse `~/.config/devc`, `~/.local/share/devc` und `~/.cache/devc` löschen. Vorher Backups und eigene Addons sichern, falls du sie behalten willst.

## 15. devc in einer eigenen VM installieren

Eine VM ist ein Mandant: eigenes LXD, eigenes devc, eigener Token. In der VM laufen LXD, devc als Dienst und der Proxy Caddy; nach außen zeigt die VM nur Caddy. Mehrere Nutzer oder Projekte bekommen je eine eigene VM.

Die Kurzfassung steht in Abschnitt 2 (Schnellstart). Hier stehen alle Einzelheiten: die Datei für den ersten Start selbst erzeugen, eine frische Ubuntu-VM damit starten, warten, Token holen. Die Dateien dafür liegen im Ordner `vm/` des Quelltexts; `vm/README.md` enthält dasselbe wie dieser Abschnitt, `docs/BAUEN.md` erklärt das Bauen Schritt für Schritt.

### Was die VM braucht

| | Mindestens | Empfohlen |
|---|---|---|
| CPU | 2 | 4 |
| RAM | 4 GiB | 8 GiB oder mehr |
| Platte | 20 GiB | 60 GiB oder mehr |

- Ubuntu 24.04 (Cloud-Image) und Netzzugang beim ersten Start, für LXD, Caddy und die Container-Images.
- Die Templates verlangen je Container 4 CPUs und 8 GiB RAM. Das sind Obergrenzen; ein Container bekommt nie mehr, als die VM hat.
- Die Container liegen direkt auf der Platte der VM (Speicherpool vom Typ `dir`) und teilen sich deren freien Platz. Das ist die schnellste Art für kleine Schreibvorgänge. Sind nach dem Start weniger als 7 GiB frei, bricht die Einrichtung mit einer Meldung ab.
- Wer lieber sehr schnelle, platzsparende Snapshots hat als schnelles Schreiben, wählt in Schritt 1 `--pool-driver btrfs`. Was sich dadurch ändert, steht in Abschnitt 16, »Speicher: `dir` oder `btrfs`«.

### Empfohlen: ein eigener Speicherpool für die VMs

LXD legt die Platte einer VM in einem Speicherpool des Rechners ab. Liegt dieser Pool auf ZFS oder btrfs, sind kleine Schreibvorgänge in der VM langsamer. Das merkst du bei `apt install`, `npm install`, `git` und beim Anlegen von Containern.

Schneller ist ein Pool vom Typ `dir`: Dort liegt die Platte der VM als einfache Datei im Dateisystem des Rechners. Kleine Schreibvorgänge sind damit schneller. Du legst den Pool einmal an, neben dem vorhandenen:

```bash
lxc storage create devc-vms dir
```

Soll der Pool auf einer bestimmten Platte liegen, gibst du einen leeren Ordner an:

```bash
sudo mkdir -p /srv/devc-vms
lxc storage create devc-vms dir source=/srv/devc-vms
```

Beim Anlegen der VM nennst du den Pool mit `-s devc-vms`. Sonst ändert sich nichts:

```bash
lxc init ubuntu:24.04 devc-acme --vm -s devc-vms -c limits.cpu=4 -c limits.memory=8GiB -d root,size=60GiB < /dev/null
lxc config set devc-acme user.user-data - < user-data
lxc start devc-acme
```

Eine vorhandene VM ziehst du so um. Sie ist dabei gestoppt, Container und Daten bleiben. Der neue Pool braucht dafür freien Platz in der vollen Größe der VM-Platte; reicht er nicht, bricht LXD ab und die VM bleibt, wo sie war:

```bash
lxc stop devc-acme
lxc move devc-acme -s devc-vms
lxc start devc-acme
```

Das musst du wissen:

- **Snapshots der ganzen VM sind volle Kopien.** `lxc snapshot devc-acme` dauert bei `dir` länger und belegt jedes Mal den Platz der ganzen Platte. Bei ZFS und btrfs kostet ein Snapshot am Anfang fast nichts.
- **Die Snapshots von devc betrifft das nicht.** Snapshots und Backups deiner Container (Abschnitt 10) entstehen in der VM; wie schnell sie sind, hängt vom Pool in der VM ab (Abschnitt 16).
- **Die ganze VM sichern** geht ohne Snapshot, als Datei: `lxc export devc-acme devc-acme.tar.gz`, zurück mit `lxc import devc-acme.tar.gz`.

Der Pool ist eine Empfehlung, keine Pflicht. Ohne `-s` landet die VM im Standardpool von LXD und läuft dort auch.

### Schritt 1: Die Datei für den ersten Start erzeugen

```bash
python3 build.py                                  # erzeugt dist/devc.pyz
python3 vm/make-user-data.py --ssh-key ~/.ssh/id_ed25519.pub -o user-data
```

| Option | Bedeutung | Standard |
|---|---|---|
| `--base-domain` | Basisdomain. GUI unter `devc.<basisdomain>`, Container-Ports unter `<port>-<container>.<basisdomain>` | `localhost` |
| `--public-port` | Port, den der Browser benutzt, wenn es nicht 443 ist (zum Beispiel 8443 bei einem Tunnel) | `443` |
| `--http-port` | Zusätzlich unverschlüsselt auf diesem Port. Nur für Basisdomains unter `localhost`, gedacht für den Zugriff über einen SSH-Tunnel | aus |
| `--pool-driver` | Speicherpool für die Container: `dir` (direkt auf der Platte der VM) oder `btrfs` (eine Pool-Datei fester Größe) | `dir` |
| `--pool-size` | Nur mit `--pool-driver btrfs`: Größe des Pools, zum Beispiel `40GiB` | 70 % des freien Platzes |
| `--ssh-key` | Öffentlicher SSH-Schlüssel für den Nutzer `ubuntu` in der VM (mehrfach möglich) | keiner |

Für den Zugriff über einen SSH-Tunnel, ohne eigene Domain:

```bash
python3 vm/make-user-data.py --public-port 8443 --http-port 8080 --ssh-key ~/.ssh/id_ed25519.pub -o user-data
```

Die Datei `user-data` enthält das Einrichtungs-Skript und `devc.pyz` und ist etwa 300 KiB groß. Sie enthält kein Geheimnis; der Token entsteht erst in der VM. Sie ist eine Kopie vom Zeitpunkt des Erzeugens: Nach einer Änderung an devc musst du sie neu erzeugen, sonst bekommen neue VMs den alten Stand.

### Schritt 2: Die VM anlegen

Mit LXD, wenn auf dem Rechner LXD läuft:

```bash
lxc init ubuntu:24.04 devc-acme --vm -c limits.cpu=4 -c limits.memory=8GiB -d root,size=60GiB < /dev/null
lxc config set devc-acme user.user-data - < user-data
lxc start devc-acme
```

Die Datei ist zu groß für die Befehlszeile. Darum erst `lxc init` und danach `lxc config set … -`, das von der Standardeingabe liest. `lxc launch -c user.user-data=…` scheitert mit »Argument list too long«.

Mit `virt-install`, auf Linux mit KVM und libvirt:

```bash
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
qemu-img create -f qcow2 -F qcow2 -b "$PWD/noble-server-cloudimg-amd64.img" devc-acme.qcow2 60G
virt-install --name devc-acme --memory 8192 --vcpus 4 \
  --disk path=devc-acme.qcow2,format=qcow2 \
  --cloud-init user-data=user-data \
  --os-variant ubuntu24.04 --network network=default --import --noautoconsole
```

### Schritt 3: Warten und den Token holen

Der erste Start dauert einige Minuten. Die VM ist fertig, wenn cloud-init es meldet:

```bash
lxc exec devc-acme -- cloud-init status --wait
lxc exec devc-acme -- tail -5 /var/log/cloud-init-output.log
```

Ohne LXD dasselbe per SSH: `ssh ubuntu@<vm> cloud-init status --wait`. Die letzten Zeilen des Protokolls nennen die Adresse der GUI. Steht dort `FEHLER:`, nennt die Zeile den Grund; nach der Korrektur in der VM `sudo /opt/devc/install/install.sh` ausführen.

Den Token gibt es nur auf der Konsole der VM:

```bash
lxc exec devc-acme -- devc token show             # oder: ssh ubuntu@<vm> devc token show
```

In der VM läuft `devc` immer als Nutzer `devc`, egal wer es aufruft. Alle Befehle aus diesem Handbuch gehen dort unverändert.

### Schritt 4: Im Browser öffnen

Mit der Basisdomain `localhost` über einen SSH-Tunnel zu dem Rechner, auf dem die VM läuft. `<vm>` ist die Adresse der VM aus `lxc list`:

```bash
ssh -N -L 8080:<vm>:8080 <nutzer>@<rechner>       # dann http://devc.localhost:8080/
ssh -N -L 8443:<vm>:443 <nutzer>@<rechner>        # dann https://devc.localhost:8443/
```

Bei HTTPS warnt der Browser, weil Caddy das Zertifikat aus einer eigenen CA ausstellt (Abschnitt 12, »Hinter einem Proxy«).

**Die VM läuft auf deinem eigenen Rechner.** Auch dann brauchst du eine Weiterleitung: Der Name `devc.localhost` zeigt im Browser immer auf deinen Rechner selbst (`127.0.0.1`), nie auf die VM. Am einfachsten geht es mit `socat`:

```bash
sudo apt install socat
socat TCP-LISTEN:8080,bind=127.0.0.1,fork,reuseaddr TCP:<vm>:8080     # dann http://devc.localhost:8080/
```

Der Befehl läuft, bis du ihn mit Strg+C beendest. Ohne offenes Terminal:

```bash
systemd-run --user --unit devc-forward socat TCP-LISTEN:8080,bind=127.0.0.1,fork,reuseaddr TCP:<vm>:8080
systemctl --user stop devc-forward        # wieder beenden
```

Nach einem Neustart des Rechners wiederholst du den Befehl. Läuft auf dem Rechner ein SSH-Server, geht auch der Tunnel von oben mit `localhost` als Ziel: `ssh -N -L 8080:<vm>:8080 localhost`.

- Für eine zweite VM nimmst du vorn einen anderen Port: `TCP-LISTEN:8081`, dann `http://devc.localhost:8081/`. Die Links übernehmen den Port aus der Adresszeile.
- Die Adresse der VM kann sich nach einem Neustart ändern. So bleibt sie fest: `lxc config device override devc-vm eth0 ipv4.address=<vm>`.

**Die Adresse der VM direkt im Browser** (`http://<vm>/` oder `http://<vm>:8080/`) führt nicht zur Oberfläche: Caddy entscheidet nach dem Namen, wohin eine Anfrage geht. Du siehst dort eine Hinweisseite mit dem richtigen Link, dem passenden Befehl für die Weiterleitung (die Adresse der VM ist schon eingesetzt) und dem Befehl für den Token. Die Seite zeigt keine Geheimnisse.

**Mehrere VMs** bedienst du nebeneinander, indem jede ihren eigenen Port auf deinem Rechner bekommt:

```bash
ssh -N -L 8080:<vm1>:8080 -L 8081:<vm2>:8080 <nutzer>@<rechner>
# VM 1: http://devc.localhost:8080/     VM 2: http://devc.localhost:8081/
```

Links und Anmeldung übernehmen den Port aus der Adresszeile; an den VMs ist dafür nichts einzustellen. Jede VM hat ihren eigenen Token und ihre eigene Anmeldung.

### Was die Einrichtung in der VM anlegt

1. LXD als Snap mit dem Speicherpool `default` (Typ `dir`, auf Wunsch `btrfs`) und dem Netz `lxdbr0`.
2. Caddy in einer festen Version, von GitHub geladen und gegen die Prüfsumme geprüft.
3. Den Nutzer `devc` (Gruppe `lxd`), `devc.pyz` unter `/opt/devc/` und den Aufruf `devc`.
4. Die `config.toml` unter `/home/devc/.config/devc/` mit `base_domain`, `public_port` und `behind_proxy = true`.
5. Das Caddyfile aus `devc proxy config`. Ein vorhandenes fremdes Caddyfile bleibt als `Caddyfile.vor-devc` erhalten.
6. Den Dienst `devc.service`. Er lauscht nur in der VM auf `127.0.0.1:8740` und startet nach einem Absturz neu.
7. Das Terminal der VM: den Ordner `/opt/devc/vmshell/` mit den erlaubten Befehlen.
8. Das Update aus der Oberfläche: das Skript `/opt/devc/install/devc-update` und eine sudo-Regel, die `devc` genau dieses Skript erlaubt.

Von außen erreichbar ist nur Caddy: die Ports 80 und 443 und der unverschlüsselte Port, falls gesetzt.

### Die Kachel »VM«: Terminal und Update

Auf dem Dashboard steht die Kachel **VM**. Sie öffnet ein Terminal in der VM selbst, nicht in einem Container.

```bash
devc list                       # alle Container
lxc list --project devcontainers
journalctl -u devc -n 50        # Log von devc
df -h /                         # freier Platz
commands                        # zeigt die erlaubten Befehle
```

- Das Terminal ist beschränkt. Erlaubt sind `devc`, `lxc` und einige Werkzeuge zum Nachsehen: `systemctl`, `journalctl`, `df`, `du`, `free`, `ps`, `top`, `uptime`, `ip`, `ss`, `ping`, `curl`, `ls`, `cat`, `less`, `grep`, `head`, `tail`, `wc`, `sort`, `date`, `hostname`, `id`, `clear`.
- Es gibt kein `cd` und keine Umleitung in Dateien. Pipes gehen: `lxc list | grep it-`.
- Du arbeitest als Nutzer `devc`, ohne `sudo`. `systemctl` zeigt den Zustand, ändert aber nichts.
- Die Beschränkung schützt vor Versehen. Sie ist keine Sicherheitsgrenze: Mit `lxc` lässt sich in der VM alles erreichen. Darum gibt es die Kachel nur dort, wo auch das Web-Terminal erlaubt ist.
- Ohne Browser: `devc vm shell`. Was eingerichtet ist, zeigt `devc vm info`.

**devc aktualisieren.** Auf der Seite der VM steht der Knopf »devc aktualisieren …«.

1. Wähle das neue `devc.pyz` aus.
2. Klicke »Hochladen und aktualisieren« und bestätige.
3. devc prüft die Datei, richtet sie ein und startet neu. Die Seite lädt sich danach von selbst neu und zeigt die Anmeldung.

- Alle Terminal-Sitzungen und Unterhaltungen enden dabei, und du meldest dich neu an. Die Container laufen weiter.
- Neben der Version steht eine Kennung, zum Beispiel `devc 1.0.0 · 3f9a1c2b7d10`. An ihr erkennst du, ob die neue Fassung läuft.
- Ohne Browser: `devc vm update dist/devc.pyz`.
- Meldet sich die neue Fassung nicht, sieh in der VM nach: `journalctl -u 'devc-update-*' -n 50`.

### Später: aktualisieren, ändern, prüfen

Das Einrichtungs-Skript liegt in der VM unter `/opt/devc/install/install.sh`. Es ist wiederholbar: Ein weiterer Lauf ändert nur, was fehlt oder neu ist, und löscht nichts.

| Aufgabe | In der VM |
|---|---|
| devc aktualisieren | In der Oberfläche: Kachel »VM«, »devc aktualisieren …«. Von Hand: neues `devc.pyz` nach `/opt/devc/install/devc.pyz` kopieren, dann `sudo /opt/devc/install/install.sh`. Der Dienst startet neu; offene Terminal-Sitzungen enden. |
| Auch `vm/install.sh` ist neu | Die neue Fassung nach `/opt/devc/install/install.sh` kopieren, dann `sudo /opt/devc/install/install.sh`. Das Update aus der Oberfläche tauscht nur `devc.pyz`. |
| Einstellungen ändern | `/home/devc/.config/devc/config.toml` bearbeiten, dann `sudo /opt/devc/install/install.sh` |
| Unverschlüsselten Port ändern oder abschalten | `DEVC_HTTP_PORT` in `/etc/devc/install.conf` ändern oder leeren, dann `install.sh` |
| Zustand prüfen | `devc doctor`, `systemctl status devc caddy`, `journalctl -u devc` |
| Neuen Token erzeugen | `devc token rotate` |

- Basisdomain und Port aus Schritt 1 gelten nur für die erste Einrichtung. Danach gilt die `config.toml`.
- Nach einem Neustart der VM laufen LXD, Caddy und devc von selbst wieder. Die Container bleiben gestoppt, bis du sie startest.
- Ein neues `devc.pyz` bringst du mit `lxc file push dist/devc.pyz devc-acme/opt/devc/install/devc.pyz` oder per `scp` in die VM.

## 16. LXD: nachsehen und warten

devc erledigt alles über LXD. Im Alltag brauchst du LXD nicht selbst zu bedienen. Für einen Blick hinter die Kulissen und für die Wartung hilft der Befehl `lxc`. Dieser Abschnitt zeigt die wichtigsten Handgriffe.

In einer VM nach Abschnitt 15 gibst du die Befehle in der VM ein, zum Beispiel nach `lxc exec devc-vm -- bash` oder per SSH.

**Die Grundregel:** Mit `lxc` nachsehen ist immer sicher. Ändern, anlegen und löschen machst du mit `devc`. devc führt eine eigene Liste seiner Container und Snapshots; was du mit `lxc` an ihm vorbei änderst, kennt es nicht.

### Das Projekt `devcontainers`

devc legt alles in einem eigenen LXD-Projekt ab, getrennt vom Projekt `default`:

- devc fasst nur an, was in seinem Projekt liegt. Container, die du selbst in `default` angelegt hast, bleiben unberührt.
- Das Projekt ist eingeschränkt: Ein Container darf nur Ordner des Hosts einbinden, die in `allowed_roots` stehen (Abschnitt 11).

`lxc` zeigt von sich aus das Projekt `default`. Dort sind die Container von devc nicht zu sehen. Es gibt zwei Wege:

```bash
# Weg 1: das Projekt bei jedem Befehl nennen
lxc list --project devcontainers

# Weg 2: einmal umschalten, danach gilt das Projekt für alle lxc-Befehle
lxc project switch devcontainers
lxc list

# zurückschalten
lxc project switch default

# Welches Projekt gilt gerade? Es trägt den Zusatz "(current)".
lxc project list
```

Das Umschalten gilt nur für deinen Nutzer und bleibt bestehen, auch nach einer neuen Anmeldung. devc selbst merkt davon nichts: Es nennt sein Projekt bei jedem Aufruf selbst.

Alle Beispiele weiter unten gehen davon aus, dass du umgeschaltet hast. Sonst hängst du `--project devcontainers` an.

Der Name des Projekts steht in der `config.toml` unter `lxd.project`. Stelle ihn nicht auf `default`: `devc init` würde dann dein Standardprojekt einschränken.

### Container ansehen

```bash
lxc list                       # alle Container mit Zustand und Adresse
lxc info py-tools              # Einzelheiten: Prozesse, Speicher, Platte, Netz
lxc config show py-tools       # Grenzen (CPU, RAM) und eingebundene Datenbereiche
lxc info py-tools --show-log   # Protokoll von LXD, wenn ein Container nicht startet
```

Eine Shell bekommst du besser mit `devc shell py-tools`: Sie startet als Nutzer `dev` in `/work`. `lxc exec py-tools -- bash` startet als root.

### Speicher: Wie voll ist es?

Alle Container teilen sich einen Speicherpool. Er heißt `default`. In einer VM nach Abschnitt 15 ist er vom Typ `dir`: Die Container liegen direkt auf der Platte der VM. Ist die Platte voll, scheitern Installationen, Snapshots und Backups.

```bash
df -h /                        # freier Platz auf der Platte
lxc storage info default       # dasselbe aus Sicht von LXD, dazu die Art des Pools ("driver")
```

Wer wie viel belegt, zeigt bei einem `dir`-Pool die Größe der Ordner:

```bash
sudo du -sh /var/snap/lxd/common/lxd/storage-pools/default/*/*
```

| Ordner | Was ist das? |
|---|---|
| `containers/devcontainers_<name>` | das System des Containers mit allen installierten Programmen |
| `custom/devcontainers_devc-<name>-work` | der Code unter `/work` |
| `custom/devcontainers_devc-<name>-home` | das Home unter `/home/dev` |
| `custom-snapshots/…` | Snapshots der Datenbereiche |
| `containers-snapshots/…` | Snapshots des Systems (von `--full`) |

Bei einem `btrfs`-Pool zeigt `lxc storage volume list default -c tnU` dieselben Einträge mit ihrer Größe.

### Speicher: Platz schaffen

In dieser Reihenfolge, das Harmloseste zuerst:

```bash
# 1. Alte Snapshots löschen
devc snapshot list py-tools
devc snapshot rm py-tools auto-20261011-144402

# 2. Container löschen, die du nicht mehr brauchst (fragt nach)
devc backup alt-projekt        # vorher sichern, falls du unsicher bist
devc rm alt-projekt

# 3. Reste suchen, die zu keinem Container gehören
devc doctor                    # nennt sie in der Zeile "orphans"

# 4. Alte Job-Logs löschen (älter als 30 Tage)
devc doctor --cleanup
```

Backups liegen als Dateien unter `~/.local/share/devc/backups/`. Auch sie belegen die Platte:

```bash
du -sh ~/.local/share/devc/backups/
df -h /
```

In einer VM liegen sie unter `/home/devc/.local/share/devc/backups/`.

Geladene Ubuntu-Abbilder belegen je einige hundert MiB. LXD räumt unbenutzte nach einigen Tagen selbst weg. `lxc image list --project default` zeigt sie.

### Speicher: mehr Platz

Mit einem `dir`-Pool gibt es nichts am Pool einzustellen: Mehr Platz heißt eine größere Platte für die VM. Auf dem Rechner, auf dem die VM läuft:

```bash
lxc config device override devc-vm root size=100GiB
lxc restart devc-vm
```

Nach dem Neustart steht der neue Platz in der VM bereit (`df -h /`). Verkleinern geht nicht.

### Speicher: `dir` oder `btrfs`

| | `dir` (Standard) | `btrfs` |
|---|---|---|
| Wo liegen die Container? | direkt auf der Platte der VM | in einer Pool-Datei fester Größe |
| Kleine Schreibvorgänge (`apt`, `npm`, `git`) | schnell | langsamer |
| Snapshot | volle Kopie: dauert und braucht den Platz der Daten | sofort fertig, belegt am Anfang fast nichts |
| Neuer Container | LXD kopiert das Ubuntu-Abbild | LXD klont das Abbild, das geht schneller |
| Obergrenze `disk` je Container | wird nicht durchgesetzt | wird durchgesetzt |
| Mehr Platz | Platte der VM vergrößern | Platte vergrößern und den Pool dazu |

Die Art des Pools legst du beim Anlegen der VM fest (`--pool-driver`, Abschnitt 15). Später lässt sie sich nicht umstellen; der Weg ist dann eine neue VM und der Umzug der Container mit Backup und Restore.

Bei einem `btrfs`-Pool wächst der Pool nicht von selbst mit. So vergrößerst du ihn, nachdem die Platte der VM größer ist:

```bash
lxc storage info default                 # "space used" und "total space" des Pools
lxc storage set default size=40GiB       # Pool auf 40 GiB vergrößern
```

### Die Platte eines Containers

Jeder Container hat eine Obergrenze für sein System, im Template unter `disk`. Ein `dir`-Pool setzt sie nicht durch: Dort kann jeder Container die Platte der VM füllen, und `df` im Container zeigt die ganze Platte. Die Grenze ändert man in der Konfiguration des Containers, nicht mit `lxc`:

```bash
devc config edit py-tools      # [resources] disk = "50GiB"
devc config apply py-tools
```

Wie voll ein Container ist, zeigt `lxc info py-tools` unter »Disk usage« oder im Container `df -h / /work /home/dev`.

### Snapshots

Ein Snapshot von devc ist ein Snapshot der Datenbereiche `/work` und `/home/dev`. Mit `--full` kommt das System des Containers dazu. Snapshots liegen im selben Pool. Bei einem `dir`-Pool ist jeder Snapshot eine volle Kopie der Daten; bei `btrfs` belegt er am Anfang fast nichts und wächst mit jeder Änderung, die danach kommt.

Anlegen, zurücksetzen und löschen immer mit devc (Abschnitt 10):

```bash
devc snapshot create py-tools --label vor-umbau
devc snapshot list py-tools
devc snapshot restore py-tools vor-umbau
devc snapshot rm py-tools vor-umbau
```

Mit `lxc` kannst du nachsehen, was im Pool wirklich liegt:

```bash
lxc storage volume info default devc-py-tools-work   # Größe und Snapshots von /work
lxc storage volume info default devc-py-tools-home   # dasselbe für /home/dev
lxc info py-tools                                    # Snapshots des Systems (von --full)
```

Ein Snapshot, den du mit `lxc` anlegst, erscheint nicht in `devc snapshot list` und nicht in der Oberfläche. Ein Snapshot, den du mit `lxc` löschst, steht dort weiter, lässt sich aber nicht mehr zurücksetzen.

Ein Snapshot schützt nicht, wenn die Platte ausfällt oder der Pool beschädigt ist: Er liegt am selben Ort wie die Daten. Dafür sind Backups da (`devc backup`), am besten auf einer anderen Platte (`-o /mnt/sicherung`).

### Netz

Die Container hängen am Netz `lxdbr0` und erreichen darüber das Internet. Das Netz gehört zum Projekt `default`:

```bash
lxc network list --project default
lxc network list-leases lxdbr0 --project default    # welche Adresse hat welcher Container?
```

### Was du nicht mit `lxc` machen solltest

| Nicht so | Sondern so | Warum |
|---|---|---|
| `lxc delete py-tools` | `devc rm py-tools` | devc führt den Container sonst weiter in seiner Liste, und die Datenbereiche bleiben als Reste liegen. |
| `lxc rename`, `lxc copy` | `devc backup` und `devc restore --name` | devc erkennt Container nur unter dem Namen, unter dem es sie angelegt hat. |
| `lxc config set py-tools limits.cpu=8` | `[resources]` in der Konfiguration, dann `devc config apply` | Die Konfiguration zeigt sonst einen anderen Wert als der Container, und die nächste Änderung an `[resources]` überschreibt deinen. |
| `lxc storage volume delete …` | `devc rm` oder `devc snapshot rm` | Ein gelöschter Datenbereich ist weg, ohne Rückfrage. |
| `lxc launch …` im Projekt `devcontainers` | `devc create` | Ein fremder Container erscheint in devc als »foreign« und ist dort gesperrt. |

`lxc stop` und `lxc start` richten keinen Schaden an; besser sind trotzdem `devc stop` und `devc start`, weil devc beim Start die Secrets in den Container legt.

### Wenn etwas hängt

| Was du siehst | Was hilft |
|---|---|
| `lxc list` zeigt keine Container | Du bist im Projekt `default`. `lxc project switch devcontainers` oder `--project devcontainers`. |
| Installationen scheitern mit »No space left on device« | Der Pool oder die Platte des Containers ist voll: »Speicher: Platz schaffen« weiter oben. |
| Ein Container startet nicht | `lxc info <name> --show-log` und `devc jobs --container <name>`. |
| `lxc` meldet »permission denied« | Dein Nutzer ist nicht in der Gruppe `lxd`. `devc doctor` prüft das. |
| Unklar, ob LXD selbst läuft | `snap services lxd` und `sudo snap logs lxd`. |

## 17. Lizenz

devc steht unter der Apache License 2.0. Copyright 2026 Alexander Ramm.

- Du darfst devc frei nutzen, ändern und weitergeben, auch kommerziell.
- Gibst du devc weiter, auch geändert, gehören die Lizenz und der Hinweis aus der Datei `NOTICE` dazu. Geänderte Dateien kennzeichnest du als geändert.
- Den vollen Text der Lizenz findest du in der Datei `LICENSE` im Repository und im `devc.pyz`.
- Mitgeliefert ist `xterm.js` für das Web-Terminal, unter der MIT-Lizenz (`devc/web/vendor/LICENSE-xterm.txt`).
