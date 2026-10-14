# devc für KI-Agenten

Dieser Text richtet sich an einen KI-Agenten (Claude Code, opencode oder einen anderen), der mit devc arbeitet. Er gilt in zwei Lagen: Du arbeitest **in** einem Container, oder du **bedienst** devc für den Nutzer von außen. Menschen können ihn ihrem Agenten als Anweisung mitgeben.

Normative Wörter: **MUSS / DARF NICHT** gelten ohne Ausnahme. **SOLL** gilt, solange der Nutzer nichts anderes sagt.

## 1. Was devc ist

- devc verwaltet Dev-Container in LXD. Ein Container entsteht aus einem Image, Addons und einer Konfiguration (`devc.toml`) und ist jederzeit neu aufbaubar.
- Erhalten bleiben nur zwei Bereiche: `/work` (Code) und `/home/dev` (Home, Einstellungen und Gedächtnis der Agents). Alles andere im Container ist Wegwerfware.
- Der Nutzer bedient devc über die Web-Oberfläche oder die Kommandozeile `devc`. Beide tun dasselbe.

## 2. Du arbeitest in einem Container

### Wo du bist

| Was | Wert |
|---|---|
| Nutzer | `dev` (UID 1000), `sudo` nur, wenn das Addon `base` es freigibt |
| Arbeitsverzeichnis | `/work` |
| Regeln dieses Containers | `/work/CLAUDE.md` (für opencode: `/work/AGENTS.md`, ein Verweis darauf) |
| Übersicht: Addons, Secrets, Konfiguration | `/work/.devc/README.md` |
| Ein Addon: Einstellungen, Aktionen, Links, Anleitung | `/work/.devc/addons/<addon>.md` |
| Konfiguration des Containers (Kopie, ohne Geheimnisse) | `/work/.devc/config.toml` |
| Secrets | Dateien unter `/run/devc/secrets/`, nach jedem Start neu |

Lies zuerst `/work/CLAUDE.md`. Der Abschnitt zwischen `<!-- devc:begin -->` und `<!-- devc:end -->` stammt von devc und beschreibt genau diesen Container: aktive Addons, ihre Parameter, freigegebene Ports.

### Regeln

- Den Block zwischen den devc-Markern in `/work/CLAUDE.md` DARFST du NICHT ändern oder entfernen. devc schreibt ihn neu und bricht ab, wenn die Marker fehlen. Eigene Notizen gehören außerhalb der Marker.
- Secrets DÜRFEN NICHT ins Repository, in Logs, in Ausgaben oder in Dateien unter `/work` gelangen. Lies sie nur aus `/run/devc/secrets/<slot>` und gib ihren Inhalt nie aus.
- Addon-Aktionen (zum Beispiel »Server starten«) startet der Nutzer über devc. Baue sie nicht selbst nach, sonst stimmt der Zustand in der Oberfläche nicht.
- Was du per `apt` oder global installierst, ist nach einem Neuaufbau weg. Dauerhaftes gehört in ein Addon oder nach `/work` bzw. `/home/dev`. Sag dem Nutzer, wenn etwas dauerhaft gebraucht wird. Für Pakete von Ubuntu gibt es das Addon `packages`: Der Nutzer trägt die Namen in `packages.apt` ein.
- Der Nutzer schreibt dir vielleicht über die Chat-Kachel von devc statt im Terminal. Dort gibt es keine Auswahl-Dialoge: Stelle Rückfragen als normalen Text. Je nach Einstellung des Nutzers (»Freigaben« im Chat) sieht er jede Nutzung eines Werkzeugs als Freigabe; fasse Befehle darum sinnvoll zusammen.
- `devc` selbst gibt es im Container nicht. Änderungen am Container (Addons, Ports, Ressourcen) macht der Nutzer; nenne ihm, was er eintragen soll.

### Web-Dienste und Ports

- Ein Dienst, den der Nutzer im Browser sehen soll, MUSS auf `0.0.0.0` lauschen. Ein Dienst auf `127.0.0.1` oder `localhost` ist von außen nicht erreichbar.
- Der Nutzer erreicht Port 3000 unter `https://3000-<container>.<basisdomain>/`. Nenne ihm nicht `http://localhost:3000`; das gilt nur im Container.
- Welche Ports eingerichtet sind, steht in `/work/CLAUDE.md` unter »Web-Proxy«. Brauchst du einen weiteren, bitte den Nutzer um diesen Eintrag in der Konfiguration des Containers:

  ```toml
  [params.webproxy]
  proxy = "3000=Vite"      # mit Anmeldung bei devc
  public = "8080=API"      # ohne Anmeldung, offen für jeden, der devc erreicht
  ```

- Ein Port unter `public` ist ungeschützt. Biete dort keine Secrets und keine ungeschützten Schreibzugriffe an, und weise den Nutzer darauf hin, wenn eine Anwendung dort ohne eigenen Schutz läuft.
- Die Anwendung läuft hinter einem Proxy unter einem eigenen Hostnamen. Entwicklungsserver mit Host-Prüfung brauchen dafür eine Freigabe (bei Vite `server.allowedHosts`).

