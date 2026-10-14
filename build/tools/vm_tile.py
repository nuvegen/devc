"""Kachel »VM« im Browser: Dashboard, beschränktes Terminal der VM, Update von devc per Upload.

Aufruf: DEVC_TOKEN=… python3 build/tools/vm_tile.py <basis-url> <neues-devc.pyz> <ordner-für-bilder>
Braucht ein devc in einer VM, die vm/install.sh eingerichtet hat. Das Update läuft wirklich: Der Dienst startet neu.
Das neue devc.pyz muss sich vom laufenden unterscheiden.
"""
import base64, json, os, socket, subprocess, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from devc.terminal import FrameReader, encode_frame, OP_TEXT

BASE, PYZ, SHOTS = sys.argv[1:4]
TOKEN = os.environ["DEVC_TOKEN"]
profile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chromium-profile")
chrome = subprocess.Popen(["chromium", "--headless=new", "--remote-debugging-port=9333", f"--user-data-dir={profile}",
                           "--no-first-run", "--disable-gpu", "--window-size=1280,900", *os.environ.get("CHROMIUM_FLAGS", "").split(), "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(300):
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
    def go(route, wait=2.0):
        js("location.hash = " + json.dumps("#" + route)); time.sleep(wait)
    texts = lambda selector: js(f"JSON.stringify(Array.from(document.querySelectorAll({json.dumps(selector)})).map(n => n.innerText.trim().replace(/\\s+/g, ' ').slice(0, 160)))")
    screen = lambda: js("JSON.stringify((document.querySelector('.xterm-rows') || {innerText: '-'}).innerText.split('\\n').map(l => l.trimEnd()).filter(Boolean).slice(-3))")
    def typ(text, wait=1.2):
        js("document.querySelector('.xterm-helper-textarea').focus()")
        call("Input.insertText", text=text)
        call("Input.dispatchKeyEvent", type="rawKeyDown", windowsVirtualKeyCode=13, key="Enter", code="Enter")
        call("Input.dispatchKeyEvent", type="char", text="\r", key="Enter", code="Enter")
        call("Input.dispatchKeyEvent", type="keyUp", windowsVirtualKeyCode=13, key="Enter", code="Enter")
        time.sleep(wait)
        print("  $", text, "→", screen())
    vm = lambda: js("fetch('/api/vm').then(r => r.json()).then(d => d.version + ' ' + d.build).catch(e => 'nicht erreichbar')")

    os.makedirs(SHOTS, exist_ok=True)
    call("Runtime.enable"); call("Log.enable"); call("Page.enable"); call("DOM.enable")
    call("Page.navigate", url=BASE + "/"); time.sleep(1.5)
    print("Login:", js(f"fetch('/api/login', {{method: 'POST', headers: {{'Content-Type': 'application/json', 'X-Devc': '1'}}, body: JSON.stringify({{token: '{TOKEN}'}})}}).then(r => r.status)"))
    call("Page.reload"); time.sleep(2.0)
    print("Dashboard-Kacheln:", texts(".tile"))
    shot("vm_dashboard")
    go("/vm", 3.5)
    print("Seite VM:", js("document.querySelector('h1').textContent"), "| Status:", js("document.querySelector('.head .sub').textContent"), "| Tabs:", texts(".tab-name"),
          "| Auswahl des Nutzers versteckt:", js("document.querySelector('.head select').hidden"), "| Knöpfe:", texts(".head .actions .btn"))
    print("  Hinweis:", texts("main > .hint")[:400])
    typ("devc list", 3.0)
    typ("lxc list --project devcontainers --format csv | wc -l", 3.0)
    typ("cd /tmp")
    typ("/bin/bash")
    typ("ls > datei")
    typ("journalctl -u devc -n 1 --no-pager | tail -1", 2.0)
    typ("commands")
    shot("vm_terminal")
    call("Page.reload"); time.sleep(3.5)
    print("  nach Neuladen wieder da:", texts(".tab-name"), screen())
    before = vm()
    go("/vm/update", 2.0)
    print("Update-Seite:", js("document.querySelector('h1').textContent"), "|", js("document.querySelector('.head .sub').textContent"))
    root = call("DOM.getDocument")["root"]["nodeId"]
    node = call("DOM.querySelector", nodeId=root, selector="input[type=file]")["nodeId"]
    call("DOM.setFileInputFiles", nodeId=node, files=[os.path.abspath(PYZ)])
    js("Array.from(document.querySelectorAll('main .btn')).find(n => n.textContent.trim() === 'Hochladen und aktualisieren').click()"); time.sleep(0.8)
    print("  Rückfrage:", js("document.getElementById('dialog').open"), "|", js("document.getElementById('dialog-text').textContent"))
    shot("vm_update_confirm")
    js("document.getElementById('dialog-ok').click()"); time.sleep(2.5)
    print("  danach:", texts(".msg"))
    shot("vm_update_wait")
    for step in range(60):
        time.sleep(2)
        if js("location.hash") == "#/vm" and js("document.querySelector('input[type=password], .tabs') !== null"):
            break
    print(f"  nach {2.5 + 2 * (step + 1):.0f} s:", js("location.hash"), "| Anmeldung steht da:", js("document.querySelector('input[type=password]') !== null"))
    shot("vm_after_update")
    js(f"fetch('/api/login', {{method: 'POST', headers: {{'Content-Type': 'application/json', 'X-Devc': '1'}}, body: JSON.stringify({{token: '{TOKEN}'}})}}).then(r => r.status)")
    call("Page.reload"); time.sleep(4.0)
    print("  nach dem Anmelden:", js("location.hash"), "| vorher:", before, "| jetzt:", vm(), "| Tabs (neue Sitzung):", texts(".tab-name"), "| Schirm:", screen())
    shot("vm_after_login")
    errors = [e for e in events if e.get("method") == "Runtime.exceptionThrown"]
    print("JS-Fehler:", [e["params"].get("exceptionDetails", {}).get("exception", {}).get("description", "")[:300] for e in errors])
finally:
    chrome.terminate()
