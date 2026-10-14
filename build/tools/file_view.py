"""Dateibetrachter im Browser (SPEC 8.10): Text, Bearbeiten und Speichern, Markdown, Bild mit Zoom, Hex.

Aufruf: DEVC_TOKEN=… file_view.py <basis-url> <container> <ordner-mit-testdateien> <ordner-für-bilder>
Erwartet im Ordner: notiz.txt, win.txt (CRLF), lies.md, bild.png, klein.svg, daten.bin. Ändert notiz.txt und win.txt.
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

    def open_file(name, wait=2.0):
        js(f"location.hash = '#/c/{NAME}/file?path=' + encodeURIComponent('{FOLDER}/{name}')"); time.sleep(wait)
    def click(label):
        hit = js("(() => { const n = Array.from(document.querySelectorAll('.fv-bar button')).find(n => n.textContent.trim() === %s); if (!n) return false; n.click(); return true; })()" % json.dumps(label))
        time.sleep(0.6)
        return hit
    def bar():
        return js("Array.from(document.querySelectorAll('.fv-bar button')).map(b => b.textContent + (b.classList.contains('on') ? '*' : '')).join(' | ')")
    def raw(name):
        return js(f"fetch('/api/containers/{NAME}/files/raw?path=' + encodeURIComponent('{FOLDER}/{name}')).then(r => r.text()).then(t => JSON.stringify(t))")

    js(f"location.hash = '#/c/{NAME}/files?path=' + encodeURIComponent('{FOLDER}')"); time.sleep(2.5)
    print("Liste:", js("Array.from(document.querySelectorAll('td a.mono')).map(a => a.textContent).join(', ')"))
    shot("liste")

    open_file("notiz.txt")
    print("Text – Leiste:", bar(), "| Zeilennummern:", js("document.querySelector('.fv-ln').textContent.trim().split('\\n').length"),
          "| Höhe der Fläche:", js("document.querySelector('.fv-body').clientHeight"), "von Fenster", js("innerHeight"))
    shot("text")
    click("Bearbeiten")
    js("(() => { const a = document.querySelector('.fv-edit'); a.value = 'NEU ' + a.value; a.dispatchEvent(new Event('input')); })()"); time.sleep(0.3)
    print("Bearbeiten – Leiste:", bar())
    shot("bearbeiten")
    click("Ansicht")
    print("Ansicht zeigt Entwurf:", js("document.querySelector('.fv-code').textContent.slice(0, 12)"), "| Datei noch alt:", raw("notiz.txt")[:14])
    print("Speichern:", click("Speichern")); time.sleep(1.5)
    print("Meldung:", js("document.getElementById('messages').textContent"), "| Datei:", raw("notiz.txt")[:18], "| Leiste:", bar())

    open_file("win.txt")
    click("Bearbeiten")
    js("(() => { const a = document.querySelector('.fv-edit'); a.value = a.value + 'dritte\\n'; a.dispatchEvent(new Event('input')); })()"); time.sleep(0.3)
    click("Speichern"); time.sleep(1.5)
    print("Windows-Zeilenenden nach dem Speichern:", raw("win.txt"))

    open_file("lies.md")
    print("Markdown – Leiste:", bar(), "| Überschrift:", js("(document.querySelector('.fv-body .doc h2') || {}).textContent"), "| Tabelle:", js("!!document.querySelector('.fv-body .doc table')"))
    shot("markdown")
    click("Text")
    print("Markdown als Text:", js("document.querySelector('.fv-code').textContent.slice(0, 9)"))

    open_file("bild.png", 3)
    size = "(() => { const i = document.querySelector('.fv-body img'); return i.width + 'x' + i.height + ' ' + document.querySelector('.fv-zoom').textContent; })()"
    print("Bild – Leiste:", bar(), "| Fit:", js(size))
    shot("bild-fit")
    click("100 %"); print("100 %:", js(size), "| Leiste:", bar())
    box = json.loads(js("JSON.stringify(document.querySelector('.fv-body').getBoundingClientRect())"))
    x, y = box["x"] + 400, box["y"] + 300
    before = js("(() => { const b = document.querySelector('.fv-body'); return b.scrollLeft + ',' + b.scrollTop; })()")
    call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
    call("Input.dispatchMouseEvent", type="mouseMoved", x=x - 150, y=y - 100, button="left")
    call("Input.dispatchMouseEvent", type="mouseReleased", x=x - 150, y=y - 100, button="left", clickCount=1)
    time.sleep(0.4)
    print("Verschieben mit der Maus:", before, "→", js("(() => { const b = document.querySelector('.fv-body'); return b.scrollLeft + ',' + b.scrollTop; })()"))
    shot("bild-100")
    click("+"); print("+:", js(size)); click("−"); click("−"); print("− −:", js(size)); click("Fit"); print("Fit:", js(size))

    open_file("klein.svg")
    print("SVG – Leiste:", bar(), "|", js(size))
    open_file("daten.bin")
    print("Binär – Leiste:", bar(), "| erste Zeile:", js("document.querySelector('.fv-hexdump').textContent.split('\\n')[0]"))
    more = js("document.querySelector('.fv-hex > button').textContent")
    js("document.querySelector('.fv-hex > button').click()"); time.sleep(0.5)
    print("Knopf:", more, "| Blöcke danach:", js("document.querySelectorAll('.fv-hexdump').length"))
    shot("hex")
    print("Fehler in der Konsole:", errors()[:6])
finally:
    chrome.terminate()
