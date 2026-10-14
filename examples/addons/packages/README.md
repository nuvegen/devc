Zusätzliche Pakete von Ubuntu für diesen Container. Für alles, was kein eigenes Addon hat.

- Parameter `apt`: Paketnamen, durch Leerzeichen getrennt. Erlaubt sind nur Namen, keine Optionen und keine Versionen.
- »Übernehmen« installiert die Pakete. Der Container startet dabei nicht neu.
- Parameter `recommends`: installiert auch die Zusatzpakete, die Ubuntu empfiehlt. Normal ist das aus.

```
devc install py-tools packages
devc config set py-tools packages.apt="jq sqlite3"
```

Ein Paket aus der Liste zu nehmen entfernt es nicht aus dem Container. Es fehlt erst, wenn der Container neu angelegt wird.

Sicherheits-Updates für die Pakete kommen wie für alle Pakete im Container mit `apt-get upgrade`.

**Beispiel: Browser für Playwright.** Playwright lädt seinen Browser selbst, braucht aber Bibliotheken des Systems. Diese Liste reicht für Chromium unter Ubuntu 24.04:

```
libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 libcairo2 libcups2t64 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0t64 libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 fonts-liberation fonts-noto-color-emoji
```

Danach im Container als Nutzer `dev`: `npx playwright install chromium`.
