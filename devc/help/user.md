# Erste Schritte mit devc

Diese Seite erklärt die Oberfläche in einfachen Worten. Alle Einzelheiten stehen im Handbuch (zweites Hilfethema).

## Was ist devc?

devc gibt dir abgeschlossene Arbeitsumgebungen, sogenannte **Container**. In einem Container arbeitet ein KI-Agent wie Claude Code oder opencode an deinem Code. Was im Container passiert, bleibt im Container: Ein Fehler dort macht nichts außerhalb kaputt.

Ein Container ist schnell angelegt und schnell wieder gelöscht. Zwei Dinge bleiben dabei erhalten, wenn du es willst:

- dein **Code** (im Container unter `/work`)
- dein **Home** mit den Einstellungen und dem Gedächtnis der Agents (unter `/home/dev`)

## Anmelden

Die Oberfläche fragt nach einem **Token**. Das ist das Passwort für diese devc-Installation. Du bekommst es von der Person, die devc eingerichtet hat, oder auf der Konsole mit:

```
devc token show
```

Die Anmeldung gilt 12 Stunden. Oben rechts meldest du dich wieder ab.

## Einen Container anlegen

1. Auf dem **Dashboard** auf »Neuer Container« klicken.
2. Eine **Vorlage** (Template) wählen, zum Beispiel »Python-Tools«. Eine Vorlage legt fest, welche Werkzeuge im Container sind.
3. Einen **Namen** vergeben: Kleinbuchstaben, Ziffern und Bindestriche, zum Beispiel `mein-projekt`.
4. Auf »Container erstellen« klicken. Das dauert ein paar Minuten, weil Werkzeuge installiert werden. Den Fortschritt siehst du auf der Seite.

Der Text in der Mitte der Seite ist die **Konfiguration** des Containers. Du kannst sie so lassen, wie die Vorlage sie vorgibt.

Fehlt ein **Secret** (zum Beispiel ein Schlüssel für Claude), sagt die Seite das vor dem Anlegen. Wie du es hinterlegst, steht unten bei »Secrets«.

## Das Dashboard eines Containers

Ein Klick auf den Namen eines Containers öffnet sein Dashboard.

- **Schnellaktionen** oben wirken sofort: Starten, Stoppen, Neu starten.
- **Kacheln** darunter führen auf eine eigene Seite. Sie lösen nichts aus.

| Kachel | Was du dort machst |
|---|---|
| Terminal | Im Container arbeiten, zum Beispiel `claude` starten |
| Dateien | Dateien ansehen, hochladen und herunterladen |
| Konfiguration | Den Container ändern oder löschen |
| Addons | Werkzeuge bedienen und Dienste im Browser öffnen |
| Secrets | Schlüssel und Passwörter für diesen Container |
| Snapshots | Den Stand der Daten festhalten und zurückholen |
| Backups | Den Container als Datei sichern und wiederherstellen |
| Log | Nachsehen, was devc getan hat |

Manche Addons bringen eigene Kacheln mit. Sie stehen hinter den festen Kacheln:

| Kachel | Addon | Was du dort machst |
|---|---|---|
| Info | `info` | Sehen, wie es dem Container geht: Auslastung, Speicherplatz, Prozesse |
| Claude | `claude-code` | Mit Claude Code schreiben, ohne ein Terminal zu öffnen |
| Git | `git` | Repositories ansehen, Commit, Push und Pull |

## Im Terminal arbeiten

Die Kachel **Terminal** öffnet eine Kommandozeile im Container. Du bist dort der Nutzer `dev` im Ordner `/work`.

- Claude Code starten: `claude`
- opencode starten: `opencode`
- Jede Sitzung ist ein Reiter. Mit **+** öffnest du eine weitere, mit **×** beendest du sie.
- Eine Sitzung läuft weiter, wenn du die Seite schließt. Beim nächsten Öffnen ist sie mit der bisherigen Ausgabe wieder da.

Beim ersten Start fragt Claude Code nach einer Anmeldung, wenn kein Schlüssel hinterlegt ist. Folge einfach den Anweisungen im Terminal. Das geht mit einem Claude-Abo.

Wer sich nicht in jedem Container neu anmelden will, hinterlegt einmal einen **Abo-Token** als Secret (Platz `claude-code.oauth_token`). Den Token erzeugt der Befehl `claude setup-token` auf einem Rechner, auf dem Claude Code schon angemeldet ist.

