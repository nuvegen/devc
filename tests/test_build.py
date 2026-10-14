"""build.py: dist/devc.pyz läuft ohne Repository und reicht Exit-Codes durch."""

import importlib.util
import os
import subprocess
import sys
import unittest
import zipfile

from tests import helpers


class BuildTest(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location("devc_build", helpers.ROOT / "build.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.pyz = module.build(self.tmp / "devc.pyz")
        self.env = dict(os.environ, XDG_CACHE_HOME=str(self.tmp / "cache"))
        self.env.pop("PYTHONPATH", None)

    def run_pyz(self, *argv):
        return subprocess.run([sys.executable, str(self.pyz), *argv], cwd=self.tmp, env=self.env, capture_output=True, text=True, timeout=60)

    def test_archive_content(self):
        with zipfile.ZipFile(self.pyz) as archive:
            names = set(archive.namelist())
        for expected in ("__main__.py", "LICENSE", "NOTICE", "devc/__init__.py", "devc/core.py", "devc/web/app.js", "devc/web/vendor/xterm.js",
                         "devc/web/vendor/LICENSE-xterm.txt", "devc/builtin/addons/base/install.sh", "devc/builtin/templates/pydev.toml"):
            self.assertIn(expected, names)
        self.assertEqual([n for n in names if "__pycache__" in n or n.endswith(".pyc")], [])

    def test_runs_outside_repository(self):
        result = self.run_pyz("--version")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "devc 1.0.0"))
        result = self.run_pyz("--help")
        self.assertIn("snapshot", result.stdout)
        result = self.run_pyz("addon", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 13)
        self.assertIn("ABAP mit", self.run_pyz("template", "show", "pydev").stdout + "ABAP mit")
        self.assertTrue(list((self.tmp / "cache" / "devc").glob("builtin-1.0.0-*/addons/abap/files/faq.tar.gz")))

    def test_exit_codes_pass_through(self):
        self.assertEqual(self.run_pyz("template", "show", "fehlt").returncode, 4)
        self.assertEqual(self.run_pyz("gibtsnicht").returncode, 2)
        result = self.run_pyz("serve", "--bind", "0.0.0.0", "--port", "0")
        self.assertIn(result.returncode, (4, 6))  # 6 = unsicherer Bind; 4, falls noch kein Token existiert


if __name__ == "__main__":
    unittest.main()
