"""Addon codex: die Bridge zwischen dem Chat und `codex app-server` (SPEC 8.13), dazu das Startskript."""

import json
import os
import stat
import subprocess
import sys

from devc import addons
from tests import helpers

SESSION = "cccccccc-1111-2222-3333-444444444444"

# Spielt `codex app-server`: JSON-RPC über stdin und stdout, feste Antworten je Nachricht.
FAKE_CODEX = r'''#!/usr/bin/env python3
import json, sys
SESSION = "cccccccc-1111-2222-3333-444444444444"
TITLE = "eeeeeeee-0000-0000-0000-000000000000"
log = open(__file__ + ".log", "a")
def out(**message):
    sys.stdout.write(json.dumps(message) + "\n"); sys.stdout.flush()
def note(method, thread, **params):
    out(method=method, params={"threadId": thread, **params})
def say(thread, text, n):
    note("item/started", thread, item={"type": "agentMessage", "id": f"m{n}", "text": ""})
    note("item/agentMessage/delta", thread, itemId=f"m{n}", delta=text[:3])
    note("item/completed", thread, item={"type": "agentMessage", "id": f"m{n}", "text": text})
def done(thread, status="completed", error=None, n=0):
    note("thread/tokenUsage/updated", thread, tokenUsage={"total": {"inputTokens": 1000 * n, "cachedInputTokens": 400, "outputTokens": 50, "reasoningOutputTokens": 7},
                                                         "last": {"inputTokens": 900}, "modelContextWindow": 128000})
    note("turn/completed", thread, turn={"id": f"turn{n}", "status": status, "error": error, "durationMs": 1500})
waiting, turns = {}, 0
if sys.argv[1:] != ["app-server"]:
    sys.exit(9)
for line in sys.stdin:
    m = json.loads(line)
    log.write(json.dumps(m) + "\n"); log.flush()
    method, params = m.get("method"), m.get("params") or {}
    if method == "initialize":
        out(id=m["id"], result={"userAgent": "devc/9.8.7 (Ubuntu 24.4.0; x86_64)"})
    elif method in ("thread/start", "thread/resume"):
        if params.get("threadId") == "dddddddd-0000-0000-0000-000000000000":
            out(id=m["id"], error={"code": -32600, "message": "thread not found"})
            continue
        thread = TITLE if params.get("ephemeral") else params.get("threadId") or SESSION
        out(id=m["id"], result={"thread": {"id": thread}, "model": "test/modell"})
        note("warning", thread, message="Model metadata not found")
    elif method == "turn/start":
        turns += 1; n = turns
        thread, text = params["threadId"], params["input"][0]["text"]
        if text == "kaputt":
            out(id=m["id"], error={"code": -32600, "message": "turn geht nicht"})
            continue
        out(id=m["id"], result={"turn": {"id": f"turn{n}", "status": "inProgress"}})
        note("turn/started", thread, turn={"id": f"turn{n}"})
        if thread == TITLE:
            say(thread, "## „Ein  schöner Titel“\n\nzweite Zeile", n); done(thread, n=n)
        elif text.startswith("befehl") or text.startswith("patch"):
            patch = text.startswith("patch")
            item = {"type": "fileChange", "id": f"c{n}", "changes": [{"path": "/work/a.txt", "kind": "add"}], "status": "inProgress"} if patch else \
                   {"type": "commandExecution", "id": f"c{n}", "command": "/bin/bash -lc 'make  test'", "cwd": "/work", "commandActions": [{"type": "unknown", "command": "make  test"}], "status": "inProgress"}
            note("item/started", thread, item={"type": "reasoning", "id": f"r{n}"})
            note("item/started", thread, item=item)
            waiting[100 + n] = (thread, item, n)
            out(id=100 + n, method="item/fileChange/requestApproval" if patch else "item/commandExecution/requestApproval",
                params={"threadId": thread, "turnId": f"turn{n}", "itemId": f"c{n}", "command": item.get("command"), "cwd": "/work", "reason": "ändert etwas"})
        elif text == "frage":
            waiting[100 + n] = (thread, None, n)
            out(id=100 + n, method="item/tool/requestUserInput", params={"threadId": thread, "questions": []})
        elif text == "rechte":
            waiting[100 + n] = (thread, None, n)
            out(id=100 + n, method="item/permissions/requestApproval", params={"threadId": thread, "permissions": {}})
        elif text == "lang":
            note("item/started", thread, item={"type": "commandExecution", "id": f"c{n}", "command": "sleep 600", "status": "inProgress"})
        elif text == "fehler":
            note("error", thread, error={"message": "stream stockt"}, willRetry=True)
            note("error", thread, error={"message": "unexpected status 401 Unauthorized: invalid api key"}, willRetry=False)
            done(thread, "failed", None, n)
        elif text == "stirb":
            sys.stderr.write("oben\n\x1b[31mERROR\x1b[0m Codex ist abgestürzt\n"); sys.exit(3)
        else:
            say(thread, "Hallo " + text, n); done(thread, n=n)
    elif method == "turn/interrupt":
        out(id=m["id"], result={})
        note("item/completed", params["threadId"], item={"type": "commandExecution", "id": f"c{turns}", "command": "sleep 600", "status": "failed", "exitCode": 130})
        done(params["threadId"], "interrupted", None, turns)
    elif "id" in m and m["id"] in waiting:
        thread, item, n = waiting.pop(m["id"])
        if item is None:
            say(thread, "Antwort: " + json.dumps(m.get("result") or m.get("error"), ensure_ascii=False), n); done(thread, n=n)
            continue
        allowed = (m.get("result") or {}).get("decision") == "accept"
        if allowed and item["type"] == "commandExecution":
            note("item/commandExecution/outputDelta", thread, itemId=item["id"], delta="zeile 1\n")
            note("item/commandExecution/outputDelta", thread, itemId=item["id"], delta="zeile 2\n")
        note("item/completed", thread, item={**item, "status": "completed" if allowed else "declined", "exitCode": (2 if "zwei" in str(n) else 0) if allowed and item["type"] == "commandExecution" else None})
        say(thread, "Erledigt", n); done(thread, n=n)
'''


