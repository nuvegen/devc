"""devc hinter einem Proxy (SPEC 11.2): Einstellungen, Host- und Origin-Prüfung, X-Forwarded-*."""

import shutil
import unittest
from unittest import mock

from devc import config, core, security, server
from devc.errors import SecurityError, ValidationError
from tests.helpers import run_cli
from tests.test_init_doctor import DoctorTest
from tests.test_lifecycle import LifecycleTestCase
from tests.test_server import ServerTestCase
from tests.test_terminal import TerminalSessionTest

PROXY = '[server]\nbehind_proxy = true\nbase_domain = "dev.example"\n'
LOCAL = '[server]\nbehind_proxy = true\nbase_domain = "localhost"\npublic_port = 8443\n'
VIA = {"X-Forwarded-Host": "devc.dev.example", "X-Forwarded-Proto": "https", "Host": "devc.dev.example"}


class ProxyConfigTest(LifecycleTestCase):
    """L1 für die neuen Schlüssel."""

    def test_defaults(self):
        self.assertEqual((self.cfg.server["base_domain"], self.cfg.server["public_port"], self.cfg.server["behind_proxy"]),
                         ("", 443, False))

    def test_valid_values(self):
        for domain in ("localhost", "acme.dev.example.com", "a-1.example", "x" * 63 + ".example"):
            self.write_config(f'[server]\nbase_domain = "{domain}"\npublic_port = 8443\nbehind_proxy = true\n')
            self.assertEqual(self.cfg.server["base_domain"], domain)

    def test_violations(self):
        cases = [
            ('base_domain = "Acme.example"', "server.base_domain"),
            ('base_domain = "acme..example"', "server.base_domain"),
            ('base_domain = "-acme.example"', "server.base_domain"),
            ('base_domain = "acme.example."', "server.base_domain"),
            ('base_domain = "acme.example:8443"', "server.base_domain"),
            ('base_domain = "https://acme.example"', "server.base_domain"),
            (f'base_domain = "{"x" * 64}.example"', "server.base_domain"),
            (f'base_domain = "{".".join(["abcdefghi"] * 22)}"', "server.base_domain"),
            ("base_domain = 1", "server.base_domain"),
            ("public_port = 0", "server.public_port"),
            ('public_port = "443"', "server.public_port"),
            ('behind_proxy = "ja"', "server.behind_proxy"),
        ]
        for line, path in cases:
            with self.subTest(line=line):
                self.write_config(f"[server]\n{line}\n")
                with self.assertRaises(ValidationError) as ctx:
                    config.load()
                self.assertEqual([(f.check, f.path) for f in ctx.exception.findings], [("L1", path)])

    def test_longest_proxy_name_fits_a_dns_label(self):
        """`<port>-<container>` darf 63 Zeichen nicht überschreiten."""
        self.assertLessEqual(len("65535-" + "a" * 31), 63)

    def test_public_names_and_urls(self):
        self.write_config(PROXY)
        self.assertEqual(security.proxy_host(self.cfg.server), "devc.dev.example")
        self.assertEqual(security.public_url(self.cfg.server), "https://devc.dev.example/")
        self.write_config(LOCAL)
        self.assertEqual(security.public_url(self.cfg.server), "https://devc.localhost:8443/")
        self.assertEqual(security.public_url(self.cfg.server, "3000-ab", "/x"), "https://3000-ab.localhost:8443/x")


