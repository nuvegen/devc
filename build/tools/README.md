# build/tools – Hilfsskripte für Prüfungen von Hand

Keine Tests der Testsuite, sondern Skripte aus dem Bau von devc 1.0. Sie setzen einen laufenden `devc serve` und echte Container voraus und enthalten feste Containernamen (`it-py`, `it-sudo`); vor dem Benutzen anpassen.

| Skript | Zweck | Braucht |
|---|---|---|
| `gui_smoke.py <basis-url> <token> <routen,kommagetrennt> <ordner-für-bilder>` | Lädt jede GUI-Route in Headless-Chromium (DevTools-Protokoll), sammelt JavaScript-Fehler, prüft die schmale Ansicht | `chromium` im PATH |
| `term_tabs.py` (gleiche Argumente) | Spielt die Terminal-Tabs durch: Eingabe, Neuladen, Wegnavigieren, zweiter Tab, Schließen, `exit` | `chromium` im PATH |
| `proxy_flow.py <adresse-des-dienstes> <token> [<bild.png>]` | Anmeldeweg eines Container-Ports: Dienst aufrufen, auf der Anmeldeseite von devc anmelden, wieder beim Dienst landen | `chromium` im PATH, devc hinter Caddy |
| `links_check.py <basis-url> <token> <container> <text-des-links> <ordner-für-bilder> [<text-zum-anklicken>]` | Links der Addon-Seite lesen, einem Link folgen, Antworten und Konsolenfehler der Zielseite melden | `chromium` im PATH, devc hinter Caddy |
| `DEVC_TOKEN=… file_view.py <basis-url> <container> <ordner-mit-testdateien> <ordner-für-bilder>` | Dateibetrachter: Text, Bearbeiten und Speichern, CRLF, Markdown, Bild mit Zoom und Verschieben, Hex. Erwartet die Testdateien aus dem Kopf des Skripts und ändert zwei davon | `chromium` im PATH |
| `DEVC_TOKEN=… backup_now.py <basis-url> <container> <ordner-für-bilder>` | Container-Dashboard ohne »Snapshot jetzt«, Seite Backups mit »Backup jetzt« und Fortschrittsbalken. Erstellt wirklich ein Backup | `chromium` im PATH |
| `DEVC_TOKEN=… file_menu.py <basis-url> <container> <ordner> <ordner-für-bilder>` | Dateiliste: Menü »…«, Vorschau und Entpacken, Löschen mit Rückfrage. Erwartet die Archive aus dem Kopf des Skripts | `chromium` im PATH |
| `DEVC_TOKEN=… addon_add.py <basis-url> <container> <addon> <parameter> <wert> <ordner-für-bilder>` | Addon hinzufügen: Auswahl, erste Einstellungen, einrichten. Fügt das Addon wirklich hinzu | `chromium` im PATH |
| `DEVC_TOKEN=… panel_git.py <basis-url> <container> <repository-im-container> <ordner-für-bilder>` | Kachel der Art `panel` am Addon `git`: Liste, Repository, Unterschied, Commit, Push, Rückgängig. Committet und pusht wirklich; das Repository braucht einen Commit, eine Änderung und einen Server | `chromium` im PATH |
| `DEVC_TOKEN=… addon_params.py <basis-url> <container> <addon> <parameter> <neuer-wert> <ordner-für-bilder>` | Addon-Seite: Kurzinfo, Einstellung ändern und übernehmen, Hilfeseite. Ändert den Parameter wirklich | `chromium` im PATH |
| `DEVC_TOKEN=… tiles_chat.py <basis-url> <container> <view-kachel> <chat-kachel> <ordner-für-bilder>` | Kacheln aus Addons: Dashboard, Seite der Art `view` mit Neuladen, Chat mit Kopfzeile, Popup der Befehle, Fenster der Einstellungen, Freigabe, Abbrechen, Neuladen der Seite, Beenden, Fortsetzen, schmale Ansicht. Als Chat-Kachel dient `echochat.chat` aus dem Ordner `echochat/` (Chat ohne KI): Ordner nach `~/.config/devc/addons/` kopieren, `devc install <container> echochat` | `chromium` im PATH, Container mit den Addons `info` und `echochat` |
| `DEVC_TOKEN=… vm_tile.py <basis-url> <neues-devc.pyz> <ordner-für-bilder>` | Kachel »VM«: Dashboard, beschränktes Terminal der VM (erlaubte und verbotene Befehle, Neuladen), Update per Upload bis zur neuen Anmeldung. Das Update läuft wirklich. Die Datei muss in einem Ordner liegen, den der Chromium-Snap lesen darf (nicht `/tmp`) | `chromium` im PATH, devc in einer VM aus `vm/install.sh` (zum Beispiel eine verschachtelte `it-vm`) |
| `sess_life.py` | Startet einen eigenen Server auf Port 8742, legt Terminal-Sitzungen an, schließt sie und beendet den Server per `SIGTERM`; zählt übrig gebliebene Prozesse | Container `it-sudo` |

Aufruf aus der Projektwurzel, zum Beispiel `python3 build/tools/gui_smoke.py http://127.0.0.1:8740 "$(python3 -m devc token show)" /,/templates,/addons /tmp/bilder`. Der Token steht dabei in der Prozessliste; das ist für eine Entwicklungs-VM hinnehmbar, im Betrieb nicht.

Zusätzliche Schalter für Chromium stehen in `CHROMIUM_FLAGS`, zum Beispiel `CHROMIUM_FLAGS=--ignore-certificate-errors` für Caddys eigene CA (`https://devc.localhost`). Das Browserprofil `chromium-profile/` entsteht neben den Skripten; für einen Lauf ohne alte Anmeldung vorher löschen.

Startet der Chromium-Snap nicht (»is not a snap cgroup«), fehlt die Nutzersitzung von systemd: einmal `sudo loginctl enable-linger $USER`, danach vor dem Aufruf `export XDG_RUNTIME_DIR=/run/user/$(id -u)`.
