Python mit venv, pip, pipx und ruff.

- Version über den Parameter `version`. 3.12 ist die Systemversion von Ubuntu 24.04; 3.13 kommt aus dem deadsnakes-PPA.
- Aktion »venv anlegen« erzeugt `/work/.venv`.
- Aktion »Abhängigkeiten installieren« führt `pip install -e /work` in der venv aus.
- `ruff` ist über pipx für den Nutzer dev installiert.
