"""Chat (Kacheln der Art chat): Protokoll, Sitzungen im Server, WebSocket und die Bridge für Claude Code (SPEC 8.13)."""

import json
import os
import stat
import subprocess
import sys
import time
import unittest

from devc import chat, core
from devc.errors import ConflictError, NotFoundError, UsageError
from devc.terminal import OP_CLOSE, OP_TEXT, encode_frame
from tests import fakes, helpers
from tests.helpers import write_addon
from tests.test_terminal import MASK, TerminalSessionTest

DEMO = {"params": [{"name": "model", "type": "string", "default": "x y"}],
        "tiles": [{"id": "chat", "title": "Echo", "kind": "chat", "run": "python3 bridge.py --model {model}"},
                  {"id": "zwei", "title": "Zwei", "kind": "chat", "run": "true"},
                  {"id": "status", "title": "Status", "kind": "view", "run": "true"}]}


class ProtocolTest(unittest.TestCase):
    def test_commands_from_browser(self):
        self.assertEqual(chat.parse_command(b'{"type":"send","text":"Hallo","x":1}'), {"type": "send", "text": "Hallo"})
        self.assertEqual(chat.parse_command(b'{"type":"permit","id":"p1","allow":true}'), {"type": "permit", "id": "p1", "allow": True, "always": False, "folder": False})
        self.assertIs(chat.parse_command(b'{"type":"permit","id":"p1","allow":true,"folder":true}')["folder"], True)
        self.assertIs(chat.parse_command(b'{"type":"permit","id":"p1","allow":true,"folder":"ja"}')["folder"], False)
        self.assertEqual(chat.parse_command(b'{"type":"permit","id":"p1","allow":false,"always":true}')["always"], True)
        self.assertEqual(chat.parse_command(b'{"type":"interrupt","x":1}'), {"type": "interrupt"})
        self.assertEqual(chat.parse_command(b'{"type":"set","name":"permissions","value":"all","x":1}'), {"type": "set", "name": "permissions", "value": "all"})
        for bad in (b'{"type":"set","name":"Gross","value":"a"}', b'{"type":"set","name":"a","value":true}', b'{"type":"set","value":"a"}',
                    json.dumps({"type": "set", "name": "a", "value": "x" * 201}).encode()):
            self.assertIsNone(chat.parse_command(bad), bad[:40])
        big = json.dumps({"type": "send", "text": "x" * (chat.MAX_TEXT + 1)}).encode()
        for bad in (b"kein json", b"[1]", b'{"type":"send","text":" "}', b'{"type":"send","text":1}', big, b'{"type":"permit","id":"p"}',
                    b'{"type":"permit","id":1,"allow":true}', b'{"type":"start","resume":"x"}', b'{"type":"stop"}'):
            self.assertIsNone(chat.parse_command(bad), bad[:40])

    def test_events_from_bridge(self):
        self.assertEqual(chat.parse_event(b'{"type":"text","text":"a"}\r'), {"type": "text", "text": "a"})
        self.assertIsNone(chat.parse_event(b"  "))
        self.assertEqual(chat.parse_event(b"bash: python3: command not found"), {"type": "error", "text": "bash: python3: command not found"})
        self.assertEqual(chat.parse_event(b'{"type":"exit","code":0}')["type"], "error")  # exit meldet nur devc selbst

    def test_busy_for_counts_from_the_start_of_work(self):
        import time
        talk = chat.Chat("s1", "ab", "demo.chat", "Echo", None, None)
        talk._event({"type": "status", "state": "idle"})
        self.assertIsNone(talk.describe()["busy_for"])
        talk._event({"type": "status", "state": "working", "text": "denkt nach"})
        time.sleep(0.12)
        talk._event({"type": "status", "state": "waiting"})  # dieselbe Arbeit, die Uhr läuft weiter
        self.assertGreaterEqual(talk.describe()["busy_for"], 0.1)
        talk._event({"type": "status", "state": "idle"})
        self.assertIsNone(talk.describe()["busy_for"])

    def test_resume_must_be_an_id(self):
        self.assertIsNone(chat.check_resume(None))
        self.assertEqual(chat.check_resume("c1cafd13-e82d"), "c1cafd13-e82d")
        for bad in ("../x", "a b", "-x", 5, "x" * 200):
            with self.assertRaises(UsageError):
                chat.check_resume(bad)


