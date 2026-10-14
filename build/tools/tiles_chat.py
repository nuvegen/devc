"""Kacheln aus Addons im Browser: Dashboard, Ansicht (view) mit Neuladen, Chat mit Ansehen, Strg+Enter, Freigabe, Abbrechen, Neuladen der Seite, Beenden, Fortsetzen.

Aufruf: DEVC_TOKEN=… python3 build/tools/tiles_chat.py <basis-url> <container> <view-kachel> <chat-kachel> <ordner-für-bilder>
Die Chat-Kachel muss eine Bridge haben, die auf »tool« eine Freigabe anfragt und auf »lang« nicht fertig wird
(Beispiel im Kopf von build/tools/README.md).
"""
import base64, json, os, socket, subprocess, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from devc.terminal import FrameReader, encode_frame, OP_TEXT

BASE, NAME, VIEW, CHAT, SHOTS = sys.argv[1:6]
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
    texts = lambda selector: js(f"JSON.stringify(Array.from(document.querySelectorAll({json.dumps(selector)})).map(n => n.innerText.trim().replace(/\\s+/g, ' ').slice(0, 90)))")
    click = lambda selector, text: js(f"(Array.from(document.querySelectorAll({json.dumps(selector)})).find(n => n.textContent.trim() === {json.dumps(text)}) || {{click() {{ return 'FEHLT'; }}}}).click()")
    def say(text, wait=1.5):
        js(f"document.querySelector('.chat-input').value = {json.dumps(text)}")
        click(".chat-buttons .btn", "Senden"); time.sleep(wait)
    state = lambda: (js("document.querySelector('.chat-head .status').textContent"), js("document.querySelector('.chat-agent').textContent"))
    # Anteil der Fensterhöhe, den der Verlauf bekommt, und ob die Seite selbst scrollt
    room = lambda: js("(() => { const r = document.querySelector('.chat-log').getBoundingClientRect(); return Math.round(100 * r.height / innerHeight) + ' % der Höhe, Seite scrollt: ' + (document.documentElement.scrollHeight > innerHeight + 1); })()")
    press = lambda k: js("document.querySelector('.chat-input').dispatchEvent(new KeyboardEvent('keydown', {key: " + json.dumps(k) + ", bubbles: true, cancelable: true}))")
    def type_(text):
        js(f"(() => {{ const i = document.querySelector('.chat-input'); i.focus(); i.value = {json.dumps(text)}; i.dispatchEvent(new Event('input', {{bubbles: true}})); }})()"); time.sleep(0.3)
    menu = lambda: js("document.querySelector('.chat-cmds').hidden") and "zu" or texts(".chat-cmd")

    os.makedirs(SHOTS, exist_ok=True)
    call("Runtime.enable"); call("Log.enable"); call("Page.enable")
    call("Page.navigate", url=BASE + "/"); time.sleep(1.5)
    print("Login:", js(f"fetch('/api/login', {{method: 'POST', headers: {{'Content-Type': 'application/json', 'X-Devc': '1'}}, body: JSON.stringify({{token: '{TOKEN}'}})}}).then(r => r.status)"))
    call("Page.reload"); time.sleep(1.5)

    go(f"/c/{NAME}")
    print("Dashboard-Kacheln:", texts(".tile .title"))
    print("  Ziele:", js("JSON.stringify(Array.from(document.querySelectorAll('.tile')).slice(8).map(n => (n.getAttribute('href') || 'ohne Ziel') + (n.target ? ' [' + n.target + ']' : '')))"))
    shot("tiles_dashboard")

    go(f"/c/{NAME}/tile/{VIEW}", 2.5)
    first = js("document.querySelector('main > .hint').textContent")
    print("Ansicht:", js("document.querySelector('h1').textContent"), "| Überschriften:", texts(".doc h3"), "| Tabellen:", js("document.querySelectorAll('.doc table').length"))
    shot("tiles_view")
    time.sleep(6.5)
    second = js("document.querySelector('main > .hint').textContent")
    print("  lädt neu:", first != second, "|", second)

    key = lambda ctrl: js("document.querySelector('.chat-input').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: " + ("true" if ctrl else "false") + ", bubbles: true, cancelable: true}))")
    go(f"/c/{NAME}/tile/{CHAT}")
    print("Chat-Start:", texts("main .btn"), "| Auswahl für Freigaben weg:", js("document.querySelector('select') === null"), "| Frühere:", texts("tbody tr"))
    shot("chat_start")
    click("tbody .btn", "Öffnen"); time.sleep(2.0)
    print("Ansehen:", js("location.hash"), "|", texts(".chat-head .status"), "| Platz:", room(), "| Verlauf:", texts(".chat-msg"), "| Knöpfe:", texts(".chat-choice .btn"), "| Eingabefeld:", js("document.querySelector('.chat-input') !== null"))
    print("  läuft dabei nichts:", js("fetch('/api/containers/" + NAME + "/chats').then(r => r.json()).then(d => d.sessions.length)"))
    shot("chat_show")
    click(".chat-choice .btn", "Zur Übersicht"); time.sleep(1.5)
    print("  zurück in der Übersicht:", texts(".actions .btn"))
    click(".actions .btn", "Neue Unterhaltung"); time.sleep(2.5)
    print("Chat läuft, Kopfzeile vor der ersten Nachricht:", state(), "| Platzhalter:", js("document.querySelector('.chat-input').placeholder"), "| Platz:", room())
    shot("chat_run_empty")
    type_("/"); print("  Popup bei »/»:", menu())
    type_("/co"); print("  bei »/co»:", menu(), "| gewählt:", texts(".chat-cmd.on .mono"))
    shot("chat_commands")
    press("ArrowDown"); print("  Pfeil runter:", texts(".chat-cmd.on .mono"))
    press("Enter"); time.sleep(0.3)
    print("  Enter übernimmt:", repr(js("document.querySelector('.chat-input').value")), "| Hilfe bleibt:", menu(), "| kein HTML:", js("document.querySelector('.chat-cmds b') === null"))
    type_("/zzz"); print("  kein Treffer:", menu())
    type_("/m"); press("Escape"); print("  Esc schließt:", menu())
    type_("/config"); key(True); time.sleep(1.5)
    print("  Befehl gesendet:", texts(".chat-msg"), "| Zeilenumbruch:", js("document.querySelectorAll('.chat-msg.doc p br').length"), "| Tabelle:", js("document.querySelectorAll('.chat-msg.doc table').length"), "| Popup:", menu())
    shot("chat_command_output")
    size = lambda: js("JSON.stringify([document.querySelector('.chat-zoom-value').textContent, Math.round(document.querySelector('.chat-msg.user').getBoundingClientRect().height), document.querySelector('.chat-log').style.zoom, document.querySelector('.chat-input').style.zoom])")
    print("  Textgröße:", size()); click(".chat-zoom .btn", "A+"); click(".chat-zoom .btn", "A+"); time.sleep(0.3)
    print("  nach 2× A+:", size(), "| Seite scrollt:", js("document.documentElement.scrollHeight > innerHeight + 1"), "| gemerkt:", js("localStorage.getItem('devc.chat.zoom')"))
    shot("chat_zoom")
    call("Page.reload"); time.sleep(3.0)
    print("  nach Neuladen:", size()); js("document.querySelector('.chat-zoom-value').click()"); time.sleep(0.3); print("  zurückgesetzt:", size())
    knob = lambda: json.loads(texts(".chat-actions > .btn"))[0]
    print("  Einstellungen, Knopf:", knob()); js("document.querySelector('.chat-actions > .btn').click()"); time.sleep(0.4)
    print("  Fenster:", texts("dialog.chat-settings legend"), texts("dialog.chat-settings label strong"), "| gewählt:", js("document.querySelector('dialog.chat-settings input:checked').parentNode.querySelector('strong').textContent"),
          "| kein HTML:", js("document.querySelector('dialog.chat-settings b') === null"))
    shot("chat_settings")
    js("document.querySelectorAll('dialog.chat-settings input')[1].click()"); time.sleep(0.8)
    print("  nach der Wahl:", knob(), "| gewählt:", js("document.querySelector('dialog.chat-settings input:checked').parentNode.querySelector('strong').textContent"))
    click("dialog.chat-settings .btn", "Schließen"); time.sleep(0.3)
    print("  Fenster zu:", js("document.querySelector('dialog.chat-settings') === null"))
    call("Page.reload"); time.sleep(3.0)
    print("  nach Neuladen:", knob())
    js("document.querySelector('.chat-input').value = 'Zeile eins'"); key(False); time.sleep(0.8)
    print("  Enter sendet nicht:", js("document.querySelectorAll('.chat-msg.user').length") == 1, "| Feld behält den Text:", js("document.querySelector('.chat-input').value"))
    js("document.querySelector('.chat-input').value = 'Hallo <b>Welt</b>\\nzweite Zeile'"); key(True); time.sleep(2.0)
    print("  Strg+Enter sendet:", texts(".chat-msg"), "| Hinweise:", texts(".chat-note"))
    print("  kein HTML eingesetzt:", js("document.querySelector('.chat-log b') === null"), "| Markdown:", js("document.querySelectorAll('.chat-msg.doc table, .chat-msg.doc ul, .chat-msg.doc pre, .chat-msg.doc strong').length"))
    say("datei bitte", 1.5)
    print("  Freigabe mit Ordner:", texts(".chat-ask .btn"), "|", texts(".chat-ask .hint"), "| kein HTML:", js("document.querySelector('.chat-ask b') === null"))
    shot("chat_folder")
    click(".chat-ask .btn", "Im Ordner erlauben"); time.sleep(2.0)
    print("  nach »Im Ordner erlauben«:", texts(".chat-ask .chat-mark"), "| Bridge bekam folder:", "Ordner freigegeben" in texts(".chat-msg"))
    say("tool bitte", 1.5)
    print("  Freigabe:", texts(".chat-ask p"), texts(".chat-ask .btn"), "| Zustand:", state()[0])
    click(".chat-ask .btn", "Erlauben"); time.sleep(2.0)
    print("  nach Erlauben:", texts(".chat-ask .chat-mark"), "| Werkzeug:", texts(".chat-tool summary"), "| Zustand:", state()[0])
    count = js("document.querySelector('.chat-log').childElementCount")
    call("Page.reload"); time.sleep(3.0)
    print("  nach Neuladen gleicher Verlauf:", count == js("document.querySelector('.chat-log').childElementCount"), count)
    say("lang", 1.0)
    go(f"/c/{NAME}", 1.0); go(f"/c/{NAME}/tile/{CHAT}", 2.5)
    print("  nach Seitenwechsel Teiltext:", texts(".chat-msg.live"), "|", state()[0])
    print("  Tätigkeit:", texts(".chat-activity"), "| kein HTML:", js("document.querySelector('.chat-activity b') === null"))
    time.sleep(2.2)
    print("  Uhr läuft (zählt über den Seitenwechsel weiter):", texts(".chat-clock"))
    shot("chat_activity")
    click(".chat-buttons .btn", "Abbrechen"); time.sleep(1.0)
    print("  nach Abbrechen:", state()[0], "|", texts(".chat-note")[-20:], "| Tätigkeit versteckt:", js("document.querySelector('.chat-activity').hidden"))
    call("Emulation.setDeviceMetricsOverride", width=360, height=740, deviceScaleFactor=1, mobile=True); time.sleep(0.8)
    shot("chat_narrow")
    print("  360 px: scrollWidth =", js("document.documentElement.scrollWidth"), "| Platz:", room(), "| kleinstes Ziel:", js("Math.min(...Array.from(document.querySelectorAll('main .btn')).filter(n => !n.hidden && n.offsetParent).map(n => Math.round(n.getBoundingClientRect().height)))"))
    call("Emulation.clearDeviceMetricsOverride")
    click(".chat-actions .btn", "Beenden"); time.sleep(0.6)
    print("  gleich nach Beenden:", state()[0], "| Eingabe gesperrt:", js("document.querySelector('.chat-input').disabled"))
    time.sleep(3.5)
    print("  danach:", state()[0], "|", texts(".chat-end"))
    shot("chat_end")
    click(".chat-end .btn", "Fortsetzen"); time.sleep(3.0)
    print("  Fortsetzen:", state(), "| Verlauf:", texts(".chat-msg"), "|", js("location.hash"))
    click(".chat-actions .btn", "Beenden"); time.sleep(4.5)
    click(".chat-end .btn", "Zur Übersicht"); time.sleep(2.0)
    print("  Zur Übersicht:", texts(".actions .btn"), texts("tbody tr"))
    errors = [e for e in events if e.get("method") == "Runtime.exceptionThrown" or (e.get("method") == "Runtime.consoleAPICalled" and e["params"]["type"] == "error") or (e.get("method") == "Log.entryAdded" and e["params"]["entry"]["level"] == "error")]
    print("JS-Fehler:", [(e["params"].get("exceptionDetails", {}).get("exception", {}).get("description") or e["params"].get("entry", {}).get("text") or str(e["params"].get("args", ""))[:200])[:300] for e in errors])
finally:
    chrome.terminate()
