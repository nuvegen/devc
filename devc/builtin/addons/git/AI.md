## Git

- Der Nutzer sieht die Repositories unter `{roots}` in devc auf der Kachel »Git«: Stand, Änderungen, Commits. Er kann dort committen, einen Commit zurücknehmen (`git revert`), pushen und pullen (nur ohne Zusammenführen, `--ff-only`).
- Du arbeitest weiter mit `git` im Terminal. Beides wirkt auf dieselben Repositories; was du committest, sieht der Nutzer dort.
- Lege neue Repositories unter `{workdir}` an, höchstens {depth} Ebenen tief, sonst findet die Kachel sie nicht.
- Schreibe die Geschichte nicht um (`git reset --hard`, `git push --force`, `git rebase` auf gepushten Commits), außer der Nutzer verlangt es ausdrücklich.
- Zugangsdaten für https stehen nie in der Adresse des Servers und nie in einer Datei unter `{workdir}`.
