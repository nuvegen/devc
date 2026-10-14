Werkzeuge für ABAP-Entwicklung gegen ein SAP-System.

- **sapcli** (pipx, Nutzer dev). Verbindung über `SAP_ASHOST`, `SAP_PORT`, `SAP_SSL`, `SAP_CLIENT`, `SAP_USER` aus den Addon-Parametern; das Passwort kommt aus dem Secret-Slot `abap.sap_password` als `SAP_PASSWORD`. Ohne Passwort fragt sapcli nach.
- **abaplint** (`@abaplint/cli`, global). Projektkonfiguration in `/work/.abaplint.json`.
- **abaplint-Transpiler** (`@abaplint/transpiler-cli`, global).
- **FAQ-Dokumente** für die KI unter `/FAQ` (aus `files/faq.tar.gz`, gehört root, für dev nur lesbar). Bei jeder Installation des Addons wird `/FAQ` neu aus dem Archiv entpackt. Eigene Dateien der KI liegen in `/FAQ/ki`, einem Symlink auf `/work/.devc/faq-ki`; ihr Inhalt bleibt beim Neuaufbau erhalten.
- Eine SAP-Verbindung pro Container. Für ein weiteres System einen weiteren Container anlegen.

Benötigt das Addon `node`.
