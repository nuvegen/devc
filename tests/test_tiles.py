"""T-L17 und Kacheln aus Addons: Liste, Ansicht (view), Link, CLI und API (SPEC 6.2, 8.13)."""

from devc import addons, core
from devc.errors import ConflictError, EngineError, JobFailedError, NotFoundError, UsageError
from tests import fakes, helpers
from tests.helpers import run_cli, write_addon
from tests.test_addons import AddonTestCase
from tests.test_lifecycle import LifecycleTestCase
from tests.test_server import ServerTestCase

PORT = {"name": "port", "type": "int", "default": 3000}
VIEW = {"id": "status", "title": "Status", "kind": "view", "run": "cat /proc/loadavg"}
DEMO = {
    "params": [PORT, {"name": "token", "type": "secret"}],
    "tiles": [
        VIEW,
        {"id": "chat", "title": "Chat", "kind": "chat", "run": "python3 bridge.py --port {port}", "description": "Reden"},
        {"id": "ui", "title": "Oberfläche", "kind": "link", "port": "{port}", "path": "/app"},
        {"id": "doku", "title": "Doku", "kind": "link", "url": "https://example.org/{container}"},
    ],
}


class TileParseTest(AddonTestCase):
    def test_kinds_and_defaults(self):
        addon = self.assert_ok(DEMO)
        self.assertEqual([t["id"] for t in addon.tiles], ["status", "chat", "ui", "doku"])
        self.assertEqual(addon.tile("status"), {"id": "status", "title": "Status", "kind": "view", "description": "",
                                                "run": "cat /proc/loadavg", "timeout": 10, "refresh": 0})
        self.assertEqual(addon.tile("chat"), {"id": "chat", "title": "Chat", "kind": "chat", "description": "Reden",
                                              "run": "python3 bridge.py --port {port}"})
        self.assertEqual(addon.tile("ui")["path"], "/app")
        self.assertIsNone(addon.tile("fehlt"))
        self.assertEqual(len(addon.to_dict()["tiles"]), 4)
        self.assertEqual(self.assert_ok({"tiles": [{**VIEW, "timeout": 60, "refresh": 2}]}).tile("status")["refresh"], 2)

    def test_l3_and_l2(self):
        self.assert_finding("L3", "tiles", {"tiles": "x"})
        self.assert_finding("L3", "tiles[0].title", {"tiles": [{"id": "a", "kind": "view", "run": "true"}]})
        self.assert_finding("L3", "tiles[0].kind", {"tiles": [{**VIEW, "kind": "frame"}]})
        self.assert_finding("L3", "tiles[0].farbe", {"tiles": [{**VIEW, "farbe": "rot"}]})
        self.assert_finding("L2", "tiles[0].id", {"tiles": [{**VIEW, "id": "Groß"}]})

    def test_l17(self):
        link = {"id": "ui", "title": "UI", "kind": "link"}
        cases = {
            "id": [VIEW, VIEW],
            "run": [{"id": "a", "title": "A", "kind": "view"}],
            "timeout": [{**VIEW, "timeout": 0}],
            "refresh": [{**VIEW, "refresh": 1}],
            "port": [{**VIEW, "port": 3000}],
            "url": [link],
            "path": [{**link, "url": "https://example.org/", "path": "/x"}],
        }
        for path, tiles in cases.items():
            with self.subTest(path=path):
                self.assert_finding("L17", f"tiles[{len(tiles) - 1}].{path}", {"params": [PORT], "tiles": tiles})
        self.assert_finding("L17", "tiles[0].run", {"tiles": [{**VIEW, "run": "echo {fehlt}"}]})
        self.assert_finding("L17", "tiles[0].run", {"params": DEMO["params"], "tiles": [{**VIEW, "run": "echo {token}"}]})
        self.assert_finding("L17", "tiles[0].run", {"tiles": [{"id": "c", "title": "C", "kind": "chat", "run": " "}]})
        self.assert_finding("L17", "tiles[0].refresh", {"tiles": [{"id": "c", "title": "C", "kind": "chat", "run": "x", "refresh": 5}]})
        self.assert_finding("L17", "tiles[0].url", {"tiles": [{**link, "url": "ftp://example.org/"}]})
        self.assert_finding("L17", "tiles[0].url", {"params": [PORT], "tiles": [{**link, "url": "https://example.org/", "port": 3000}]})
        self.assert_finding("L17", "tiles[0].port", {"tiles": [{**link, "port": 80}]})
        self.assert_finding("L17", "tiles[0].path", {"tiles": [{**link, "port": 3000, "path": "app"}]})
        self.assert_finding("L17", "tiles[0].run", {"tiles": [{**link, "port": 3000, "run": "true"}]})