class ChatSessionTest(TerminalSessionTest):
    def setUp(self):
        super().setUp()
        write_addon(self.config_dir / "addons", data=DEMO)
        file = self.cfg.paths.instance_file("ab")
        file.write_text(file.read_text().replace("addons = []", 'addons = ["demo"]'))

    def open_chat(self, **body):
        status, data = self.request("POST", "/api/containers/ab/chats", {"tile": "demo.chat", **body})
        self.assertEqual(status, 201, data)
        return data

    def attach(self, sid):
        sock, stream, status, headers, key = self.connect(path=f"/api/containers/ab/chat?session={sid}")
        self.assertEqual(status.split()[1], "101")
        return sock, self.frames(sock, stream)

    def events(self, frames, until):
        found = []
        while not found or found[-1]["type"] != until:
            opcode, payload = frames.read_message()
            self.assertEqual(opcode, OP_TEXT)
            found.append(json.loads(payload))
        return found

    def say(self, sock, **command):
        sock.sendall(encode_frame(OP_TEXT, json.dumps(command).encode(), MASK))

    def test_start_line_and_command(self):
        data = self.open_chat(resume="abc-123", mode="edits")  # mode gibt es nicht mehr und wird nicht weitergereicht
        self.assertEqual((data["tile"], data["title"]), ("demo.chat", "Echo"))
        call = self.provider.called("exec_pipe")[0]
        self.assertEqual((call[1][1], call[2]), (["bash", "-lc", "python3 bridge.py --model 'x y'"], {"user": "dev", "cwd": "/work"}))
        sock, frames = self.attach(data["id"])
        ready = self.events(frames, "status")[0]
        self.assertEqual((ready["session"], ready["start"]), ("abc-123", {"type": "start", "resume": "abc-123"}))
        listed = self.request("GET", "/api/containers/ab/chats")[1]["sessions"]
        self.assertEqual([(s["id"], s["state"], s["session"], s["attached"]) for s in listed], [(data["id"], "idle", "abc-123", 1)])

    def test_conversation_and_permission(self):
        sock, frames = self.attach(self.open_chat()["id"])
        self.events(frames, "status")
        self.say(sock, type="send", text="Hallo")
        self.assertEqual([(e["type"], e.get("text")) for e in self.events(frames, "turn_end")],
                         [("user", "Hallo"), ("text_delta", "Echo: "), ("text", "Echo: Hallo"), ("turn_end", None)])
        self.say(sock, type="send", text="frag")
        self.assertEqual(self.events(frames, "permission")[-1], {"type": "permission", "id": "p-1", "tool": "Bash", "summary": "ls"})
        self.say(sock, type="kaputt")  # unbekannte Befehle erreichen die Bridge nicht
        self.say(sock, type="permit", id="p-1", allow=True, always=True)
        self.assertEqual(self.events(frames, "permission_done")[-1], {"type": "permission_done", "id": "p-1", "allow": True, "always": True})
        self.say(sock, type="interrupt")
        self.assertEqual(self.events(frames, "turn_end")[-1]["text"], "abgebrochen")
        self.say(sock, type="send", text="müll")
        self.assertEqual(self.events(frames, "error")[-1], {"type": "error", "text": "kein json"})

    def test_survives_disconnect_and_replays(self):
        sid = self.open_chat()["id"]
        sock, frames = self.attach(sid)
        self.events(frames, "status")
        self.say(sock, type="send", text="eins")
        self.events(frames, "turn_end")
        self.say(sock, type="send", text="halb")  # Antwort bleibt mittendrin stehen
        self.events(frames, "text_delta")
        sock.sendall(encode_frame(OP_CLOSE, b"", MASK))
        sock.close()
        sock, frames = self.attach(sid)
        replay = self.events(frames, "text_delta")
        self.assertEqual([e["type"] for e in replay], ["ready", "status", "user", "text", "turn_end", "user", "text_delta"])
        self.assertEqual((replay[3]["text"], replay[-1]["text"]), ("Echo: eins", "Echo: "))

    def test_close_ends_process_and_logs_without_content(self):
        sid = self.open_chat()["id"]
        sock, frames = self.attach(sid)
        self.events(frames, "status")
        self.say(sock, type="send", text="geheimer Text")
        self.events(frames, "turn_end")
        self.assertEqual(self.request("DELETE", f"/api/containers/ab/chats/{sid}")[0], 200)
        ending = self.events(frames, "exit")  # »Beenden« sagt es der Bridge, die noch aufräumen darf
        self.assertEqual(ending, [{"type": "status", "state": "ending"}, {"type": "text", "text": "Titel geschrieben"}, {"type": "exit", "code": 0}])
        self.assertEqual(frames.read_message()[0], OP_CLOSE)
        for _ in range(100):
            if not self.request("GET", "/api/containers/ab/chats")[1]["sessions"]:
                break
            time.sleep(0.05)
        self.assertEqual(self.request("GET", "/api/containers/ab/chats")[1]["sessions"], [])
        self.assertEqual(self.request("DELETE", f"/api/containers/ab/chats/{sid}")[1]["error"], "E_NOT_FOUND")
        jobs = [self.request("GET", f"/api/jobs/{j['id']}")[1] for j in self.request("GET", "/api/jobs?container=ab")[1]["jobs"] if j["kind"] == "chat"]
        logs = " ".join(job["log"] for job in jobs)
        self.assertIn("Unterhaltung demo.chat gestartet", logs)
        self.assertIn("Unterhaltung demo.chat beendet", logs)
        self.assertNotIn("geheimer Text", logs)

    def test_bridge_ends_on_its_own(self):
        sock, frames = self.attach(self.open_chat()["id"])
        self.events(frames, "status")
        self.say(sock, type="send", text="ende")
        self.assertEqual(self.events(frames, "exit")[-1]["code"], 0)

    def test_limits_and_errors(self):
        self.open_chat()
        status, error = self.request("POST", "/api/containers/ab/chats", {"tile": "demo.chat"})
        self.assertEqual((status, error["error"]), (409, "E_CONFLICT"))
        self.assertEqual(self.request("POST", "/api/containers/ab/chats", {"tile": "demo.zwei"})[0], 201)
        for body, code in (({"tile": "demo.status"}, "E_USAGE"), ({"tile": "demo.fehlt"}, "E_NOT_FOUND"), ({}, "E_USAGE"),
                           ({"tile": "demo.x", "resume": "../x"}, "E_USAGE")):
            self.assertEqual(self.request("POST", "/api/containers/ab/chats", body)[1]["error"], code, body)
        self.assertEqual(self.request("GET", "/api/containers/ab/chat?session=abcd", headers={"Origin": f"http://127.0.0.1:{self.port}"})[1]["error"], "E_NOT_FOUND")
        self.assertEqual(self.request("GET", "/api/containers/ab/chats", auth=False)[0], 401)
        self.assertEqual(len(self.provider.called("exec_pipe")), 2)

    def test_server_close_ends_chats(self):
        self.open_chat()
        proc = self.provider.pipes[0]
        self.server.app.chats.close_all()
        self.assertIsNotNone(proc.poll())
        self.assertEqual(self.server.app.chats.list("ab"), [])

    def test_history(self):
        answer = json.dumps({"sessions": [{"id": "s-2", "title": "Zweite", "updated": 20.5}, {"id": "s-1", "title": "T" * 300}, {"x": 1}, "y"]})
        self.provider.exec_results.append((["bash", "-lc", "python3 bridge.py --model 'x y' --sessions"], fakes.ExecResult(0, answer, "")))
        status, data = self.request("GET", "/api/containers/ab/chats/history?tile=demo.chat")
        self.assertEqual(status, 200)
        self.assertEqual(data["sessions"], [{"id": "s-2", "title": "Zweite", "updated": 20.5}, {"id": "s-1", "title": "T" * 200, "updated": None}])
        self.provider.exec_results.insert(0, (["bash", "-lc"], fakes.ExecResult(0, "kein json", "")))
        self.assertEqual(self.request("GET", "/api/containers/ab/chats/history?tile=demo.chat")[1]["error"], "E_JOB_FAILED")
        self.assertEqual(self.request("GET", "/api/containers/ab/chats/history?tile=demo.status")[1]["error"], "E_USAGE")

    def test_stats(self):
        self.provider.exec_results.append((["bash", "-lc", "python3 bridge.py --model 'x y' --stats s-1"], fakes.ExecResult(0, json.dumps({"stats": "## Zeit\n"}), "")))
        status, data = self.request("GET", "/api/containers/ab/chats/stats?tile=demo.chat&session=s-1")
        self.assertEqual((status, data), (200, {"name": "ab", "tile": "demo.chat", "session": "s-1", "text": "## Zeit\n"}))
        self.assertEqual(self.provider.called("exec_pipe"), [])  # die Statistik startet keine Unterhaltung
        for query in ("tile=demo.chat", "tile=demo.chat&session=../x"):
            self.assertEqual(self.request("GET", f"/api/containers/ab/chats/stats?{query}")[1]["error"], "E_USAGE", query)
        # Eine Bridge ohne Statistik: irgendeine Ausgabe, leerer Text oder Exit ≠ 0
        for result in (fakes.ExecResult(0, "kein json", ""), fakes.ExecResult(0, json.dumps({"stats": ""}), ""), fakes.ExecResult(2, "", "")):
            self.provider.exec_results.insert(0, (["bash", "-lc"], result))
            self.assertEqual(self.request("GET", "/api/containers/ab/chats/stats?tile=demo.chat&session=s-1")[1]["error"], "E_JOB_FAILED")

    def test_transcript_shows_without_starting(self):
        lines = "\n".join(json.dumps(e) for e in ({"type": "user", "text": "Frage"}, {"type": "text_delta", "text": "An"}, {"type": "status", "state": "idle"},
                                                 {"type": "text", "text": "Antwort"}, {"type": "turn_end", "ok": True})) + "\nkein json\n"
        self.provider.exec_results.append((["bash", "-lc", "python3 bridge.py --model 'x y' --show s-1"], fakes.ExecResult(0, lines, "")))
        status, data = self.request("GET", "/api/containers/ab/chats/transcript?tile=demo.chat&session=s-1")
        self.assertEqual((status, data["session"]), (200, "s-1"))
        self.assertEqual([e["type"] for e in data["events"]], ["user", "text", "turn_end", "error"])
        self.assertEqual(self.provider.called("exec_pipe"), [])
        for query in ("tile=demo.chat", "tile=demo.chat&session=../x", "tile=demo.chat&session=a%20b"):
            self.assertEqual(self.request("GET", f"/api/containers/ab/chats/transcript?{query}")[1]["error"], "E_USAGE", query)
        self.provider.exec_results.insert(0, (["bash", "-lc"], fakes.ExecResult(1, "", "")))
        self.assertEqual(self.request("GET", "/api/containers/ab/chats/transcript?tile=demo.chat&session=s-1")[1]["error"], "E_JOB_FAILED")