## 3. Du bedienst devc für den Nutzer

### Grundregeln

- Vor jedem Löschen (`rm`, `snapshot rm`, `snapshot restore`, `restore --replace`, `addon rm`, `template rm`, `secret rm`) MUSST du den Nutzer fragen. `--yes` setzt du nur, wenn er dem konkreten Schritt zugestimmt hat.
- Secret-Werte DÜRFEN NICHT auf der Kommandozeile stehen. Erlaubt sind `=@datei`, `=-` (Standardeingabe) und `=shared:<name>` (Verweis auf den Secret-Store).
- Das Token (`devc token show`) ist das Passwort der Installation. Gib es nicht aus, außer der Nutzer verlangt es ausdrücklich.
- Fasse nur Container an, die devc verwaltet. LXD direkt (`lxc …`) SOLLST du nicht benutzen; devc verliert sonst den Überblick.
- Rate nicht. `devc <befehl> --help` zeigt die Optionen, `devc doctor` den Zustand der Installation.

### Die wichtigsten Befehle

| Ziel | Befehl |
|---|---|
| Übersicht | `devc list`, `devc info <name>` |
| Vorlagen und Addons ansehen | `devc template list`, `devc addon list`, `devc addon show <id>` |
| Container anlegen | `devc create <name> --template <id> --yes` (fehlende Secrets: `--secret <slot>=shared:<name>`) |
| Starten, Stoppen | `devc start <name>`, `devc stop <name>`, `devc restart <name>` |
| Befehl im Container | `devc exec <name> -- <befehl…>` (`--root` für root) |
| Konfiguration | `devc config show <name>`, ändern, dann `devc config apply <name>` (`--dry-run` zeigt den Plan) |
| Addon ergänzen | `devc install <name> <addon> --param <addon>.<param>=<wert>` |
| Aktionen und Links | `devc action list <name>`, `devc action run <name> <addon>.<aktion>` |
| Die VM, in der devc läuft | `devc vm info` (was ist eingerichtet, welche Fassung läuft), `devc vm update <devc.pyz>` (neue Fassung einrichten; der Dienst startet neu, Sitzungen enden: vorher den Nutzer fragen), `devc vm shell` (beschränkte Shell für Menschen) |
| Kacheln aus Addons | `devc tile list <name>`, `devc tile show <name> <addon>.<id>` (gibt den Text einer Kachel der Art `view` aus, zum Beispiel `info.status`: Auslastung, Speicherplatz, Prozesse), `devc tile panel <name> <addon>.<id> [--view <id> | --action <id>] [--arg <name>=<wert>] [--value <name>=<wert>]` (Daten einer Kachel der Art `panel` als JSON, zum Beispiel `git.repos`) |
| Dateien | `devc push <name> <lokal> [<ziel>]`, `devc pull <name> <quelle> [<lokal>]` (relativ zu `/work`) |
| Addon-Einstellungen | `devc config set <name> <addon>.<param>=<wert> …` schreibt die Werte in die `devc.toml` und richtet nur dieses Addon neu ein; seine laufenden Aktionen starten neu, Terminal-Sitzungen bleiben |
| Archive und Löschen | `devc file archive <name> <archiv>` (Inhalt zeigen), `devc file unpack <name> <archiv> [--into <ordner>]`, `devc file rm <name> <pfad> --yes`. Löschen nur auf ausdrücklichen Wunsch des Nutzers. |
| Sichern | `devc snapshot create <name> --label <text>`, `devc backup <name>` |
| Jobs und Logs | `devc jobs --container <name>`, `devc jobs --follow <job-id>` |
| Hilfe | `devc help show user`, `devc help show technical`, `devc help show ai` |

Mit `--json` gibt jeder Befehl ein JSON-Objekt aus; im Fehlerfall `{"error": "E_…", "message": "…", "details": {…}}`. Werte Ergebnisse so aus, nicht über den Text für Menschen.

### Fehler

| Code | Exit | Bedeutung | Was du tust |
|---|---|---|---|
| `E_USAGE` | 2 | falscher Aufruf | `--help` lesen, Aufruf korrigieren |
| `E_ENGINE` | 3 | LXD fehlt oder meldet einen Fehler | `devc doctor`, dem Nutzer berichten |
| `E_NOT_FOUND` | 4 | Container, Addon, Template … fehlt | Namen mit `list` prüfen |
| `E_CONFLICT` | 5 | Name belegt, Container beschäftigt oder läuft nicht | Zustand prüfen, warten oder starten |
| `E_VALIDATION` | 5 | eine Prüfung (L1–L16) schlägt fehl | die genannte Stelle korrigieren |
| `E_MISSING_SECRET` | 5 | ein Secret hat keinen Wert | den Nutzer bitten, es zu hinterlegen |
| `E_SECURITY` | 6 | nicht erlaubt (Pfad, Port, Bind) | nicht umgehen; dem Nutzer berichten |
| `E_JOB_FAILED` | 1 | ein Schritt eines Jobs schlug fehl | Log mit `devc jobs --follow <job-id>` lesen |