Claude Code geht auch mit einem anderen Anbieter, der die Schnittstelle von Anthropic nachbildet, zum Beispiel BytePlus. Wähle dafür auf der Seite **Addons** bei `claude-code` »Dienst: Anderer Dienst«. Trage die Adresse und das Modell des Anbieters ein und klicke auf »Übernehmen«. Den Schlüssel des Anbieters setzt du unter **Secrets** bei `claude-code.auth_token`. Anbieter nennen ihn oft `ANTHROPIC_AUTH_TOKEN`.

## Die VM: Terminal und Update

Läuft devc in einer eigenen VM, steht auf dem Dashboard die Kachel **VM**.

- Sie öffnet ein Terminal in der VM selbst. Dort gehen `devc`, `lxc` und einige Befehle zum Nachsehen, zum Beispiel `df -h` oder `journalctl -u devc`. `commands` zeigt die Liste.
- Mit »devc aktualisieren …« spielst du eine neue Fassung von devc ein: Datei `devc.pyz` auswählen, »Hochladen und aktualisieren« klicken, bestätigen.
- Beim Update startet devc neu. Offene Terminals und Unterhaltungen enden, und du meldest dich neu an. Deine Container laufen weiter.

## Mit Claude schreiben

Hat der Container das Addon `claude-code`, gibt es die Kachel **Claude**.

1. Klicke auf »Neue Unterhaltung«.
2. Schreibe deine Nachricht und klicke »Senden« (oder drücke Strg+Enter). Enter allein macht eine neue Zeile.
3. Will Claude etwas tun, zum Beispiel einen Befehl ausführen, fragt es vorher. Du antwortest mit »Erlauben«, »Immer erlauben« oder »Ablehnen«. Geht es um eine Datei, erlaubt »Im Ordner erlauben« alle Änderungen in diesem Ordner und seinen Unterordnern.
4. Fragt Claude zu oft? Klicke oben rechts auf »Freigaben: …« und wähle »Dateien im Projekt frei« oder »Alles erlauben«. Die Wahl gilt für diesen Container, auch für spätere Unterhaltungen.

Bei langen Unterhaltungen zeigt der Chat nur den neueren Teil. Ganz oben steht dann »Ältere Einträge anzeigen«. Jeder Klick holt ein weiteres Stück dazu.

Mit dem Addon `codex` gibt es genauso die Kachel **Codex**: derselbe Chat mit Codex CLI und einem Modell von OpenAI oder einem kompatiblen Dienst wie Novita.

- Oben siehst du, welches Modell antwortet, schon bevor du schreibst.
- Ist dir der Text zu klein: »A+« oben rechts vergrößert ihn, »A−« verkleinert ihn.
- »Statistik« oben rechts zeigt Zahlen zur Unterhaltung: wie lange Claude gearbeitet hat, wie viele Tokens es gebraucht hat und welche Werkzeuge wie lange liefen.
- Tippst du `/`, zeigt ein Popup die Befehle, zum Beispiel `/model sonnet` für ein anderes Modell. Die Pfeiltasten wählen, Enter übernimmt den Befehl, Strg+Enter sendet ihn.
- Was Claude getan hat, steht eingeklappt in der Unterhaltung. Ein Klick klappt es auf.
- Solange Claude arbeitet, steht unter der Unterhaltung, was es gerade tut und wie lange schon. Bei längerer Arbeit meldet es sich zwischendurch kurz.
- »Abbrechen« stoppt die laufende Antwort.
- Du kannst die Seite verlassen. Die Unterhaltung läuft weiter und ist beim nächsten Öffnen wieder da.
- »Beenden« schließt die Unterhaltung. Claude gibt ihr dabei einen kurzen Titel; das dauert einige Sekunden.
- Unter »Frühere Unterhaltungen« öffnest du eine alte Unterhaltung zum Nachlesen. Mit »Fortsetzen« schreibst du dort weiter.

Meldet Claude, dass die Anmeldung fehlt: Trage unter **Secrets** den Token oder den API-Key ein, oder starte im Terminal einmal `claude` und melde dich an.

## Git: Commit, Push und Pull

Mit dem Addon **git** bekommt der Container die Kachel **Git**. Du fügst es über »Addon hinzufügen« dazu.

- Die Kachel zeigt alle Repositories unter `/work` und `/home/dev`. »Öffnen« zeigt eins davon.
- Unter **Änderungen** stehen die Dateien, die sich geändert haben. »Unterschied« zeigt, was genau.
- **Commit erstellen:** Beschreibung eintragen und auf »Commit erstellen« klicken. Normal kommen alle Änderungen hinein. Für einzelne Dateien klickst du bei diesen auf »Vormerken« und nimmst den Haken bei »Alle Änderungen aufnehmen« weg.
- **Push** schickt deine Commits zum Server. **Pull** holt neue Commits vom Server.
- **Rückgängig** bei einem Commit nimmt seine Änderung mit einem neuen Commit zurück. Es geht nichts verloren.