class CodexBridgeTest(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "bin" / "codex"
        self.fake.parent.mkdir()
        self.fake.write_text(FAKE_CODEX.replace("#!/usr/bin/env python3", "#!" + sys.executable))
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.env = {**os.environ, "PATH": f"{self.fake.parent}:{os.environ['PATH']}", "XDG_STATE_HOME": str(self.tmp / "state")}
        self.bridge = str(helpers.EXAMPLES / "addons" / "codex" / "bridge.py")
        self.state = self.tmp / "state" / "devc-chat" / "codex"

    def run_bridge(self, start, steps, args=("--model", "test/modell"), skip=0):
        """Schickt nach jedem `turn_end` oder `permission` den nächsten Schritt; liefert alle Ereignisse.
        `skip`: so viele `turn_end` stammen aus dem alten Verlauf und lösen nichts aus."""
        proc = subprocess.Popen([sys.executable, self.bridge, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=self.env, cwd=self.tmp)
        self.addCleanup(proc.kill)
        steps = [start, *steps]
        found = []

        def step():
            if steps:
                item = steps.pop(0)  # eine Liste: mehrere Befehle auf einmal
                proc.stdin.write("".join(json.dumps(command) + "\n" for command in (item if isinstance(item, list) else [item])))
                proc.stdin.flush()
            else:
                proc.stdin.close()

        step()
        step()
        for line in proc.stdout:
            found.append(json.loads(line))
            if found[-1]["type"] in ("turn_end", "permission"):
                if skip:
                    skip -= 1
                else:
                    step()
        self.assertEqual(proc.stderr.read(), "")
        code = proc.wait(10)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()
        return found, code

    def sent(self) -> list[dict]:
        return [json.loads(line) for line in (self.tmp / "bin" / "codex.log").read_text().splitlines()]

    def test_turn_is_translated_and_recorded(self):
        events, code = self.run_bridge({"type": "start", "resume": None}, [{"type": "send", "text": "Welt"}])
        self.assertEqual(code, 0)
        ready = [e for e in events if e["type"] == "ready"]
        self.assertEqual((ready[0]["agent"], ready[0]["session"], ready[0]["model"], ready[0]["settings"][0]["value"]), ("Codex", None, "test/modell", "ask"))
        self.assertEqual([o["value"] for o in ready[0]["settings"][0]["options"]], ["ask", "all"])
        self.assertEqual(ready[1:], [{"type": "ready", "agent": "Codex", "version": "9.8.7"}, {"type": "ready", "agent": "Codex", "session": SESSION, "model": "test/modell"}])
        rest = [e for e in events if e["type"] not in ("ready", "status")]
        self.assertEqual(rest, [{"type": "user", "text": "Welt"}, {"type": "text_delta", "text": "Hal"}, {"type": "text", "text": "Hallo Welt"},
                                {"type": "turn_end", "ok": True, "duration_ms": 1500}])
        self.assertEqual([(e["state"], e.get("text")) for e in events if e["type"] == "status"],
                         [("idle", None), ("working", None), ("working", "wartet auf das Modell"), ("working", "schreibt"), ("idle", None)])
        stored = [json.loads(line)["type"] for line in (self.state / f"{SESSION}.jsonl").read_text().splitlines()]
        self.assertEqual(stored, ["user", "text", "turn_end"])
        sent = self.sent()
        self.assertEqual([m.get("method") for m in sent], ["initialize", "initialized", "thread/start", "turn/start"])
        # Der Container ist die Grenze: keine Sandbox von Codex, dafür jede Änderung als Freigabe.
        self.assertEqual(sent[2]["params"], {"cwd": str(self.tmp.resolve()), "approvalPolicy": "untrusted", "sandbox": "danger-full-access"})
        self.assertEqual(sent[3]["params"], {"threadId": SESSION, "input": [{"type": "text", "text": "Welt", "text_elements": []}]})

    def test_command_with_permission_allow_deny_and_always(self):
        events, _ = self.run_bridge({"type": "start"}, [
            {"type": "send", "text": "befehl"}, {"type": "permit", "id": "a101", "allow": False},
            {"type": "send", "text": "befehl"}, {"type": "permit", "id": "a102", "allow": True, "always": True},
            {"type": "send", "text": "befehl"},                                                           # Shell ist freigegeben
            {"type": "send", "text": "patch"}, {"type": "permit", "id": "a104", "allow": True}])          # eine andere Art fragt wieder
        asked = [e for e in events if e["type"] == "permission"]
        self.assertEqual([(e["id"], e["tool"], e["summary"]) for e in asked], [("a101", "Shell", "make test"), ("a102", "Shell", "make test"), ("a104", "Patch", "/work/a.txt")])
        self.assertEqual(json.loads(asked[0]["input"]), {"command": "/bin/bash -lc 'make  test'", "cwd": "/work", "reason": "ändert etwas"})
        self.assertEqual([(e["id"], e["allow"]) for e in events if e["type"] == "permission_done"], [("a101", False), ("a102", True), ("a104", True)])
        starts = [(e["id"], e["name"], e["summary"]) for e in events if e["type"] == "tool_start"]
        self.assertEqual(starts, [("c1", "Shell", "make test"), ("c2", "Shell", "make test"), ("c3", "Shell", "make test"), ("c4", "Patch", "/work/a.txt")])
        ends = [(e["ok"], e["output"]) for e in events if e["type"] == "tool_end"]
        self.assertEqual(ends[:3], [(False, "Vom Nutzer abgelehnt"), (True, "zeile 1\nzeile 2\n"), (True, "zeile 1\nzeile 2\n")])
        self.assertTrue(ends[3][0])
        states = [(e["state"], e.get("text")) for e in events if e["type"] == "status"]
        self.assertIn(("working", "denkt nach"), states)
        self.assertIn(("working", "Shell: make test"), states)
        self.assertIn(("waiting", None), states)
        self.assertIn(("working", "wertet das Ergebnis aus"), states)
        answers = [m["result"]["decision"] for m in self.sent() if "result" in m]
        self.assertEqual(answers, ["decline", "accept", "accept", "accept"])

    def test_setting_allow_everything_is_kept(self):
        events, _ = self.run_bridge({"type": "start"}, [
            [{"type": "set", "name": "permissions", "value": "kaputt"}, {"type": "send", "text": "befehl"}],
            {"type": "set", "name": "permissions", "value": "all"},   # die offene Frage ist damit beantwortet
            {"type": "send", "text": "patch"}])
        self.assertEqual([e["id"] for e in events if e["type"] == "permission"], ["a101"])
        self.assertEqual([(e["id"], e["allow"]) for e in events if e["type"] == "permission_done"], [("a101", True)])
        self.assertEqual([e["ok"] for e in events if e["type"] == "tool_end"], [True, True])
        self.assertEqual([e["settings"][0]["value"] for e in events if e["type"] == "ready" and "settings" in e], ["ask", "all"])
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "befehl"}])
        self.assertEqual(next(e for e in events if e["type"] == "ready")["settings"][0]["value"], "all")
        self.assertEqual([e for e in events if e["type"] == "permission"], [])

    def test_other_requests_of_codex(self):
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "frage"}, {"type": "send", "text": "rechte"}])
        texts = [e["text"] for e in events if e["type"] == "text"]
        self.assertIn("Stelle sie als normalen Text", texts[0])   # Rückfragen als Auswahl gibt es nicht
        self.assertEqual(texts[1], 'Antwort: {"permissions": {}, "scope": "turn"}')  # keine zusätzlichen Rechte
        self.assertEqual([e for e in events if e["type"] == "permission"], [])

    def test_messages_wait_for_the_running_turn_and_interrupt(self):
        events, _ = self.run_bridge({"type": "start"}, [
            [{"type": "send", "text": "eins"}, {"type": "send", "text": "zwei"}], {"type": "set", "name": "x", "value": "y"},
            [{"type": "send", "text": "lang"}, {"type": "send", "text": "später"}, {"type": "interrupt"}]])
        kinds = [(e["type"], e.get("text")) for e in events if e["type"] in ("user", "text", "turn_end")]
        self.assertEqual(kinds[:2], [("user", "eins"), ("user", "zwei")])  # die zweite Nachricht wartet, bis die erste Antwort fertig ist
        self.assertEqual([text for kind, text in kinds if kind == "text"], ["Hallo eins", "Hallo zwei"])
        self.assertEqual(events[-2], {"type": "turn_end", "ok": False, "duration_ms": 1500, "text": "Abgebrochen."})
        self.assertEqual(events[-1], {"type": "status", "state": "idle"})
        sent = self.sent()
        # Der Abbruch kam, bevor Codex die Kennung der Antwort genannt hatte: Er folgt, sobald sie da ist.
        self.assertEqual(next(m["params"] for m in sent if m.get("method") == "turn/interrupt"), {"threadId": SESSION, "turnId": "turn3"})
        # Die wartende Nachricht verfällt mit dem Abbruch.
        self.assertEqual([m["params"]["input"][0]["text"] for m in sent if m.get("method") == "turn/start"], ["eins", "zwei", "lang"])

    def test_errors(self):
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "fehler"}, {"type": "send", "text": "kaputt"}])
        ends = [e for e in events if e["type"] == "turn_end"]
        self.assertEqual(ends[0]["ok"], False)
        self.assertIn("codex.api_key", ends[0]["text"])
        self.assertIn("401 Unauthorized", ends[0]["text"])
        self.assertEqual(ends[1], {"type": "turn_end", "ok": False, "text": "turn geht nicht"})
        self.assertIn(("retry", "Der Dienst antwortet nicht, neuer Versuch …"), [(e["state"], e.get("text")) for e in events if e["type"] == "status"])
        events, code = self.run_bridge({"type": "start"}, [{"type": "send", "text": "stirb"}])
        self.assertEqual((code, events[-1]), (3, {"type": "error", "text": "oben\nERROR Codex ist abgestürzt"}))
        events, _ = self.run_bridge({"type": "start", "resume": "dddddddd-0000-0000-0000-000000000000"}, [{"type": "send", "text": "x"}])
        self.assertIn("konnte die Unterhaltung nicht öffnen: thread not found", events[-1]["text"])
        self.fake.unlink()
        self.env["PATH"] = "/usr/bin:/bin"
        events, code = self.run_bridge({"type": "start"}, [])
        self.assertEqual(code, 1)
        self.assertIn("codex wurde im Container nicht gefunden", events[0]["text"])

    def test_resume_sessions_title_and_stats(self):
        run = lambda *args: subprocess.run([sys.executable, self.bridge, *args], capture_output=True, text=True, env=self.env, timeout=20).stdout
        self.assertEqual(json.loads(run("--sessions")), {"sessions": []})
        events, code = self.run_bridge({"type": "start"}, [{"type": "send", "text": "Erste   Frage"}, {"type": "send", "text": "befehl"},
                                                           {"type": "permit", "id": "a102", "allow": True}, {"type": "end"}])
        self.assertEqual((code, events[-1]), (0, {"type": "status", "state": "ending"}))
        # Der Titel entsteht in einer eigenen Unterhaltung, die Codex nicht speichert und die nichts ausführen darf.
        sent = self.sent()
        title = next(m["params"] for m in sent if m.get("method") == "thread/start" and m["params"].get("ephemeral"))
        self.assertEqual((title["approvalPolicy"], title["sandbox"]), ("never", "read-only"))
        asked = [m["params"]["input"][0]["text"] for m in sent if m.get("method") == "turn/start"][-1]
        self.assertIn("höchstens 10 Wörtern", asked)
        self.assertIn("Nutzer: Erste   Frage", asked)
        self.assertIn("Agent: Hallo Erste   Frage", asked)
        listed = json.loads(run("--sessions"))["sessions"]
        self.assertEqual([(s["id"], s["title"]) for s in listed], [(SESSION, "Ein schöner Titel")])
        self.assertNotIn("Titel", (self.state / f"{SESSION}.jsonl").read_text())

        stats = json.loads(run("--stats", SESSION))["stats"]
        for line in ("| Arbeitszeit von Codex, alle Antworten | 0:03 |", "| Antworten | 2 |", "| Eingabe gesamt | 2.000 |", "| davon aus dem Cache gelesen | 400 |",
                     "| Ausgabe | 50 |", "| Kontext beim letzten Aufruf | 900 |", "| Größe des Kontextfensters | 128.000 |", "| Modell | `test/modell` |", "| Shell | 1 | 0:00 |"):
            self.assertIn(line + "\n", stats)
        self.assertIn("noch keine Zahlen", json.loads(run("--stats", "aaaaaaaa-0000"))["stats"])
        self.assertEqual(json.loads(run("--stats", "../../etc"))["stats"], "")

        shown = run("--show", SESSION)
        self.assertEqual([json.loads(line)["type"] for line in shown.splitlines()], ["user", "text", "turn_end", "user", "tool_start", "tool_end", "text", "turn_end"])
        self.assertEqual(run("--show", "../x"), "")

        (self.tmp / "bin" / "codex.log").unlink()
        events, _ = self.run_bridge({"type": "start", "resume": SESSION}, [{"type": "send", "text": "Zweite"}], skip=2)
        self.assertEqual([e["text"] for e in events if e["type"] == "user"], ["Erste   Frage", "befehl", "Zweite"])  # der alte Verlauf kommt zuerst
        resumed = next(m["params"] for m in self.sent() if m.get("method") == "thread/resume")
        self.assertEqual((resumed["threadId"], resumed["approvalPolicy"]), (SESSION, "untrusted"))
        self.assertIn("| Antworten | 3 |\n", json.loads(run("--stats", SESSION))["stats"])
        # Ohne `end` (devc endet) und ohne neue Nachricht entsteht kein neuer Titel.
        self.assertEqual([m for m in self.sent() if (m.get("params") or {}).get("ephemeral")], [])
        (self.tmp / "bin" / "codex.log").unlink()
        self.run_bridge({"type": "start", "resume": "../../etc"}, [{"type": "send", "text": "x"}])
        self.assertEqual([m.get("method") for m in self.sent()], ["initialize", "initialized", "thread/start", "turn/start"])  # ungültiges resume: neue Unterhaltung