Ein Container, dessen Anlage noch läuft, zeigt »wird eingerichtet«: warten, nichts anwenden. Ein Container, dessen Anlage abbrach, ist »unvollständig«. Nach der Korrektur setzt `devc config apply <name>` die Einrichtung fort; neu anlegen ist nicht nötig.

### Die Konfiguration eines Containers

`devc.toml` ist die einzige Quelle für den Soll-Zustand. Unbekannte Schlüssel sind ein Fehler.

```toml
name = "mein-projekt"
template = "pydev@1.0.0"          # nur Herkunft
platform = "lxd"
addons = ["claude-code", "python", "webproxy"]

[resources]
cpu = 4
memory = "8GiB"
disk = "30GiB"

[data]
code = "volume"                   # oder: shared = "/srv/dev/mein-projekt"

[params.python]
version = "3.12"

[params.webproxy]
proxy = "3000=Vite"

[secrets]
"claude-code.api_key" = "anthropic"   # Name im Secret-Store, nie der Wert
# Abo statt API: "claude-code.oauth_token" = "<name im store>" (Token aus `claude setup-token`); hat Vorrang vor api_key
# Anderer Dienst (z. B. BytePlus): [params.claude-code] provider = "custom", base_url, model; Secret "claude-code.auth_token"
```

- `data` lässt sich nachträglich nicht ändern; der Weg ist Backup und Restore mit geänderter Datei.
- Ein Addon lässt sich nur abwählen, wenn es ein `uninstall`-Skript hat; sonst den Container neu aufbauen.
- Benötigt ein Addon ein anderes (`requires`), MUSS das auch in `addons` stehen. devc ergänzt es nicht von selbst.

### Ein eigenes Addon schreiben

Ein Addon ist ein Ordner mit drei Pflichtdateien: `addon.toml`, `README.md` (Hilfe für Menschen) und `AI.md` (Regeln für den Agenten im Container, wird in `/work/CLAUDE.md` eingefügt).

```toml
id = "vite"
title = "Vite"
version = "1.0.0"
requires = ["base", "node"]
install = ["install.sh"]

[[params]]
name = "port"
type = "int"
default = 5173
min = 1024
max = 65535

[[actions]]
id = "start"
label = "Server starten"
run = "npm run dev -- --host 0.0.0.0 --port {port}"
detach = true

[[actions]]
id = "stop"
label = "Server stoppen"
stops = "start"

[[links]]
label = "Vite öffnen"
port = "{port}"
when = "start"
```

- `install.sh` läuft im Container als root mit `bash -euo pipefail` und MUSS wiederholbar sein. Parameter kommen als `DEVC_PARAM_<NAME>`, Secrets als Pfad in `DEVC_SECRET_<NAME>`.
- Prüfen: `devc addon validate <ordner>`. Einspielen: als `.tar.gz` mit genau diesem einen Ordner, `devc addon import <archiv>`.
- Parametertypen: `string`, `int`, `bool`, `choice`, `secret`, `ports`. Ein `secret` hat nie einen Default.
- Darstellung in der GUI (optional): `label` (Beschriftung), `group` (Kasten mit Überschrift), `show_if = "name=wert1|wert2"` (nur zeigen, wenn ein anderer `choice`- oder `bool`-Parameter diesen Wert hat), `option_labels` (Texte zu den `options` eines `choice`), `hidden = true` (nicht zeigen). `description` bleibt ein kurzer Hinweis in einer Zeile; Ausführliches gehört ins README.
- Eigene Kacheln auf dem Container-Dashboard: `[[tiles]]` mit `id`, `title` und `kind`. `view` zeigt die Ausgabe von `run` als Markdown (optional `refresh` in Sekunden), `panel` ruft `run` mit einer Anfrage als JSON auf und zeichnet aus der Antwort Tabellen, Knöpfe und Formulare (Vorlage: `examples/addons/git/gitpanel.py`), `link` öffnet `port` oder `url` in einem neuen Tab, `chat` startet mit `run` ein Programm, das mit devc JSON-Zeilen tauscht (Protokoll: `devc help show technical`, Abschnitt »Eigene Kacheln«). Ein Addon DARF devc kein HTML liefern; eine eigene Oberfläche läuft als Dienst im Container und wird mit `link` verknüpft.

## 4. Was du nie tust

- Secrets ausgeben, protokollieren, ins Repository schreiben oder als Argument übergeben.
- Daten, Container, Snapshots oder Backups löschen, ohne dass der Nutzer dem konkreten Schritt zugestimmt hat.
- Den devc-Block in `/work/CLAUDE.md` verändern.
- Einen Dienst über einen `public`-Port freigeben, ohne den Nutzer auf die fehlende Anmeldung hinzuweisen.
- Eine Sperre von devc umgehen (`E_SECURITY`, gesperrte Ports unter 1024, Pfade außerhalb von `/work`).
- Repositories auf einem Git-Server anlegen oder `git push` ausführen, wenn der Nutzer es nicht ausdrücklich verlangt.