Für Push und Pull über https braucht die Kachel ein **Token**. GitHub und GitLab nehmen kein Passwort mehr. Du legst das Token dort in den Einstellungen deines Kontos an und trägst es beim Hinzufügen des Addons bei `token` ein. Später änderst du es auf der Seite **Secrets** bei `git.token`. Das Feld `username` bleibt mit einem Token leer.

## Dateien

Die Kachel **Dateien** zeigt den Ordner `/work`. Du kannst Ordner öffnen, Dateien herunterladen und Dateien hochladen. Einen ganzen Ordner lädst du als Archiv herunter.

Am Ende jeder Zeile steht ein Knopf **…** mit einem kleinen Menü:

- **Herunterladen** holt die Datei auf deinen Rechner.
- **Entpacken …** gibt es bei Archiven (`.zip`, `.tar.gz`). Du siehst zuerst eine Vorschau: wohin entpackt wird und was dabei entsteht. Du wählst »Hier« oder »In einen neuen Ordner«. Erst der Knopf »Entpacken« führt es aus.
- **Löschen …** löscht die Datei oder den Ordner. devc fragt vorher nach.

So lädst du einen ganzen Ordner hoch: auf deinem Rechner als `.zip` packen, hochladen, dann im Menü »Entpacken …« wählen.

Ein Klick auf eine Datei öffnet sie:

- **Text** siehst du mit Zeilennummern. Markdown-Dateien (`.md`) erscheinen fertig formatiert; mit den Knöpfen »Text« und »Markdown« schaltest du um.
- **Bilder** siehst du als Bild. Mit −, + und »100 %« änderst du die Größe, »Fit« passt das Bild ins Fenster. Mit gedrückter Maustaste verschiebst du es.
- **Andere Dateien** erscheinen als Hex-Ansicht, also als Folge von Bytes.

Text und Markdown kannst du ändern: auf »Bearbeiten« klicken, schreiben, dann »Speichern«. »Ansicht« zeigt, wie es aussieht, auch vor dem Speichern. Speichere, bevor du die Seite verlässt; sonst sind die Änderungen weg.

## Addons: Werkzeuge und Links

Ein **Addon** bringt ein Werkzeug in den Container, zum Beispiel Python, Node oder opencode. Die Kachel **Addons** zeigt alle Addons des Containers. Zu jedem Addon gibt es einen kurzen Satz und einen Link »Hilfe zu …« mit der ausführlichen Beschreibung.

Manche Addons haben Knöpfe:

- **Aktionen** starten oder stoppen etwas im Container, zum Beispiel »Server starten«.
- **Links** öffnen etwas im Browser, zum Beispiel »opencode öffnen«. Ein Link ist erst anklickbar, wenn die passende Aktion läuft.

Oben auf der Seite steht **Springen zu:** mit allen Addons. Ein Klick bringt dich direkt zu diesem Addon.

Unter jedem Addon stehen seine **Einstellungen**, zum Beispiel der Port oder der Ordner des HTTP-Servers. Unter jedem Feld steht ein kurzer Hinweis, mehr steht in »Hilfe zu …«. Manche Felder erscheinen erst nach einer Auswahl. Ein Beispiel ist Claude Code: Bei »Dienst« wählst du »Anthropic« oder »Anderer Dienst«, darunter stehen nur die passenden Felder. Du änderst einen Wert und klickst auf »Übernehmen«. Das wirkt nur auf dieses Addon: Läuft sein Server gerade, startet er mit dem neuen Wert neu. Deine Terminals und ein laufendes `claude` bleiben, wie sie sind.

### Ein Addon hinzufügen

1. Auf der Seite **Addons** oder **Konfiguration** auf »Addon hinzufügen« klicken.
2. In der Liste das Addon suchen und »Auswählen« klicken. Addons, die der Container schon hat, tragen den Vermerk »aktiv«.
3. Die Einstellungen eintragen. Hat das Addon ein Secret, zum Beispiel ein Passwort, kannst du es gleich mit eintragen oder später unter **Secrets** setzen. Darunter steht die Anleitung des Addons.
4. Auf »Hinzufügen und einrichten« klicken.

Der Container läuft dabei weiter. Braucht das Addon ein anderes Addon, kommt dieses von selbst mit.

Beispiel: Du willst den Ordner `meinefiles` im Browser sehen. Beim Addon `httpserver` trägst du bei `dir` den Wert `/work/meinefiles` ein, klickst »Übernehmen« und danach »Im Browser öffnen«.