class TileTestCase(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        write_addon(self.config_dir / "addons", data=DEMO)
        self.create(addons=["info", "demo"])
        self.provider.calls.clear()

    def bash(self):
        return [c for c in self.provider.called("exec") if c[1][1][:2] == ["bash", "-lc"]]


class TileTest(TileTestCase):
    def test_list(self):
        result = core.tile_list(self.cfg, "py-tools")
        tiles = {t["ref"]: t for t in result["tiles"]}
        self.assertEqual(list(tiles), ["claude-code.chat", "git.repos", "info.status", "demo.status", "demo.chat", "demo.ui", "demo.doku"])
        self.assertEqual(tiles["info.status"], {
            "ref": "info.status", "addon": "info", "id": "status", "title": "Info", "kind": "view",
            "description": "System, Auslastung, Speicherplatz, Prozesse", "active": True, "refresh": 5})
        self.assertEqual((tiles["demo.ui"]["url"], tiles["demo.ui"]["port"], tiles["demo.ui"]["path"]), ("http://10.0.0.5:3000/app", 3000, "/app"))
        self.assertEqual(tiles["demo.doku"]["url"], "https://example.org/py-tools")
        self.assertEqual(self.provider.called("exec"), [])  # die Liste führt nichts im Container aus

        core.stop(self.cfg, "py-tools")
        tiles = {t["ref"]: t for t in core.tile_list(self.cfg, "py-tools")["tiles"]}
        self.assertEqual([t["active"] for t in tiles.values()], [False] * 7)
        self.assertIsNone(tiles["demo.ui"]["url"])

    def test_show_runs_command_as_dev(self):
        self.provider.exec_results.append((["bash", "-lc", "bash /opt/devc/addons/info/info.sh 10"], fakes.ExecResult(0, "## Container\n", "")))
        result = core.tile_show(self.cfg, "py-tools", "info.status")
        self.assertEqual(result, {"name": "py-tools", "tile": "info.status", "title": "Info", "refresh": 5, "text": "## Container\n", "truncated": False})
        call = self.bash()[0]
        self.assertEqual((call[2]["user"], call[2]["cwd"], call[2]["timeout"]), ("dev", "/work", 10))

    def test_show_uses_params_quoted(self):
        file = self.cfg.paths.instance_file("py-tools")
        file.write_text(file.read_text() + "\n[params.info]\nprocesses = 3\n")
        core.tile_show(self.cfg, "py-tools", "info.status")
        self.assertEqual(self.bash()[0][1][1][2], "bash /opt/devc/addons/info/info.sh 3")

    def test_show_failures(self):
        self.provider.exec_results.append((["bash", "-lc"], fakes.ExecResult(2, "", "erste\nletzte Zeile\n")))
        with self.assertRaises(JobFailedError) as ctx:
            core.tile_show(self.cfg, "py-tools", "info.status")
        self.assertEqual(ctx.exception.message, "Kachel info.status endete mit Exit 2: letzte Zeile")
        self.provider.fail["exec"] = EngineError("zu langsam", details={"timeout": True})
        with self.assertRaises(JobFailedError) as ctx:
            core.tile_show(self.cfg, "py-tools", "info.status")
        self.assertIn("Zeitüberschreitung nach 10 s", ctx.exception.message)
        del self.provider.fail["exec"]
        for spec in ("info.fehlt", "node.status", "kaputt"):
            with self.assertRaises(NotFoundError):
                core.tile_show(self.cfg, "py-tools", spec)
        with self.assertRaises(UsageError):
            core.tile_show(self.cfg, "py-tools", "demo.chat")
        core.stop(self.cfg, "py-tools")
        with self.assertRaises(ConflictError):
            core.tile_show(self.cfg, "py-tools", "info.status")

    def test_long_output_is_cut(self):
        self.provider.exec_results.append((["bash", "-lc"], fakes.ExecResult(0, "x" * 300000, "")))
        result = core.tile_show(self.cfg, "py-tools", "info.status")
        self.assertEqual((len(result["text"]), result["truncated"]), (256 * 1024, True))

    def test_cli(self):
        self.provider.exec_results.append((["bash", "-lc"], fakes.ExecResult(0, "## Container\n\nText\n", "")))
        code, out, err = run_cli("tile", "list", "py-tools")
        self.assertEqual(code, 0)
        self.assertIn("info.status       view   Info", out)
        self.assertIn("demo.ui           link   Oberfläche  http://10.0.0.5:3000/app", out)
        self.assertEqual(run_cli("tile", "show", "py-tools", "info.status"), (0, "## Container\n\nText\n", ""))
        self.assertEqual(run_cli("tile", "show", "py-tools", "x.y")[0], 4)


class InfoAddonTest(helpers.TempEnvTestCase):
    def test_script_prints_markdown(self):
        import subprocess

        script = helpers.EXAMPLES / "addons" / "info" / "info.sh"
        out = subprocess.run(["bash", str(script), "2"], capture_output=True, text=True, timeout=20)
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        for heading in ("## Container", "## Auslastung", "## Speicherplatz", "## Prozesse"):
            self.assertIn(heading, out.stdout)
        self.assertIn("| Was | Wert |\n|---|---|\n| Name | ", out.stdout)
        self.assertNotIn("## Prozesse", subprocess.run(["bash", str(script), "0"], capture_output=True, text=True, timeout=20).stdout)


class TileApiTest(ServerTestCase):
    def test_list_and_view(self):
        self.create(addons=["info"])
        self.provider.exec_results.append((["bash", "-lc", "bash /opt/devc/addons/info/info.sh 10"], fakes.ExecResult(0, "## Container\n", "")))
        status, data = self.request("GET", "/api/containers/py-tools/tiles")
        self.assertEqual((status, [t["ref"] for t in data["tiles"]], data["running"]), (200, ["claude-code.chat", "git.repos", "info.status"], True))
        status, data = self.request("GET", "/api/containers/py-tools/tiles/info.status/view")
        self.assertEqual((status, data["text"], data["refresh"]), (200, "## Container\n", 5))
        self.assertEqual(self.request("GET", "/api/containers/py-tools/tiles/info.x/view")[1]["error"], "E_NOT_FOUND")
        self.assertEqual(self.request("GET", "/api/containers/py-tools/tiles", auth=False)[0], 401)
