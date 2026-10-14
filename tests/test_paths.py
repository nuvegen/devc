"""Host-Pfade aus SPEC 5.2."""

import unittest
from pathlib import Path

from devc import paths
from tests import helpers


class PathsTest(helpers.TempEnvTestCase):
    def test_xdg(self):
        p = paths.resolve()
        self.assertEqual(p.config_file, self.config_dir / "config.toml")
        self.assertEqual(p.token, self.config_dir / "token")
        self.assertEqual(p.addons, self.config_dir / "addons")
        self.assertEqual(p.templates, self.config_dir / "templates")
        self.assertEqual(p.shared_secrets, self.config_dir / "secrets" / "_shared")
        self.assertEqual(p.container_secrets("py-tools"), self.config_dir / "secrets" / "py-tools")
        self.assertEqual(p.db, self.data_dir / "devc.sqlite")
        self.assertEqual(p.instance_file("py-tools"), self.data_dir / "containers" / "py-tools" / "devc.toml")
        self.assertEqual(p.job_log("j-4f1a09c2b7de"), self.data_dir / "logs" / "j-4f1a09c2b7de.log")
        self.assertEqual(p.snapshot_dir("py-tools", "s1"), self.data_dir / "snapshots" / "py-tools" / "s1")
        self.assertEqual(p.backups, self.data_dir / "backups")
        self.assertEqual(p.lock_file("py-tools"), self.data_dir / "locks" / "py-tools.lock")

    def test_home_fallback(self):
        env = {"HOME": "/home/x", "XDG_CONFIG_HOME": "relativ"}
        p = paths.resolve(env=env)
        self.assertEqual(p.config_file, Path("/home/x/.config/devc/config.toml"))
        self.assertEqual(p.state, Path("/home/x/.local/share/devc"))

    def test_overrides(self):
        env = {"HOME": "/home/x"}
        p = paths.resolve("/etc/devc.toml", {"state": "~/zustand", "addons": "/srv/addons"}, env)
        self.assertEqual(p.config_file, Path("/etc/devc.toml"))
        self.assertEqual(p.state, Path("/home/x/zustand"))
        self.assertEqual(p.db, Path("/home/x/zustand/devc.sqlite"))
        self.assertEqual(p.backups, Path("/home/x/zustand/backups"))
        self.assertEqual(p.addons, Path("/srv/addons"))
        self.assertEqual(p.templates, Path("/home/x/.config/devc/templates"))


if __name__ == "__main__":
    unittest.main()
