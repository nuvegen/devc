Macht Dienste, die im Container laufen, im Browser erreichbar, zum Beispiel einen Entwicklungsserver. Für jeden eingetragenen Port zeigt die Addon-Seite einen Link.

Das Addon installiert nichts und startet nichts. Den Dienst startest du selbst, zum Beispiel im Terminal.

### Einen Port freigeben

Trage den Port auf der Addon-Seite im Feld `proxy` ein und klicke »Übernehmen«:

```
3000
```

Auf der Seite erscheint der Link »Port 3000«. Er führt auf `https://3000-<container>.<basisdomain>/`.

### Mehrere Ports freigeben

Schreibe alle Ports in dasselbe Feld, getrennt durch Leerzeichen. Je Feld sind bis zu 32 Ports möglich, jeder von 1024 bis 65535:

```
3000 5173 8080
```

Das ergibt drei Links: »Port 3000«, »Port 5173« und »Port 8080«.

### Den Links Namen geben

Hänge an einen Port `=` und einen Namen an. Der Name hat höchstens 40 Zeichen und enthält kein Leerzeichen:

```
3000=Vite 5173=Storybook 8080
```

Das ergibt die Links »Vite«, »Storybook« und »Port 8080«.

### Ports ohne Anmeldung

Ein Port in `proxy` verlangt die Anmeldung bei devc. Programme und Skripte, die eine API aufrufen, können diese Anmeldung nicht mitmachen. Für sie gibt es das zweite Feld `public`, mit derselben Schreibweise:

| Feld | Inhalt | Wer kommt an den Dienst? |
|---|---|---|
| `proxy` | `3000=Vite 5173=Storybook` | nur, wer bei devc angemeldet ist |
| `public` | `9000=API 9001=Webhook` | jeder, der den Proxy erreicht |

Ein Port in `public` ist offen. devc prüft dort nichts; die Anwendung muss sich selbst schützen, zum Beispiel mit einem eigenen Schlüssel. Trage einen Port nur in eines der beiden Felder ein.

### In der Konfiguration

Dieselben Werte stehen in der Konfiguration des Containers. Du kannst sie auch dort ändern, danach »Anwenden«:

```toml
addons = ["python", "webproxy"]

[params.webproxy]
proxy = "3000=Vite 5173=Storybook 8080"
public = "9000=API"
```

### Auf der Kommandozeile

```bash
# mehrere Ports für einen vorhandenen Container setzen
devc config set mein-container "webproxy.proxy=3000=Vite 5173=Storybook 8080"

# beide Felder auf einmal
devc config set mein-container "webproxy.proxy=3000=Vite" "webproxy.public=9000=API"

# alle Ports wieder entfernen
devc config set mein-container "webproxy.proxy="

# gleich beim Anlegen
devc create web --template pydev --addon webproxy --param "webproxy.proxy=3000=Vite 8080"
```

Der Wert ersetzt immer die ganze Liste. Um einen Port zu ergänzen, schreibst du die alten Ports mit hin.

### Beispiel: ein zweiter Ordner im Browser

Im Terminal des Containers einen Server für den Ordner `/work/meinefiles` auf Port 8001 starten:

```bash
python3 -m http.server 8001 --directory /work/meinefiles
```

Dann im Feld `proxy` den Port ergänzen, zum Beispiel `3000=Vite 8001=Meine-Dateien`, und »Übernehmen« klicken.

### Wenn der Link nicht geht

| Was du siehst | Was hilft |
|---|---|
| »antwortet kein Dienst« (Fehler 502) | Der Dienst läuft nicht, oder er lauscht nur auf `127.0.0.1`. Starte ihn so, dass er auf `0.0.0.0` lauscht, zum Beispiel `npm run dev -- --host 0.0.0.0` oder `flask run --host 0.0.0.0`. |
| »nicht gefunden« | Der Container ist gestoppt. |
| Die Anmeldeseite von devc statt einer API-Antwort | Der Port steht in `proxy`. Für Aufrufe durch Programme gehört er in `public`. |
| Die Adresse beginnt mit `http://<Container-IP>:` | devc läuft ohne Proxy. Der Link geht dann nur vom Host aus. |

### Gut zu wissen

- Eine Änderung der Ports startet nichts neu. Laufende Dienste und Terminals bleiben, wie sie sind.
- Erreichbar ist ein Port auch ohne Eintrag, wenn du die Adresse von Hand eingibst. Das Addon liefert die Links, und nur `public` hebt die Anmeldung auf.
- Für einen Dienst mit eigener Start- und Stopp-Aktion ist ein eigenes Addon mit `[[links]]` der passende Weg. Vorlage: `httpserver`.
