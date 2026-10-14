"""T-L1: config.toml (SPEC 6.1)."""

import tomllib
import unittest

from devc import config
from devc.errors import ValidationError
from tests import helpers


class ConfigTest(helpers.TempEnvTestCase):
    def test_missing_file_gives_defaults(self):
        cfg = config.load()
        self.assertFalse(cfg.exists)
        self.assertEqual(cfg.platform, "auto")
        self.assertEqual(cfg.allowed_roots, [])
        self.assertEqual(cfg.server["bind"], "127.0.0.1")
        self.assertEqual(cfg.server["port"], 8740)
        self.assertEqual(cfg.server["allowed_hosts"], ["localhost", "127.0.0.1"])
        self.assertIs(cfg.server["terminal"], True)
        self.assertEqual(cfg.defaults, {"image": "ubuntu:24.04", "cpu": 4, "memory": "8GiB", "disk": "30GiB", "addons": ["claude-code"]})
        self.assertEqual(cfg.lxd, {"project": "devcontainers", "storage_pool": "default", "network": "lxdbr0", "shared_mode": "idmap"})
        self.assertEqual(cfg.paths.state, self.data_dir)

    def test_partial_file_overlays_defaults(self):
        root = self.tmp / "srv"
        root.mkdir()
        self.write_config(f'allowed_roots = ["{root}"]\n[server]\nport = 9000\n[paths]\nbackups = "~/b"\n')
        cfg = config.load()
        self.assertTrue(cfg.exists)
        self.assertEqual(cfg.allowed_roots, [str(root)])
        self.assertEqual(cfg.server["port"], 9000)
        self.assertEqual(cfg.server["bind"], "127.0.0.1")
        self.assertEqual(cfg.paths.backups, self.tmp / "home" / "b")

    def test_explicit_file(self):
        file = self.tmp / "other.toml"
        file.write_text('[lxd]\nproject = "p2"\n', encoding="utf-8")
        cfg = config.load(file)
        self.assertEqual(cfg.lxd["project"], "p2")
        self.assertEqual(cfg.config_file, file)

    def test_example_config_passes_except_missing_root(self):
        data = tomllib.loads((helpers.EXAMPLES / "config.toml").read_text(encoding="utf-8"))
        data["allowed_roots"] = []
        self.assertEqual(config.validate(data), [])

    def test_default_text_is_valid_and_complete(self):
        data = tomllib.loads(config.default_text())
        self.assertEqual(config.validate(data), [])
        self.assertEqual(data["server"], config.DEFAULTS["server"])
        self.assertEqual(data["paths"]["state"], str(self.data_dir))
        self.assertEqual(sorted(data["paths"]), ["addons", "backups", "secrets", "state", "templates"])

    def assert_l1(self, text, path):
        self.write_config(text)
        with self.assertRaises(ValidationError) as ctx:
            config.load()
        findings = ctx.exception.findings
        self.assertTrue(all(f.check == "L1" for f in findings))
        self.assertIn(path, [f.path for f in findings])
        return ctx.exception

    def test_invalid_toml(self):
        self.write_config("platform = \n")
        with self.assertRaises(ValidationError) as ctx:
            config.load()
        self.assertEqual(ctx.exception.details["check"], "L1")
        self.assertEqual(ctx.exception.details["line"], 1)

    def test_not_utf8(self):
        self.config_dir.mkdir(parents=True)
        (self.config_dir / "config.toml").write_bytes(b'platform = "\xff"\n')
        with self.assertRaises(ValidationError):
            config.load()

    def test_violations(self):
        cases = [
            ('plattform = "lxd"\n', "plattform"),
            ('[server]\nprot = 1\n', "server.prot"),
            ('platform = "docker"\n', "platform"),
            ('platform = 3\n', "platform"),
            ('server = "x"\n', "server"),
            ('[server]\nport = 0\n', "server.port"),
            ('[server]\nport = 70000\n', "server.port"),
            ('[server]\nport = "8740"\n', "server.port"),
            ('[server]\nport = true\n', "server.port"),
            ('[server]\nbind = ""\n', "server.bind"),
            ('[server]\nterminal = "ja"\n', "server.terminal"),
            ('[server]\nallowed_hosts = "localhost"\n', "server.allowed_hosts"),
            ('[server]\ntls_cert = 1\n', "server.tls_cert"),
            ('[defaults]\ncpu = 0\n', "defaults.cpu"),
            ('[defaults]\nmemory = "8GB"\n', "defaults.memory"),
            ('[defaults]\ndisk = 30\n', "defaults.disk"),
            ('[defaults]\naddons = ["Claude"]\n', "defaults.addons"),
            ('[defaults]\nimage = ""\n', "defaults.image"),
            ('[lxd]\nshared_mode = "bind"\n', "lxd.shared_mode"),
            ('[lxd]\nproject = ""\n', "lxd.project"),
            ('[paths]\nstate = "relativ/pfad"\n', "paths.state"),
            ('[paths]\nlogs = "/x"\n', "paths.logs"),
            ('allowed_roots = ["relativ"]\n', "allowed_roots"),
            ('allowed_roots = ["/gibt/es/nicht/devc"]\n', "allowed_roots"),
            ('allowed_roots = "/srv"\n', "allowed_roots"),
        ]
        for text, path in cases:
            with self.subTest(text=text):
                self.assert_l1(text, path)

    def test_all_findings_reported(self):
        error = self.assert_l1('platform = "x"\n[server]\nport = 0\n', "platform")
        self.assertEqual(len(error.findings), 2)


if __name__ == "__main__":
    unittest.main()
