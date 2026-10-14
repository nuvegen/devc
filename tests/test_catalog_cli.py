"""CLI und Use-Cases für Addon- und Template-Katalog."""

import json
import os
import stat
import sys
import unittest

from devc import config, core
from devc.errors import NotFoundError, ValidationError
from tests import helpers
from tests.helpers import run_cli, write_addon

MINE = 'id = "mine"\ntitle = "Meins"\nversion = "1.0.0"\naddons = ["claude-code"]\n\n[data]\ncode = "volume"\n\n[secrets]\n"claude-code.api_key" = "anthropic"\n'


class AddonCliTest(helpers.TempEnvTestCase):
    def test_list(self):
        write_addon(self.config_dir / "addons", "python", {"version": "9.0.0"})
        write_addon(self.config_dir / "addons", "kaputt", {"version": "x"})
        code, out, err = run_cli("addon", "list")
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "VERSION", "HERKUNFT", "TITEL"])
        self.assertRegex(out, r"(?m)^python\s+9\.0\.0\s+eigen \(überschreibt mitgeliefert\)\s+Python$")
        self.assertRegex(out, r"(?m)^base\s+1\.1\.0\s+mitgeliefert\s+Grundsystem$")
        self.assertIn("fehlerhaft: kaputt", out)
        data = json.loads(run_cli("addon", "list", "--json")[1])
        self.assertEqual(len(data["addons"]), 12)
        self.assertEqual(data["broken"][0]["id"], "kaputt")

    def test_show(self):
        code, out, err = run_cli("addon", "show", "httpserver")
        self.assertEqual(code, 0)
        self.assertIn("Aktion httpserver.start: Server starten", out)
        self.assertIn("python3 -m http.server", out)  # README
        data = json.loads(run_cli("addon", "show", "abap", "--json")[1])
        self.assertEqual(data["archives"], [{"src": "files/faq.tar.gz", "dest": "/FAQ"}])
        self.assertIn("sapcli", data["readme"])
        self.assertEqual(run_cli("addon", "show", "fehlt")[0], 4)

    def test_show_broken_addon(self):
        write_addon(self.config_dir / "addons", "kaputt", {"version": "x"})
        with self.assertRaises(ValidationError):
            core.addon_show(config.load(), "kaputt")

    def test_validate(self):
        for directory in sorted((helpers.EXAMPLES / "addons").iterdir()):
            with self.subTest(addon=directory.name):
                self.assertEqual(run_cli("addon", "validate", str(directory)), (0, "", ""))
        bad = write_addon(self.tmp / "x", "demo", {"version": "1", "requires": "base"})
        code, out, err = run_cli("addon", "validate", str(bad))
        self.assertEqual((code, out), (5, ""))
        self.assertEqual(sorted(err.splitlines()), [
            "L3 requires: requires muss eine Liste gültiger IDs sein",
            "L3 version: version muss eine Version x.y.z sein",
        ])
        code, out, err = run_cli("addon", "validate", str(bad), "--json")
        error = json.loads(out)
        self.assertEqual((code, error["error"], error["details"]["check"]), (5, "E_VALIDATION", "L3"))
        self.assertEqual(run_cli("addon", "validate", str(self.tmp / "fehlt"))[0], 4)