class ChatDisabledTest(helpers.TempEnvTestCase):
    def test_same_gate_as_terminal(self):
        from tests.test_terminal import TerminalDisabledTest

        case = TerminalDisabledTest("test_insecure_bind_blocks_terminal")
        case.setUp()
        try:
            case.make()
            for method, path in (("GET", "chats"), ("POST", "chats"), ("DELETE", "chats/abcd"), ("GET", "chats/history?tile=a.b"),
                                 ("GET", "chats/transcript?tile=a.b&session=x"), ("GET", "chat?session=ab")):
                self.assertEqual(case.request(method, f"/api/containers/ab/{path}")[1]["error"], "E_SECURITY", path)
            self.assertEqual(case.provider.called("exec_pipe"), [])
        finally:
            case.doCleanups()


# Spielt `claude -p --input-format stream-json …`: antwortet auf jede Nachricht mit festen Ereignissen.
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
session = args[args.index("--resume") + 1] if "--resume" in args else "11111111-2222-3333-4444-555555555555"
if args == ["--version"]:
    print("9.9.8 (Claude Code)")
    sys.exit(0)
if "--fork-session" in args:  # Aufruf für den Titel
    open(__file__ + ".title-args", "w").write(json.dumps(args))
    print('## „Ein  schöner Titel“\n\nzweite Zeile')
    sys.exit(0)
