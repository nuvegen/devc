"""Addon-Seite eines Containers (SPEC 8.10): Kurzinfo, Einstellungen ändern und übernehmen, Hilfeseite je Addon.

Aufruf: DEVC_TOKEN=… addon_params.py <basis-url> <container> <addon> <parameter> <neuer-wert> <ordner-für-bilder>
Ändert den Parameter wirklich.
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


    js(f"location.hash = '#/c/{NAME}/addons'"); time.sleep(3)
    card = "Array.from(document.querySelectorAll('section.addon')).find(s => s.querySelector('h2').textContent === %s)" % json.dumps(ADDON)
    field = card + ".querySelectorAll('.param')"
    print("Karte:", js(card + ".innerText.replace(/\\n+/g, ' | ').slice(0, 420)"))
    print("Hilfe-Block alt vorhanden:", js("!!document.querySelector('section.addon .help')"), "| Übernehmen gesperrt:", js(card + ".querySelector('.addon-config button').disabled"))
    shot("addons")
    js("(() => { const row = Array.from(%s).find(r => r.querySelector('.mono').textContent === %s); const i = row.querySelector('input, select'); i.value = %s; i.dispatchEvent(new Event('input', {bubbles: true})); })()" % (field, json.dumps(PARAM), json.dumps(VALUE)))
    time.sleep(0.3)
    print("Nach der Eingabe gesperrt:", js(card + ".querySelector('.addon-config button').disabled"))
    js(card + ".querySelector('.addon-config button').click()")
    for _ in range(60):
        time.sleep(1)
        text = js("document.getElementById('messages').textContent") or ""
        if "übernommen." in text or "fehlgeschlagen" in text:
            break
    print("Meldung:", text)
    time.sleep(1.5)
    print("Karte danach:", js(card + ".innerText.replace(/\\n+/g, ' | ').slice(0, 260)"))
    js(f"location.hash = '#/c/{NAME}/addons/{ADDON}'"); time.sleep(2.5)
    print("Hilfeseite:", js("document.querySelector('h1').textContent"), "|", js("document.querySelector('.doc').innerText.slice(0, 120).replace(/\\n/g, ' ')"), "| Parameterzeilen:", js("document.querySelectorAll('tbody tr').length"))
    shot("addon-hilfe")
    print("Fehler in der Konsole:", [e for e in errors() if "favicon" not in e and "api/status" not in e][:6])
finally:
    chrome.terminate()
