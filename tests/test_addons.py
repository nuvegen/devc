"""T-L3 bis T-L8, T-Order und Katalog-Auflösung (SPEC 6.2, 7.6, 7.7)."""

import filecmp
import os
import subprocess
import unittest

from devc import addons
from tests import helpers
from tests.helpers import write_addon


class AddonTestCase(helpers.TempEnvTestCase):
    def load(self, data=None, **kwargs):
        directory = write_addon(self.tmp / "addons", data=data, **kwargs)
        return addons.load(directory)

    def assert_ok(self, data=None, **kwargs):
        addon, findings = self.load(data, **kwargs)
        self.assertEqual([f.as_line() for f in findings], [])
        self.assertIsNotNone(addon)
        return addon

    def assert_finding(self, check, path, data=None, **kwargs):
        addon, findings = self.load(data, **kwargs)
        self.assertIsNone(addon)
        self.assertIn((check, path), [(f.check, f.path) for f in findings], [f.as_line() for f in findings])
        return findings


class ExamplesTest(AddonTestCase):
    def test_all_examples_pass(self):
        directories = sorted(p for p in (helpers.EXAMPLES / "addons").iterdir() if p.is_dir())
        self.assertEqual(len(directories), 12)
        for directory in directories:
            with self.subTest(addon=directory.name):
                addon, findings = addons.load(directory)
                self.assertEqual([f.as_line() for f in findings], [])
                self.assertEqual(addon.id, directory.name)

    def test_builtin_matches_examples(self):
        def same(a, b):
            cmp = filecmp.dircmp(a, b)
            if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
                return False
            return all(same(os.path.join(a, d), os.path.join(b, d)) for d in cmp.common_dirs)

        self.assertTrue(same(helpers.EXAMPLES / "addons", addons.BUILTIN / "addons"))
        for file in (addons.BUILTIN / "templates").glob("*.toml"):
            self.assertEqual(file.read_bytes(), (helpers.EXAMPLES / "templates" / file.name).read_bytes())

    def test_httpserver_model(self):
        addon, _ = addons.load(helpers.EXAMPLES / "addons" / "httpserver")
        self.assertEqual(addon.all_requires, ("base", "python"))
        self.assertEqual(addon.values(), {"port": 8000, "dir": "/work/out"})
        self.assertEqual(addon.values({"port": 9000}), {"port": 9000, "dir": "/work/out"})
        self.assertEqual(addon.action("start")["detach"], True)
        self.assertEqual(addon.action("stop")["stops"], "start")
        self.assertEqual(addon.action("stop")["user"], "dev")
        self.assertEqual(addon.links[0]["when"], "start")
        self.assertEqual(addon.files[0]["overwrite"], False)

    def test_abap_model(self):
        addon, _ = addons.load(helpers.EXAMPLES / "addons" / "abap")
        self.assertEqual(addon.secret_slots(), ["abap.sap_password"])
        self.assertEqual(addon.archives, ({"src": "files/faq.tar.gz", "dest": "/FAQ"},))
        self.assertNotIn("sap_password", addon.values({"sap_host": "h"}))
        self.assertEqual(addon.param("sap_client").max_length, 3)