class ProxyModeTest(LifecycleTestCase):
    """L14: Kombinationen der Einstellungen beim Server-Start."""

    def problems(self, text, **kwargs):
        self.write_config(text)
        return [(f.check, f.path, f.severity) for f in security.proxy_problems(self.cfg.server, **kwargs)]

    def test_no_proxy_is_fine(self):
        self.assertEqual(self.problems(""), [])
        self.assertEqual(self.problems(PROXY), [])

    def test_behind_proxy_needs_base_domain(self):
        self.assertEqual(self.problems("[server]\nbehind_proxy = true\n"), [("L14", "server.base_domain", "error")])

    def test_behind_proxy_needs_loopback(self):
        self.assertEqual(self.problems(PROXY + 'bind = "0.0.0.0"\n'), [("L14", "server.bind", "error")])
        self.assertEqual(self.problems(PROXY, bind="192.168.1.5"), [("L14", "server.bind", "error")])

    def test_behind_proxy_tls_files_belong_to_the_proxy(self):
        """Hinter dem Proxy sind tls_cert/tls_key dessen Zertifikat (Caddyfile); devc selbst macht kein TLS."""
        self.assertEqual(self.problems(PROXY + 'tls_cert = "/c.pem"\ntls_key = "/k.pem"\n'), [])
        self.assertEqual(self.problems(PROXY + 'tls_cert = "/c.pem"\n'), [("L14", "server.tls_cert", "error")])
        self.assertEqual(self.problems(PROXY + 'tls_key = "/k.pem"\n'), [("L14", "server.tls_cert", "error")])
        core.init(self.cfg)
        self.write_config(PROXY + 'tls_cert = "/gibt/es/nicht/c.pem"\ntls_key = "/gibt/es/nicht/k.pem"\n')
        srv = server.make_server(self.cfg, port=0)  # die Dateien liest Caddy, nicht devc
        self.addCleanup(srv.server_close)
        self.assertEqual((srv.app.mode.proxy, srv.app.mode.tls), (True, False))

    def test_base_domain_without_behind_proxy_warns(self):
        self.assertEqual(self.problems('[server]\nbase_domain = "dev.example"\n'), [("L14", "server.behind_proxy", "warning")])

    def test_bind_mode(self):
        mode = security.bind_mode("127.0.0.1", tls=False, proxy=True)
        self.assertEqual((mode.proxy, mode.loopback, mode.tls, mode.insecure, mode.terminal), (True, True, False, False, True))
        self.assertFalse(security.bind_mode("127.0.0.1", tls=False, proxy=True, terminal=False).terminal)
        self.assertFalse(security.bind_mode("127.0.0.1", tls=False).proxy)

    def test_make_server_refuses_bad_combinations(self):
        core.init(self.cfg)
        self.write_config(PROXY)
        with self.assertRaises(SecurityError) as ctx:
            server.make_server(self.cfg, bind="0.0.0.0", port=0, allow_insecure_bind=True)
        self.assertEqual((ctx.exception.code, ctx.exception.details["check"]), ("E_SECURITY", "L14"))
        self.write_config("[server]\nbehind_proxy = true\n")
        with self.assertRaises(ValidationError) as ctx:
            server.make_server(self.cfg, port=0)
        self.assertEqual(ctx.exception.details["check"], "L14")

    def test_cli_exit_code(self):
        core.init(self.cfg)
        self.write_config(PROXY)
        code, out, err = run_cli("serve", "--bind", "0.0.0.0", "--port", "0", "--allow-insecure-bind")
        self.assertEqual((code, out), (6, ""))
        self.assertIn("behind_proxy", err)


