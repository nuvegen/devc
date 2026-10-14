Codex CLI von OpenAI mit einer Chat-Kachel. Codex arbeitet wie Claude Code: Es liest und ändert Dateien und führt Befehle aus. Das Modell kommt von OpenAI oder von einem kompatiblen Dienst, zum Beispiel Novita.

### Einrichten

1. `base_url`: die Adresse des Dienstes. Voreingestellt ist Novita (`https://api.novita.ai/openai/v1`). Leer bedeutet OpenAI selbst.
2. `model`: der Name des Modells, genau wie der Dienst ihn nennt, zum Beispiel `deepseek/deepseek-v3.1`.
3. Secret `codex.api_key`: der API-Key des Dienstes.

Der Dienst muss die »Responses API« von OpenAI anbieten (`<base_url>/responses`). Codex spricht nur diese; die ältere »Chat Completions API« allein genügt nicht. Das Modell muss Werkzeuge aufrufen können.

```bash
devc install py-tools codex
devc config set py-tools codex.model=deepseek/deepseek-v3.1
devc secret set py-tools codex.api_key
```

### Chat in devc

Auf dem Dashboard des Containers steht die Kachel »Codex«.

1. Kachel öffnen und »Neue Unterhaltung« klicken.
2. Nachricht schreiben und »Senden« klicken (oder Strg+Enter). Enter allein macht eine neue Zeile.
3. Will Codex etwas ändern, erscheint eine Frage. Antworte mit »Erlauben«, »Immer erlauben« oder »Ablehnen«.

- Befehle, die nur lesen (`ls`, `cat`, `grep`), führt Codex ohne Rückfrage aus.
- »Immer erlauben« gilt für alle Befehle bis zum Ende der Unterhaltung.
- Fragt Codex zu oft, klicke oben rechts auf »Freigaben: …« und wähle »Alles erlauben«. Dann fragt Codex nicht mehr und darf im Container alles, was der Nutzer `dev` darf. Die Wahl gilt für alle Unterhaltungen in diesem Container.
- »Statistik« zeigt Arbeitszeit, Tokens und Werkzeuge der Unterhaltung.
- »Abbrechen« stoppt die laufende Antwort. Schreibst du, während Codex arbeitet, kommt die Nachricht danach an die Reihe.
- »Beenden« schließt die Unterhaltung. Codex fasst sie dabei in wenigen Wörtern zusammen; das ist ihr Titel in der Liste.
- Frühere Unterhaltungen stehen in der Liste; »Öffnen« zeigt sie, »Fortsetzen« führt sie weiter.
- Bei langen Unterhaltungen zeigt der Chat nur den neueren Teil. »Ältere Einträge anzeigen« oben im Verlauf holt den Rest Stück für Stück zurück.

### Im Terminal

`codex` startet Codex mit denselben Einstellungen (Adresse, Modell, API-Key). Im Container gibt es keine zweite Sandbox; starte es deshalb so:

```bash
codex --sandbox danger-full-access
```

### Gut zu wissen

- Codex liegt unter `/usr/local/lib/devc-codex/`, das Startskript unter `/usr/local/bin/codex`. Einstellungen und Unterhaltungen von Codex liegen in `~/.codex` und bleiben beim Neuaufbau erhalten.
- `release = latest` lädt die neueste Version beim ersten Einrichten und bleibt dann dabei. Für ein Update eine feste Version eintragen, zum Beispiel `0.162.1`, und »Anwenden« klicken.
- Kennt Codex ein Modell nicht, arbeitet es mit allgemeinen Vorgaben. Dateien ändert es dann über Befehle in der Shell.
