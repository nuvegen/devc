## Umgebung

- Du arbeitest als Nutzer `{user}` im Container `{container}`, Arbeitsverzeichnis `{workdir}`.
- Home `/home/dev` und `{workdir}` bleiben beim Neuaufbau des Containers erhalten; alles andere nicht.
- Vorhanden sind unter anderem `git`, `curl` und `rg` (ripgrep) für die Suche in Dateien.
- Systempakete nicht dauerhaft per apt nachinstallieren, sondern den Nutzer bitten, ein passendes devc-Addon zu ergänzen.
