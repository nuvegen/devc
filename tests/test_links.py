"""Addon-Links mit `port` und `path` (SPEC 6.2, 7.3, L8)."""

import unittest

from devc import core
from tests.test_addons import L8Test
from tests.test_lifecycle import LifecycleTestCase
from tests.test_proxy import LOCAL, PROXY


class PortLinkCheckTest(L8Test):
    BASE = {
        "params": [{"name": "port", "type": "int", "default": 8000}, {"name": "key", "type": "secret"},
                   {"name": "dir", "type": "string", "default": "x"}, {"name": "ohne", "type": "int"}],
        "actions": L8Test.BASE["actions"],
    }

    def test_ok(self):
        addon = self.assert_ok(self.links(
            {"label": "Fest", "port": 3000},
            {"label": "Param", "port": "{port}", "path": "/app/{dir}/", "when": "start"},
            {"label": "Alt", "url": "http://{container_ip}:{port}/"},
        ))
        self.assertEqual(addon.links[0], {"label": "Fest", "port": 3000, "path": "/", "public": False, "when": ""})
        self.assertEqual(addon.links[1], {"label": "Param", "port": "{port}", "path": "/app/{dir}/", "public": False, "when": "start"})
        self.assertEqual(addon.links[2], {"label": "Alt", "url": "http://{container_ip}:{port}/", "when": ""})

    def test_violations(self):
        cases = [
            ({"label": "L", "port": 3000, "url": "http://x/"}, "links[0].url"),
            ({"label": "L", "port": 80}, "links[0].port"),
            ({"label": "L", "port": 65536}, "links[0].port"),
            ({"label": "L", "port": True}, "links[0].port"),
            ({"label": "L", "port": "3000"}, "links[0].port"),
            ({"label": "L", "port": "{dir}"}, "links[0].port"),
            ({"label": "L", "port": "{key}"}, "links[0].port"),
            ({"label": "L", "port": "{unbekannt}"}, "links[0].port"),
            ({"label": "L", "port": "{ohne}"}, "links[0].port"),
            ({"label": "L", "port": "1{port}"}, "links[0].port"),
            ({"label": "L", "port": "{container}"}, "links[0].port"),
            ({"label": "L", "port": 3000, "path": "app"}, "links[0].path"),
            ({"label": "L", "port": 3000, "path": "//evil.example/"}, "links[0].path"),
            ({"label": "L", "port": 3000, "path": "/a b"}, "links[0].path"),
            ({"label": "L", "port": 3000, "path": "/{key}"}, "links[0].path"),
            ({"label": "L", "port": 3000, "path": "/{unbekannt}"}, "links[0].path"),
            ({"label": "L", "url": "http://x/", "path": "/a"}, "links[0].path"),
            ({"label": "L", "port": 3000, "when": "once"}, "links[0].when"),
        ]
        for link, path in cases:
            with self.subTest(link=link):
                self.assert_finding("L8", path, self.links(link))

    def test_one_of_url_and_port_is_required(self):
        self.assert_finding("L3", "links[0].url", self.links({"label": "L"}))