class L3Test(AddonTestCase):
    def test_minimal_ok(self):
        addon = self.assert_ok()
        self.assertEqual((addon.platforms, addon.requires, addon.install), (("lxd", "apple"), (), ()))

    def test_missing_manifest_and_bad_toml(self):
        directory = self.tmp / "leer"
        directory.mkdir()
        self.assertEqual(addons.load(directory)[1][0].check, "L3")
        directory = write_addon(self.tmp / "addons")
        (directory / "addon.toml").write_text("id = \n")
        self.assertEqual(addons.load(directory)[1][0].check, "L3")

    def test_violations(self):
        cases = [
            ({"title": None}, "title"),
            ({"version": None}, "version"),
            ({"version": "1.0"}, "version"),
            ({"title": ""}, "title"),
            ({"title": "x" * 61}, "title"),
            ({"description": "x" * 201}, "description"),
            ({"unbekannt": 1}, "unbekannt"),
            ({"platforms": ["docker"]}, "platforms"),
            ({"requires": "base"}, "requires"),
            ({"requires": ["Base"]}, "requires"),
            ({"install": [1]}, "install"),
            ({"params": "x"}, "params"),
            ({"params": [{"name": "a"}]}, "params[0].type"),
            ({"params": [{"name": "a", "type": "float"}]}, "params[0].type"),
            ({"params": [{"name": "a", "type": "int", "extra": 1}]}, "params[0].extra"),
            ({"files": [{"src": "f"}]}, "files[0].dest"),
            ({"files": [{"src": "f", "dest": "relativ"}]}, "files[0].dest"),
            ({"files": [{"src": "f", "dest": "/x", "mode": "644"}]}, "files[0].mode"),
            ({"files": [{"src": "f", "dest": "/x", "owner": "www"}]}, "files[0].owner"),
            ({"archives": [{"src": "a.zip", "dest": "/x"}]}, "archives[0].src"),
            ({"actions": [{"id": "a", "label": "", "run": "true"}]}, "actions[0].label"),
            ({"actions": [{"id": "a", "label": "A", "run": "true", "timeout": 0}]}, "actions[0].timeout"),
            ({"actions": [{"id": "a", "label": "A", "run": "true", "user": "www"}]}, "actions[0].user"),
            ({"links": [{"label": "L"}]}, "links[0].url"),
        ]
        for data, path in cases:
            with self.subTest(path=path, data=data):
                self.assert_finding("L3", path, data, files={"f": "x", "a.zip": "x"})

    def test_id_must_match_directory(self):
        self.assert_finding("L3", "id", dirname="anders")

    def test_invalid_names_are_l2(self):
        self.assert_finding("L2", "id", addon_id="Demo_X", dirname="demo")
        self.assert_finding("L2", "params[0].name", {"params": [{"name": "Port", "type": "int"}]})
        self.assert_finding("L2", "actions[0].id", {"actions": [{"id": "a-b", "label": "A", "run": "true"}]})


class L4Test(AddonTestCase):
    def test_ok_with_files(self):
        data = {
            "install": ["install.sh"], "uninstall": ["bin/uninstall.sh"],
            "files": [{"src": "files/a.txt", "dest": "/work/a.txt"}],
        }
        self.assert_ok(data, files={"install.sh": "true\n", "bin/uninstall.sh": "true\n", "files/a.txt": "a"})

    def test_missing_readme_and_ai(self):
        self.assert_finding("L4", "README.md", readme=None)
        self.assert_finding("L4", "AI.md", addon_id="ohne-ai", ai=None)

    def test_missing_or_escaping_files(self):
        outside = self.tmp / "geheim.txt"
        outside.write_text("x")
        cases = [
            ({"install": ["fehlt.sh"]}, "install[0]"),
            ({"uninstall": ["../geheim.txt"]}, "uninstall[0]"),
            ({"install": ["/etc/passwd"]}, "install[0]"),
            ({"install": ["unterordner"]}, "install[0]"),
            ({"install": ["link.sh"]}, "install[0]"),
        ]
        for data, path in cases:
            with self.subTest(data=data):
                directory = write_addon(self.tmp / "addons", data=data)
                (directory / "unterordner").mkdir(exist_ok=True)
                if not (directory / "link.sh").is_symlink():
                    (directory / "link.sh").symlink_to(outside)
                addon, findings = addons.load(directory)
                self.assertIsNone(addon)
                self.assertIn(("L4", path), [(f.check, f.path) for f in findings])

    def test_src_outside_is_rejected(self):
        self.assert_finding("L4", "archives[0].src", {"archives": [{"src": "fehlt.tar.gz", "dest": "/FAQ"}]})
        findings = self.assert_finding("L3", "files[0].src", {"files": [{"src": "../x", "dest": "/work/x"}]})
        self.assertIn(("L4", "files[0].src"), [(f.check, f.path) for f in findings])

    def test_symlink_inside_addon_is_fine(self):
        directory = write_addon(self.tmp / "addons", data={"install": ["alias.sh"]}, files={"install.sh": "true\n"})
        (directory / "alias.sh").symlink_to("install.sh")
        self.assertIsNotNone(addons.load(directory)[0])


