"""Anmeldeweg eines Container-Ports im Browser (SPEC 8.11): Dienst aufrufen, bei devc anmelden, wieder beim Dienst landen.

Aufruf: proxy_flow.py <adresse-des-dienstes> <token> [<bild.png>]
"""
import base64, json, os, socket, subprocess, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from devc.terminal import FrameReader, encode_frame, OP_TEXT

URL, TOKEN, SHOT = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "")
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
    counter = [0]
    def call(method, **params):
        counter[0] += 1
        sock.sendall(encode_frame(OP_TEXT, json.dumps({"id": counter[0], "method": method, "params": params}).encode(), os.urandom(4)))
        while True:
            _, payload = frames.read_message()
            message = json.loads(payload)
            if message.get("id") == counter[0]:
                return message.get("result", message)
    def js(expression):
        return call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True).get("result", {}).get("value")
    def where(label):
        print(f"{label}: {js('location.href')} | Titel: {js('document.title')}")
    call("Runtime.enable"); call("Page.enable")
    call("Page.navigate", url=URL); time.sleep(2.5)
    where("1. Dienst ohne Anmeldung")
    print("   Anmeldeformular:", js("document.querySelector('form.login') ? 'da' : 'FEHLT'"))
    js("document.getElementById('token').focus()")
    call("Input.insertText", text=TOKEN)
    js("document.querySelector('form.login button[type=submit]').click()"); time.sleep(3)
    where("2. nach der Anmeldung")
    ok = js("location.href") .split("?")[0].rstrip("/") == URL.split("?")[0].rstrip("/")
    print("   Cookies, die das Skript der Seite sieht:", repr(js("document.cookie")))
    print("   Text:", (js("document.body.innerText") or "")[:80].replace("\n", " "))
    call("Page.navigate", url=URL); time.sleep(2)
    where("3. erneuter Aufruf")
    if SHOT:
        open(SHOT, "wb").write(base64.b64decode(call("Page.captureScreenshot", format="png")["data"]))
    print("Ergebnis:", "OK" if ok else "FEHLER: nicht beim Dienst gelandet")
finally:
    chrome.terminate()