def out(**event):
    sys.stdout.write(json.dumps(event) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    message = json.loads(line)
    if message["type"] == "control_response":
        answer = message["response"]["response"]
        out(type="user", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": answer["behavior"] != "allow",
            "content": [{"type": "text", "text": answer.get("message") or json.dumps(answer["updatedInput"])}]}]})
        out(type="result", subtype="success", is_error=False, total_cost_usd=0.5, duration_ms=7, session_id=session)
        continue
    if message["type"] == "control_request" and message["request"]["subtype"] == "initialize":
        out(type="control_response", response={"subtype": "success", "request_id": message["request_id"], "response": {
            **({} if "--model" in args else {"claude_code_version": "9.9.9"}), "models": [{"value": "default", "resolvedModel": "test-default"}, {"value": "sonnet", "resolvedModel": "test-sonnet"}],
            "commands": [{"name": "plug:lang", "description": "x" * 300, "argumentHint": ""}, {"name": "config", "description": "Einstellungen\n  ändern", "argumentHint": "key=value"},
                         {"name": "clear", "description": "Leeren"}, {"name": 7}, "kaputt", {"name": "__intern", "description": "x"}, {"name": "alt", "description": "(removed) weg"}]}})
        continue
    if message["type"] == "control_request":
        out(type="result", subtype="error_during_execution", is_error=True, total_cost_usd=0.5, duration_ms=7)
        continue
    text = message["message"]["content"][0]["text"]
    out(type="system", subtype="init", session_id=session, model="test-model", claude_code_version="9.9.9", args=args)
    if text == "stirb":
        sys.stderr.write("oben\nAnmeldung fehlt\n"); sys.exit(3)
    if text == "/config":
        usage = "Usage: /config key=value [key=value ...]\n  autoCompact=true|false\n  language=<value>\n  model=default|sonnet[1m]"
        out(type="assistant", message={"model": "<synthetic>", "content": [{"type": "text", "text": usage}]}, local_command_run={"command": "config", "args": ""})
        out(type="result", subtype="success", is_error=False, total_cost_usd=0, duration_ms=1, session_id=session)
        continue
    if text == "arbeite":
        out(type="system", subtype="status", status="requesting")
        out(type="stream_event", event={"type": "content_block_start", "content_block": {"type": "thinking"}})
        out(type="stream_event", event={"type": "content_block_start", "content_block": {"type": "thinking"}})
        out(type="assistant", message={"content": [{"type": "tool_use", "id": "t9", "name": "Task", "input": {"description": "Suche", "prompt": "lang"}}]})
        out(type="assistant", parent_tool_use_id="t9", message={"content": [{"type": "tool_use", "id": "u1", "name": "Bash", "input": {"command": "grep -r x ."}}]})
        out(type="user", parent_tool_use_id="t9", message={"content": [{"type": "tool_result", "tool_use_id": "u1", "content": "geheim"}]})
        out(type="user", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t9", "content": "fertig"}]})
        out(type="system", subtype="status", status=None)
        out(type="stream_event", event={"type": "content_block_start", "content_block": {"type": "text"}})
        out(type="assistant", message={"content": [{"type": "text", "text": "Erledigt"}]})
        out(type="result", subtype="success", is_error=False, total_cost_usd=0.1, duration_ms=5, session_id=session)
        continue
    if text == "/usage":
        out(type="assistant", message={"model": "<synthetic>", "content": [{"type": "text", "text": "Sitzung: 4 %\nWoche: 2 %\n\nLetzte 24 h\n  88 % mit viel Kontext"}]}, local_command_source="x")
        out(type="assistant", message={"model": "<synthetic>", "content": [{"type": "text", "text": "## Kontext\n\n| A | B |\n|---|---|\n| 1 | 2 |"}]}, local_command_source="x")
        out(type="result", subtype="success", is_error=False, total_cost_usd=0, duration_ms=1, session_id=session)
        continue
    if text.startswith("datei "):
        _, name, path, number = text.split()
        out(type="control_request", request_id="r-" + number, request={"subtype": "can_use_tool", "tool_name": name, "input": {"file_path": path, "content": "x"}})
        continue
    if text.startswith("tool"):
        name = "AskUserQuestion" if text == "tool frage" else "Bash"
        out(type="assistant", message={"content": [{"type": "tool_use", "id": "t1", "name": name, "input": {"command": "ls -la"}}]})
        out(type="control_request", request_id="r-" + text[-1], request={"subtype": "can_use_tool", "tool_name": name, "input": {"command": "ls -la"}})
        continue
    out(type="stream_event", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hal"}})
    out(type="stream_event", event={"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{"}})
    out(type="assistant", parent_tool_use_id="x", message={"content": [{"type": "text", "text": "vom Unter-Agenten"}]})
    out(type="assistant", message={"content": [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "Hallo " + text}]})
    out(type="result", subtype="success", is_error=False, total_cost_usd=0.25, duration_ms=12, session_id=session)