class ForwardedTest(unittest.TestCase):
    """X-Forwarded-* gelten nur von Loopback; der Host muss devc.<base_domain> sein."""

    def check(self, headers, peer="127.0.0.1", domain="dev.example"):
        return security.forwarded(peer, headers, domain)

    def test_forwarded_from_loopback(self):
        for peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            result = self.check({"X-Forwarded-Host": "devc.dev.example", "X-Forwarded-Proto": "https", "Host": "devc.dev.example"}, peer)
            self.assertEqual((result.host, result.tls), ("devc.dev.example", True))
        result = self.check({"X-Forwarded-Host": "Devc.Dev.Example:8443", "X-Forwarded-Proto": "HTTPS"})
        self.assertEqual((result.host, result.tls), ("devc.dev.example:8443", True))

    def test_host_header_is_the_fallback(self):
        self.assertEqual(self.check({"Host": "devc.dev.example", "X-Forwarded-Proto": "https"}).host, "devc.dev.example")

    def test_first_value_of_a_list_counts(self):
        result = self.check({"X-Forwarded-Host": "devc.dev.example, intern", "X-Forwarded-Proto": "https, http"})
        self.assertEqual((result.host, result.tls), ("devc.dev.example", True))

    def test_non_loopback_peer_is_refused(self):
        """Gefälschte Köpfe von außen: Wer nicht der Proxy ist, kommt nicht durch."""
        for peer in ("192.168.178.20", "10.119.122.1", "2001:db8::1", ""):
            with self.assertRaises(SecurityError, msg=peer):
                self.check(VIA, peer)

    def test_wrong_host(self):
        for host in ("evil.example", "dev.example", "3000-ab.dev.example", "devc.dev.example.evil.example", "127.0.0.1:8740"):
            with self.assertRaises(SecurityError, msg=host):
                self.check({"X-Forwarded-Host": host, "X-Forwarded-Proto": "https", "Host": "devc.dev.example"})
        # der Host-Kopf zählt nicht, wenn der Proxy einen anderen Namen meldet
        with self.assertRaises(SecurityError):
            self.check({"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https", "Host": "devc.dev.example"})

    def test_plain_http_only_for_localhost(self):
        for proto in ("http", "", "ftp"):
            with self.assertRaises(SecurityError, msg=proto):
                self.check({"X-Forwarded-Host": "devc.dev.example", "X-Forwarded-Proto": proto})
        with self.assertRaises(SecurityError):
            self.check({"X-Forwarded-Host": "devc.dev.example"})
        for domain in ("localhost", "acme.localhost"):
            result = self.check({"X-Forwarded-Host": f"devc.{domain}:8080", "X-Forwarded-Proto": "http"}, domain=domain)
            self.assertEqual((result.host, result.tls), (f"devc.{domain}:8080", False))
        with self.assertRaises(SecurityError):
            self.check({"X-Forwarded-Host": "devc.notlocalhost", "X-Forwarded-Proto": "http"}, domain="notlocalhost")

    def test_origin(self):
        fw = security.Forwarded("devc.dev.example", True)
        for origin in (None, "https://devc.dev.example", "https://devc.dev.example:443", "HTTPS://Devc.Dev.Example"):
            security.check_origin(origin, fw)
        for origin in ("https://3000-ab.dev.example", "http://devc.dev.example", "https://devc.dev.example:8443", "null", "https://evil.example"):
            with self.assertRaises(SecurityError, msg=origin):
                security.check_origin(origin, fw)
        security.check_origin("https://devc.localhost:8443", security.Forwarded("devc.localhost:8443", True))
        security.check_origin("http://devc.localhost:8080", security.Forwarded("devc.localhost:8080", False))


