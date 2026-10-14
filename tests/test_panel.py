"""Kacheln der Art `panel` (SPEC 8.13) und das Addon `git`, das sie als erstes nutzt."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from devc import core, panel
from devc.errors import JobFailedError, NotFoundError, UsageError
from tests import fakes, helpers
from tests.helpers import run_cli, write_addon
from tests.test_addons import AddonTestCase
from tests.test_lifecycle import LifecycleTestCase
from tests.test_server import ServerTestCase

PANEL = {"id": "liste", "title": "Liste", "kind": "panel", "run": "python3 panel.py"}
DEMO = {"id": "demo", "tiles": [PANEL, {"id": "status", "title": "Status", "kind": "view", "run": "true"}]}
PAGE = {"title": "Dinge", "blocks": [
    {"type": "text", "text": "**Hallo**"},
    {"type": "table", "columns": ["A", "B"], "rows": [{"cells": ["1", "2"], "actions": [{"label": "Öffnen", "view": "ding", "args": {"nr": "1"}}]}]},
    {"type": "form", "title": "Neu", "fields": [{"name": "text", "type": "multiline", "required": True}, {"name": "alle", "type": "bool", "value": True},
                                                 {"name": "art", "type": "choice", "options": ["a", "b"], "value": "b"}],
     "submit": {"label": "Anlegen", "action": "neu", "confirm": "Sicher?", "style": "danger"}},
    {"type": "actions", "actions": [{"label": "Zurück", "view": ""}]},
    {"type": "code", "text": "x = 1"},
]}


class ParseTest(AddonTestCase):
    def test_panel_tile(self):
        addon = self.assert_ok({"tiles": [PANEL]})
        self.assertEqual(addon.tile("liste"), {"id": "liste", "title": "Liste", "kind": "panel", "description": "", "run": "python3 panel.py", "timeout": 30})
        self.assertEqual(self.assert_ok({"tiles": [{**PANEL, "timeout": 300}]}).tile("liste")["timeout"], 300)
        self.assert_finding("L17", "tiles[0].timeout", {"tiles": [{**PANEL, "timeout": 301}]})
        self.assert_finding("L17", "tiles[0].refresh", {"tiles": [{**PANEL, "refresh": 5}]})
        self.assert_finding("L17", "tiles[0].run", {"tiles": [{"id": "a", "title": "A", "kind": "panel"}]})


class ProtocolTest(unittest.TestCase):
    def test_request(self):
        self.assertEqual(panel.request(), {"view": "", "args": {}})
        self.assertEqual(panel.request(view="repo", args={"repo": "/work/x"}), {"view": "repo", "args": {"repo": "/work/x"}})
        self.assertEqual(panel.request(action="commit", args={"repo": "/w"}, values={"message": "Text", "all": True}),
                         {"action": "commit", "args": {"repo": "/w"}, "values": {"message": "Text", "all": True}})
        bad = [dict(view="a", action="b"), dict(view="Groß"), dict(view="a b"), dict(action=""), dict(action="x", values={"a": 1}), dict(action="x", values=[]),
               dict(args={"a": 1}), dict(args={"A": "x"}), dict(args=[]), dict(args={"a": "x" * 5000}), dict(args={"a": "x\x00"}),
               dict(view="a", values={"x": "y"}), dict(args={f"k{i}": "v" for i in range(21)})]
        for kwargs in bad:
            with self.subTest(kwargs=kwargs), self.assertRaises(UsageError):
                panel.request(**kwargs)

    def test_page_keeps_only_known_parts(self):
        raw = json.loads(json.dumps(PAGE))
        raw["html"] = "<script>"
        raw["blocks"][0]["html"] = "<b>"
        raw["blocks"][1]["rows"][0]["actions"][0]["href"] = "javascript:alert(1)"
        page = panel.page(raw)
        self.assertEqual(set(page), {"title", "note", "blocks"})
        self.assertEqual(page["blocks"][0], {"type": "text", "title": "", "text": "**Hallo**"})
        self.assertEqual(page["blocks"][1]["rows"][0], {"cells": ["1", "2"], "actions": [{"label": "Öffnen", "args": {"nr": "1"}, "view": "ding"}]})
        form = page["blocks"][2]
        self.assertEqual([(f["name"], f["type"], f["value"], f["required"]) for f in form["fields"]],
                         [("text", "multiline", "", True), ("alle", "bool", True, False), ("art", "choice", "b", False)])
        self.assertEqual(form["submit"], {"label": "Anlegen", "args": {}, "action": "neu", "confirm": "Sicher?", "style": "danger"})
        self.assertEqual(page["blocks"][3]["actions"], [{"label": "Zurück", "args": {}, "view": ""}])

    def test_bad_pages(self):
        def block(**kwargs):
            return {"blocks": [kwargs]}

        cases = [
            [], {}, {"blocks": "x"}, {"blocks": [1]}, block(type="html", text="<p>"), block(type="text"), block(type="text", text=3),
            block(type="table", columns=["A"], rows=[{"cells": ["1", "2"]}]), block(type="table", columns=[], rows=[]), block(type="table", columns=["A"], rows=[{"cells": [1]}]),
            block(type="actions", actions=[{"label": "X"}]), block(type="actions", actions=[{"label": "X", "view": "a", "action": "b"}]),
            block(type="actions", actions=[{"label": "", "view": "a"}]), block(type="actions", actions=[{"label": "X", "action": "Böse"}]),
            block(type="actions", actions=[{"label": "X", "view": "a", "args": {"k": 1}}]),
            block(type="form", fields=[{"name": "a", "type": "password"}], submit={"label": "X", "action": "a"}),
            block(type="form", fields=[{"name": "a", "type": "text"}, {"name": "a", "type": "text"}], submit={"label": "X", "action": "a"}),
            block(type="form", fields=[], submit={"label": "X", "view": "a"}), block(type="form", fields=[{"name": "a", "type": "choice", "options": []}], submit={"label": "X", "action": "a"}),
            {"blocks": [{"type": "text", "text": "x"}] * 51},
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(panel._Bad):
                panel.page(raw)

    def test_limits(self):
        page = panel.page({"blocks": [{"type": "table", "columns": ["A"], "rows": [{"cells": ["x" * 2000]}] * 600}, {"type": "code", "text": "y" * 300000}]})
        self.assertEqual((len(page["blocks"][0]["rows"]), page["blocks"][0]["truncated"], len(page["blocks"][0]["rows"][0]["cells"][0])), (500, True, 1000))
        self.assertEqual(len(page["blocks"][1]["text"]), 256 * 1024)

    def test_outcome(self):
        self.assertEqual(panel.outcome({"ok": True, "message": "Fertig", "extra": 1}), {"ok": True, "message": "Fertig", "output": ""})
        self.assertEqual(panel.outcome({"ok": False, "message": "Nein", "output": "x", "view": {"view": "repo", "args": {"repo": "/w"}}}),
                         {"ok": False, "message": "Nein", "output": "x", "view": {"view": "repo", "args": {"repo": "/w"}}})
        for raw in ({}, {"ok": "ja"}, {"ok": True, "view": "repo"}, {"ok": True, "view": {"view": "A"}}, {"ok": True, "message": 1}):
            with self.subTest(raw=raw), self.assertRaises(panel._Bad):
                panel.outcome(raw)


class PanelTest(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        write_addon(self.config_dir / "addons", data=DEMO)
        self.create(addons=["demo"])
        self.provider.calls.clear()

    def answer(self, data, rc=0, err=""):
        self.provider.exec_results.insert(0, (["bash", "-lc", "python3 panel.py"], fakes.ExecResult(rc, data if isinstance(data, str) else json.dumps(data), err)))

    def test_view(self):
        self.answer(PAGE)
        result = core.tile_panel(self.cfg, "py-tools", "demo.liste", view="ding", args={"nr": "1"})
        self.assertEqual((result["name"], result["tile"], result["title"], len(result["blocks"])), ("py-tools", "demo.liste", "Dinge", 5))
        call = self.provider.called("exec")[0]
        self.assertEqual((call[2]["user"], call[2]["cwd"], call[2]["timeout"]), ("dev", "/work", 30))
        self.assertEqual(json.loads(call[2]["stdin"]), {"view": "ding", "args": {"nr": "1"}})
        self.assertEqual([j for j in core.jobs(self.cfg, "py-tools")["jobs"] if j["kind"] == "tile"], [])  # Ansehen hinterlässt nichts im Log

    def test_action_goes_through_stdin_and_is_logged_without_values(self):
        self.answer({"ok": True, "message": "Angelegt", "output": "1 Datei"})
        result = core.tile_panel(self.cfg, "py-tools", "demo.liste", action="neu", values={"text": "streng geheim"})
        self.assertEqual((result["ok"], result["message"], result["output"]), (True, "Angelegt", "1 Datei"))
        call = self.provider.called("exec")[0]
        self.assertNotIn("streng geheim", json.dumps(call[1], ensure_ascii=False))
        self.assertEqual(json.loads(call[2]["stdin"])["values"], {"text": "streng geheim"})
        job = [j for j in core.jobs(self.cfg, "py-tools")["jobs"] if j["kind"] == "tile"][0]
        log = self.cfg.paths.job_log(job["id"]).read_text()
        self.assertIn("Kachel demo.liste: Aktion neu ausgeführt", log)
        self.assertNotIn("streng geheim", log)

    def test_failures(self):
        for data, rc, part in (("kein json", 0, "kein JSON"), ({"blocks": [{"type": "html"}]}, 0, "ungültig"), ("", 3, "Exit 3"), ("x" * (1024 * 1024 + 1), 0, "größer")):
            self.answer(data, rc, "letzte Zeile\n")
            with self.subTest(part=part), self.assertRaises(JobFailedError) as ctx:
                core.tile_panel(self.cfg, "py-tools", "demo.liste")
            self.assertIn(part, ctx.exception.message)
        with self.assertRaises(UsageError):
            core.tile_panel(self.cfg, "py-tools", "demo.status")  # andere Art
        with self.assertRaises(NotFoundError):
            core.tile_panel(self.cfg, "py-tools", "demo.fehlt")
        with self.assertRaises(UsageError):
            core.tile_panel(self.cfg, "py-tools", "demo.liste", view="x", action="y")

    def test_cli(self):
        self.answer({"ok": True, "message": "Gut"})
        code, out, err = run_cli("tile", "panel", "py-tools", "demo.liste", "--action", "neu", "--arg", "repo=/work/a=b", "--value", "alle=true", "--value", "text=Hallo")
        self.assertEqual((code, json.loads(out)["message"]), (0, "Gut"))
        self.assertEqual(json.loads(self.provider.called("exec")[0][2]["stdin"]), {"action": "neu", "args": {"repo": "/work/a=b"}, "values": {"alle": True, "text": "Hallo"}})
        self.assertEqual(run_cli("tile", "panel", "py-tools", "demo.liste", "--arg", "ohne")[0], 2)


class PanelApiTest(ServerTestCase):
    def test_panel(self):
        write_addon(self.config_dir / "addons", data=DEMO)
        self.create(addons=["demo"])
        self.provider.exec_results.insert(0, (["bash", "-lc", "python3 panel.py"], fakes.ExecResult(0, json.dumps(PAGE), "")))
        status, data = self.request("POST", "/api/containers/py-tools/tiles/demo.liste/panel", {"view": "", "args": {}})
        self.assertEqual((status, data["title"], data["blocks"][1]["columns"]), (200, "Dinge", ["A", "B"]))
        self.assertEqual(self.request("POST", "/api/containers/py-tools/tiles/demo.liste/panel", {"view": "x", "action": "y"})[0], 400)
        self.assertEqual(self.request("POST", "/api/containers/py-tools/tiles/demo.status/panel", {})[0], 400)
        self.assertEqual(self.request("POST", "/api/containers/py-tools/tiles/demo.liste/panel", {}, auth=False)[0], 401)
        kinds = {t["ref"]: t["kind"] for t in self.request("GET", "/api/containers/py-tools/tiles")[1]["tiles"]}
        self.assertEqual(kinds["demo.liste"], "panel")


SCRIPT = helpers.EXAMPLES / "addons" / "git" / "gitpanel.py"


class GitAddonTest(unittest.TestCase):
    """Das Skript der Kachel »Git« gegen echte Repositories in einem Temp-Ordner."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.org",
                    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.org"}
        self.work = self.root / "work"
        self.repo = self.work / "projekt"
        self.repo.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("eins\n")
        self.git("add", "a.txt")
        self.git("commit", "-q", "-m", "Erster Commit")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, capture_output=True, text=True, check=True).stdout

    def ask(self, asked, user=""):
        result = subprocess.run([sys.executable, str(SCRIPT), "--roots", str(self.work), "--depth", "3", "--user", user], input=json.dumps(asked) + "\n",
                                env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        raw = json.loads(result.stdout)
        return panel.outcome(raw) if "action" in asked else panel.page(raw)  # jede Antwort MUSS dem Protokoll genügen

    def here(self, **more):
        return {"repo": str(self.repo), **more}

    def test_list_finds_repositories(self):
        (self.work / "tief" / "a" / "b" / "c" / "zu-tief").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.work / "tief" / "a" / "b" / "c" / "zu-tief")], env=self.env, check=True)
        subprocess.run(["git", "init", "-q", str(self.work / "node_modules" / "paket")], env=self.env, check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.work / "leer")], env=self.env, check=True)
        page = self.ask({"view": "", "args": {}})
        rows = page["blocks"][0]["rows"]
        self.assertEqual([r["cells"][0] for r in rows], [str(self.work / "leer"), str(self.repo)])
        self.assertEqual(rows[0]["cells"][1:], ["main", "keine", "kein Gegenstück auf dem Server", "noch kein Commit"])
        self.assertEqual(rows[1]["cells"][1:4], ["main", "keine", "kein Gegenstück auf dem Server"])
        self.assertTrue(rows[1]["cells"][4].endswith("· Erster Commit"))
        self.assertEqual(rows[1]["actions"][0]["args"], self.here())

    def test_repo_changes_stage_commit(self):
        (self.repo / "a.txt").write_text("zwei\n")
        (self.repo / "neu mit leer.txt").write_text("x\n")
        page = self.ask({"view": "repo", "args": self.here()})
        changes = next(b for b in page["blocks"] if b["title"] == "Änderungen")
        self.assertEqual([r["cells"] for r in changes["rows"]], [["a.txt", "geändert", "nein"], ["neu mit leer.txt", "neu, noch nicht in git", "nein"]])
        form = next(b for b in page["blocks"] if b["type"] == "form")
        self.assertEqual([f["value"] for f in form["fields"]], ["", True])

        self.assertTrue(self.ask({"action": "stage", "args": self.here(file="neu mit leer.txt")})["ok"])
        page = self.ask({"view": "repo", "args": self.here()})
        changes = next(b for b in page["blocks"] if b["title"] == "Änderungen")
        self.assertEqual(changes["rows"][1]["cells"], ["neu mit leer.txt", "neu", "ja"])
        self.assertEqual([a["label"] for a in changes["rows"][1]["actions"]], ["Unterschied", "Nicht vormerken"])
        self.assertTrue(self.ask({"action": "unstage", "args": self.here(file="neu mit leer.txt")})["ok"])
        self.assertTrue(self.ask({"action": "stage", "args": self.here(file="neu mit leer.txt")})["ok"])

        result = self.ask({"action": "commit", "args": self.here(), "values": {"message": "Nur die neue\n\nMit Text.", "all": False}})
        self.assertEqual((result["ok"], result["message"], result["view"]), (True, "Commit erstellt.", {"view": "repo", "args": self.here()}))
        self.assertEqual(self.git("status", "--porcelain").strip(), "M a.txt")
        self.assertEqual(self.git("log", "-1", "--pretty=%B").strip(), "Nur die neue\n\nMit Text.")
        self.assertTrue(self.ask({"action": "commit", "args": self.here(), "values": {"message": "Rest", "all": True}})["ok"])
        self.assertEqual(self.git("status", "--porcelain"), "")

        for values, part in (({"message": " ", "all": True}, "Beschreibung fehlt"), ({"message": "x", "all": False}, "nichts vorgemerkt")):
            result = self.ask({"action": "commit", "args": self.here(), "values": values})
            self.assertEqual(result["ok"], False)
            self.assertIn(part, result["message"])

    def test_diff_commit_and_revert(self):
        (self.repo / "a.txt").write_text("zwei\n")
        (self.repo / "neu.txt").write_text("frisch\n")
        diff = self.ask({"view": "diff", "args": self.here(file="a.txt")})
        self.assertIn("-eins\n+zwei", diff["blocks"][1]["text"])
        self.assertEqual(self.ask({"view": "diff", "args": self.here(file="neu.txt")})["blocks"][1]["text"], "frisch\n")
        self.git("commit", "-q", "-am", "Zweiter Commit")
        page = self.ask({"view": "repo", "args": self.here()})
        log = page["blocks"][-1]
        self.assertEqual([r["cells"][3] for r in log["rows"]], ["Zweiter Commit", "Erster Commit"])
        commit = log["rows"][0]["actions"][1]["args"]["commit"]
        self.assertIn("confirm", log["rows"][0]["actions"][1])
        shown = self.ask({"view": "commit", "args": self.here(commit=commit)})
        self.assertIn("Zweiter Commit", shown["blocks"][1]["text"])

        result = self.ask({"action": "revert", "args": self.here(commit=commit)})  # neu.txt ist nicht in git und stört nicht
        self.assertEqual((result["ok"], (self.repo / "a.txt").read_text()), (True, "eins\n"))
        self.assertEqual(len(self.git("log", "--oneline").splitlines()), 3)
        (self.repo / "a.txt").write_text("ungesichert\n")
        result = self.ask({"action": "revert", "args": self.here(commit=commit)})
        self.assertEqual((result["ok"], (self.repo / "a.txt").read_text()), (False, "ungesichert\n"))
        self.assertIn("unverändert", result["message"])

    def test_push_and_pull_with_a_local_server(self):
        server = self.root / "server.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(server)], env=self.env, check=True)
        self.assertEqual(self.ask({"action": "push", "args": self.here()})["ok"], False)  # noch kein Server eingetragen
        self.git("remote", "add", "origin", str(server))
        result = self.ask({"action": "push", "args": self.here()})
        self.assertEqual((result["ok"], result["message"]), (True, "Push erledigt."))
        self.assertEqual(self.git("rev-parse", "main", cwd=server), self.git("rev-parse", "HEAD"))
        (self.repo / "a.txt").write_text("zwei\n")
        self.git("commit", "-q", "-am", "Zweiter Commit")
        page = self.ask({"view": "repo", "args": self.here()})
        self.assertIn("| Stand zum Server | 1 zu pushen |", page["blocks"][1]["text"])
        self.assertEqual([r["cells"][4] for r in page["blocks"][-1]["rows"]], ["noch nicht gepusht", "auf dem Server"])
        self.assertEqual(page["blocks"][0]["actions"], [{"label": "Zurück zur Liste", "args": {}, "view": ""}])
        self.assertEqual([a["label"] for a in next(b for b in page["blocks"] if b["title"] == "Server")["actions"]], ["Push (1)", "Pull"])
        self.assertTrue(self.ask({"action": "push", "args": self.here()})["ok"])
        self.assertEqual(self.ask({"action": "pull", "args": self.here()})["message"], "Pull erledigt. Es gab nichts Neues.")

        # Jemand anderes pusht: Pull holt den Commit; die eigene, nicht committete Datei bleibt.
        other = self.root / "anderer"
        subprocess.run(["git", "clone", "-q", str(server), str(other)], env=self.env, check=True)
        (other / "b.txt").write_text("vom Kollegen\n")
        self.git("add", "b.txt", cwd=other)
        self.git("commit", "-q", "-m", "Commit vom Kollegen", cwd=other)
        self.git("push", "-q", cwd=other)
        (self.repo / "notiz.txt").write_text("meins\n")
        result = self.ask({"action": "pull", "args": self.here()})
        self.assertEqual((result["ok"], result["message"], (self.repo / "b.txt").read_text(), (self.repo / "notiz.txt").read_text()), (True, "Pull erledigt.", "vom Kollegen\n", "meins\n"))

        # Beide haben neue Commits: Pull ändert nichts und sagt, wie es weitergeht; Push weist auf den Pull hin.
        (other / "b.txt").write_text("noch einer\n")
        self.git("commit", "-q", "-am", "Zweiter vom Kollegen", cwd=other)
        self.git("push", "-q", cwd=other)
        (self.repo / "a.txt").write_text("drei\n")
        self.git("commit", "-q", "-am", "Eigener Commit")
        before = self.git("rev-parse", "HEAD")
        result = self.ask({"action": "push", "args": self.here()})
        self.assertEqual(result["ok"], False)
        self.assertIn("Mache zuerst einen Pull", result["message"])
        result = self.ask({"action": "pull", "args": self.here()})
        self.assertEqual((result["ok"], self.git("rev-parse", "HEAD")), (False, before))
        self.assertIn("im Terminal", result["message"])

    def test_refuses_paths_outside(self):
        outside = self.root / "anders"
        subprocess.run(["git", "init", "-q", str(outside)], env=self.env, check=True)
        for args in ({"repo": str(outside)}, {"repo": str(self.work)}, {"repo": str(self.repo / ".." / ".." / "anders")}, {}):
            page = self.ask({"view": "repo", "args": args})
            self.assertIn("Kein Repository", page["blocks"][0]["text"])
            self.assertEqual(self.ask({"action": "push", "args": args})["ok"], False)
        for file in ("/etc/passwd", "../anders/x", ""):
            self.assertEqual(self.ask({"action": "stage", "args": self.here(file=file)})["ok"], False)
        self.assertEqual(self.ask({"action": "revert", "args": self.here(commit="HEAD; id")})["ok"], False)
        self.assertEqual(self.ask({"action": "gibtsnicht", "args": self.here()})["ok"], False)

    def test_credentials_never_appear_in_arguments(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"credential.helper=" + helper', text)
        secret = self.root / "secret"
        secret.write_text("tok-4711\n")
        code = f"import runpy, sys; m = runpy.run_path({str(SCRIPT)!r}); m['credential'].__globals__['SECRET'] = {str(secret)!r}; sys.exit(m['credential']('get'))"
        out = subprocess.run([sys.executable, "-c", code], env={**self.env, "DEVC_GIT_USER": "alex"}, capture_output=True, text=True)
        self.assertEqual(out.stdout, "username=alex\npassword=tok-4711\n")
        out = subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True)
        self.assertEqual(out.stdout, "username=x-access-token\npassword=tok-4711\n")  # ein Token braucht keinen Benutzernamen
        secret.write_text("")
        out = subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True)
        self.assertEqual(out.stdout, "")  # ohne Token keine halbe Antwort


if __name__ == "__main__":
    unittest.main()
