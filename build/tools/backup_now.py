"""Schnellaktionen (SPEC 8.10): Container-Dashboard ohne »Snapshot jetzt«, Seite Backups mit »Backup jetzt«, Fortschritt und
Wiederaufnahme der Meldung nach einem Seitenwechsel.

Aufruf: DEVC_TOKEN=… backup_now.py <basis-url> <container> <ordner-für-bilder>
Erstellt wirklich ein Backup des Containers.
"""
import base64, json, os, socket, subprocess, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from devc.terminal import FrameReader, encode_frame, OP_TEXT

BASE, NAME, SHOTS = sys.argv[1:4]
TOKEN = os.environ["DEVC_TOKEN"]
profile = os.path.join(os.path.dirname(__file__), "chromium-profile")
chrome = subprocess.Popen(["chromium", "--headless=new", "--remote-debugging-port=9333", f"--user-data-dir={profile}",
                           "--no-first-run", "--disable-gpu", "--window-size=1280,900", *os.environ.get("CHROMIUM_FLAGS", "").split(), "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(100):
        try:
            targets = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json"))
            break
        except OSError:
            time.sleep(0.2)
    ws_url = [t for t in targets if t["type"] == "page"][0]["webSocketDebuggerUrl"]
    sock = socket.create_connection(("127.0.0.1", 9333))
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(f"GET /{ws_url.split('/', 3)[3]} HTTP/1.1\r\nHost: 127.0.0.1:9333\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
    stream = sock.makefile("rb")
    while stream.readline().strip():
        pass
    frames = FrameReader(stream.read, None, require_mask=False)
    counter, events = [0], []
    def call(method, **params):
        counter[0] += 1
        sock.sendall(encode_frame(OP_TEXT, json.dumps({"id": counter[0], "method": method, "params": params}).encode(), os.urandom(4)))
        while True:
            _, payload = frames.read_message()
            message = json.loads(payload)
            if message.get("id") == counter[0]:
                return message.get("result", message)
            events.append(message)
    def js(expression):
        return call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True).get("result", {}).get("value")
    def shot(name):
        open(os.path.join(SHOTS, name + ".png"), "wb").write(base64.b64decode(call("Page.captureScreenshot", format="png")["data"]))
    def errors():
        found = [e for e in events if e.get("method") == "Runtime.exceptionThrown" or (e.get("method") == "Log.entryAdded" and e["params"]["entry"]["level"] == "error")]
        return [(e["params"].get("exceptionDetails", {}).get("exception", {}).get("description") or e["params"].get("entry", {}).get("text", ""))[:200] + " " + e["params"].get("entry", {}).get("url", "")[:120] for e in found]
    call("Runtime.enable"); call("Log.enable"); call("Page.enable"); call("Network.enable")
    call("Page.navigate", url=BASE + "/"); time.sleep(1.5)
    js(f"fetch('/api/login', {{method: 'POST', headers: {{'Content-Type': 'application/json', 'X-Devc': '1'}}, body: JSON.stringify({{token: '{TOKEN}'}})}}).then(r => r.status)")
    call("Page.reload"); time.sleep(1.5)  # die GUI merkt die Anmeldung erst beim Neuladen

    js(f"location.hash = '#/c/{NAME}'"); time.sleep(3)
    print("Dashboard-Knöpfe:", js("Array.from(document.querySelectorAll('main button')).map(b => b.textContent).join(' | ')"))
    shot("dashboard")
    js(f"location.hash = '#/c/{NAME}/backups'"); time.sleep(2.5)
    print("Backups-Knöpfe:", js("Array.from(document.querySelectorAll('main .btn')).map(b => b.textContent).slice(0, 4).join(' | ')"))
    before = js("document.querySelectorAll('tbody tr').length")
    js("Array.from(document.querySelectorAll('main button')).find(b => b.textContent === 'Backup jetzt').click()")
    time.sleep(0.5)
    print("Dialog offen:", js("!!document.querySelector('dialog[open]')"), "| Seite:", js("location.hash"))
    # Wegnavigieren und zurück: Meldung und Balken müssen wieder erscheinen.
    probe = "(() => { const b = document.querySelector('.job-progress'); return (document.getElementById('messages').textContent || '').slice(0, 16) + ' | ' + (b && !b.hidden ? b.textContent : 'kein Balken'); })()"
    time.sleep(3)
    js("location.hash = '#/help'"); time.sleep(1.5)
    print("Auf der Hilfeseite:", js(probe))
    js(f"location.hash = '#/c/{NAME}'"); time.sleep(2)
    print("Container-Dashboard:", js(probe))
    js(f"location.hash = '#/c/{NAME}/backups'"); time.sleep(2)
    print("Zurück auf Backups:", js(probe))
    shot("zurueck")
    seen = []
    for _ in range(240):
        time.sleep(0.5)
        text = js("document.getElementById('messages').textContent") or ""
        if "Backup erstellt." in text or "fehlgeschlagen" in text:
            break
        step = js("(() => { const b = document.querySelector('.job-progress'); return b && !b.hidden ? b.querySelector('progress').value + ' | ' + b.textContent : null; })()")
        if step and (not seen or seen[-1] != step):
            seen.append(step)
            if len(seen) == 8:
                shot("fortschritt")
    print("Stände des Balkens:", len(seen), "| erster:", seen[:1], "| mittlerer:", seen[len(seen) // 2:len(seen) // 2 + 1], "| letzter:", seen[-1:])
    time.sleep(1.5)
    print("Meldung:", text, "| Zeilen vorher/nachher:", before, js("document.querySelectorAll('tbody tr').length"), "| Seite:", js("location.hash"))
    print("Oberste Zeile:", js("document.querySelector('tbody tr').innerText.replace(/\\s+/g, ' ').slice(0, 90)"))
    shot("backups")
    print("Fehler in der Konsole:", [e for e in errors() if "favicon" not in e and "api/status" not in e][:6])
finally:
    chrome.terminate()