class L6Test(AddonTestCase):
    def params(self, *params):
        return {"params": list(params)}

    def test_ok(self):
        addon = self.assert_ok(self.params(
            {"name": "s", "type": "string", "default": "abc", "max_length": 3},
            {"name": "i", "type": "int", "default": 5, "min": 5, "max": 5},
            {"name": "b", "type": "bool", "default": False},
            {"name": "c", "type": "choice", "options": ["a", "b"], "default": "b"},
            {"name": "k", "type": "secret", "required": True},
            {"name": "n", "type": "string", "required": True},
            {"name": "p", "type": "string", "default": "ab", "pattern": "[a-z]*"},
        ))
        self.assertEqual(addon.values(), {"s": "abc", "i": 5, "b": False, "c": "b", "p": "ab"})
        self.assertIsNone(addon.param("p").problem("xyz"))
        self.assertIsNotNone(addon.param("p").problem("xyz1"))
        self.assertEqual(addon.param("p").to_dict()["pattern"], "[a-z]*")
        self.assertNotIn("pattern", addon.param("s").to_dict())
        self.assertIsNone(addon.param("i").problem(5))
        self.assertIsNotNone(addon.param("i").problem(6))
        self.assertIsNotNone(addon.param("i").problem(True))
        self.assertIsNotNone(addon.param("s").problem("abcd"))
        self.assertIsNotNone(addon.param("c").problem("z"))
        self.assertIsNotNone(addon.param("b").problem(1))
        self.assertIsNotNone(addon.param("k").problem("x"))

    def test_view_fields(self):
        addon = self.assert_ok(self.params(
            {"name": "mode", "type": "choice", "options": ["a", "b"], "option_labels": ["Erst", "Zweit"], "default": "a", "label": "Art"},
            {"name": "url", "type": "string", "default": "", "group": "Zweit", "show_if": "mode=b"},
            {"name": "key", "type": "secret", "group": "Zweit", "show_if": "mode=a|b"},
            {"name": "on", "type": "bool", "default": True},
            {"name": "x", "type": "string", "default": "", "show_if": "on=true", "hidden": True},
        ))
        self.assertEqual(addon.param("mode").to_dict()["option_labels"], ["Erst", "Zweit"])
        self.assertEqual(addon.param("mode").view(), {"label": "Art", "option_labels": ["Erst", "Zweit"]})
        self.assertEqual(addon.param("url").view(), {"group": "Zweit", "show_if": {"param": "mode", "values": ["b"]}})
        self.assertEqual(addon.param("key").view()["show_if"]["values"], ["a", "b"])
        self.assertEqual(addon.param("x").view(), {"show_if": {"param": "on", "values": ["true"]}, "hidden": True})
        self.assertEqual(addon.param("on").view(), {})

    def test_view_field_violations(self):
        mode = {"name": "mode", "type": "choice", "options": ["a", "b"], "default": "a"}
        cases = [
            ([dict(mode, option_labels=["A"])], "params[0].option_labels"),
            ([dict(mode, option_labels=["A", ""])], "params[0].option_labels"),
            ([{"name": "s", "type": "string", "option_labels": ["A"]}], "params[0].option_labels"),
            ([mode, {"name": "s", "type": "string", "show_if": "mode"}], "params[1].show_if"),
            ([mode, {"name": "s", "type": "string", "show_if": "mode=c"}], "params[1].show_if"),
            ([mode, {"name": "s", "type": "string", "show_if": "fehlt=a"}], "params[1].show_if"),
            ([mode, {"name": "s", "type": "string", "show_if": "s=a"}], "params[1].show_if"),
            ([dict(mode, show_if="mode=a")], "params[0].show_if"),
        ]
        for params, path in cases:
            with self.subTest(params=params):
                self.assert_finding("L6", path, {"params": params})
        self.assert_finding("L3", "params[0].label", {"params": [dict(mode, label="")]})
        self.assert_finding("L3", "params[0].hidden", {"params": [dict(mode, hidden="ja")]})

    def test_violations(self):
        cases = [
            ([{"name": "a", "type": "int"}, {"name": "a", "type": "bool"}], "params[1].name"),
            ([{"name": "c", "type": "choice"}], "params[0].options"),
            ([{"name": "c", "type": "choice", "options": []}], "params[0].options"),
            ([{"name": "s", "type": "string", "options": ["a"]}], "params[0].options"),
            ([{"name": "c", "type": "choice", "options": ["a"], "default": "b"}], "params[0].default"),
            ([{"name": "i", "type": "int", "default": "5"}], "params[0].default"),
            ([{"name": "i", "type": "int", "default": True}], "params[0].default"),
            ([{"name": "i", "type": "int", "default": 1, "min": 2}], "params[0].default"),
            ([{"name": "i", "type": "int", "default": 9, "max": 8}], "params[0].default"),
            ([{"name": "i", "type": "int", "min": 3, "max": 2}], "params[0].min"),
            ([{"name": "s", "type": "string", "min": 1}], "params[0].min"),
            ([{"name": "s", "type": "string", "default": "abcd", "max_length": 3}], "params[0].default"),
            ([{"name": "i", "type": "int", "max_length": 3}], "params[0].max_length"),
            ([{"name": "s", "type": "string", "default": "A", "pattern": "[a-z]*"}], "params[0].default"),
            ([{"name": "s", "type": "string", "pattern": "[a-z"}], "params[0].pattern"),
            ([{"name": "s", "type": "string", "pattern": 3}], "params[0].pattern"),
            ([{"name": "i", "type": "int", "pattern": "[0-9]"}], "params[0].pattern"),
            ([{"name": "b", "type": "bool", "default": "true"}], "params[0].default"),
            ([{"name": "k", "type": "secret", "default": "x"}], "params[0].default"),
            ([{"name": "container", "type": "string"}], "params[0].name"),
            ([{"name": "container_ip", "type": "string"}], "params[0].name"),
            ([{"name": "workdir", "type": "string"}], "params[0].name"),
            ([{"name": "user", "type": "string"}], "params[0].name"),
        ]
        for params, path in cases:
            with self.subTest(params=params):
                self.assert_finding("L6", path, {"params": params})