class ProxiedServerTest(ServerTestCase):
    config_text = PROXY

    def test_mode_and_status(self):
        self.assertTrue(self.server.app.mode.proxy)
        status, data = self.request("GET", "/api/status", headers=VIA)
        self.assertEqual(status, 200)
        self.assertEqual((data["behind_proxy"], data["insecure"], data["terminal_allowed"]), (True, False, True))

    def test_direct_access_without_proxy_name_is_refused(self):
        for headers in ({}, {"Host": "localhost"}, {"Host": "devc.dev.example"}, {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"}):
            status, error = self.request("GET", "/api/status", headers=headers)
            self.assertEqual((status, error["error"]), (403, "E_SECURITY"), headers)
        status, error = self.request("GET", "/", headers={"Host": "evil.example"}, auth=False)
        self.assertEqual(status, 403)

    def test_forged_headers_from_non_loopback(self):
        with mock.patch.object(server.Handler, "_peer", lambda handler: "192.168.178.20"):
            status, error = self.request("GET", "/api/status", headers=VIA)
        self.assertEqual((status, error["error"]), (403, "E_SECURITY"))

    def test_cookie_is_secure_and_bound_to_the_host(self):
        response, _ = self.request("POST", "/api/login", {"token": self.token}, headers=VIA, auth=False, raw=True)
        cookie = response.getheader("Set-Cookie")
        self.assertEqual(response.status, 204)
        self.assertIn("; Secure", cookie)
        self.assertNotIn("domain=", cookie.lower())  # eine App im Container darf das Cookie nie sehen
        session = cookie.split(";")[0]
        self.assertEqual(self.request("GET", "/api/status", headers={**VIA, "Cookie": session})[0], 200)

    def test_foreign_origin_is_refused(self):
        cookie = self.request("POST", "/api/login", {"token": self.token}, headers=VIA, auth=False, raw=True)[0].getheader("Set-Cookie").split(";")[0]
        headers = {**VIA, "Cookie": cookie, "X-Devc": "1"}
        self.assertEqual(self.request("PUT", "/api/secrets/abc", b"wert", headers={**headers, "Origin": "https://devc.dev.example"})[0], 204)
        for origin in ("https://3000-ab.dev.example", "http://devc.dev.example", "null"):
            status, error = self.request("PUT", "/api/secrets/abc", b"wert", headers={**headers, "Origin": origin})
            self.assertEqual((status, error["error"]), (403, "E_SECURITY"), origin)
            self.assertEqual(self.request("GET", "/api/status", headers={**headers, "Origin": origin})[0], 403)


class LocalProxyServerTest(ServerTestCase):
    """*.localhost, auch unverschlüsselt durch den Tunnel."""

    config_text = LOCAL

    def test_http_on_localhost_gives_plain_cookie(self):
        via = {"X-Forwarded-Host": "devc.localhost:8080", "X-Forwarded-Proto": "http"}
        response, _ = self.request("POST", "/api/login", {"token": self.token}, headers=via, auth=False, raw=True)
        self.assertEqual(response.status, 204)
        self.assertNotIn("Secure", response.getheader("Set-Cookie"))

    def test_https_on_localhost_gives_secure_cookie(self):
        via = {"X-Forwarded-Host": "devc.localhost:8443", "X-Forwarded-Proto": "https"}
        response, _ = self.request("POST", "/api/login", {"token": self.token}, headers=via, auth=False, raw=True)
        self.assertIn("; Secure", response.getheader("Set-Cookie"))


class ProxiedTerminalTest(TerminalSessionTest):
    """Das Terminal (WebSocket) geht über die weitergeleitete Verbindung."""

    config_text = PROXY
    request_headers = VIA
    ws_host = "devc.dev.example"
    ws_forward = ["X-Forwarded-Host: devc.dev.example", "X-Forwarded-Proto: https"]
    ws_origin = "https://devc.dev.example"

    def test_origin_of_a_container_app_is_rejected(self):
        sock, stream, status, headers, key = self.connect(origin="https://3000-ab.dev.example")
        self.assertEqual(status.split()[1], "403")

    def test_plain_origin_is_rejected(self):
        sock, stream, status, headers, key = self.connect(origin="http://devc.dev.example")
        self.assertEqual(status.split()[1], "403")


class ProxyDoctorTest(DoctorTest):
    def proxy_points(self, text, caddy="/usr/bin/caddy"):
        self.write_config(text)
        with mock.patch.object(shutil, "which", lambda name: caddy if name == "caddy" else None):
            try:
                result = core.doctor(config.load())
            except Exception as error:
                result = error.details
        return [(p["path"], p["severity"]) for p in result["points"] if p["path"] in ("proxy", "caddy")], result

    def test_no_proxy(self):
        points, result = self.proxy_points("", caddy=None)
        self.assertEqual(points, [("proxy", "ok")])

    def test_proxy_ok(self):
        points, result = self.proxy_points(PROXY)
        self.assertEqual(points, [("proxy", "ok"), ("caddy", "ok")])
        self.assertIn("https://devc.dev.example/", " ".join(p["message"] for p in result["points"]))

    def test_missing_caddy(self):
        points, result = self.proxy_points(PROXY, caddy=None)
        self.assertEqual(points, [("proxy", "ok"), ("caddy", "warning")])
        self.assertEqual(result["errors"], 0)

    def test_bad_combinations(self):
        points, result = self.proxy_points("[server]\nbehind_proxy = true\n")
        self.assertEqual(points, [("proxy", "error")])
        self.assertEqual([p["check"] for p in result["points"] if p["path"] == "proxy"], ["L14"])
        points, result = self.proxy_points('[server]\nbase_domain = "dev.example"\n')
        self.assertEqual(points, [("proxy", "warning"), ("caddy", "ok")])
        points, result = self.proxy_points(PROXY + 'bind = "0.0.0.0"\n')
        self.assertEqual(points, [("proxy", "error"), ("caddy", "ok")])


del DoctorTest, TerminalSessionTest  # nur als Basisklassen; sonst liefen ihre Tests hier doppelt


if __name__ == "__main__":
    unittest.main()
