opencode CLI, installiert mit dem offiziellen Installationsskript.

- Starten: im Terminal `opencode` im Verzeichnis `/work`.
- Im Browser: Aktion »Weboberfläche starten«, dann »opencode öffnen«. Der Link führt über den Proxy von devc (`https://<port>-<container>.<basisdomain>/`); der Port steht im Parameter `port` (Standard 4096). »Weboberfläche stoppen« beendet sie.
- Anmeldung beim Anbieter: in opencode `/connect` aufrufen und den Key eingeben. Der Zugang liegt im Home-Volume und bleibt beim Neuaufbau erhalten.
- opencode liest die Projektregeln aus `AGENTS.md`; devc legt sie als Verweis auf `CLAUDE.md` an.