class L7Test(AddonTestCase):
    PARAMS = [
        {"name": "port", "type": "int", "default": 8000},
        {"name": "key", "type": "secret"},
        {"name": "opt", "type": "string"},
        {"name": "need", "type": "string", "required": True},
    ]

    def actions(self, *actions):
        return {"params": self.PARAMS, "actions": list(actions)}

    def test_ok(self):
        addon = self.assert_ok(self.actions(
            {"id": "start", "label": "Start", "run": "serve {port} {need} {container} {container_ip} {workdir} {user} {{x}}", "detach": True},
            {"id": "stop", "label": "Stopp", "stops": "start"},
            {"id": "once", "label": "Einmal", "run": "true", "user": "root", "timeout": 3600},
        ), ai="Port {port}, Nutzer {user}\n")
        self.assertEqual(addon.action("once")["timeout"], 3600)
        self.assertEqual(addon.action("start")["timeout"], 300)

    def test_violations(self):
        start = {"id": "start", "label": "Start", "run": "true", "detach": True}
        cases = [
            ([start, dict(start)], "actions[1].id"),
            ([{"id": "a", "label": "A"}], "actions[0].run"),
            ([{"id": "a", "label": "A", "run": "true", "stops": "start"}, start], "actions[0].run"),
            ([{"id": "a", "label": "A", "run": "  "}], "actions[0].run"),
            ([{"id": "stop", "label": "S", "stops": "fehlt"}], "actions[0].stops"),
            ([{"id": "once", "label": "O", "run": "true"}, {"id": "stop", "label": "S", "stops": "once"}], "actions[1].stops"),
            ([start, {"id": "stop", "label": "S", "stops": "start", "detach": True}], "actions[1].detach"),
            ([{"id": "a", "label": "A", "run": "echo {unbekannt}"}], "actions[0].run"),
            ([{"id": "a", "label": "A", "run": "echo {key}"}], "actions[0].run"),
            ([{"id": "a", "label": "A", "run": "echo {opt}"}], "actions[0].run"),
            ([{"id": "a", "label": "A", "run": "echo {"}], "actions[0].run"),
        ]
        for actions, path in cases:
            with self.subTest(actions=actions):
                self.assert_finding("L7", path, self.actions(*actions))

    def test_ai_md_placeholders(self):
        self.assert_finding("L7", "AI.md", {"params": self.PARAMS}, ai="Key {key}\n")
        self.assert_finding("L7", "AI.md", {"params": self.PARAMS}, ai="Unbekannt {nix}\n")
        self.assert_finding("L7", "AI.md", ai='JSON {"a": 1}\n')


