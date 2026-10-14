Eine Kachel »Git« für den Container: Repositories finden, den Stand ansehen, committen und pushen, ohne ein Terminal zu öffnen.

**So benutzt du es**

1. Kachel »Git« öffnen. Die Liste zeigt alle Repositories unter `/work` und `/home/dev`.
2. »Öffnen« zeigt ein Repository: Zweig, Server, geänderte Dateien, die letzten Commits.
3. Für einen Commit die Beschreibung eintragen und »Commit erstellen« klicken.
4. »Push« schickt die Commits zum Server, »Pull« holt neue vom Server.

**Dateien auswählen**

Normal nimmt ein Commit alle Änderungen auf. Sollen nur einzelne Dateien hinein, klicke bei diesen auf »Vormerken« und schalte im Formular »Alle Änderungen aufnehmen« aus. »Unterschied« zeigt, was sich in einer Datei geändert hat.

**Einen Commit rückgängig machen**

»Rückgängig« bei einem Commit erzeugt einen neuen Commit, der diese Änderung zurücknimmt (`git revert`). Es geht nichts verloren, auch nicht nach einem Push. Hast du nicht committete Änderungen an denselben Dateien, lehnt git ab; das Repository bleibt dann, wie es war.

**Push und Pull**

- »Push« schickt deine Commits zum Server.
- »Pull« holt die Commits vom Server. Er führt nichts zusammen: Haben du und der Server beide neue Commits, ändert der Pull nichts und sagt dir das. Das Zusammenführen machst du dann im Terminal.
- Die Zahlen an den Knöpfen und in der Liste stammen vom letzten Push oder Pull.

**Token für https einrichten**

GitHub und GitLab nehmen über https kein Passwort mehr, sondern ein Token. Du legst es dort in den Einstellungen deines Kontos an (bei GitHub: »Personal access token« mit Schreibrecht auf das Repository) und hinterlegst es in devc:

```
devc secret set py-tools git.token
```

In der Oberfläche: Seite »Secrets«, Eintrag `git.token`. Einen Benutzernamen braucht es mit einem Token nicht. Nur bei einem Server mit Benutzername und Passwort trägst du `username` ein und nimmst das Passwort als `git.token`.

Das Secret liegt im Container unter `/run/devc/secrets/git.token` und ist dort für den Nutzer `dev` lesbar, also auch für eine KI im Container. Nimm darum ein Token, das nur darf, was nötig ist.

Für Adressen mit ssh (`git@…`) braucht es kein Token; dort gilt der Schlüssel des Containers.

**Einstellungen**

| Name | Bedeutung |
|---|---|
| `roots` | Ordner, in denen gesucht wird |
| `depth` | so viele Ebenen tief |
| `username` | Benutzername für https; mit einem Token leer lassen |
| Secret `token` | Token für https, bei anderen Servern das Passwort |

Ordner wie `node_modules` und Ordner, die mit einem Punkt beginnen, lässt die Suche aus. Repositories innerhalb eines Repositorys zeigt die Liste nicht.

**Was die Kachel nicht macht**

Zweige wechseln, zusammenführen und Konflikte lösen gehören ins Terminal.
