import json, sys, time
def emit(kind, **f):
    sys.stdout.write(json.dumps({"type": kind, **f}, ensure_ascii=False) + "\n"); sys.stdout.flush()
if "--sessions" in sys.argv:
    print(json.dumps({"sessions": [{"id": "alt-1", "title": "Eine frühere Unterhaltung", "updated": time.time() - 3600}]})); sys.exit(0)
def old():
    emit("user", text="Alte Frage"); emit("text", text="Alte Antwort"); emit("turn_end", ok=True)
if "--stats" in sys.argv:
    print(json.dumps({"stats": "## Zeit\n\n| Was | Dauer | Anteil |\n|---|---|---|\n| Gesamt | 1:02:03 | |\n| Modell | 0:40 | 57 % der Arbeitszeit |\n\n## Tokens\n\n| Was | Zahl |\n|---|---|\n| Eingabe gesamt | 4.510 |\n\nHinweis <b>kein html</b>.\n"})); sys.exit(0)
if "--show" in sys.argv:
    old(); sys.exit(0)
start = json.loads(sys.stdin.readline())
if start.get("resume"):
    old()
emit("ready", agent="Echo", version="1.0", model="kein Modell", session=start.get("resume") or "neu-1",
     commands=[{"name": "clear", "hint": "", "description": "Verlauf leeren"}, {"name": "config", "hint": "key=value", "description": "Eine Einstellung setzen <b>kein html</b>"},
               {"name": "context", "hint": "", "description": "Zeigt, wie voll der Kontext ist. " + "Lang " * 40}, {"name": "model", "hint": "<name>", "description": "Modell wählen"}])
level = "ask"
def settings():
    return [{"name": "permissions", "label": "Freigaben", "value": level, "hint": "Gilt sofort <b>kein html</b>.",
             "options": [{"value": "ask", "label": "Nachfragen", "hint": "Vor jeder Änderung fragen."},
                         {"value": "files", "label": "Dateien im Projekt frei", "hint": "Dateien ohne Rückfrage ändern."},
                         {"value": "all", "label": "Alles erlauben", "hint": "Nicht mehr fragen <b>kein html</b>."}]}]
emit("ready", settings=settings())
emit("status", state="idle")
pending = None
def answer(text):
    for word in ("Du ", "hast ", "geschrieben: "):
        emit("text_delta", text=word); time.sleep(0.15)
    emit("text", text="Du hast geschrieben: **" + text + "**\n\n- Punkt eins\n- Punkt `zwei`\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```\ncode <b>kein html</b>\n```")
    emit("turn_end", ok=True, duration_ms=450, cost_usd=0.0); emit("status", state="idle")
for line in sys.stdin:
    c = json.loads(line)
    if c["type"] == "send":
        emit("user", text=c["text"]); emit("status", state="working")
        if "datei" in c["text"]:
            emit("permission", id="p2", tool="Edit", summary="/work/src/a.py", input="{}", folder="/work/src <b>kein html</b>")
            emit("status", state="waiting"); pending = c["text"]
        elif "tool" in c["text"]:
            emit("tool_start", id="t1", name="Bash", summary="ls -la /work", input='{\n  "command": "ls -la /work"\n}')
            emit("permission", id="p1", tool="Bash", summary="ls -la /work", input='{\n  "command": "ls -la /work"\n}')
            emit("status", state="waiting"); pending = c["text"]
        elif c["text"].startswith("/"):
            emit("text", text="Zeile eins  \nZeile zwei\nfließt weiter\n\n| Einstellung | Werte |\n|---|---|\n| `model` | `a`, `b` |")
            emit("turn_end", ok=True, duration_ms=1, cost_usd=0.0); emit("status", state="idle")
        elif "lang" in c["text"]:
            emit("status", state="working", text="Bash: sleep 600 <b>kein html</b>")
            emit("text_delta", text="Ich denke lange nach …")
        else:
            answer(c["text"])
    elif c["type"] == "permit":
        emit("permission_done", id=c["id"], allow=c["allow"]); emit("status", state="working")
        if c.get("folder"):
            emit("text", text="Ordner freigegeben")
        emit("tool_end", id="t1", ok=c["allow"], output="datei1\ndatei2" if c["allow"] else "Vom Nutzer abgelehnt")
        answer(pending or "")
    elif c["type"] == "set":
        level = c["value"]; emit("ready", settings=settings())
        if level == "all" and pending is not None:
            emit("permission_done", id="p1", allow=True); emit("permission_done", id="p2", allow=True); emit("status", state="working")
            emit("tool_end", id="t1", ok=True, output="datei1"); answer(pending); pending = None
    elif c["type"] == "end":
        emit("status", state="ending"); time.sleep(1.5); break
    elif c["type"] == "interrupt":
        emit("turn_end", ok=False, text="Abgebrochen."); emit("status", state="idle")
