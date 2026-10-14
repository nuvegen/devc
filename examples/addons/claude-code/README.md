Claude Code CLI, installiert mit dem offiziellen nativen Installer nach `~/.local/bin/claude`.

- Starten: im Terminal `claude` im Verzeichnis `/work`.
- Anmeldung, drei Wege:
  - Ohne Secret: einmalig `claude` starten und dem Browser-Flow folgen (Abo oder Console). Die Anmeldung bleibt in `~/.claude`.
  - Abo mit Token: Auf einem Rechner mit angemeldetem Claude Code `claude setup-token` ausführen und den Token als Secret-Slot `claude-code.oauth_token` hinterlegen (wird als `CLAUDE_CODE_OAUTH_TOKEN` in Login-Shells gesetzt).
  - API-Key: Secret-Slot `claude-code.api_key` (wird als `ANTHROPIC_API_KEY` in Login-Shells gesetzt; Claude Code fragt einmal, ob der Key verwendet werden soll).
- Sind Token und API-Key beide hinterlegt, gilt der Token; der API-Key wird dann nicht gesetzt.
- Modell: Das Feld »Modell« (`anthropic_model`) gilt für Terminal und Chat. Zur Wahl stehen `sonnet`, `opus` und `haiku`. Leer gilt die Voreinstellung von Claude Code.

### Anderer Dienst statt Anthropic

Manche Anbieter bilden die Schnittstelle von Anthropic nach, zum Beispiel BytePlus ModelArk. Wähle dann bei »Dienst« den Punkt »Anderer Dienst«. Es erscheinen drei Felder:

| Feld | Name | Beim Anbieter heißt es |
|---|---|---|
| Adresse | `base_url` | `ANTHROPIC_BASE_URL` |
| Schlüssel (Secret) | `auth_token` | `ANTHROPIC_AUTH_TOKEN` oder API-Key |
| Modell | `model` | `ANTHROPIC_MODEL`, zum Beispiel `ark-code-latest` |

Terminal und Chat benutzen danach diesen Dienst. Abo-Token und API-Key von Anthropic gehen nie an den anderen Dienst. Mit »Dienst: Anthropic« kehrst du zurück; die Felder des anderen Dienstes bleiben gespeichert, gelten dann aber nicht.

```bash
devc config set py-tools claude-code.provider=custom
devc config set py-tools claude-code.base_url=https://ark.ap-southeast.bytepluses.com/api/coding
devc config set py-tools claude-code.model=ark-code-latest
devc secret set py-tools claude-code.auth_token
devc config apply py-tools
```
- Einstellungen, Sitzungen und Memory liegen in `~/.claude` und `~/.claude.json` im Home-Volume und bleiben beim Neuaufbau erhalten.
- Updates installiert Claude Code selbst im Hintergrund. Der Parameter `channel` wählt `stable` oder `latest`.

### Chat in devc

Auf dem Dashboard des Containers steht die Kachel »Claude«. Dort schreibst du mit Claude Code, ohne ein Terminal zu öffnen.

1. Kachel »Claude« öffnen und »Neue Unterhaltung« klicken.
2. Nachricht schreiben und »Senden« klicken (oder Strg+Enter). Enter allein macht eine neue Zeile.
3. Will Claude ein Werkzeug benutzen, erscheint eine Frage. Antworte mit »Erlauben«, »Immer erlauben« oder »Ablehnen«.

- Oben stehen die Version von Claude Code und das Modell, schon bevor du schreibst.
- »A−« und »A+« oben rechts ändern die Textgröße.
- »Statistik« oben rechts zeigt Zahlen zur Unterhaltung: wie lange das Modell und die Werkzeuge gearbeitet haben, wie lange Claude auf dich gewartet hat, die Tokens (Eingabe, Cache, Ausgabe, Kontext) und die Zeit je Werkzeug.
- Tippst du `/`, zeigt ein Popup die Befehle von Claude Code, zum Beispiel `/model sonnet`, `/config` oder `/clear`. Die Pfeiltasten wählen, Enter übernimmt den Befehl, Strg+Enter sendet ihn.
- »Immer erlauben« gilt für dieses Werkzeug bis zum Ende der Unterhaltung.
- »Im Ordner erlauben« erscheint, wenn Claude eine Datei ändern will. Danach fragt Claude für Dateien in diesem Ordner und seinen Unterordnern nicht mehr, bis die Unterhaltung endet.
- Fragt Claude zu oft, klicke oben rechts auf »Freigaben: …« und wähle eine Stufe:
  - »Nachfragen«: Claude fragt vor jeder Änderung an einer Datei und vor jedem Befehl.
  - »Dateien im Projekt frei«: Claude ändert Dateien unter `/work` ohne Rückfrage. Vor Befehlen fragt es weiter.
  - »Alles erlauben«: Claude fragt nicht mehr. Es darf im Container alles, was der Nutzer `dev` darf.

  Die Wahl gilt sofort und für alle Unterhaltungen in diesem Container.
- Solange Claude arbeitet, steht unter dem Verlauf, was es gerade tut und wie lange schon.
- Bei längerer Arbeit meldet sich Claude zwischendurch kurz mit dem Stand. Das Feld `chat_updates` schaltet das ab.
- »Abbrechen« stoppt die laufende Antwort.
- Die Unterhaltung läuft weiter, wenn du die Seite verlässt. Sie endet mit »Beenden«, mit dem Stopp des Containers oder mit einem Neustart von devc.
- Bei langen Unterhaltungen zeigt der Chat nur den neueren Teil. »Ältere Einträge anzeigen« oben im Verlauf holt den Rest Stück für Stück zurück.
- »Beenden« schließt die Unterhaltung. Claude fasst sie dabei in wenigen Wörtern zusammen; das ist ihr Titel in der Liste.
- Unter »Frühere Unterhaltungen« zeigt »Öffnen« eine beendete Unterhaltung zum Ansehen. Erst »Fortsetzen« startet sie wieder. Der Verlauf liegt im Container unter `~/.local/state/devc-chat/claude-code/`.
- Der Chat arbeitet im Ordner `/work` und braucht eine Anmeldung wie oben (Token, API-Key oder einmal `claude` im Terminal).
- Das Modell kommt aus dem Feld »Modell«, wie im Terminal. Das alte Feld `chat_model` (nur für den Chat) wirkt weiter, wenn es gesetzt ist, steht aber nicht mehr in der Oberfläche.
- Zum Anmelden nimm weiter das Terminal. Manche `/`-Befehle brauchen auch ein Terminal; Claude meldet das dann im Chat.