class L8Test(AddonTestCase):
    BASE = {
        "params": [{"name": "port", "type": "int", "default": 8000}, {"name": "key", "type": "secret"}],
        "actions": [
            {"id": "start", "label": "Start", "run": "true", "detach": True},
            {"id": "once", "label": "Einmal", "run": "true"},
        ],
    }

    def links(self, *links):
        return dict(self.BASE, links=list(links))

    def test_ok(self):
        addon = self.assert_ok(self.links(
            {"label": "Auf", "url": "http://{container_ip}:{port}/", "when": "start"},
            {"label": "Doku", "url": "https://example.org/"},
        ))
        self.assertEqual(addon.links[1]["when"], "")

    def test_violations(self):
        cases = [
            ({"label": "L", "url": "ftp://x/"}, "links[0].url"),
            ({"label": "L", "url": "javascript:alert(1)"}, "links[0].url"),
            ({"label": "L", "url": "http://x/{unbekannt}"}, "links[0].url"),
            ({"label": "L", "url": "http://x/{key}"}, "links[0].url"),
            ({"label": "L", "url": "http://x/", "when": "once"}, "links[0].when"),
            ({"label": "L", "url": "http://x/", "when": "fehlt"}, "links[0].when"),
        ]
        for link, path in cases:
            with self.subTest(link=link):
                self.assert_finding("L8", path, self.links(link))


class PackagesAddonTest(unittest.TestCase):
    """Addon packages: Die Liste nimmt nur Paketnamen an."""

    def setUp(self):
        self.addon, findings = addons.load(helpers.EXAMPLES / "addons" / "packages")
        self.assertEqual(findings, [])

    def test_names_only(self):
        param = self.addon.param("apt")
        for value in ("", "libnss3", "  libnss3   libgbm1 ", "g++ libatk1.0-0t64 fonts-liberation", "a2ps"):
            self.assertIsNone(param.problem(value), value)
        for value in ("-y", "--allow-unauthenticated", "libnss3; reboot", "a b\nc", "$(id)", "Libnss3", "curl=8.5", "./paket.deb",
                      "a,b", "a\tb", "`id`", "a|b", "x", "foo:amd64"):
            self.assertIsNotNone(param.problem(value), value)

    def test_script_checks_again(self):
        script = helpers.EXAMPLES / "addons" / "packages" / "install.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)
        text = script.read_text(encoding="utf-8")
        self.assertIn('apt-get install -y "${options[@]}" -- "${names[@]}"', text)
        for word in ("eval", "sh -c", "bash -c", "remove", "purge"):
            self.assertNotIn(word, text)