class PortLinkTest(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        core.init(self.cfg)

    def links(self):
        return {a["id"]: a for a in core.action_list(self.cfg, "py-tools")["addons"]}["httpserver"]["links"]

    def test_without_base_domain_the_link_points_to_the_container(self):
        self.create()
        self.assertEqual(self.links(), [{"label": "Im Browser öffnen", "url": "http://10.0.0.5:8000/", "active": False, "port": 8000, "path": "/"}])
        core.stop(self.cfg, "py-tools")
        self.assertEqual(self.links()[0]["url"], None)

    def test_with_base_domain_the_link_is_the_proxy_address(self):
        self.create()
        self.write_config(PROXY)
        self.assertEqual(self.links(), [{"label": "Im Browser öffnen", "url": "https://8000-py-tools.dev.example/", "active": False, "port": 8000, "path": "/"}])
        core.action_run(self.cfg, "py-tools", "httpserver.start")
        self.assertTrue(self.links()[0]["active"])
        self.write_config(LOCAL)
        self.assertEqual(self.links()[0]["url"], "https://8000-py-tools.localhost:8443/")
        core.stop(self.cfg, "py-tools")
        link = self.links()[0]
        self.assertEqual((link["url"], link["active"]), ("https://8000-py-tools.localhost:8443/", False))

    def test_parameter_changes_the_port(self):
        self.create(params=["httpserver.port=9000"])
        self.write_config(PROXY)
        self.assertEqual(self.links()[0]["url"], "https://9000-py-tools.dev.example/")

    def test_port_below_1024_has_no_proxy_address(self):
        """Ein Parameter darf kleiner sein als die Grenze des Proxys; dann gibt es keinen Link."""
        import shutil
        from devc import addons, tomlw
        import tomllib
        target = self.cfg.paths.addons / "httpserver"
        shutil.copytree(addons.BUILTIN / "addons" / "httpserver", target)
        data = tomllib.loads((target / "addon.toml").read_text(encoding="utf-8"))
        data["params"][0]["min"] = 1
        (target / "addon.toml").write_text(tomlw.dumps(data), encoding="utf-8")
        self.create(params=["httpserver.port=80"])
        self.write_config(PROXY)
        link = self.links()[0]
        self.assertEqual((link["url"], link["active"], link["port"]), (None, False, 80))

    def test_builtin_addons_use_port_links(self):
        cat = core.catalog(self.cfg)
        for addon_id in ("httpserver", "opencode"):
            links = cat.get(addon_id).links
            self.assertTrue(links, addon_id)
            for link in links:
                self.assertIn("port", link, addon_id)
                self.assertNotIn("url", link, addon_id)

    def test_opencode_listens_on_all_interfaces(self):
        """Dienste der mitgelieferten Addons lauschen auf 0.0.0.0."""
        addon = core.catalog(self.cfg).get("opencode")
        web = next(a for a in addon.actions if a["id"] == "web")
        self.assertIn("--hostname 0.0.0.0", web["run"])
        self.assertIn("--port {port}", web["run"])
        self.assertTrue(web["detach"])
        self.assertEqual(addon.links[0]["when"], "web")


class PortsParamTest(L8Test):
    """Parametertyp `ports` und Links mit `ports` (SPEC 6.2, 7.8)."""

    BASE = {
        "params": [{"name": "ports", "type": "ports", "default": "3000=Vite 8080"}, {"name": "port", "type": "int", "default": 8000},
                   {"name": "text", "type": "string", "default": "3000"}],
        "actions": L8Test.BASE["actions"],
    }

    def test_ok(self):
        addon = self.assert_ok(self.links({"label": "Port", "ports": "{ports}", "path": "/x"}))
        self.assertEqual(addon.links[0], {"label": "Port", "ports": "{ports}", "path": "/x", "public": False, "when": ""})

    def test_violations(self):
        cases = [
            ({"label": "L", "ports": "{port}"}, "links[0].ports"),
            ({"label": "L", "ports": "{text}"}, "links[0].ports"),
            ({"label": "L", "ports": "{unbekannt}"}, "links[0].ports"),
            ({"label": "L", "ports": "3000 8080"}, "links[0].ports"),
            ({"label": "L", "ports": 3000}, "links[0].ports"),
            ({"label": "L", "ports": "{ports}", "port": 3000}, "links[0].url"),
            ({"label": "L", "ports": "{ports}", "url": "http://x/"}, "links[0].url"),
            ({"label": "L", "ports": "{ports}", "path": "x"}, "links[0].path"),
        ]
        for link, path in cases:
            with self.subTest(link=link):
                self.assert_finding("L8", path, self.links(link))

    def test_public(self):
        addon = self.assert_ok(self.links({"label": "Offen", "ports": "{ports}", "public": True}, {"label": "Fest", "port": 3000, "public": False}))
        self.assertEqual((addon.links[0]["public"], addon.links[1]["public"]), (True, False))
        self.assert_finding("L8", "links[0].public", self.links({"label": "L", "url": "http://x/", "public": True}))
        self.assert_finding("L3", "links[0].public", self.links({"label": "L", "port": 3000, "public": "ja"}))

    def test_values(self):
        from devc import addons
        good = {"": [], "3000": [(3000, "")], "3000=Vite 8080": [(3000, "Vite"), (8080, "")], "  1024=a   65535=Ä-b_c.1 ": [(1024, "a"), (65535, "Ä-b_c.1")]}
        for value, expected in good.items():
            self.assertEqual(addons.parse_ports(value), expected, value)
        for value in ("80", "1023", "65536", "abc", "3000=", "=x", "3000=a=b", "3000 3000", "03000", "3000=" + "x" * 41, "3000,8080", "3000=a\tb\n"[:-1] + "\x00", 3000, None, ["3000"]):
            with self.assertRaises(ValueError, msg=repr(value)):
                addons.parse_ports(value)

    def test_default_is_checked(self):
        for default in ("80", "3000 3000", 3000):
            data = dict(self.BASE, params=[{"name": "ports", "type": "ports", "default": default}])
            self.assert_finding("L6", "params[0].default", data)


class PortsAddonTest(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        core.init(self.cfg)

    def links(self):
        return {a["id"]: a for a in core.action_list(self.cfg, "py-tools")["addons"]}["webproxy"]["links"]

    def test_one_link_per_entry(self):
        self.create(addons=["webproxy"], params=["webproxy.proxy=3000=Vite 8080"])
        self.assertEqual(self.links(), [
            {"label": "Vite", "url": "http://10.0.0.5:3000/", "active": True, "port": 3000, "path": "/"},
            {"label": "Port 8080", "url": "http://10.0.0.5:8080/", "active": True, "port": 8080, "path": "/"}])
        self.write_config(PROXY)
        self.assertEqual([l["url"] for l in self.links()], ["https://3000-py-tools.dev.example/", "https://8080-py-tools.dev.example/"])
        core.stop(self.cfg, "py-tools")
        self.assertEqual([l["active"] for l in self.links()], [False, False])

    def test_public_ports(self):
        """Ports ohne Anmeldung: eigene Liste, im Link gekennzeichnet, dem Proxy bekannt."""
        self.create(addons=["webproxy"], params=["webproxy.proxy=3000=Vite", "webproxy.public=8080=API 9000"])
        self.write_config(PROXY)
        links = self.links()
        self.assertEqual([(l["label"], l["port"], l.get("public", False)) for l in links], [("Vite", 3000, False), ("API", 8080, True), ("Port 9000", 9000, True)])
        self.assertEqual(core.proxy_target(self.cfg, "py-tools", 8080), {"container": "py-tools", "port": 8080, "upstream": "10.0.0.5:8080", "public": True})
        self.assertFalse(core.proxy_target(self.cfg, "py-tools", 3000)["public"])
        self.assertFalse(core.proxy_target(self.cfg, "py-tools", 8000)["public"])  # httpserver: Link ohne public

    def test_empty_list_gives_no_links(self):
        self.create(addons=["webproxy"], params=["webproxy.proxy="])
        self.assertEqual(self.links(), [])

    def test_instance_value_is_checked(self):
        from devc.errors import ValidationError
        for value in ("80", "3000 3000", "abc"):
            with self.assertRaises(ValidationError, msg=value):
                self.create(addons=["webproxy"], params=[f"webproxy.proxy={value}"])

    def test_agent_docs_name_the_ports(self):
        self.create(addons=["webproxy"], params=["webproxy.proxy=3000=Vite"])
        docs = self.provider.files["py-tools"]["/work/CLAUDE.md"]
        self.assertIn("0.0.0.0", docs)
        self.assertIn("3000=Vite", docs)

    def test_addon_needs_no_install_script(self):
        addon = core.catalog(self.cfg).get("webproxy")
        self.assertEqual((addon.install, addon.params[0].type, addon.params[0].default), ((), "ports", ""))
        self.assertEqual(addon.uninstall, ("uninstall.sh",))  # nichts installiert, also per config apply abwählbar
        self.assertEqual([(p.name, p.type, p.default) for p in addon.params], [("proxy", "ports", ""), ("public", "ports", "")])


del L8Test, LifecycleTestCase


if __name__ == "__main__":
    unittest.main()
