# Präsentation von devc

Eine kurze Präsentation mit den wichtigsten Funktionen: Container mit einem Klick, Claude Code im Browser, Terminal, Git und Addons. Sie ist eine HTML-Seite, kein PowerPoint: Jeder Browser zeigt sie, und der Quelltext bleibt lesbar.

| Datei | Inhalt |
|---|---|
| `index.html` | die Folien, eine `<section class="slide">` je Folie, Gestaltung im `<style>` oben |
| `img/*.png` | die Bildschirmfotos |
| `build.py` | baut alles zu einer Datei zusammen |
| `screenshots.py` | nimmt die Bildschirmfotos neu auf |
| `demo/` | Beispielprojekt und gestellte Unterhaltung für die Aufnahmen |

## Ansehen

`index.html` im Browser öffnen. Blättern mit den Pfeiltasten, der Leertaste oder dem Mausrad.

Für ein PDF: im Browser drucken, Ziel »Als PDF speichern«, Hintergrundgrafiken einschalten. Jede Folie wird eine Seite.

## Eine einzige Datei bauen

```bash
python3 pitch/build.py
```

Das ergibt `dist/devc-pitch.html` mit eingebetteten Bildern, zum Verschicken, und `dist/devc-pitch.zip` mit dieser Datei und den Quellen. `dist/` liegt nicht im Repository.

## Folien ändern

Text steht direkt in `index.html`. Eine neue Folie ist eine Kopie eines `<section class="slide">`-Blocks; die `id` (`f1`, `f2`, …) macht sie direkt aufrufbar, zum Beispiel `index.html#f5`. Die Klasse `wide` nimmt die ganze Breite ohne Bild, `dark` macht den Hintergrund dunkel. Die Nummer unten rechts steht in `<span class="no">`.

## Bildschirmfotos neu aufnehmen

Die Bilder zeigen die echte Oberfläche, mit einer Ausnahme: Die Unterhaltung mit Claude ist gestellt. `demo/bridge.py` spielt einen festen Ablauf ab und ruft kein Modell auf. So ist das Bild wiederholbar und braucht keine Anmeldung.

Die Aufnahmen brauchen `chromium` und eine eigene devc-Instanz mit Testcontainern. Nie die Instanz mit echten Containern nehmen.

1. Eine zweite devc-Instanz starten: als derselbe Nutzer wie der Dienst (`devc`; ein anderer Nutzer ließe `devc init` die UID-Zuordnung des LXD-Projekts ändern), mit eigenem Ordner für `XDG_CONFIG_HOME`, `XDG_DATA_HOME` und `XDG_CACHE_HOME` und einer `config.toml` mit `[server]` `port = 8741`.
2. In deren Ordner `config/devc/addons/` eine Kopie von `examples/addons/claude-code` legen, darin `bridge.py` durch `pitch/demo/bridge.py` ersetzen und in der `addon.toml` die Zeile `install = …` entfernen.
3. Einen Eintrag `anthropic` im Secret-Store anlegen (ein beliebiger Text; die Vorlagen verlangen ihn).
4. Container anlegen: `it-webshop` aus `pydev`, dazu nach Wunsch weitere wie `it-api` aus `webdev`.
5. `pitch/demo/setup.sh` in `it-webshop` als Nutzer `dev` ausführen und die Aktion `httpserver.start` starten.
6. Aufnehmen:

   ```bash
   export XDG_RUNTIME_DIR=/run/user/1000
   DEVC_TOKEN=… python3 pitch/screenshots.py http://127.0.0.1:8741 it-webshop pitch/img
   ```

7. Die Testcontainer wieder löschen.

Das Skript nimmt zwei Dinge aus dem Bild: Container anderer Instanzen (in der Liste als »fremd«) und den Rahmen, den die Seite für die Tastatur um den Inhalt zieht.
