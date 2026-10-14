"""vm/: install.sh und die cloud-init-Datei."""

import base64
import gzip
import importlib.util
import re
import subprocess
import unittest
from pathlib import Path

from tests.helpers import ROOT

VM = ROOT / "vm"
spec = importlib.util.spec_from_file_location("make_user_data", VM / "make-user-data.py")
make_user_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_user_data)
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGtestschluessel nutzer@rechner"


class InstallScriptTest(unittest.TestCase):
    def setUp(self):
        self.text = (VM / "install.sh").read_text(encoding="utf-8")

    def test_syntax(self):
        result = subprocess.run(["bash", "-n", str(VM / "install.sh")], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_never_prints_the_token(self):
        for line in self.text.splitlines():
            if "token show" in line or "token rotate" in line:
                self.assertTrue(line.lstrip().startswith(("say ", "#")), line)

    def test_caddy_is_pinned_and_verified(self):
        self.assertRegex(self.text, r'CADDY_VERSION="\d+\.\d+\.\d+"')
        self.assertIn("sha512sum -c", self.text)

    def test_nothing_is_deleted_outside_temp(self):
        for line in self.text.splitlines():
            if re.search(r"\brm\b", line):
                self.assertRegex(line, r'rm -r?f "\$(tmp|new)"', line)
        for word in ("lxc delete", "storage delete", "userdel", "storage volume delete"):
            self.assertNotIn(word, self.text)


class UserDataTest(unittest.TestCase):
    def build(self, **kwargs):
        options = dict(base_domain="localhost", public_port=8443, http_port=8080, pool_size="", keys=[KEY])
        options.update(kwargs)
        return make_user_data.build((VM / "cloud-init.yaml").read_text(encoding="utf-8"), b"#!/bin/sh\necho hallo\n", b"PK\x03\x04pyz", **options)

    def test_content(self):
        text = self.build()
        self.assertTrue(text.startswith("#cloud-config\n"))
        self.assertNotRegex(text, r"@[A-Z_]+@")
        self.assertNotIn("Das ist eine Vorlage", text)
        script = re.search(r"encoding: gz\+b64\n    content: (\S+)", text).group(1)
        self.assertEqual(gzip.decompress(base64.b64decode(script)), b"#!/bin/sh\necho hallo\n")
        pyz = re.findall(r"encoding: gz\+b64\n    content: (\S+)", text)[1]
        self.assertEqual(gzip.decompress(base64.b64decode(pyz)), b"PK\x03\x04pyz")
        for line in ("DEVC_BASE_DOMAIN=localhost", "DEVC_PUBLIC_PORT=8443", "DEVC_HTTP_PORT=8080", "DEVC_POOL_DRIVER=dir", "DEVC_POOL_SIZE=", f'ssh_authorized_keys: ["{KEY}"]',
                     "  - [/opt/devc/install/install.sh]"):
            self.assertIn(line + "\n", text)

    def test_pool_driver(self):
        """dir ist der Standard; btrfs mit fester Größe bleibt wählbar."""
        text = self.build(pool_driver="btrfs", pool_size="30GiB")
        self.assertIn("DEVC_POOL_DRIVER=btrfs\n", text)
        self.assertIn("DEVC_POOL_SIZE=30GiB\n", text)
        for kwargs in (dict(pool_size="30GiB"), dict(pool_driver="zfs"), dict(pool_driver="dir", pool_size="30GiB")):
            with self.assertRaises(ValueError, msg=kwargs):
                self.build(**kwargs)
        script = (VM / "install.sh").read_text(encoding="utf-8")
        self.assertIn('DEVC_POOL_DRIVER="${env_driver:-${DEVC_POOL_DRIVER:-dir}}"', script)
        self.assertIn("driver: $DEVC_POOL_DRIVER", script)

    def test_is_reproducible(self):
        self.assertEqual(self.build(), self.build())

    def test_without_key_and_http_port(self):
        text = self.build(keys=[], http_port=None, base_domain="acme.dev.example.com", public_port=443)
        self.assertIn("ssh_authorized_keys: []\n", text)
        self.assertIn("DEVC_HTTP_PORT=\n", text)

    def test_bad_values(self):
        for kwargs in (dict(base_domain="Acme.example"), dict(base_domain="a b"), dict(base_domain="x\nrunaway: 1"), dict(base_domain="dev.example", http_port=8080),
                       dict(pool_driver="btrfs", pool_size="30G"), dict(keys=["kein schluessel"]), dict(keys=[KEY + "\nruncmd: [x]"]), dict(keys=['ssh-ed25519 AAAA" , "x'])):
            with self.assertRaises(ValueError, msg=kwargs):
                self.build(**kwargs)

    def test_cli(self):
        import contextlib, io, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pyz = f"{tmp}/devc.pyz"
            Path(pyz).write_bytes(b"PK")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(make_user_data.main(["--pyz", pyz]), 0)
                self.assertEqual(make_user_data.main(["--pyz", pyz, "--base-domain", "dev.example", "--http-port", "8080"]), 2)
            self.assertTrue(out.getvalue().startswith("#cloud-config"))
            self.assertIn("Fehler:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
