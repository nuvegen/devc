"""Dateiliste im Browser (SPEC 8.10): Menü »…«, Vorschau und Entpacken, Löschen mit Rückfrage.

Aufruf: DEVC_TOKEN=… file_menu.py <basis-url> <container> <ordner> <ordner-für-bilder>
Erwartet im Ordner: pkg.tar.gz (ein Ordner pkg/), den entpackten Ordner pkg/ und lose.zip (mehrere Einträge oben). Legt lose/ an und löscht es wieder.
"""
import base64, json, os, socket, subprocess, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from devc.terminal import FrameReader, encode_frame, OP_TEXT

BASE, NAME, FOLDER, SHOTS = sys.argv[1:5]
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


    listing = f"location.hash = '#/c/{NAME}/files?path=' + encodeURIComponent('{FOLDER}')"
    names = "Array.from(document.querySelectorAll('td a.mono')).map(a => a.textContent).join(', ')"
    def menu(name):
        return "Array.from(document.querySelectorAll('tbody tr')).find(r => r.querySelector('td a.mono') && r.querySelector('td a.mono').textContent === %s).querySelector('details.menu')" % json.dumps(name)
    js(listing); time.sleep(2.5)
    print("Liste:", js(names))
    js(menu("pkg.tar.gz") + ".querySelector('summary').click()"); time.sleep(0.4)
    print("Menü eines Archivs:", js(menu("pkg.tar.gz") + ".querySelector('.menu-list').innerText.replace(/\\n/g, ' | ')"),
          "| sichtbar im Fenster:", js("(() => { const b = document.querySelector('details.menu[open] .menu-list').getBoundingClientRect(); return b.top >= 0 && b.bottom <= innerHeight && b.left >= 0 && b.right <= innerWidth; })()"))
    shot("menue")
    print("Menü eines Ordners:", js(menu("pkg/") + ".querySelector('.menu-list').innerText.replace(/\\n/g, ' | ')"))
    js(f"location.hash = '#/c/{NAME}/unpack?path=' + encodeURIComponent('{FOLDER}/pkg.tar.gz')"); time.sleep(2.5)
    state = "(() => document.querySelector('input[value=here]').checked ? 'hier' : 'neuer Ordner')()"
    rows = "Array.from(document.querySelectorAll('tbody tr')).map(r => r.innerText.replace(/\\t/g, ' · ')).join(' || ')"
    print("Archiv mit einem Ordner – vorgewählt:", js(state), "|", js(rows))
    shot("entpacken-einzeln")
    js(f"location.hash = '#/c/{NAME}/unpack?path=' + encodeURIComponent('{FOLDER}/lose.zip')"); time.sleep(2.5)
    print("Loses Archiv – vorgewählt:", js(state), "| Ordnername:", js("document.querySelector('.unpack-target input[type=text]').value"), "|", js(rows))
    print("Warnung:", js("(document.querySelector('.msg.warn') || {}).textContent"))
    shot("entpacken-lose")
    js("document.querySelector('input[value=here]').click()"); time.sleep(1)
    print("Umgeschaltet auf hier:", js(rows))
    js("document.querySelector('input[value=folder]').click()"); time.sleep(1)
    js("Array.from(document.querySelectorAll('.fv-bar button')).find(b => b.textContent === 'Entpacken').click()"); time.sleep(3)
    print("Nach dem Entpacken:", js("location.hash"), "|", js("document.getElementById('messages').textContent"), "|", js(names))
    js(listing); time.sleep(2)
    js(menu("lose/") + ".querySelector('summary').click()"); time.sleep(0.3)
    js(menu("lose/") + ".querySelector('button.danger').click()"); time.sleep(0.5)
    print("Rückfrage:", js("document.getElementById('dialog-text').textContent"))
    shot("loeschen")
    js("document.getElementById('dialog-ok').click()"); time.sleep(2.5)
    print("Nach dem Löschen:", js("document.getElementById('messages').textContent"), "|", js(names))
    print("Fehler in der Konsole:", [e for e in errors() if "favicon" not in e and "api/status" not in e][:6])
finally:
    chrome.terminate()
