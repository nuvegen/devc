"""IT-E2E gegen echtes LXD (SPEC 12). Läuft nur mit DEVC_IT=1:

    DEVC_IT=1 python3 -m unittest tests.it_e2e -v
    DEVC_IT=1 DEVC_IT_PYZ=dist/devc.pyz python3 -m unittest tests.it_e2e -v   # mit dem zipapp

Legt die Container it-e2e und it-e2e2 (bzw. $DEVC_IT_NAME und $DEVC_IT_NAME2) im Projekt aus lxd.project an und räumt sie am Ende auf.
DEVC_IT_KEEP=1 lässt sie stehen; DEVC_IT_STATE=<verzeichnis> hält dann den devc-Zustand dafür fest.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# DEVC_IT_NAME ändert den Namen (immer mit Präfix it-), z. B. wenn ein früherer Lauf Container stehen ließ.
BASE = os.environ.get("DEVC_IT_NAME", "it-e2e")
assert BASE.startswith("it-"), "Testcontainer müssen mit it- beginnen"
NAMES = (BASE, BASE + "2")


@unittest.skipUnless(os.environ.get("DEVC_IT") == "1", "Integrationstest: nur mit DEVC_IT=1")
class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keep = os.environ.get("DEVC_IT_KEEP") == "1"
        state = os.environ.get("DEVC_IT_STATE")
        cls.state = Path(state) if state else Path(tempfile.mkdtemp(prefix="devc-it-"))
        cls.state.mkdir(parents=True, exist_ok=True)
        pyz = os.environ.get("DEVC_IT_PYZ")
        cls.command = [sys.executable, str(Path(pyz).resolve())] if pyz else [sys.executable, "-m", "devc"]
        # HOME bleibt: der lxc-Client braucht seine eigene Konfiguration.
        cls.env = dict(os.environ, XDG_CONFIG_HOME=str(cls.state / "config"), XDG_DATA_HOME=str(cls.state / "data"),
                       XDG_CACHE_HOME=str(cls.state / "cache"))
        cls.key = cls.state / "platzhalter-key"
        cls.key.write_text("sk-ant-platzhalter-kein-echter-key")
        cls.key.chmod(0o600)

    @classmethod
    def tearDownClass(cls):
        if cls.keep:
            print(f"\nDEVC_IT_KEEP=1: Container {', '.join(NAMES)} und Zustand {cls.state} bleiben stehen", file=sys.stderr)
            return
        for name in NAMES:
            cls.devc("rm", name, "--yes", check=False)
        if not os.environ.get("DEVC_IT_STATE"):
            shutil.rmtree(cls.state, ignore_errors=True)

    @classmethod
    def devc(cls, *argv, check=True, timeout=1800):
        result = subprocess.run([*cls.command, *argv], cwd=ROOT, env=cls.env, capture_output=True, text=True, timeout=timeout)
        if check and result.returncode != 0:
            raise AssertionError(f"devc {' '.join(argv)} endete mit {result.returncode}:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
        return result

    def json(self, *argv):
        return json.loads(self.devc(*argv, "--json").stdout)

    def test_full_cycle(self):
        for name in NAMES:
            self.assertNotIn(name, [c["name"] for c in self.json("list")["containers"]], f"{name} existiert schon")

        # create mit pydev
        self.devc("create", NAMES[0], "--template", "pydev", "--yes", "--secret", f"claude-code.api_key=@{self.key}")
        info = self.json("info", NAMES[0])
        self.assertEqual((info["status"], info["incomplete"]), ("running", False))
        self.assertIn("devc:begin", self.devc("exec", NAMES[0], "--", "cat", "/work/CLAUDE.md").stdout)
        self.assertRegex(self.devc("exec", NAMES[0], "--", "bash", "-lc", "claude --version").stdout, r"\d+\.\d+")

        # Aktion httpserver.start + HTTP-Abruf
        self.devc("action", "run", NAMES[0], "httpserver.start")
        body = ""
        for _ in range(20):
            try:
                body = urllib.request.urlopen(f"http://{info['ip']}:8000/", timeout=3).read().decode()
                break
            except OSError:
                time.sleep(0.5)
        self.assertIn("httpserver läuft", body)
        self.assertEqual(self.devc("action", "run", NAMES[0], "httpserver.start", check=False).returncode, 5)
        self.devc("action", "run", NAMES[0], "httpserver.stop")

        # Daten, Snapshot, Änderung, Zurücksetzen
        marker = self.state / "marker.txt"
        marker.write_text("stand-1\n")
        self.devc("push", NAMES[0], str(marker), "src/marker.txt")
        self.devc("snapshot", "create", NAMES[0], "--label", "stand-1")
        marker.write_text("stand-2\n")
        self.devc("push", NAMES[0], str(marker), "src/marker.txt")
        self.devc("snapshot", "restore", NAMES[0], "stand-1", "--yes")
        self.assertEqual(self.devc("exec", NAMES[0], "--", "cat", "/work/src/marker.txt").stdout, "stand-1\n")

        # backup, restore als neuer Container
        out = self.state / "backups"
        out.mkdir(exist_ok=True)
        archive = self.devc("backup", NAMES[0], "-o", str(out), "-q").stdout.strip()
        self.assertTrue(Path(archive).is_file())
        self.devc("secret", "set", "--shared", f"anthropic=@{self.key}")
        self.devc("restore", archive, "--name", NAMES[1], "--yes")
        sums = [self.devc("exec", name, "--", "sha256sum", "/work/src/marker.txt", "/work/out/index.html").stdout for name in NAMES]
        self.assertEqual(sums[0], sums[1])
        self.assertIn(f"# Dev-Container {NAMES[1]}", self.devc("exec", NAMES[1], "--", "cat", "/work/CLAUDE.md").stdout)
        self.assertEqual(sorted(c["name"] for c in self.json("list")["containers"] if c["name"] in NAMES), list(NAMES))

        # rm
        if not self.keep:
            for name in NAMES:
                self.devc("rm", name, "--yes")
            self.assertEqual([c["name"] for c in self.json("list")["containers"] if c["name"] in NAMES], [])


if __name__ == "__main__":
    unittest.main()