class ClaudeOtherServiceTest(helpers.TempEnvTestCase):
    """Addon claude-code mit einem anderen Dienst: Parameter und die Prüfung im Installationsskript."""

    def test_params(self):
        addon, findings = addons.load(helpers.EXAMPLES / "addons" / "claude-code")
        self.assertEqual(findings, [])
        params = {p.name: p for p in addon.params}
        self.assertEqual((params["base_url"].default, params["model"].default), ("", ""))
        for name, good, bad in (("base_url", ["", "https://ark.ap-southeast.bytepluses.com/api/coding"], ["ark.example", "https://x/ y", "https://x/$(id)"]),
                                ("model", ["", "glm-5.2", "deepseek-v4-pro[1m]", "a/b:c"], ["a b", "$(id)", "-x", 'a"b'])):
            import re
            for value in good:
                self.assertIsNotNone(re.fullmatch(params[name].pattern, value), (name, value))
            for value in bad:
                self.assertIsNone(re.fullmatch(params[name].pattern, value), (name, value))

    def test_install_script_refuses_values_that_do_not_fit(self):
        script = str(helpers.EXAMPLES / "addons" / "claude-code" / "install.sh")
        for name, value in (("BASE_URL", "https://x/$(id)"), ("BASE_URL", "ark.example"), ("MODEL", "a b"), ("MODEL", "$(id)"), ("MODEL", "a'b")):
            env = {"PATH": os.environ["PATH"], "DEVC_PARAM_BASE_URL": "", "DEVC_PARAM_MODEL": "", f"DEVC_PARAM_{name}": value}
            result = subprocess.run(["bash", script], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual((result.returncode, result.stderr.strip()), (1, f"{name.lower()} ist ungültig"), (name, value))
        for extra, message in (({"DEVC_PARAM_PROVIDER": "x"}, "provider ist ungültig"),
                               ({"DEVC_PARAM_ANTHROPIC_MODEL": "gpt"}, "anthropic_model ist ungültig"),
                               ({"DEVC_PARAM_PROVIDER": "custom"}, "Anderer Dienst gewählt, aber keine Adresse (base_url) eingetragen")):
            env = dict({"PATH": os.environ["PATH"]}, **extra)
            result = subprocess.run(["bash", script], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual((result.returncode, result.stderr.strip()), (1, message), extra)

    def test_fields_are_grouped_by_service(self):
        addon, _ = addons.load(helpers.EXAMPLES / "addons" / "claude-code")
        view = {p.name: (p.group, p.show_if) for p in addon.params}
        self.assertEqual(view["provider"], ("", ()))
        for name in ("oauth_token", "api_key", "anthropic_model"):
            self.assertEqual(view[name], ("Anthropic", ("provider", ("anthropic",))), name)
        for name in ("base_url", "auth_token", "model"):
            self.assertEqual(view[name], ("Anderer Dienst", ("provider", ("custom",))), name)
        self.assertTrue(addon.param("chat_model").hidden)


class CodexAddonTest(helpers.TempEnvTestCase):
    def test_manifest(self):
        addon, findings = addons.load(helpers.EXAMPLES / "addons" / "codex")
        self.assertEqual(findings, [])
        params = {p.name: p for p in addon.params}
        self.assertEqual((params["base_url"].default, params["model"].required, params["api_key"].type, params["release"].default),
                         ("https://api.novita.ai/openai/v1", True, "secret", "latest"))
        self.assertEqual([(t["kind"], t["run"]) for t in addon.tiles], [("chat", "python3 /opt/devc/addons/codex/bridge.py --model {model}")])

    def test_install_script_refuses_values_that_do_not_fit(self):
        script = str(helpers.EXAMPLES / "addons" / "codex" / "install.sh")
        for name, value in (("BASE_URL", "https://x/$(id)"), ("BASE_URL", "ftp://x"), ("MODEL", "a b"), ("MODEL", 'a"b'), ("MODEL", ""), ("RELEASE", "1.2"), ("RELEASE", "../x")):
            env = {"PATH": os.environ["PATH"], "DEVC_PARAM_BASE_URL": "https://api.example.org/v1", "DEVC_PARAM_MODEL": "a/b", "DEVC_PARAM_RELEASE": "latest", f"DEVC_PARAM_{name}": value}
            result = subprocess.run(["bash", script], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual((result.returncode, result.stderr.strip()), (1, f"{name.lower()} ist ungültig"), (name, value))
