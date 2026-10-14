"""GUI-Rauchtest: lädt jede Route in Chromium (headless, DevTools-Protokoll) und sammelt JS-Fehler."""
import base64, json, os, socket, subprocess, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from devc.terminal import FrameReader, encode_frame, OP_TEXT

BASE, TOKEN, ROUTES, SHOTS = sys.argv[1], sys.argv[2], sys.argv[3].split(","), sys.argv[4]
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
    path = "/" + ws_url.split("/", 3)[3]
    sock = socket.create_connection(("127.0.0.1", 9333))
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:9333\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
    stream = sock.makefile("rb")
    while stream.readline().strip():
        pass
    frames = FrameReader(stream.read, None, require_mask=False)
    counter = [0]
    events = []
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
        result = call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        return result.get("result", {}).get("value")
    call("Runtime.enable"); call("Log.enable"); call("Page.enable")
    call("Page.navigate", url=BASE + "/")
    time.sleep(1.5)
    print("Login-Seite:", js("document.title"), "|", js("document.querySelector('form.login') ? 'Formular da' : 'KEIN Formular'"))
    print("Login:", js(f"fetch('/api/login', {{method: 'POST', headers: {{'Content-Type': 'application/json', 'X-Devc': '1'}}, body: JSON.stringify({{token: '{TOKEN}'}})}}).then(r => r.status)"))
    call("Page.reload"); time.sleep(1.5)  # die GUI merkt die Anmeldung erst beim Neuladen
    problems = 0
    for route in ROUTES:
        del events[:]
        js("location.hash = " + json.dumps("#" + route))
        time.sleep(2.0 if "terminal" not in route else 3.5)
        title = js("document.title")
        h1 = js("(document.querySelector('h1') || {}).textContent")
        text = js("document.getElementById('main').innerText.length")
        alerts = js("Array.from(document.querySelectorAll('.msg.error')).map(n => n.textContent).join(' | ')")
        wide = js("document.documentElement.scrollWidth > window.innerWidth")
        errors = [e for e in events if e.get("method") in ("Runtime.exceptionThrown",) or (e.get("method") == "Runtime.consoleAPICalled" and e["params"]["type"] == "error") or (e.get("method") == "Log.entryAdded" and e["params"]["entry"]["level"] == "error")]
        details = "; ".join((e["params"].get("exceptionDetails", {}).get("exception", {}).get("description") or e["params"].get("entry", {}).get("text") or str(e["params"].get("args", ""))[:200])[:300] for e in errors)
        flag = "OK " if not errors and not alerts and h1 not in ("Fehler", "Seite nicht gefunden", None) else "!! "
        problems += flag != "OK "
        print(f"{flag}{route:34} h1={h1!r:28} text={text:5} breit={wide} {('FEHLERMELDUNG: ' + alerts) if alerts else ''} {details}")
        if SHOTS:
            data = call("Page.captureScreenshot", format="png")["data"]
            open(os.path.join(SHOTS, "shot" + route.replace("/", "_").replace("?", "_").replace("=", "_") + ".png"), "wb").write(base64.b64decode(data))
    # schmale Ansicht (360 px): kein horizontales Scrollen der Seite
    call("Emulation.setDeviceMetricsOverride", width=360, height=740, deviceScaleFactor=1, mobile=True)
    for route in ("/", "/c/it-py", "/c/it-py/addons", "/c/it-py/config", "/secrets"):
        js("location.hash = " + json.dumps("#" + route)); time.sleep(1.8)
        print(f"360px {route:20} scrollWidth={js('document.documentElement.scrollWidth')} kleinstes Ziel={js('Math.min(...Array.from(document.querySelectorAll(\"main .btn, .nav a\")).map(n => Math.round(n.getBoundingClientRect().height)))')}")
    print("Probleme:", problems)
finally:
    chrome.terminate()
