"""Bildschirmfotos für die Präsentation (pitch/img/*.png).

Aufruf, aus der Wurzel des Repositorys:
    DEVC_TOKEN=… python3 pitch/screenshots.py <basis-url> <container> <ordner-für-bilder>

Braucht `chromium` und eine vorbereitete devc-Instanz; die Schritte stehen in pitch/README.md.
Die Unterhaltung mit Claude ist gestellt (pitch/demo/bridge.py), alles andere ist die echte Oberfläche.
"""
import base64, json, os, socket, subprocess, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from devc.terminal import FrameReader, encode_frame, OP_TEXT

BASE, NAME, SHOTS = sys.argv[1:4]
QUESTION = "Der Test für den Rabatt ab 100 Euro schlägt fehl. Kannst du nachsehen und es beheben?"
TOKEN = os.environ["DEVC_TOKEN"]
profile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build", "tools", "chromium-profile")
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


    # Doppelte Auflösung, damit die Bilder auch groß gezeigt scharf sind.
    call("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=2, mobile=False)
    def wait(expression, seconds=40):
        for _ in range(seconds * 4):
            if js(expression):
                return True
            time.sleep(0.25)
        return False
    def go(route, pause=2.5):
        js("location.hash = " + json.dumps("#" + route)); time.sleep(pause)
    def click(label):
        return js("(() => { const b = Array.from(document.querySelectorAll('a.btn, button.btn, a.tile')).find(e => e.textContent.trim() === %s); if (b) b.click(); return !!b; })()" % json.dumps(label))
    def typ(text, pause=1.5):
        js("document.querySelector('.xterm-helper-textarea').focus()")
        call("Input.insertText", text=text)
        call("Input.dispatchKeyEvent", type="rawKeyDown", windowsVirtualKeyCode=13, key="Enter", code="Enter")
        call("Input.dispatchKeyEvent", type="char", text="\r", key="Enter", code="Enter")
        call("Input.dispatchKeyEvent", type="keyUp", windowsVirtualKeyCode=13, key="Enter", code="Enter")
        time.sleep(pause)

    # Der Rahmen, den die Seite nach jedem Wechsel für die Tastatur setzt, gehört nicht ins Bild.
    js("document.head.append(Object.assign(document.createElement('style'), {textContent: '#main:focus { outline: none !important; }'}))")
    go("/")
    # Container, die nicht zu dieser Instanz gehören (»fremd«), gehören nicht ins Bild.
    js("document.querySelectorAll('tbody tr').forEach(r => { if (/fremd/.test(r.innerText)) r.remove(); })")
    js("(() => { const n = document.querySelectorAll('tbody tr').length; document.querySelector('.head .sub').textContent = n + ' Container · ' + n + ' laufen'; })()")
    shot("01-dashboard")

    go("/new")
    shot("02-neuer-container")

    go(f"/c/{NAME}")
    shot("03-container")

    go(f"/c/{NAME}/tile/claude-code.chat")
    click("Neue Unterhaltung"); wait("!!document.querySelector('.chat-input')")
    time.sleep(1.5)
    js("(() => { const t = document.querySelector('.chat-input'); t.value = %s; t.dispatchEvent(new Event('input', {bubbles: true})); })()" % json.dumps(QUESTION))
    click("Senden"); wait("document.querySelectorAll('.chat-msg.agent').length >= 3 && /bereit/.test(document.querySelector('.chat-head').innerText)")
    time.sleep(1)
    js("document.querySelector('.chat-log').scrollTop = 0")
    shot("04-claude")
    click("Beenden"); time.sleep(3)

    # Sitzungen von früheren Läufen schließen, damit das Terminal leer beginnt.
    js("fetch('/api/containers/%s/terminals').then(r => r.json()).then(d => Promise.all(d.sessions.map(s => fetch('/api/containers/%s/terminals/' + s.id, {method: 'DELETE', headers: {'X-Devc': '1'}}))))" % (NAME, NAME))
    time.sleep(1)
    go(f"/c/{NAME}/terminal", 3)
    click("Neue Sitzung"); wait("!!document.querySelector('.xterm-helper-textarea')"); time.sleep(2)
    typ("cd webshop && git status -sb")
    typ("python3 -m unittest -q")
    typ("git log --oneline")
    shot("05-terminal")

    go(f"/c/{NAME}/tile/git.repos?v=repo&a.repo=%2Fwork%2Fwebshop", 4)
    shot("06-git")

    go(f"/c/{NAME}/addons", 3)
    js("(() => { const s = Array.from(document.querySelectorAll('section.addon')).find(s => s.querySelector('h2').textContent === 'webproxy'); if (s) s.scrollIntoView({block: 'start'}); window.scrollBy(0, -16); })()")
    time.sleep(0.5)
    shot("07-addons")
    print("Bilder:", sorted(f for f in os.listdir(SHOTS) if f.endswith(".png")))
    print("Fehler in der Konsole:", [e for e in errors() if "favicon" not in e and "api/status" not in e][:6])
finally:
    chrome.terminate()
