## ABAP

- Zielsystem: `{sap_host}`, Mandant `{sap_client}`, Benutzer `{sap_user}`. Die Verbindungsdaten sind in Login-Shells als `SAP_*` gesetzt.
- `sapcli` für Zugriffe auf das System (Objekte lesen, aktivieren, abapGit). Vor ändernden Aufrufen den Nutzer fragen.
- `abaplint` für statische Prüfungen, Konfiguration in `{workdir}/.abaplint.json`.
- Vor der Arbeit `/FAQ` lesen. Eigene Erkenntnisse nur als Markdown in `/FAQ/ki` ablegen, eine Datei pro Thema; Dateien außerhalb von `/FAQ/ki` nie ändern.
- Keine Kunden- oder Produktivdaten in Dateien unter `{workdir}` ablegen.