### Einen eigenen Dienst im Browser öffnen

Läuft in deinem Container ein Web-Dienst, zum Beispiel ein Entwicklungsserver auf Port 3000? Dann trägst du den Port im Addon **webproxy** ein:

1. Auf der Seite **Addons** auf »Addon hinzufügen« klicken und `webproxy` auswählen.
2. Bei `proxy` den Port eintragen, zum Beispiel `3000=Vite`.
3. Auf »Hinzufügen und einrichten« klicken.

Hat der Container `webproxy` schon, änderst du den Wert auf der Seite **Addons** und klickst »Übernehmen«.

Danach steht auf der Seite **Addons** ein Link »Vite«. Wichtig: Der Dienst im Container muss auf `0.0.0.0` lauschen, nicht nur auf `127.0.0.1`. Sonst meldet der Browser »antwortet kein Dienst«.

Beim ersten Öffnen fragt devc noch einmal nach der Anmeldung. Das schützt den Dienst vor Fremden.

**Ohne Anmeldung** geht es mit der Zeile `public = "8080=API"` statt `proxy`. Das brauchst du für Programme, die sich nicht anmelden können. Vorsicht: Ein solcher Port ist für jeden offen, der diese devc-Installation erreicht.

## Secrets

Ein **Secret** ist ein Schlüssel oder Passwort, zum Beispiel der API-Schlüssel für Claude. devc zeigt Secrets nie wieder an und schreibt sie in keine Datei deines Projekts.

- **Secret-Store** (oben in der Leiste): Secrets, die mehrere Container benutzen. Einmal hinterlegen, in jedem Container verwenden.
- Kachel **Secrets** im Container: Hier wählst du für jeden Platz ein Secret aus dem Store oder gibst eines nur für diesen Container ein.

## Daten sichern

| | Snapshot | Backup |
|---|---|---|
| Was ist es? | Ein festgehaltener Stand der Daten | Eine Datei mit dem ganzen Container |
| Wo liegt es? | In devc | In devc, und du kannst es herunterladen |
| Wofür? | Vor einem riskanten Schritt: schnell zurück | Aufbewahren, umziehen, neu aufbauen |

- Unter **Snapshots** hältst du den Stand fest und setzt die Daten auf einen früheren Stand zurück. Alles danach geht dabei verloren; devc fragt vorher nach.
- Unter **Backups** sichert **Backup jetzt** den ganzen Container sofort, ohne Rückfrage. »Backup erstellen« lässt dich Inhalt und Namen wählen. Wiederherstellen kannst du über den alten Container oder als neuen. Ein Balken zeigt, wie weit devc ist.

Secrets sind in einem Backup nie enthalten. Nach dem Wiederherstellen ordnest du sie neu zu.

## Einen Container ändern oder löschen

Unter **Konfiguration** steht der Text, der den Container beschreibt. Nach einer Änderung klickst du auf »Anwenden«. devc zeigt vorher, was es tun wird.

Löschen geht über »Container löschen …« auf dem Dashboard. Mit dem Häkchen »Datenbereich behalten« bleiben Code und Home erhalten. Ohne das Häkchen sind die Daten weg.

## Wenn etwas nicht klappt

| Was du siehst | Was hilft |
|---|---|
| »Container ist beschäftigt« | Es läuft noch ein Vorgang. Kurz warten, unter **Log** nachsehen. |
| »Container läuft nicht« | Auf dem Dashboard »Starten« klicken. |
| Der Container »wird eingerichtet« | Das Anlegen läuft noch. Warten und die Seite neu laden. Den Fortschritt zeigt **Log**. |
| Der Container ist »unvollständig« | Das Anlegen ist abgebrochen. Unter **Konfiguration** »Anwenden« setzt fort. Der Grund steht unter **Log**. |
| »Secret fehlt« | Das Secret im Secret-Store oder unter **Secrets** hinterlegen. |
| »antwortet kein Dienst« beim Öffnen eines Ports | Der Dienst im Container läuft nicht oder lauscht nur auf `127.0.0.1`. |
| Die Anmeldeseite kommt wieder | Die Anmeldung ist nach 12 Stunden abgelaufen oder devc wurde neu gestartet. Neu anmelden. |
| Nach einem Neustart sind die Container gestoppt | Das ist normal. Auf dem Dashboard wieder starten. |

Unter **Log** steht zu jedem Vorgang, was devc getan hat und wo es gescheitert ist. Diese Zeilen helfen auch der Person, die devc betreut.