'''


class ClaudeBridgeTest(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        fake = self.tmp / "bin" / "claude"
        fake.parent.mkdir()
        fake.write_text(FAKE_CLAUDE.replace("#!/usr/bin/env python3", "#!" + sys.executable))
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.env = {**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}", "XDG_STATE_HOME": str(self.tmp / "state")}
        self.bridge = str(helpers.EXAMPLES / "addons" / "claude-code" / "bridge.py")

    def test_stats_from_the_transcript_of_claude_code(self):
        session = "0f0f0f0f-aaaa-bbbb-cccc-121212121212"
        project = self.tmp / "claude" / "projects" / "-work"
        (project / session / "subagents").mkdir(parents=True)
        at = lambda second: f"2026-10-13T{8 + second // 3600:02d}:{second % 3600 // 60:02d}:{second % 60:02d}.000Z"
        usage = lambda fresh, out, read, write: {"input_tokens": fresh, "output_tokens": out, "cache_read_input_tokens": read, "cache_creation_input_tokens": write}
        rows = [
            {"type": "user", "timestamp": at(0), "message": {"content": "Hallo"}},
            {"type": "assistant", "timestamp": at(10), "message": {"id": "m1", "model": "claude-opus-5-5", "usage": usage(100, 1, 0, 2000), "content": [{"type": "text", "text": "Ich"}]}},
            {"type": "assistant", "timestamp": at(12), "message": {"id": "m1", "model": "claude-opus-5-5", "usage": usage(100, 50, 0, 2000),
                                                                   "content": [{"type": "tool_use", "id": "t1", "name": "Bash"}, {"type": "tool_use", "id": "t2", "name": "Read"}]}},
            {"type": "user", "timestamp": at(42), "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}, {"type": "tool_result", "tool_use_id": "t2"}]}},
            {"type": "assistant", "timestamp": at(50), "message": {"id": "m2", "model": "claude-opus-5-5", "usage": usage(10, 200, 2100, 300), "content": [{"type": "text", "text": "Fertig"}]}},
            {"type": "summary", "timestamp": at(51)},
            {"type": "user", "timestamp": at(3710), "message": {"content": "Danke"}},
            {"type": "assistant", "timestamp": at(3715), "message": {"id": "m3", "model": "<synthetic>", "content": [{"type": "text", "text": "x"}]}},
        ]
        (project / f"{session}.jsonl").write_text("kaputte zeile\n" + "".join(json.dumps(row) + "\n" for row in rows))
        (project / session / "subagents" / "agent-1.jsonl").write_text(json.dumps(
            {"type": "assistant", "timestamp": at(20), "message": {"id": "s1", "usage": usage(5, 7, 1000, 0), "content": []}}) + "\n")
        env = {**self.env, "CLAUDE_CONFIG_DIR": str(self.tmp / "claude")}
        run = lambda *args: json.loads(subprocess.run([sys.executable, self.bridge, "--model", "", *args], env=env, capture_output=True, text=True, timeout=20).stdout)["stats"]
        text = run("--stats", session)
        for line in ("| Gesamt, erste bis letzte Nachricht | 1:01:55 | |", "| Modell | 0:25 | 45 % der Arbeitszeit |", "| Werkzeuge | 0:30 | 55 % der Arbeitszeit |",
                     "| Warten auf dich | 1:01:00 | 2 Nachrichten von dir |", "| Aufrufe des Modells | 2 |", "| Eingabe gesamt | 4.510 |",
                     "| davon aus dem Cache gelesen | 2.100 (47 %) |", "| davon in den Cache geschrieben | 2.300 |", "| davon ohne Cache | 110 |", "| Ausgabe | 250 |",
                     "| Kontext beim letzten Aufruf | 2.410 |", "| Modell | `claude-opus-5-5` |", "| Bash | 1 | 0:15 |", "| Read | 1 | 0:15 |",
                     "| Unter-Agenten | 1 |", "| Eingabe gesamt | 1.005 |"):
            self.assertIn(line + "\n", text)
        self.assertIn("noch keine Zahlen", run("--stats", "0f0f0f0f-0000-0000-0000-000000000000"))
        self.assertEqual(run("--stats", "../../etc"), "")

    def run_bridge(self, start, steps, args=("--model", "")):
        """Schickt nach jedem `turn_end` oder `permission` den nächsten Schritt; liefert alle Ereignisse."""
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
                step()
        self.assertEqual(proc.stderr.read(), "")
        code = proc.wait(10)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()
        # Die Antwort auf `initialize` kommt irgendwann nach dem Start; die Tests sehen sie getrennt.
        self.started = [e for e in found if "commands" in e]
        return [e for e in found if "commands" not in e], code

    def test_turn_is_translated_and_recorded(self):
        events, code = self.run_bridge({"type": "start", "resume": None}, [{"type": "send", "text": "Welt"}])
        self.assertEqual(code, 0)
        self.assertEqual([e["type"] for e in events], ["ready", "status", "user", "status", "ready", "text_delta", "text", "turn_end", "status"])
        ready = events[4]
        self.assertEqual((ready["agent"], ready["version"], ready["model"], ready["session"]),
                         ("Claude Code", "9.9.9", "test-model", "11111111-2222-3333-4444-555555555555"))
        self.assertEqual((events[5]["text"], events[6]["text"]), ("Hal", "Hallo Welt"))
        self.assertEqual(events[7], {"type": "turn_end", "ok": True, "cost_usd": 0.25, "duration_ms": 12})
        self.assertEqual([e["state"] for e in events if e["type"] == "status"], ["idle", "working", "idle"])
        stored = (self.tmp / "state" / "devc-chat" / "claude-code" / "11111111-2222-3333-4444-555555555555.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(line)["type"] for line in stored], ["user", "text", "turn_end"])

    def test_start_names_version_model_and_commands(self):
        self.env["HOME"] = str(self.tmp)
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "Welt"}])
        self.assertEqual({key: value for key, value in events[0].items() if key != "settings"}, {"type": "ready", "agent": "Claude Code", "session": None})
        self.assertEqual(len(self.started), 1)
        ready = self.started[0]
        self.assertEqual((ready["type"], ready["version"], ready["model"]), ("ready", "9.9.9", "test-default"))
        self.assertEqual([c["name"] for c in ready["commands"]], ["clear", "config", "plug:lang"])
        self.assertEqual(ready["commands"][1], {"name": "config", "hint": "key=value", "description": "Einstellungen ändern"})
        self.assertEqual(len(ready["commands"][2]["description"]), 140)
        # Das gewählte Modell steht sofort da, noch bevor Claude antwortet.
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "Welt"}], ("--model", "sonnet"))
        self.assertEqual((events[0]["model"], self.started[0]["model"]), ("sonnet", "test-sonnet"))
        self.assertEqual(self.started[0]["version"], "9.9.8")  # nennt Claude Code die Version nicht selbst: aus `claude --version`
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "Welt"}], ("--model", "eigenes-modell"))
        self.assertEqual(self.started[0]["model"], "eigenes-modell")
        # Ohne chat_model gilt die Einstellung von Claude Code.
        (self.tmp / ".claude").mkdir()
        (self.tmp / ".claude" / "settings.json").write_text('{"model": "sonnet"}')
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "Welt"}])
        self.assertEqual((events[0]["model"], self.started[0]["model"]), ("sonnet", "test-sonnet"))

    def test_activity_shows_that_claude_is_still_working(self):
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "arbeite"}])
        status = [(e["state"], e.get("text")) for e in events if e["type"] == "status"]
        self.assertEqual(status, [("idle", None), ("working", None), ("working", "wartet auf das Modell"), ("working", "denkt nach"),
                                  ("working", "Task: Suche"), ("working", "Unter-Agent · Bash: grep -r x ."), ("working", "wertet das Ergebnis aus"),
                                  ("working", "schreibt"), ("idle", None)])
        # Vom Unter-Agenten kommt nur die Tätigkeit an, kein Werkzeug und kein Ergebnis.
        self.assertEqual([e["id"] for e in events if e["type"] in ("tool_start", "tool_end")], ["t9", "t9"])
        # Bei einer offenen Freigabe bleibt der Zustand »wartet«; danach geht es mit der Tätigkeit weiter.
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "tool 1"}, {"type": "permit", "id": "r-1", "allow": True, "always": False}])
        status = [(e["state"], e.get("text")) for e in events if e["type"] == "status"]
        self.assertEqual(status, [("idle", None), ("working", None), ("working", "Bash: ls -la"), ("waiting", None), ("working", "Bash: ls -la"),
                                  ("working", "wertet das Ergebnis aus"), ("idle", None)])

    def test_updates_prompt_is_a_parameter(self):
        probe = self.tmp / "bin" / "claude"
        probe.write_text(f"#!{sys.executable}\nimport json, sys\nopen({str(self.tmp / 'args.json')!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
        self.run_bridge({"type": "start"}, [], ("--model", "", "--updates", "true"))
        seen = json.loads((self.tmp / "args.json").read_text())
        self.assertIn("auf dem Laufenden", seen[seen.index("--append-system-prompt") + 1])
        self.run_bridge({"type": "start"}, [], ("--model", "", "--updates", "false"))
        self.assertNotIn("--append-system-prompt", json.loads((self.tmp / "args.json").read_text()))
        # So setzt devc den Parameter in den Befehl der Kachel ein.
        from devc import addons
        addon = addons.load(helpers.EXAMPLES / "addons" / "claude-code")[0]
        self.assertEqual((addon.tiles[0]["run"], [p.default for p in addon.params if p.name == "chat_updates"]),
                         ("python3 /opt/devc/addons/claude-code/bridge.py --model {chat_model} --updates {chat_updates}", [True]))

    def test_output_of_local_commands_becomes_markdown(self):
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "/config"}, {"type": "send", "text": "/usage"}])
        texts = [e["text"] for e in events if e["type"] == "text"]
        self.assertEqual(texts[0], "Aufruf: `/config key=value [key=value ...]`\n\n| Einstellung | Werte |\n|---|---|\n"
                                   "| `autoCompact` | `true`, `false` |\n| `language` | `<value>` |\n| `model` | `default`, `sonnet[1m]` |")
        # Einfacher Text behält seine Zeilen (zwei Leerzeichen am Ende = Zeilenumbruch in Markdown); Markdown bleibt, wie es ist.
        self.assertEqual(texts[1], "Sitzung: 4 %  \nWoche: 2 %\n\nLetzte 24 h  \n88 % mit viel Kontext")
        self.assertEqual(texts[2], "## Kontext\n\n| A | B |\n|---|---|\n| 1 | 2 |")
        # Eine Antwort des Modells bleibt unverändert.
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "a\nb"}])
        self.assertEqual([e["text"] for e in events if e["type"] == "text"], ["Hallo a\nb"])

    def test_arguments_for_claude(self):
        # Ein gespieltes claude, das nur seine Argumente festhält.
        probe = self.tmp / "bin" / "claude"
        probe.write_text(f"#!{sys.executable}\nimport json, sys\nopen({str(self.tmp / 'args.json')!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
        self.run_bridge({"type": "start", "resume": "aaaaaaaa-1111", "mode": "edits"}, [], ("--model", "sonnet"))
        seen = json.loads((self.tmp / "args.json").read_text())
        self.assertEqual(seen, ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                                "--permission-prompt-tool", "stdio", "--model", "sonnet", "--resume", "aaaaaaaa-1111"])
        self.run_bridge({"type": "start", "resume": "../../etc"}, [])
        seen = json.loads((self.tmp / "args.json").read_text())
        self.assertEqual(seen[-2:], ["--permission-prompt-tool", "stdio"])  # kein Modell, kein ungültiges resume

    def test_permission_allow_always_and_deny(self):
        events, _ = self.run_bridge({"type": "start"}, [
            {"type": "send", "text": "tool 1"}, {"type": "permit", "id": "r-1", "allow": False},
            {"type": "send", "text": "tool 2"}, {"type": "permit", "id": "r-2", "allow": True, "always": True},
            {"type": "send", "text": "tool 3"}])
        asked = [e for e in events if e["type"] == "permission"]
        self.assertEqual([(e["id"], e["tool"], e["summary"]) for e in asked], [("r-1", "Bash", "ls -la"), ("r-2", "Bash", "ls -la")])  # r-3: immer erlaubt
        self.assertEqual(json.loads(asked[0]["input"]), {"command": "ls -la"})
        self.assertEqual([(e["id"], e["allow"]) for e in events if e["type"] == "permission_done"], [("r-1", False), ("r-2", True)])
        ends = [e for e in events if e["type"] == "tool_end"]
        self.assertEqual([(e["ok"], e["output"]) for e in ends], [(False, "Vom Nutzer abgelehnt"), (True, '{"command": "ls -la"}'), (True, '{"command": "ls -la"}')])
        start = next(e for e in events if e["type"] == "tool_start")
        self.assertEqual((start["id"], start["name"], start["summary"]), ("t1", "Bash", "ls -la"))
        self.assertIn("waiting", [e.get("state") for e in events])

    def test_permission_for_a_folder_with_subfolders(self):
        base = self.tmp / "proj"
        ask = lambda tool, path, number: {"type": "send", "text": f"datei {tool} {path} {number}"}
        events, _ = self.run_bridge({"type": "start"}, [
            ask("Edit", base / "src" / "a.txt", 1), {"type": "permit", "id": "r-1", "allow": True, "folder": True},
            ask("Write", base / "src" / "tief" / "unten" / "b.txt", 2),      # im freigegebenen Ordner: keine Frage, auch für ein anderes Werkzeug
            ask("Edit", base / "src" / ".." / "anders" / "c.txt", 3), {"type": "permit", "id": "r-3", "allow": True},   # `..` führt hinaus: Frage
            ask("Edit", base / "anders" / "d.txt", 4), {"type": "permit", "id": "r-4", "allow": False},   # einmal erlauben gilt nicht für den Ordner
            ask("Edit", base / "srcx" / "e.txt", 5), {"type": "permit", "id": "r-5", "allow": False},     # nur ein ähnlicher Name
            {"type": "send", "text": "tool 6"}, {"type": "permit", "id": "r-6", "allow": True, "folder": True},  # Bash kennt keinen Ordner
            {"type": "send", "text": "tool 7"}, {"type": "permit", "id": "r-7", "allow": False},
        ])
        asked = [(e["id"], e.get("folder")) for e in events if e["type"] == "permission"]
        self.assertEqual(asked, [("r-1", str(base / "src")), ("r-3", str(base / "anders")), ("r-4", str(base / "anders")),
                                 ("r-5", str(base / "srcx")), ("r-6", None), ("r-7", None)])

    def test_setting_files_in_the_project_are_free(self):
        ask = lambda path, number: {"type": "send", "text": f"datei Edit {path} {number}"}
        outside = self.tmp.parent / "woanders" / "b.txt"
        events, _ = self.run_bridge({"type": "start"}, [
            [{"type": "set", "name": "permissions", "value": "files"}, ask(self.tmp / "src" / "a.txt", 1)],   # im Projekt: keine Frage
            ask(self.tmp / "src" / ".." / ".." / "woanders" / "b.txt", 2), {"type": "permit", "id": "r-2", "allow": False},  # `..` führt hinaus
            {"type": "send", "text": "tool 3"}, {"type": "permit", "id": "r-3", "allow": False},  # Befehle fragen weiter
        ])
        self.assertEqual([(e["id"], e.get("folder")) for e in events if e["type"] == "permission"], [("r-2", str(outside.parent.resolve())), ("r-3", None)])
        offered = [e["settings"] for e in events if e["type"] == "ready" and "settings" in e]
        self.assertEqual([[(s["name"], s["label"], s["value"], [o["value"] for o in s["options"]]) for s in found] for found in offered],
                         [[("permissions", "Freigaben", value, ["ask", "files", "all"])] for value in ("ask", "files")])
        self.assertTrue(all(o["label"] and o["hint"] for o in offered[0][0]["options"]))
        # Die Wahl gilt für den Container: Die nächste Unterhaltung beginnt damit.
        events, _ = self.run_bridge({"type": "start"}, [ask(self.tmp / "c.txt", 4)])
        self.assertEqual(next(e for e in events if e["type"] == "ready")["settings"][0]["value"], "files")
        self.assertEqual([e for e in events if e["type"] == "permission"], [])

    def test_setting_allow_everything(self):
        events, _ = self.run_bridge({"type": "start"}, [
            [{"type": "set", "name": "permissions", "value": "kaputt"}, {"type": "set", "name": "anderes", "value": "all"}, {"type": "send", "text": "tool 1"}],
            {"type": "set", "name": "permissions", "value": "all"},   # die offene Frage ist damit beantwortet
            {"type": "send", "text": "tool 2"}, {"type": "send", "text": f"datei Write {self.tmp.parent / 'x.txt'} 3"}, {"type": "send", "text": "tool frage"}])
        self.assertEqual([e["id"] for e in events if e["type"] == "permission"], ["r-1"])
        self.assertEqual([(e["id"], e["allow"]) for e in events if e["type"] == "permission_done"], [("r-1", True)])
        self.assertEqual([e["ok"] for e in events if e["type"] == "tool_end"], [True, True, True, False])  # Rückfragen als Werkzeug bleiben abgelehnt
        self.assertEqual([e["settings"][0]["value"] for e in events if e["type"] == "ready" and "settings" in e], ["ask", "all"])

    def test_questions_tool_is_refused(self):
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "tool frage"}])
        self.assertEqual([e for e in events if e["type"] == "permission"], [])
        self.assertIn("Stelle Rückfragen als normalen Text", next(e for e in events if e["type"] == "tool_end")["output"])

    def test_interrupt(self):
        events, _ = self.run_bridge({"type": "start"}, [{"type": "send", "text": "tool 1"}, {"type": "interrupt"}])
        self.assertEqual([(e["id"], e["allow"]) for e in events if e["type"] == "permission_done"], [("r-1", False)])
        self.assertEqual(events[-2]["type"], "turn_end")

    def test_resume_replays_history_and_lists_sessions(self):
        self.run_bridge({"type": "start"}, [{"type": "send", "text": "Erste   Frage"}])
        session = "11111111-2222-3333-4444-555555555555"
        events, _ = self.run_bridge({"type": "start", "resume": session}, [{"type": "send", "text": "Zweite"}])
        self.assertEqual([e["type"] for e in events[:5]], ["user", "text", "turn_end", "ready", "status"])
        self.assertEqual(events[3]["session"], session)
        stored = (self.tmp / "state" / "devc-chat" / "claude-code" / f"{session}.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(line)["type"] for line in stored], ["user", "text", "turn_end"] * 2)
        out = subprocess.run([sys.executable, self.bridge, "--model", "", "--sessions"], capture_output=True, text=True, env=self.env)
        listed = json.loads(out.stdout)["sessions"]
        self.assertEqual([(s["id"], s["title"]) for s in listed], [(session, "Erste Frage")])
        self.assertIsInstance(listed[0]["updated"], float)

    def test_end_writes_a_title_and_show_prints_the_history(self):
        session = "11111111-2222-3333-4444-555555555555"
        events, code = self.run_bridge({"type": "start"}, [{"type": "send", "text": "Erste Frage"}, {"type": "end"}], ("--model", "sonnet"))
        self.assertEqual((code, events[-1]), (0, {"type": "status", "state": "ending"}))
        args = json.loads((self.tmp / "bin" / "claude.title-args").read_text())
        self.assertEqual(args[:7], ["-p", "--resume", session, "--fork-session", "--no-session-persistence", "--output-format", "text"])
        self.assertEqual(args[7:9], ["--model", "sonnet"])
        self.assertIn("höchstens 10 Wörtern", args[9])
        listed = lambda: json.loads(subprocess.run([sys.executable, self.bridge, "--model", "", "--sessions"], capture_output=True, text=True, env=self.env).stdout)["sessions"]
        self.assertEqual([(s["id"], s["title"]) for s in listed()], [(session, "Ein schöner Titel")])
        stored = (self.tmp / "state" / "devc-chat" / "claude-code" / f"{session}.jsonl").read_text()
        self.assertNotIn("Titel", stored)  # die Frage nach dem Titel steht nicht im Verlauf

        shown = subprocess.run([sys.executable, self.bridge, "--model", "", "--show", session], capture_output=True, text=True, env=self.env)
        self.assertEqual([json.loads(line)["type"] for line in shown.stdout.splitlines()], ["user", "text", "turn_end"])
        self.assertEqual(subprocess.run([sys.executable, self.bridge, "--show", "../x"], capture_output=True, text=True, env=self.env).stdout, "")

        # Ohne neue Nachricht entsteht kein neuer Titel; ohne `end` (devc endet) auch nicht.
        (self.tmp / "bin" / "claude.title-args").unlink()
        self.run_bridge({"type": "start", "resume": session}, [{"type": "end"}])
        self.run_bridge({"type": "start", "resume": session}, [{"type": "send", "text": "Noch eine"}])
        self.assertFalse((self.tmp / "bin" / "claude.title-args").exists())

    def test_claude_fails_or_is_missing(self):
        events, code = self.run_bridge({"type": "start"}, [{"type": "send", "text": "stirb"}])
        self.assertEqual((code, events[-1]), (3, {"type": "error", "text": "oben\nAnmeldung fehlt"}))
        (self.tmp / "bin" / "claude").unlink()
        self.env["PATH"] = "/usr/bin:/bin"
        self.env["HOME"] = str(self.tmp)
        events, code = self.run_bridge({"type": "start"}, [])
        self.assertEqual(code, 1)
        self.assertIn("claude wurde im Container nicht gefunden", events[0]["text"])
