"""devc init, doctor (inkl. W3, W5) und token."""

import json
import os
import stat
import unittest
from unittest import mock

from devc import config, core
from devc.errors import EngineError, Finding, NotFoundError
from devc.providers.base import Volume
from tests import fakes, helpers
from tests.helpers import run_cli


class CoreTestCase(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.provider = fakes.FakeProvider()
        patcher = mock.patch.object(core, "_provider", lambda cfg: self.provider)
        patcher.start()
        self.addCleanup(patcher.stop)

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)


class InitTest(CoreTestCase):
    def test_creates_everything(self):
        self.provider.init_steps = ["Projekt devcontainers angelegt"]
        result = core.init(config.load())
        self.assertIn("Projekt devcontainers angelegt", result["changed"])
        for directory in ("addons", "templates", "secrets", "secrets/_shared"):
            self.assertTrue((self.config_dir / directory).is_dir(), directory)
        for directory in ("containers", "logs", "snapshots", "backups", "locks"):
            self.assertTrue((self.data_dir / directory).is_dir(), directory)
        self.assertEqual(self.mode(self.config_dir / "secrets"), 0o700)
        self.assertEqual(self.mode(self.config_dir / "secrets" / "_shared"), 0o700)
        self.assertEqual(self.mode(self.config_dir / "token"), 0o600)
        self.assertGreaterEqual(len((self.config_dir / "token").read_text().strip()), 32)
        written = config.load()
        self.assertTrue(written.exists)
        self.assertEqual(written.data["server"], config.DEFAULTS["server"])
        self.assertEqual(len(self.provider.called("init_project")), 1)

    def test_idempotent(self):
        core.init(config.load())
        token = (self.config_dir / "token").read_text()
        text = (self.config_dir / "config.toml").read_text()
        result = core.init(config.load())
        self.assertEqual(result, {"changed": []})
        self.assertEqual((self.config_dir / "token").read_text(), token)
        self.assertEqual((self.config_dir / "config.toml").read_text(), text)

    def test_keeps_existing_config_and_fixes_modes(self):
        self.write_config("# meine Config\n[server]\nport = 9000\n")
        (self.config_dir / "secrets").mkdir(mode=0o755)
        core.init(config.load())
        self.assertEqual((self.config_dir / "config.toml").read_text(), "# meine Config\n[server]\nport = 9000\n")
        self.assertEqual(self.mode(self.config_dir / "secrets"), 0o700)

    def test_engine_error_propagates(self):
        self.provider.fail["init_project"] = EngineError("lxc project: kaputt")
        with self.assertRaises(EngineError):
            core.init(config.load())

    def test_cli(self):
        self.provider.init_steps = ["Projekt devcontainers angelegt"]
        code, out, err = run_cli("init")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Projekt devcontainers angelegt\n", out)
        code, out, err = run_cli("init")
        self.assertEqual((code, out), (0, "Projekt devcontainers angelegt\n"))
        self.provider.init_steps = []
        self.assertEqual(run_cli("init"), (0, "Alles vorhanden, nichts geändert\n", ""))
        self.assertEqual(run_cli("init", "-q"), (0, "", ""))
        code, out, err = run_cli("init", "--json")
        self.assertEqual(json.loads(out), {"changed": []})