class TemplateCliTest(helpers.TempEnvTestCase):
    def fake_editor(self, body: str) -> None:
        script = self.tmp / "editor"
        script.write_text(f"#!{sys.executable}\nimport sys\npath = sys.argv[-1]\ntext = open(path).read()\n{body}\nopen(path, 'w').write(text)\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        os.environ["EDITOR"] = str(script)
        os.environ.pop("VISUAL", None)
        self.addCleanup(os.environ.pop, "EDITOR", None)

    def test_list_and_show(self):
        (self.config_dir / "templates").mkdir(parents=True)
        (self.config_dir / "templates" / "mine.toml").write_text(MINE)
        code, out, err = run_cli("template", "list")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"(?m)^mine\s+1\.0\.0\s+eigen\s+Meins$")
        self.assertRegex(out, r"(?m)^pydev\s+1\.0\.0\s+mitgeliefert\s+Python-Tools$")
        self.assertEqual(run_cli("template", "show", "mine"), (0, MINE, ""))
        shown = json.loads(run_cli("template", "show", "pydev", "--json")[1])
        self.assertEqual(shown["meta"]["addons"], ["claude-code", "python", "httpserver", "webproxy", "git"])
        self.assertTrue(shown["text"].startswith("# Mitgeliefertes Template"))
        self.assertEqual(run_cli("template", "show", "fehlt")[0], 4)

    def test_validate_acceptance(self):
        """abapdev meldet nur W1, sonst nichts."""
        code, out, err = run_cli("template", "validate", str(helpers.EXAMPLES / "templates" / "abapdev.toml"))
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(line.startswith("W1 secrets.") for line in lines))

    def test_validate_with_store_entries_is_silent(self):
        shared = self.config_dir / "secrets" / "_shared"
        shared.mkdir(parents=True)
        for name in ("anthropic", "s4dev", "git-deploy"):
            (shared / name).write_text("geheim-4711")
        result = run_cli("template", "validate", str(helpers.EXAMPLES / "templates" / "abapdev.toml"))
        self.assertEqual(result, (0, "", ""))
        self.assertEqual(core.store_names(config.load()), {"anthropic", "s4dev", "git-deploy"})

    def test_validate_errors(self):
        file = self.tmp / "mine.toml"
        file.write_text(MINE.replace('"claude-code"', '"abap"').replace("claude-code.api_key", "abap.sap_password"))
        code, out, err = run_cli("template", "validate", str(file))
        self.assertEqual((code, out), (5, ""))
        self.assertIn("L5 addons: Addon abap benötigt node\n", err)
        other = self.tmp / "anders.toml"
        other.write_text(MINE)
        self.assertIn("L9 id: ", run_cli("template", "validate", str(other))[2])
        self.assertEqual(run_cli("template", "validate", str(self.tmp / "fehlt.toml"))[0], 4)
        (self.tmp / "x.txt").write_text(MINE)
        self.assertEqual(run_cli("template", "validate", str(self.tmp / "x.txt"))[0], 2)

    def test_put_stores_text_with_comments(self):
        text = "# mein Kommentar\n" + MINE
        result = core.template_put(config.load(), "mine", text)
        self.assertEqual([f["check"] for f in result["findings"]], ["W1"])
        self.assertEqual((self.config_dir / "templates" / "mine.toml").read_text(), text)

    def test_put_rejects_invalid_and_changes_nothing(self):
        core.template_put(config.load(), "mine", MINE)
        for template_id, text in (("mine", MINE.replace('"1.0.0"', '"1"')), ("anders", MINE), ("../x", MINE), ("mine", "id = ")):
            with self.subTest(template_id=template_id):
                with self.assertRaises(ValidationError):
                    core.template_put(config.load(), template_id, text)
        self.assertEqual((self.config_dir / "templates" / "mine.toml").read_text(), MINE)
        self.assertEqual([p.name for p in (self.config_dir / "templates").iterdir()], ["mine.toml"])

    def test_edit_builtin_saves_own_copy(self):
        self.fake_editor("text = text.replace('cpu = 4', 'cpu = 8')")
        code, out, err = run_cli("template", "edit", "pydev")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("W1 ", out)
        own = (self.config_dir / "templates" / "pydev.toml").read_text()
        self.assertIn("cpu = 8", own)
        self.assertIn("# Mitgeliefertes Template", own)
        self.assertEqual(json.loads(run_cli("template", "show", "pydev", "--json")[1])["meta"]["origin"], "override")

    def test_edit_invalid_result_is_not_saved(self):
        self.fake_editor("text = text.replace('version = \"1.0.0\"', 'version = \"kaputt\"')")
        code, out, err = run_cli("template", "edit", "pydev")
        self.assertEqual(code, 5)
        self.assertIn("L9 version: ", err)
        self.assertFalse((self.config_dir / "templates" / "pydev.toml").exists())

    def test_edit_failing_editor(self):
        os.environ["EDITOR"] = "false"
        self.addCleanup(os.environ.pop, "EDITOR", None)
        os.environ.pop("VISUAL", None)
        self.assertEqual(run_cli("template", "edit", "pydev")[0], 2)
        self.assertEqual(run_cli("template", "edit", "fehlt")[0], 4)


class DraftUseCaseTest(helpers.TempEnvTestCase):
    def test_from_template(self):
        result = core.draft(config.load(), "py-tools", "pydev")
        self.assertIn('name = "py-tools"\ntemplate = "pydev@1.0.0"\nplatform = "lxd"\n', result["text"])
        self.assertEqual([f["check"] for f in result["findings"]], ["W1"])

    def test_without_template_uses_defaults(self):
        self.write_config('[defaults]\ncpu = 2\naddons = ["python"]\n')
        result = core.draft(config.load(), "py-tools")
        self.assertIn('addons = ["python"]', result["text"])
        self.assertIn("cpu = 2", result["text"])
        self.assertEqual(result["findings"], [])

    def test_errors(self):
        with self.assertRaises(ValidationError) as ctx:
            core.draft(config.load(), "Py_Tools", "pydev")
        self.assertEqual(ctx.exception.details["check"], "L2")
        with self.assertRaises(NotFoundError):
            core.draft(config.load(), "py-tools", "fehlt")


if __name__ == "__main__":
    unittest.main()
