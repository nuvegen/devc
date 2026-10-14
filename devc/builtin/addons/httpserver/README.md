Zeigt Dateien aus einem Ordner des Containers im Browser, zum Beispiel erzeugte HTML-Seiten. Dahinter steckt der einfache Server von Python (`python3 -m http.server`).

### So benutzt du es

1. Auf der Addon-Seite »Server starten« klicken.
2. »Im Browser öffnen« klicken. Der Link ist aktiv, solange der Server läuft.
3. »Server stoppen« beendet ihn wieder.

Der Server zeigt den Ordner aus dem Feld `dir`, am Anfang `/work/out`. Liegt dort eine `index.html`, erscheint sie; sonst siehst du die Liste der Dateien.

### Einen anderen Ordner zeigen

Trage auf der Addon-Seite den Ordner im Feld `dir` ein und klicke »Übernehmen«:

```
/work/meinefiles
```

Läuft der Server gerade, startet devc ihn mit dem neuen Ordner neu. Gibt es den Ordner noch nicht, legt devc ihn an. Terminals und andere Programme im Container laufen dabei weiter.

### Einen anderen Port nehmen

Im Feld `port` steht der Port im Container, am Anfang 8000. Erlaubt sind 1024 bis 65535. Ändere ihn, wenn auf 8000 schon etwas anderes läuft.

### In der Konfiguration

```toml
addons = ["python", "httpserver"]

[params.httpserver]
port = 8000
dir = "/work/meinefiles"
```

### Auf der Kommandozeile

```bash
# Ordner und Port ändern; ein laufender Server startet neu
devc config set mein-container httpserver.dir=/work/meinefiles httpserver.port=8010

# Server starten und stoppen
devc action run mein-container httpserver.start
devc action run mein-container httpserver.stop

# Was läuft gerade, und unter welcher Adresse?
devc action list mein-container
```

### Mehrere Ordner oder mehrere Server

Dieses Addon startet genau einen Server. Brauchst du einen zweiten, starte ihn im Terminal des Containers auf einem anderen Port:

```bash
python3 -m http.server 8001 --directory /work/zweiter-ordner
```

Den Link dazu liefert das Addon `webproxy`: dort im Feld `proxy` den Port eintragen, zum Beispiel `8001=Zweiter-Ordner`. Mehrere Ports trennst du durch Leerzeichen; mehr dazu in der Hilfe zu `webproxy`.

### Die Adresse

| devc läuft … | Adresse | Erreichbar |
|---|---|---|
| hinter dem Proxy | `https://<port>-<container>.<basisdomain>/` | überall, wo die Oberfläche erreichbar ist, nach Anmeldung bei devc |
| ohne Proxy | `http://<Container-IP>:<port>/` | nur vom Host aus |

### Gut zu wissen

- Der Server liefert Dateien nur aus. Hochladen geht über die Seite »Dateien«.
- Jeder, der bei devc angemeldet ist, sieht alle Dateien in dem Ordner. Lege dort keine Secrets ab.
- Nach einem Neustart des Containers läuft der Server nicht von selbst wieder: noch einmal »Server starten« klicken.

Benötigt das Addon `python`.
