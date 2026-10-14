"""T-L9, W1 und T-Draft (SPEC 6.3, 7.4)."""

import tomllib
import unittest

from devc import addons, instance, templates
from devc.errors import ValidationError
from tests import helpers

MINIMAL = '''id = "demo"
title = "Demo"
version = "1.0.0"

[data]
code = "volume"
'''


class TemplateTestCase(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.catalog = addons.catalog(self.tmp / "leer")

    def check(self, text, expected_id="demo", stores=None):
        return templates.check_text(text, expected_id, self.catalog, stores)[1]

    def assert_ok(self, text, **kwargs):
        self.assertEqual([f.as_line() for f in self.check(text, **kwargs)], [])

    def assert_finding(self, check, path, text, **kwargs):
        findings = self.check(text, **kwargs)
        self.assertIn((check, path), [(f.check, f.path) for f in findings], [f.as_line() for f in findings])


class L9Test(TemplateTestCase):
    def test_minimal(self):
        self.assert_ok(MINIMAL)

    def test_examples(self):
        for file in sorted((helpers.EXAMPLES / "templates").glob("*.toml")):
            with self.subTest(template=file.name):
                self.assert_ok(file.read_text(encoding="utf-8"), expected_id=file.stem)

    def test_invalid_toml(self):
        with self.assertRaises(ValidationError) as ctx:
            self.check('id = "demo"\ntitle = \n')
        self.assertEqual((ctx.exception.details["check"], ctx.exception.details["line"]), ("L9", 2))

    def test_head(self):
        cases = [
            (MINIMAL.replace('title = "Demo"\n', ""), "L9", "title"),
            (MINIMAL.replace('version = "1.0.0"\n', ""), "L9", "version"),
            (MINIMAL.replace('id = "demo"\n', ""), "L9", "id"),
            (MINIMAL.replace('"1.0.0"', '"1.0"'), "L9", "version"),
            (MINIMAL.replace('id = "demo"', 'id = "anders"'), "L9", "id"),
            (MINIMAL.replace('id = "demo"', 'id = "Demo!"'), "L2", "id"),
            ('unbekannt = 1\n' + MINIMAL, "L9", "unbekannt"),
            ('name = "x"\n' + MINIMAL, "L9", "name"),
            ('platforms = ["docker"]\n' + MINIMAL, "L9", "platforms"),
        ]
        for text, check, path in cases:
            with self.subTest(path=path, check=check):
                self.assert_finding(check, path, text)

    def test_head_keys_must_be_single_line(self):
        multi = MINIMAL.replace('title = "Demo"', 'title = """Demo"""').replace("Demo\"\"\"", "De\\\nmo\"\"\"")
        self.assertEqual(tomllib.loads(multi)["title"], "Demo")
        self.assert_finding("L9", "title", multi)
        array = 'platforms = [\n  "lxd",\n]\n' + MINIMAL
        self.assert_finding("L9", "platforms", array)
        self.assert_ok('platforms = ["lxd"]  # Kommentar\n' + MINIMAL)
        self.assert_ok(MINIMAL.replace('title = "Demo"', "  title   =   'Demo'  "))

    def test_addons(self):
        self.assert_finding("L9", "addons", 'addons = ["gibtsnicht"]\n' + MINIMAL)
        self.assert_finding("L9", "addons", 'addons = "python"\n' + MINIMAL)
        self.assert_finding("L5", "addons", 'addons = ["abap"]\n' + MINIMAL)
        self.assert_ok('addons = ["node", "abap"]\n' + MINIMAL)

    def test_resources_and_image(self):
        self.assert_ok(MINIMAL + '\n[resources]\ncpu = 2\nmemory = "512MiB"\ndisk = "1TiB"\n')
        for body, path in [
            ("[resources]\ncpu = 0\n", "resources.cpu"),
            ('[resources]\nmemory = "8GB"\n', "resources.memory"),
            ("[resources]\ndisk = 30\n", "resources.disk"),
            ("[resources]\nram = 1\n", "resources.ram"),
        ]:
            with self.subTest(path=path):
                self.assert_finding("L9", path, MINIMAL + "\n" + body)
        self.assert_finding("L9", "image", 'image = ""\n' + MINIMAL)

    def test_data(self):
        head = 'id = "demo"\ntitle = "Demo"\nversion = "1.0.0"\n'
        self.assert_ok(head + '[data]\nshared = "/srv/dev/${name}"\n')
        for body in ("", "[data]\n", '[data]\ncode = "volume"\nshared = "/srv/x"\n', 'data = "volume"\n'):
            with self.subTest(body=body):
                self.assert_finding("L9", "data", head + body)
        self.assert_finding("L9", "data.code", head + '[data]\ncode = "zfs"\n')
        self.assert_finding("L9", "data.shared", head + '[data]\nshared = "relativ"\n')
        self.assert_finding("L9", "data.extra", head + '[data]\ncode = "volume"\nextra = 1\n')

    def test_params(self):
        base = 'addons = ["python", "claude-code"]\n' + MINIMAL
        self.assert_ok(base + '\n[params.python]\nversion = "3.13"\n\n[params.base]\nsudo = true\n')
        cases = [
            ('[params.python]\nversion = "2.7"\n', "params.python.version"),
            ('[params.python]\nversion = 3\n', "params.python.version"),
            ("[params.python]\nfehlt = 1\n", "params.python.fehlt"),
            ("[params.node]\nversion = \"22\"\n", "params.node"),
            ('[params.claude-code]\napi_key = "sk-geheim"\n', "params.claude-code.api_key"),
            ('[params]\npython = "3.12"\n', "params.python"),
            ("[params.base]\nsudo = \"ja\"\n", "params.base.sudo"),
        ]
        for body, path in cases:
            with self.subTest(path=path):
                self.assert_finding("L9", path, base + "\n" + body)

    def test_secrets(self):
        base = 'addons = ["claude-code"]\n' + MINIMAL
        self.assert_ok(base + '\n[secrets]\n"claude-code.api_key" = "anthropic"\n"git.key" = "git-deploy"\n')
        cases = [
            ('"claude-code.channel" = "anthropic"', 'secrets."claude-code.channel"'),
            ('"abap.sap_password" = "s4dev"', 'secrets."abap.sap_password"'),
            ('"claude-code.fehlt" = "x"', 'secrets."claude-code.fehlt"'),
            ('"kaputt" = "x"', 'secrets."kaputt"'),
            ('"claude-code.api_key" = "Kein Name!"', 'secrets."claude-code.api_key"'),
            ('"claude-code.api_key" = 5', 'secrets."claude-code.api_key"'),
        ]
        for line, path in cases:
            with self.subTest(line=line):
                self.assert_finding("L9", path, base + "\n[secrets]\n" + line + "\n")

    def test_git_files_docs(self):
        for remote in ("ssh://git@host/x.git", "https://host/x.git", "git@host:team/x.git", "ssh://git@[GIT-HOST]/${name}.git", ""):
            with self.subTest(remote=remote):
                self.assert_ok(MINIMAL + f'\n[git]\nremote = "{remote}"\n')
        for remote in ("file:///etc", "http://host/x.git", "ext::sh -c id", "--upload-pack=x"):
            with self.subTest(remote=remote):
                self.assert_finding("L9", "git.remote", MINIMAL + f'\n[git]\nremote = "{remote}"\n')
        self.assert_finding("L9", "git.branch", MINIMAL + '\n[git]\nbranch = "main"\n')

        self.assert_ok(MINIMAL + '\n[[files]]\ndest = "/home/dev/.x"\ncontent = "a"\nmode = "0600"\noverwrite = false\n')
        for dest in ("/etc/passwd", "/work", "/work/", "/work/../etc/x", "relativ", "/home/devx/a"):
            with self.subTest(dest=dest):
                self.assert_finding("L9", "files[0].dest", MINIMAL + f'\n[[files]]\ndest = "{dest}"\ncontent = "a"\n')
        self.assert_finding("L9", "files[0].content", MINIMAL + '\n[[files]]\ndest = "/work/a"\n')
        self.assert_finding("L9", "files[0].owner", MINIMAL + '\n[[files]]\ndest = "/work/a"\ncontent = ""\nowner = "root"\n')
        self.assert_finding("L9", "docs.readme", MINIMAL + "\n[docs]\nreadme = 1\n")
        self.assert_finding("L9", "docs.extra", MINIMAL + '\n[docs]\nextra = "x"\n')


class W1Test(TemplateTestCase):
    TEXT = 'addons = ["claude-code"]\n' + MINIMAL + '\n[secrets]\n"claude-code.api_key" = "anthropic"\n'

    def test_missing_store_entry_is_warning(self):
        findings = self.check(self.TEXT, stores=set())
        self.assertEqual([(f.check, f.severity) for f in findings], [("W1", "warning")])
        self.assertIn("anthropic", findings[0].message)

    def test_existing_store_entry(self):
        self.assertEqual(self.check(self.TEXT, stores={"anthropic"}), [])

    def test_abapdev_reports_only_w1(self):
        text = (helpers.EXAMPLES / "templates" / "abapdev.toml").read_text(encoding="utf-8")
        findings = self.check(text, "abapdev", stores=set())
        self.assertEqual([f.check for f in findings], ["W1", "W1", "W1"])


class DraftTest(TemplateTestCase):
    """T-Draft."""

    def test_every_example_gives_valid_instance(self):
        for file in sorted((helpers.EXAMPLES / "templates").glob("*.toml")):
            with self.subTest(template=file.name):
                text = templates.draft(file.read_text(encoding="utf-8"), "abap-rap", self.catalog, file.stem)
                data, findings = instance.check_text(text, self.catalog)
                self.assertEqual([f.as_line() for f in findings], [])
                self.assertEqual((data["name"], data["template"], data["platform"]), ("abap-rap", f"{file.stem}@1.0.0", "lxd"))
                for key in templates.HEAD_KEYS:
                    self.assertNotIn(key, data)
                self.assertNotIn("${", text)

    def test_matches_example_instance_structure(self):
        text = templates.draft((helpers.EXAMPLES / "templates" / "pydev.toml").read_text(encoding="utf-8"), "py-tools", self.catalog)
        self.assertEqual(text.splitlines()[:5], [
            "# Entwurf aus pydev@1.0.0 – gilt nur für diese Instanz",
            'name = "py-tools"',
            'template = "pydev@1.0.0"',
            'platform = "lxd"',
            "# Mitgeliefertes Template: Python-Tools mit Claude Code und HTTP-Server",
        ])
        expected = tomllib.loads((helpers.EXAMPLES / "instance" / "py-tools.devc.toml").read_text(encoding="utf-8"))
        expected["data"] = {"code": "volume"}  # im Beispiel von Hand auf Shared Folder geändert
        self.assertEqual(tomllib.loads(text), expected)

    def test_comments_stay_and_placeholders_are_replaced(self):
        text = templates.draft((helpers.EXAMPLES / "templates" / "abapdev.toml").read_text(encoding="utf-8"), "abap-rap", self.catalog)
        self.assertIn("# Mitgeliefertes Template: ABAP-Entwicklung", text)
        self.assertIn('code = "volume"              # oder: shared = "/srv/dev/abap-rap"', text)
        self.assertIn("[secrets]                    # nur Namen im geteilten Store, nie Werte", text)
        self.assertEqual(tomllib.loads(text)["git"]["remote"], "ssh://git@[GIT-HOST]/abap-rap.git")

    def test_workdir_placeholder(self):
        source = MINIMAL + '\n[[files]]\ndest = "/work/README.md"\ncontent = "Projekt ${name} in ${workdir}"\n'
        data = tomllib.loads(templates.draft(source, "ab", self.catalog))
        self.assertEqual(data["files"], [{"dest": "/work/README.md", "content": "Projekt ab in /work"}])

    def test_only_head_lines_before_first_table_are_removed(self):
        source = MINIMAL + '\n[docs]\nreadme = """\ntitle = "bleibt"\nid = "bleibt"\n"""\n'
        data = tomllib.loads(templates.draft(source, "ab", self.catalog))
        self.assertEqual(data["docs"]["readme"], 'title = "bleibt"\nid = "bleibt"\n')

    def test_invalid_template_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            templates.draft(MINIMAL.replace('"1.0.0"', '"1"'), "ab", self.catalog)
        self.assertEqual(ctx.exception.details["check"], "L9")

    def test_blank_draft(self):
        defaults = {"image": "ubuntu:24.04", "cpu": 4, "memory": "8GiB", "disk": "30GiB", "addons": ["claude-code"]}
        text = templates.blank_draft("ab", defaults)
        data, findings = instance.check_text(text, self.catalog)
        self.assertEqual(findings, [])
        self.assertEqual(data, {
            "name": "ab", "platform": "lxd", "image": "ubuntu:24.04", "addons": ["claude-code"],
            "resources": {"cpu": 4, "memory": "8GiB", "disk": "30GiB"}, "data": {"code": "volume"},
        })


class LocateTest(helpers.TempEnvTestCase):
    def test_own_overrides_builtin(self):
        own = self.tmp / "own"
        own.mkdir()
        (own / "pydev.toml").write_text(MINIMAL.replace("demo", "pydev"))
        (own / "mine.toml").write_text(MINIMAL.replace("demo", "mine"))
        found = templates.locate(own)
        self.assertEqual({key: origin for key, (_, origin) in found.items()}, {"abapdev": "builtin", "pydev": "override", "webdev": "builtin", "mine": "own"})
        self.assertEqual(templates.read("pydev", *found["pydev"]).title, "Demo")


if __name__ == "__main__":
    unittest.main()
