"""Addon zu einem Container hinzufügen (SPEC 8.10): Auswahl aus allen Addons, erste Einstellungen, einrichten.

Aufruf: DEVC_TOKEN=… addon_add.py <basis-url> <container> <addon> <parameter> <wert> <ordner-für-bilder>
Fügt das Addon wirklich hinzu. Es darf im Container noch nicht aktiv sein.
"""
import base64, json, os, socket, subprocess, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from devc.terminal import FrameReader, encode_frame, OP_TEXT

BASE, NAME, ADDON, PARAM, VALUE, SHOTS = sys.argv[1:7]
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


    js(f"location.hash = '#/c/{NAME}/config'"); time.sleep(2.5)
    print("Knopf auf Konfiguration:", js("Array.from(document.querySelectorAll('a.btn')).filter(a => a.textContent === 'Addon hinzufügen').map(a => a.getAttribute('href')).join()"))
    js(f"location.hash = '#/c/{NAME}/addons'"); time.sleep(2.5)
    print("Knopf auf Addons:", js("Array.from(document.querySelectorAll('a.btn')).filter(a => a.textContent === 'Addon hinzufügen').map(a => a.getAttribute('href')).join()"))
    js("Array.from(document.querySelectorAll('a.btn')).find(a => a.textContent === 'Addon hinzufügen').click()"); time.sleep(2.5)
    print("Auswahl:", js("document.querySelector('h1').textContent"), "|", js("Array.from(document.querySelectorAll('tbody tr')).map(r => r.querySelector('.mono').textContent + ':' + r.lastElementChild.textContent).join(' ')"))
    shot("addon-auswahl")
    js("Array.from(document.querySelectorAll('tbody tr')).find(r => r.querySelector('.mono').textContent === %s).querySelector('a.btn').click()" % json.dumps(ADDON)); time.sleep(2.5)
    print("Schritt 2:", js("document.querySelector('h1').textContent"), "|", js("document.querySelector('.card').innerText.replace(/\\n+/g, ' | ').slice(0, 400)"))
    js("(() => { const row = Array.from(document.querySelectorAll('.param')).find(r => r.querySelector('.mono').textContent === %s); const i = row.querySelector('input, select'); i.value = %s; i.dispatchEvent(new Event('input', {bubbles: true})); })()" % (json.dumps(PARAM), json.dumps(VALUE)))
    shot("addon-einstellungen")
    js("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Hinzufügen und einrichten').click()")
    for _ in range(240):
        time.sleep(1)
        text = js("document.getElementById('messages').textContent") or ""
        if "hinzugefügt." in text or "fehlgeschlagen" in text or "E_" in text:
            break
    print("Meldung:", text, "| Seite:", js("location.hash"))
    time.sleep(1.5)
    shot("addon-danach")
    print("Aktiv:", js("Array.from(document.querySelectorAll('section.addon h2')).map(e => e.textContent).join(' ')"))
    print("Fehler in der Konsole:", [e for e in errors() if "favicon" not in e and "api/status" not in e][:6])
finally:
    chrome.terminate()
