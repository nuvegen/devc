## Web-Proxy

- Der Nutzer erreicht Dienste in diesem Container im Browser über diese Ports: {proxy} (Port oder Port=Beschriftung; leer heißt: keiner eingerichtet).
- Ein Dienst muss dafür auf `0.0.0.0` lauschen, nicht auf `127.0.0.1` oder `localhost`, und einen dieser Ports benutzen.
- Ohne Anmeldung erreichbar (für API-Aufrufe von außen) sind: {public}. Ein Dienst auf einem solchen Port ist für jeden offen, der den Proxy erreicht; dort keine Secrets und keine ungeschützten Schreibzugriffe anbieten.
- Weitere Ports trägt der Nutzer in der Konfiguration des Containers unter `[params.webproxy]` ein; nicht selbst ändern.
