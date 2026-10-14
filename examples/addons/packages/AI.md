## Pakete von Ubuntu

- Zusätzlich installiert (Parameter `apt`): `{apt}`. Leer heißt: keine.
- Fehlt dir ein Paket, installiere es nicht stillschweigend selbst: Nach dem Neuanlegen des Containers wäre es wieder weg. Nenne dem Nutzer den Paketnamen. Er trägt ihn ein mit `devc config set {container} packages.apt="<bisherige Liste> <neues-paket>"`, oder in der Oberfläche beim Addon »Pakete«.
- Die Liste nimmt nur Paketnamen von Ubuntu an (`apt`), keine Optionen, keine Versionen, keine fremden Paketquellen.
- Werkzeuge ohne root gehören nach `/home/dev` oder `{workdir}`, zum Beispiel `pipx install …`, `npm install …`, `npx playwright install chromium`.
