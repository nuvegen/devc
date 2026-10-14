Zeigt auf einer eigenen Seite, wie es dem Container geht: System, Auslastung, Speicherplatz, Prozesse und installierte Addons.

### So benutzt du es

1. Öffne das Dashboard des Containers.
2. Klicke auf die Kachel »Info«.

Die Seite lädt sich alle 5 Sekunden neu, solange sie offen ist. Der Container muss dafür laufen.

### Was die Seite zeigt

| Abschnitt | Inhalt |
|---|---|
| Container | Name, System, Kernel, Laufzeit, IP-Adresse, Uhrzeit |
| Auslastung | CPUs, Last, Arbeitsspeicher, Zahl der Prozesse |
| Speicherplatz | `/`, `/work` und `/home/dev` |
| Prozesse | Die Prozesse mit der meisten Rechenzeit |
| Addons | Installierte Addons mit Version |

### Mehr oder weniger Prozesse zeigen

Im Feld `processes` steht, wie viele Prozesse die Seite zeigt, am Anfang 10. Mit 0 fehlt der Abschnitt.

```toml
addons = ["info"]

[params.info]
processes = 20
```

### Auf der Kommandozeile

```bash
# Dieselbe Seite als Text
devc tile show mein-container info.status

# Zahl der Prozesse ändern
devc config set mein-container info.processes=20
```

### Gut zu wissen

- Das Addon installiert nichts im Container. Die Seite entsteht bei jedem Aufruf neu aus einem kleinen Skript.
- Die Werte gelten für den Container, nicht für den Rechner, auf dem er läuft.
- Das Addon ist auch ein Beispiel für eigene Kacheln: Das Skript `info.sh` gibt Markdown aus, devc zeigt es an.
