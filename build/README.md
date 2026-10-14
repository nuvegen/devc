# build/ – Bau-Unterlagen von devc

Hier liegt, womit devc gebaut wurde und womit es gepflegt wird. Zum Benutzen von devc braucht man nichts davon.

| Datei / Ordner | Zweck |
|---|---|
| `SPEC-devc.md` | Verbindliche Spezifikation von devc 1.0 |
| `tools/` | Hilfsskripte für Prüfungen von Hand (GUI im Headless-Browser, Terminal-Sitzungen), siehe `tools/README.md` |
| `gui/` | GUI-Mockups (Entwurf 2) als statische HTML-Seiten |

**Nach jeder Änderung an devc, `vm/install.sh` oder `vm/cloud-init.yaml`:** erst `python3 build.py`, dann `dist/user-data` neu erzeugen (`python3 vm/make-user-data.py … -o dist/user-data`). Die Datei enthält eine Kopie von `devc.pyz` und `install.sh` und veraltet sonst unbemerkt; neue VMs bekämen den alten Stand.

Für eine Korrektur: `claude` in der Projektwurzel starten. Die Spec gilt weiter; Tests und Build stehen Schritt für Schritt in `docs/BAUEN.md`, kurz im `README.md` der Projektwurzel unter »Entwicklung«.
