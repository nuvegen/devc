"""T-L10, T-L11, T-L12, T-L15, Zusammenführen (7.5) und Unterschiede."""

import tomllib
import unittest

from devc import addons, instance
from devc.errors import SecurityError, UsageError
from tests import helpers
from tests.helpers import write_addon

MINIMAL = 'name = "py-tools"\n\n[data]\ncode = "volume"\n'
DEFAULTS = {"image": "ubuntu:24.04", "cpu": 4, "memory": "8GiB", "disk": "30GiB", "addons": ["claude-code"]}


class InstanceTestCase(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.catalog = addons.catalog(self.tmp / "leer")

    def check(self, text, stores=None):
        return instance.check_text(text, self.catalog, stores)[1]

    def assert_ok(self, text):
        self.assertEqual([f.as_line() for f in self.check(text)], [])

    def assert_finding(self, check, path, text):
        findings = self.check(text)
        self.assertIn((check, path), [(f.check, f.path) for f in findings], [f.as_line() for f in findings])


class L10Test(InstanceTestCase):
    def test_minimal_and_example(self):
        self.assert_ok(MINIMAL)
        self.assert_ok((helpers.EXAMPLES / "instance" / "py-tools.devc.toml").read_text(encoding="utf-8"))
        self.assert_ok('template = "pydev@1.0.0"\nplatform = "lxd"\n' + MINIMAL)

    def test_name(self):
        self.assert_finding("L10", "name", '[data]\ncode = "volume"\n')
        self.assert_finding("L2", "name", MINIMAL.replace("py-tools", "Py_Tools"))
        self.assert_finding("L2", "name", MINIMAL.replace('"py-tools"', "5"))

    def test_platform_and_template(self):
        self.assert_finding("L10", "platform", 'platform = "apple"\n' + MINIMAL)
        self.assert_finding("L10", "template", 'template = "pydev"\n' + MINIMAL)
        self.assert_ok('template = ""\n' + MINIMAL)

    def test_template_head_keys_are_forbidden(self):
        for line in ('id = "x1"', 'title = "T"', 'version = "1.0.0"', 'description = "d"', 'platforms = ["lxd"]'):
            with self.subTest(line=line):
                findings = self.check(line + "\n" + MINIMAL)
                key = line.split(" ")[0]
                self.assertEqual([(f.check, f.path) for f in findings], [("L10", key)])

    def test_unknown_key(self):
        self.assert_finding("L10", "nmae", 'nmae = "x"\n' + MINIMAL)

    def test_leftover_placeholders(self):
        self.assert_finding("L10", "git.remote", MINIMAL + '\n[git]\nremote = "ssh://git@host/${name}.git"\n')
        self.assert_finding("L10", "files[0].content", MINIMAL + '\n[[files]]\ndest = "/work/a"\ncontent = "in ${workdir}"\n')
        self.assert_finding("L10", "docs.ai", MINIMAL + '\n[docs]\nai = "x ${y}"\n')

    def test_common_fields_use_l10(self):
        self.assert_finding("L10", "addons", 'addons = ["gibtsnicht"]\n' + MINIMAL)
        self.assert_finding("L10", "resources.cpu", MINIMAL + "\n[resources]\ncpu = 0\n")
        self.assert_finding("L10", "data", 'name = "ab"\n')
        self.assert_finding("L5", "addons", 'addons = ["abap"]\n' + MINIMAL)

    def test_required_params(self):
        text = 'addons = ["node", "abap"]\n' + MINIMAL
        findings = self.check(text)
        self.assertEqual(
            sorted(f.path for f in findings if f.check == "L10"),
            ["params.abap.sap_client", "params.abap.sap_host", "params.abap.sap_user"],
        )
        self.assert_ok(text + '\n[params.abap]\nsap_host = "h"\nsap_client = "100"\nsap_user = "u"\n')
        self.assert_finding("L10", "params.abap.sap_client", text + '\n[params.abap]\nsap_host = "h"\nsap_client = "1000"\nsap_user = "u"\n')


class L15Test(InstanceTestCase):
    def test_addon_without_platform(self):
        root = self.tmp / "cat"
        write_addon(root, "base")
        write_addon(root, "maconly", {"platforms": ["apple"]})
        self.catalog = addons.catalog(self.tmp / "leer", root)
        self.assert_finding("L15", "addons", 'addons = ["maconly"]\n' + MINIMAL)
        self.assert_ok(MINIMAL)


class L11Test(InstanceTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "srv"
        (self.root / "py-tools").mkdir(parents=True)
        (self.tmp / "fremd").mkdir()

    def data(self, shared):
        return {"name": "py-tools", "data": {"shared": str(shared)}}

    def test_inside_allowed_root(self):
        self.assertEqual(instance.check_shared(self.data(self.root / "py-tools"), [str(self.root)]), [])
        self.assertEqual(instance.check_shared({"data": {"code": "volume"}}, []), [])

    def test_outside_is_security_error(self):
        for roots in ([str(self.root)], []):
            with self.subTest(roots=roots):
                with self.assertRaises(SecurityError) as ctx:
                    instance.check_shared(self.data(self.tmp / "fremd"), roots)
                self.assertEqual((ctx.exception.code, ctx.exception.details["check"]), ("E_SECURITY", "L11"))

    def test_symlink_and_dotdot_escape(self):
        (self.root / "link").symlink_to(self.tmp / "fremd")
        for shared in (self.root / "link", f"{self.root}/py-tools/../../fremd"):
            with self.subTest(shared=shared):
                with self.assertRaises(SecurityError):
                    instance.check_shared(self.data(shared), [str(self.root)])

    def test_sibling_prefix_is_outside(self):
        (self.tmp / "srv2").mkdir()
        with self.assertRaises(SecurityError):
            instance.check_shared(self.data(self.tmp / "srv2"), [str(self.root)])

    def test_missing_or_relative(self):
        for shared in (self.root / "fehlt", "relativ"):
            with self.subTest(shared=shared):
                findings = instance.check_shared(self.data(shared), [str(self.root)])
                self.assertEqual([(f.check, f.path) for f in findings], [("L11", "data.shared")])


class L12Test(InstanceTestCase):
    def data(self, extra=""):
        text = 'addons = ["claude-code", "node", "abap"]\n' + MINIMAL + extra
        return tomllib.loads(text)

    def test_optional_secrets_are_not_missing(self):
        data = self.data()
        self.assertEqual(sorted(instance.secret_slots(data, self.catalog)), ["abap.sap_password", "claude-code.api_key", "claude-code.auth_token", "claude-code.oauth_token"])
        self.assertEqual(instance.missing_secrets(data, self.catalog, set()), [])

    def test_named_slots_need_store_entry_or_value(self):
        data = self.data('\n[secrets]\n"claude-code.api_key" = "anthropic"\n"git.key" = "git-deploy"\n')
        self.assertEqual(instance.missing_secrets(data, self.catalog, set()), ["claude-code.api_key", "git.key"])
        self.assertEqual(instance.missing_secrets(data, self.catalog, {"anthropic"}), ["git.key"])
        self.assertEqual(instance.missing_secrets(data, self.catalog, {"anthropic"}, provided={"git.key"}), [])

    def test_required_secret_param(self):
        root = self.tmp / "cat"
        write_addon(root, "base")
        write_addon(root, "vault", {"params": [{"name": "token", "type": "secret", "required": True}]})
        catalog = addons.catalog(self.tmp / "leer", root)
        data = tomllib.loads('addons = ["vault"]\n' + MINIMAL)
        self.assertEqual(instance.missing_secrets(data, catalog, set()), ["vault.token"])
        self.assertEqual(instance.missing_secrets(data, catalog, set(), provided={"vault.token"}), [])


class MergeTest(InstanceTestCase):
    """SPEC 7.5."""

    def test_no_options_keeps_text(self):
        text = "# Kommentar\n" + MINIMAL
        self.assertEqual(instance.merge(text, self.catalog), text)

    def test_options_win_over_template(self):
        text = '# Entwurf aus pydev@1.0.0 – gilt nur für diese Instanz\naddons = ["claude-code", "python"]\n' + MINIMAL + '\n[params.python]\nversion = "3.12"\n'
        merged = instance.merge(
            text, self.catalog,
            addons=["httpserver", "python"], params=["python.version=3.13", "httpserver.port=9000", "base.sudo=true"],
            secret_stores={"claude-code.api_key": "anthropic"}, shared="/srv/dev/x", git="ssh://git@host/x.git",
            image="ubuntu:22.04", cpu=2, memory="4GiB",
        )
        self.assertTrue(merged.startswith("# Entwurf aus pydev@1.0.0"))
        data = tomllib.loads(merged)
        self.assertEqual(data["addons"], ["claude-code", "python", "httpserver"])
        self.assertEqual(data["params"], {"python": {"version": "3.13"}, "httpserver": {"port": 9000}, "base": {"sudo": True}})
        self.assertEqual(data["secrets"], {"claude-code.api_key": "anthropic"})
        self.assertEqual(data["data"], {"shared": "/srv/dev/x"})
        self.assertEqual(data["git"], {"remote": "ssh://git@host/x.git"})
        self.assertEqual((data["image"], data["resources"]), ("ubuntu:22.04", {"cpu": 2, "memory": "4GiB"}))
        self.assertEqual([f.as_line() for f in instance.check_data(data, self.catalog)], [])

    def test_bad_params(self):
        for spec in ("python", "python.version", "python.fehlt=1", "fehlt.x=1", "httpserver.port=viele", "base.sudo=ja", "claude-code.api_key=sk", "=x"):
            with self.subTest(spec=spec):
                with self.assertRaises(UsageError):
                    instance.merge(MINIMAL, self.catalog, params=[spec])


class DiffTest(InstanceTestCase):
    def diff(self, old, new):
        return instance.diff(tomllib.loads(old), tomllib.loads(new), self.catalog, DEFAULTS)

    def test_no_change(self):
        text = 'addons = ["python"]\n' + MINIMAL
        result = self.diff(text, text + '\n[params.python]\nversion = "3.12"\n')  # Default ausgeschrieben
        self.assertFalse(result["changed"])

    def test_changes(self):
        old = 'addons = ["python", "node"]\n' + MINIMAL + '\n[params.python]\nversion = "3.12"\n'
        new = 'addons = ["python", "httpserver"]\n' + MINIMAL + '\n[params.python]\nversion = "3.13"\n\n[resources]\ncpu = 2\n\n[git]\nremote = "https://h/x.git"\n\n[docs]\nai = "x"\n\n[[files]]\ndest = "/work/a"\ncontent = "a"\n\n[secrets]\n"git.key" = "k"\n'
        result = self.diff(old, new)
        self.assertEqual(result["addons_added"], ["httpserver"])
        self.assertEqual(result["addons_removed"], ["node"])
        self.assertEqual(result["params_changed"], ["python"])
        for key in ("resources", "git", "docs", "files", "secrets", "changed"):
            self.assertTrue(result[key], key)
        self.assertFalse(result["data"])
        self.assertFalse(result["image"])

    def test_data_change(self):
        result = self.diff(MINIMAL, 'name = "py-tools"\n[data]\nshared = "/srv/dev/x"\n')
        self.assertTrue(result["data"])


if __name__ == "__main__":
    unittest.main()
