"""Kachel der Art panel am Beispiel des Addons git (SPEC 8.13): Liste, Repository, Unterschied, Commit, Push, Rückgängig.

Aufruf: DEVC_TOKEN=… panel_git.py <basis-url> <container> <repository-im-container> <ordner-für-bilder>
Der Container braucht das Addon git und ein Repository mit einem Commit, einer Änderung und einem Server (origin).
Committet und pusht wirklich.
"""
import base64, json, os, socket, subprocess, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from devc.terminal import FrameReader, encode_frame, OP_TEXT

BASE, NAME, REPO, SHOTS = sys.argv[1:5]
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


    def wait(expression, seconds=40):
        for _ in range(seconds * 4):
            if js(expression):
                return True
            time.sleep(0.25)
        return False
    def click(label, scope="document"):
        return js("(() => { const b = Array.from(%s.querySelectorAll('a.btn, button.btn')).find(e => e.textContent === %s); if (b) b.click(); return !!b; })()" % (scope, json.dumps(label)))
    message = "(document.getElementById('messages') || {}).textContent"
    js(f"location.hash = '#/c/{NAME}'"); time.sleep(2.5)
    print("Kachel:", js("Array.from(document.querySelectorAll('a.tile')).filter(a => a.querySelector('.title').textContent === 'Git').map(a => a.innerText.replace(/\\n+/g, ' | ')).join()"))
    js("Array.from(document.querySelectorAll('a.tile')).find(a => a.querySelector('.title').textContent === 'Git').click()")
    wait("document.querySelectorAll('tbody tr').length > 0")
    print("Liste:", js("Array.from(document.querySelectorAll('tbody tr')).map(r => r.innerText.replace(/\\s*\\n\\s*|\\t/g, ' · ')).join(' || ')"))
    shot("git-liste")
    js("Array.from(document.querySelectorAll('tbody tr')).find(r => r.firstElementChild.textContent === %s).querySelector('a.btn').click()" % json.dumps(REPO))
    wait("document.querySelector('h1').textContent !== 'Git'")
    print("Repository:", js("document.querySelector('h1').textContent"), "| Adresse:", js("location.hash"), "| Titel:", js("document.title"))
    print("Abschnitte:", js("Array.from(document.querySelectorAll('h2.sec')).map(e => e.textContent).join(' | ')"))
    print("Änderungen:", js("Array.from(document.querySelectorAll('section')).find(s => (s.querySelector('h2') || {}).textContent === 'Änderungen').innerText.replace(/\\s*\\n\\s*|\\t/g, ' · ').slice(0, 300)"))
    shot("git-repo")
    click("Unterschied"); wait("!!document.querySelector('pre.panel-code')")
    print("Unterschied:", js("document.querySelector('pre.panel-code').textContent.replace(/\\n/g, ' ⏎ ').slice(0, 160)"))
    shot("git-diff")
    click("Zurück zum Repository"); wait("!!document.querySelector('textarea')")
    click("Commit erstellen"); time.sleep(0.5)
    print("Ohne Beschreibung:", js(message))
    js("(() => { const t = document.querySelector('textarea'); t.value = 'Zweiter Commit aus der Kachel'; t.dispatchEvent(new Event('input', {bubbles: true})); })()")
    click("Commit erstellen"); wait(message + ".includes('Commit erstellt')")
    print("Commit:", js(message), "| Push-Knopf:", js("Array.from(document.querySelectorAll('button.btn')).map(b => b.textContent).filter(t => t.startsWith('Push')).join()"))
    click("Push (1)"); wait(message + ".includes('Push erledigt')")
    print("Push:", js(message), "| Ausgabe:", js("(document.querySelector('details .panel-code') || {}).textContent"))
    shot("git-push")
    js("Array.from(document.querySelectorAll('tbody tr')).find(r => r.innerText.includes('Zweiter Commit')).querySelectorAll('button.btn')[0].click()"); time.sleep(0.6)
    print("Rückfrage:", js("document.getElementById('dialog-text').textContent"))
    shot("git-rueckfrage")
    js("document.getElementById('dialog-ok').click()"); wait(message + ".includes('rückgängig')")
    print("Rückgängig:", js(message))
    print("Commits:", js("Array.from(document.querySelectorAll('section')).find(s => (s.querySelector('h2') || {textContent: ''}).textContent.startsWith('Letzte Commits')).innerText.replace(/\\s*\\n\\s*|\\t/g, ' · ').slice(0, 420)"))
    shot("git-danach")
    js("history.back()"); time.sleep(2)
    print("Zurück im Browser:", js("location.hash"), "|", js("document.querySelector('h1').textContent"))
    print("Fehler in der Konsole:", [e for e in errors() if "favicon" not in e and "api/status" not in e][:6])
finally:
    chrome.terminate()