class DoctorTest(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.provider.findings = [Finding("doctor", "lxc", "LXD 6.9 erreichbar", "ok")]
        for name, value in (("getuid", 1000), ("geteuid", 1000)):
            patcher = mock.patch.object(os, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(core, "_in_lxd_group", return_value=True)
        self.in_group = patcher.start()
        self.addCleanup(patcher.stop)

    def points(self, result):
        return {(p["check"], p["path"]): p["severity"] for p in result["points"]}

    def test_ok(self):
        result = core.doctor(config.load())
        points = self.points(result)
        self.assertEqual(result["errors"], 0)
        for point in ("python", "lxc", "group", "allowed_roots", "orphans"):
            self.assertEqual(points[("doctor", point)], "ok", point)
        self.assertNotIn("cleaned", result)

    def test_provider_error_fails_with_engine_code(self):
        self.provider.findings = [Finding("doctor", "project", "Projekt devcontainers fehlt; devc init ausführen", "error")]
        with self.assertRaises(EngineError) as ctx:
            core.doctor(config.load())
        self.assertEqual(ctx.exception.details["errors"], 1)
        self.assertNotIn(("doctor", "orphans"), self.points(ctx.exception.details))

    def test_missing_lxc(self):
        with mock.patch.object(core, "_provider", side_effect=EngineError("lxc wurde nicht im PATH gefunden")):
            with self.assertRaises(EngineError) as ctx:
                core.doctor(config.load())
        self.assertEqual(self.points(ctx.exception.details)[("doctor", "lxc")], "error")

    def test_group_warning(self):
        self.in_group.return_value = False
        self.assertEqual(self.points(core.doctor(config.load()))[("doctor", "group")], "warning")

    def test_w5_shift_with_foreign_uid(self):
        with mock.patch.object(os, "getuid", return_value=1001):
            self.assertNotIn(("W5", "shift"), self.points(core.doctor(config.load())))  # Standard ist idmap
            self.write_config('[lxd]\nshared_mode = "shift"\n')
            result = core.doctor(config.load())
            self.assertEqual(self.points(result)[("W5", "shift")], "warning")
        self.assertNotIn(("W5", "shift"), self.points(core.doctor(config.load())))

    def test_w3_orphans(self):
        self.provider.add("py-tools")
        self.provider.volume_list = [Volume("devc-py-tools-work"), Volume("devc-py-tools-home"), Volume("devc-alt-home")]
        for name in ("py-tools", "weg"):
            (self.data_dir / "containers" / name).mkdir(parents=True)
        result = core.doctor(config.load())
        orphans = [p["message"] for p in result["points"] if p["check"] == "W3"]
        self.assertEqual(len(orphans), 2)
        self.assertTrue(any(m.endswith("containers/weg") for m in orphans))
        self.assertIn("Volume ohne Container: devc-alt-home", orphans)
        # devc löscht Waisen nicht selbst
        self.assertTrue((self.data_dir / "containers" / "weg").is_dir())
        self.assertEqual(result["errors"], 0)

    def test_allowed_roots_listed(self):
        root = self.tmp / "srv"
        root.mkdir()
        self.write_config(f'allowed_roots = ["{root}"]\n')
        result = core.doctor(config.load())
        self.assertIn(str(root), [p["message"] for p in result["points"] if p["path"] == "allowed_roots"][0])

    def test_xterm_missing_is_warning(self):
        with mock.patch.object(core, "web_file", return_value=self.tmp / "fehlt.js"):
            self.assertEqual(self.points(core.doctor(config.load()))[("doctor", "xterm")], "warning")
        present = self.tmp / "xterm.js"
        present.write_text("")
        with mock.patch.object(core, "web_file", return_value=present):
            self.assertEqual(self.points(core.doctor(config.load()))[("doctor", "xterm")], "ok")

    def test_cleanup_removes_only_old_logs(self):
        logs = self.data_dir / "logs"
        logs.mkdir(parents=True)
        now = core.clock().timestamp()
        for name, age_days in (("j-000000000001.log", 31), ("j-000000000002.log", 29)):
            (logs / name).write_text("x")
            os.utime(logs / name, (now - age_days * 86400, now - age_days * 86400))
        (logs / "notiz.txt").write_text("x")
        os.utime(logs / "notiz.txt", (0, 0))
        self.assertEqual(sorted(p.name for p in logs.iterdir()), ["j-000000000001.log", "j-000000000002.log", "notiz.txt"])
        core.doctor(config.load())
        self.assertEqual(len(list(logs.iterdir())), 3)  # ohne --cleanup wird nichts gelöscht
        result = core.doctor(config.load(), cleanup=True)
        self.assertEqual(result["cleaned"], ["j-000000000001.log"])
        self.assertEqual(sorted(p.name for p in logs.iterdir()), ["j-000000000002.log", "notiz.txt"])

    def test_cli_output_and_exit(self):
        code, out, err = run_cli("doctor")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("ok      lxc: LXD 6.9 erreichbar\n", out)
        self.write_config('[lxd]\nshared_mode = "shift"\n')
        with mock.patch.object(os, "getuid", return_value=1001):
            code, out, err = run_cli("doctor", "-q")
        self.assertIn("WARNUNG W5 shift: ", out)
        self.assertNotIn("ok ", out)
        self.provider.findings = [Finding("doctor", "pool", "Storage-Pool default fehlt; lxd.storage_pool anpassen", "error")]
        code, out, err = run_cli("doctor")
        self.assertEqual(code, 3)
        self.assertIn("FEHLER  pool: Storage-Pool default fehlt", out)
        self.assertEqual(err, "Fehler [E_ENGINE]: doctor meldet 1 Fehler\n")
        code, out, err = run_cli("doctor", "--json")
        self.assertEqual((code, json.loads(out)["error"]), (3, "E_ENGINE"))


class TokenTest(CoreTestCase):
    def test_show_without_init(self):
        with self.assertRaises(NotFoundError):
            core.token_show(config.load())
        code, out, err = run_cli("token", "show")
        self.assertEqual((code, out), (4, ""))

    def test_show_and_rotate(self):
        core.init(config.load())
        first = core.token_show(config.load())["token"]
        self.assertEqual(run_cli("token", "show"), (0, first + "\n", ""))
        second = core.token_rotate(config.load())["token"]
        self.assertNotEqual(first, second)
        self.assertEqual(core.token_show(config.load())["token"], second)
        self.assertEqual(self.mode(self.config_dir / "token"), 0o600)
        self.assertEqual([p.name for p in self.config_dir.glob("token*")], ["token"])


class GetProviderTest(helpers.TempEnvTestCase):
    def test_auto_without_lxc(self):
        from devc import providers

        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(EngineError):
                providers.get_provider(config.load())

    def test_lxd_from_config(self):
        from devc import providers

        self.write_config('platform = "lxd"\n[lxd]\nproject = "p2"\nstorage_pool = "tank"\nnetwork = "br1"\nshared_mode = "idmap"\n')
        provider = providers.get_provider(config.load())
        self.assertEqual((provider.project, provider.pool, provider.network, provider.shared_mode), ("p2", "tank", "br1", "idmap"))
        self.assertEqual(provider.uid, os.getuid())


if __name__ == "__main__":
    unittest.main()
