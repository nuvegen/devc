"""Gemeinsame Hilfen für die Tests (SPEC 12)."""

import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "devc"
EXAMPLES = ROOT / "examples"


class TempEnvTestCase(unittest.TestCase):
    """Jeder Test bekommt ein eigenes HOME, XDG_CONFIG_HOME und XDG_DATA_HOME."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="devc-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._saved = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME")}
        self.addCleanup(self._restore_env)
        os.environ["HOME"] = str(self.tmp / "home")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "config")
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "data")
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
            Path(os.environ[key]).mkdir(parents=True)
        self.config_dir = self.tmp / "config" / "devc"
        self.data_dir = self.tmp / "data" / "devc"

    def _restore_env(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def write_config(self, text: str) -> Path:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        file = self.config_dir / "config.toml"
        file.write_text(text, encoding="utf-8")
        return file


def run_cli(*argv):
    """Ruft devc.__main__.main auf; liefert (exit, stdout, stderr)."""
    from devc.__main__ import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:  # argparse: --help, --version
            code = exc.code or 0
    return code, out.getvalue(), err.getvalue()


def write_addon(root, addon_id="demo", data=None, ai="## Demo\n", readme="Demo\n", files=None, dirname=None) -> Path:
    """Legt ein Addon-Verzeichnis an; `data` überlagert einen minimalen Kopf."""
    from devc import tomlw

    directory = Path(root) / (dirname or addon_id)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"id": addon_id, "title": addon_id.capitalize(), "version": "1.0.0"}
    manifest.update(data or {})
    manifest = {key: value for key, value in manifest.items() if value is not None}
    (directory / "addon.toml").write_text(tomlw.dumps(manifest), encoding="utf-8")
    if readme is not None:
        (directory / "README.md").write_text(readme, encoding="utf-8")
    if ai is not None:
        (directory / "AI.md").write_text(ai, encoding="utf-8")
    for name, content in (files or {}).items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return directory