class CatalogTest(helpers.TempEnvTestCase):
    def test_builtin_only(self):
        cat = addons.catalog(self.tmp / "gibtsnicht")
        self.assertEqual(sorted(cat.addons), ["abap", "base", "claude-code", "codex", "git", "httpserver", "info", "node", "opencode", "packages", "python", "webproxy"])
        self.assertEqual({a.origin for a in cat.addons.values()}, {"builtin"})
        self.assertEqual(cat.broken, {})

    def test_own_overrides_builtin(self):
        own = self.tmp / "own"
        write_addon(own, "python", {"version": "9.0.0"})
        write_addon(own, "extra")
        cat = addons.catalog(own)
        self.assertEqual((cat.get("python").version, cat.get("python").origin), ("9.0.0", "override"))
        self.assertEqual(cat.get("python").summary()["origin_label"], "eigen (überschreibt mitgeliefert)")
        self.assertEqual(cat.get("extra").origin, "own")
        self.assertEqual(cat.get("base").origin, "builtin")

    def test_broken_own_addon_hides_builtin(self):
        own = self.tmp / "own"
        write_addon(own, "python", {"version": "kaputt"})
        cat = addons.catalog(own)
        self.assertIsNone(cat.get("python"))
        self.assertEqual(cat.broken["python"][0].check, "L3")


class OrderTest(helpers.TempEnvTestCase):
    """T-Order und L5."""

    def catalog(self, **requires):
        root = self.tmp / "cat"
        write_addon(root, "base")
        for addon_id, spec in requires.items():
            data = spec if isinstance(spec, dict) else {"requires": spec}
            write_addon(root, addon_id.replace("_", "-"), data)
        cat = addons.catalog(self.tmp / "leer", root)
        self.assertEqual(cat.broken, {})
        return cat

    def test_base_first_and_list_order_kept(self):
        cat = self.catalog(aa=[], bb=[], cc=[])
        self.assertEqual(addons.resolve_order(["cc", "aa", "bb"], cat), (["base", "cc", "aa", "bb"], []))
        self.assertEqual(addons.resolve_order(["cc", "base", "aa", "cc"], cat)[0], ["base", "cc", "aa"])
        self.assertEqual(addons.resolve_order([], cat), (["base"], []))

    def test_requires_reorders(self):
        cat = self.catalog(app=["lib", "tool"], lib=["core"], tool=[], core=[])
        order, findings = addons.resolve_order(["app", "tool", "lib", "core"], cat)
        self.assertEqual((order, findings), (["base", "tool", "core", "lib", "app"], []))

    def test_examples(self):
        cat = addons.catalog(self.tmp / "leer")
        self.assertEqual(
            addons.resolve_order(["claude-code", "opencode", "node", "abap"], cat),
            (["base", "claude-code", "opencode", "node", "abap"], []),
        )
        self.assertEqual(addons.resolve_order(["httpserver", "python"], cat)[0], ["base", "python", "httpserver"])

    def test_missing_dependency_is_not_added(self):
        cat = addons.catalog(self.tmp / "leer")
        order, findings = addons.resolve_order(["abap"], cat)
        self.assertEqual([(f.check, f.path, f.message) for f in findings], [("L5", "addons", "Addon abap benötigt node")])
        self.assertNotIn("node", order)

    def test_cycle(self):
        cat = self.catalog(aa=["bb"], bb=["cc"], cc=["aa"], dd=[])
        order, findings = addons.resolve_order(["aa", "bb", "cc", "dd"], cat)
        self.assertEqual(order, ["base", "dd"])
        self.assertEqual([f.check for f in findings], ["L5"])
        self.assertIn("Zyklus", findings[0].message)

    def test_conflicts(self):
        cat = self.catalog(aa={"conflicts": ["bb"]}, bb=[], cc=[])
        self.assertEqual(addons.resolve_order(["aa", "cc"], cat)[1], [])
        findings = addons.resolve_order(["aa", "bb"], cat)[1]
        self.assertEqual([f.check for f in findings], ["L5"])
        self.assertIn("verträgt sich nicht", findings[0].message)

    def test_unknown_addons_are_skipped(self):
        cat = self.catalog(aa=[])
        self.assertEqual(addons.resolve_order(["aa", "fehlt"], cat), (["base", "aa"], []))


if __name__ == "__main__":
    unittest.main()
