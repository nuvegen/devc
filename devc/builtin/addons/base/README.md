Grundsystem jedes devc-Containers. Wird immer installiert.

- Nutzer `dev` (UID/GID 1000), Home `/home/dev` liegt im Home-Volume und bleibt beim Neuaufbau erhalten.
- Werkzeuge: git, curl, ssh-Client, nano, less, unzip, ripgrep (`rg`).
- Locale `de_DE.UTF-8`, Zeitzone über den Parameter `timezone`.
- `sudo` ohne Passwort nur, wenn der Parameter `sudo = true` gesetzt ist.
- git-Identität über `git_name` und `git_email`. Ein Git-Remote mit Deploy-Key wird über `[git]` in der devc.toml und den Secret-Slot `git.key` eingerichtet.
