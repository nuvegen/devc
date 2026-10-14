"""Addon-Links im Browser: GUI anmelden, Links der Addon-Seite lesen, einem Link folgen.

Aufruf: links_check.py <basis-url> <token> <container> <text-des-links> <ordner-für-bilder> [<text-zum-anklicken>]
"""
import base64, json, os, socket, subprocess, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from devc.terminal import FrameReader, encode_frame, OP_TEXT

BASE, TOKEN, NAME, LABEL, SHOTS = sys.argv[1:6]
CLICK = sys.argv[6] if len(sys.argv) > 6 else ""
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
    js(f"location.hash = '#/c/{NAME}/addons'"); time.sleep(2.5)
    links = js("JSON.stringify(Array.from(document.querySelectorAll('a.btn[target=_blank]')).map(a => [a.textContent, a.href]))")
    print("Links der Addon-Seite:", links)
    print("Angezeigte Adressen:", js("JSON.stringify(Array.from(document.querySelectorAll('p.cmd')).map(p => p.textContent))"))
    shot("addons")
    target = dict(json.loads(links)).get(LABEL)
    if not target:
        raise SystemExit(f"FEHLER: Link {LABEL!r} fehlt oder ist nicht aktiv")
    del events[:]
    call("Page.navigate", url=target); time.sleep(8)
    print("Nach dem Klick:", js("location.href"), "| Titel:", js("document.title"))
    print("Text der Seite:", (js("document.body.innerText") or "")[:300].replace("\n", " | "))
    bad = [(e["params"]["response"]["status"], e["params"]["response"]["url"][:110]) for e in events if e.get("method") == "Network.responseReceived" and e["params"]["response"]["status"] >= 400]
    kinds = {}
    for e in events:
        if e.get("method") == "Network.responseReceived":
            kinds[e["params"]["type"]] = kinds.get(e["params"]["type"], 0) + 1
    print("Antworten nach Art:", kinds, "| WebSockets:", sum(e.get("method") == "Network.webSocketCreated" for e in events))
    print("Antworten ab 400:", bad[:8])
    print("Fehler in der Konsole:", errors()[:6])
    shot("ziel")
    if CLICK:
        hit = js("(() => { const n = Array.from(document.querySelectorAll('button, a, [role=button], div, span')).filter(n => n.innerText && n.innerText.trim() === %s).pop(); if (!n) return false; n.click(); return true; })()" % json.dumps(CLICK))
        time.sleep(3)
        print(f"Klick auf {CLICK!r}:", "getroffen" if hit else "NICHT GEFUNDEN", "| Text danach:", (js("document.body.innerText") or "")[:400].replace("\n", " | "))
        shot("klick")
finally:
    chrome.terminate()
