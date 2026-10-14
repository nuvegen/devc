"""Container-Ports über den Proxy (SPEC 8.11): Auth-Endpunkt, Anmeldeweg, Caddyfile."""

import unittest
from urllib.parse import parse_qs, quote, urlsplit

from devc import core, security
from devc.errors import NotFoundError, SecurityError, UsageError, ValidationError
from tests.helpers import run_cli
from tests.test_lifecycle import LifecycleTestCase
from tests.test_proxy import LOCAL, PROXY, VIA
from tests.test_server import INSTANCE, ServerTestCase

HOST = "8000-ab.dev.example"


class NameTest(unittest.TestCase):
    def test_proxy_name(self):
        good = {"8000-ab.dev.example": (8000, "ab"), "3000-py-tools.dev.example:8443": (3000, "py-tools"),
                "80-ab.dev.example": (80, "ab"), "65535-ab.DEV.example": (65535, "ab")}
        for host, expected in good.items():
            self.assertEqual(security.proxy_name(host, "dev.example"), expected, host)
        for host in ("devc.dev.example", "ab.dev.example", "8000-ab.evil.example", "8000-ab.dev.example.evil.example", "8000-.dev.example",
                     "08000-ab.dev.example", "65536-ab.dev.example", "0-ab.dev.example", "8000-ab-.dev.example", "8000-AB_.dev.example",
                     "x.8000-ab.dev.example", "8000-ab", "", None, "8000-ab.dev.example@evil.example"):
            self.assertIsNone(security.proxy_name(host, "dev.example"), host)

    def test_return_url(self):
        check = lambda to, domain="dev.example": security.proxy_return_url(to, domain)
        self.assertEqual(check("https://8000-ab.dev.example/"), "https://8000-ab.dev.example/")
        self.assertEqual(check("https://8000-AB.dev.example"), "https://8000-ab.dev.example/")
        self.assertEqual(check("https://8000-ab.dev.example:8443/a/b?x=1&y=2#teil"), "https://8000-ab.dev.example:8443/a/b?x=1&y=2")
        self.assertEqual(check("https://8000-ab.dev.example/a?devc_ticket=alt&x=1"), "https://8000-ab.dev.example/a?x=1")
        self.assertEqual(check("http://8000-ab.localhost:8080/", "localhost"), "http://8000-ab.localhost:8080/")
        bad = ["", None, "/pfad", "//evil.example/", "https://evil.example/", "https://devc.dev.example/", "http://8000-ab.dev.example/",
               "https://8000-ab.dev.example@evil.example/", "https://user:pw@8000-ab.dev.example/", "https://8000-ab.dev.example.evil.example/",
               "javascript:alert(1)", "https://8000-ab.dev.example:x/", "https://8000-ab.dev.example/a b", "https://8000-ab.dev.example\\@evil.example/",
               "https://8000-ab.dev.example/\n", "ftp://8000-ab.dev.example/", "https://8000-ab.dev.example/" + "a" * 2100]
        for to in bad:
            with self.assertRaises(UsageError, msg=to):
                check(to)

    def test_strip_ticket(self):
        self.assertEqual(security.strip_ticket("/a?x=1&devc_ticket=T&y=2"), ("/a?x=1&y=2", "T"))
        self.assertEqual(security.strip_ticket("/?devc_ticket=T"), ("/", "T"))
        self.assertEqual(security.strip_ticket("/a?x=1"), ("/a?x=1", None))
        self.assertEqual(security.strip_ticket("/a"), ("/a", None))
        self.assertEqual(security.strip_ticket("/a?devc_ticket"), ("/a", ""))
        self.assertEqual(security.strip_ticket("/a?mein_devc_ticket=1"), ("/a?mein_devc_ticket=1", None))


class ProxySessionsTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.proxy = security.ProxySessions(clock=lambda: self.now[0])

    def test_ticket_is_single_use_and_bound_to_the_host(self):
        ticket = self.proxy.ticket(HOST)
        self.assertIsNone(self.proxy.redeem(ticket, "9000-ab.dev.example"))
        self.assertIsNone(self.proxy.redeem(ticket, HOST))  # der Fehlversuch hat es verbraucht
        ticket = self.proxy.ticket(HOST)
        session = self.proxy.redeem(ticket, HOST)
        self.assertTrue(self.proxy.valid(session, HOST))
        self.assertIsNone(self.proxy.redeem(ticket, HOST))
        self.assertIsNone(self.proxy.redeem("erfunden", HOST))
        self.assertIsNone(self.proxy.redeem("", HOST))

    def test_ticket_expires(self):
        ticket = self.proxy.ticket(HOST)
        self.now[0] += security.TICKET_SECONDS + 1
        self.assertIsNone(self.proxy.redeem(ticket, HOST))

    def test_session_is_bound_to_the_host_and_expires(self):
        session = self.proxy.redeem(self.proxy.ticket(HOST), HOST)
        self.assertFalse(self.proxy.valid(session, "9000-ab.dev.example"))
        self.assertFalse(self.proxy.valid(None, HOST))
        self.now[0] += security.SESSION_SECONDS + 1
        self.assertFalse(self.proxy.valid(session, HOST))

    def test_clear(self):
        session = self.proxy.redeem(self.proxy.ticket(HOST), HOST)
        self.proxy.clear()
        self.assertFalse(self.proxy.valid(session, HOST))


class TargetTest(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        core.init(self.cfg)
        core.create(self.cfg, "ab", text=INSTANCE)

    def test_running_container(self):
        self.assertEqual(core.proxy_target(self.cfg, "ab", 8000), {"container": "ab", "port": 8000, "upstream": "10.0.0.5:8000", "public": False})

    def test_port_range(self):
        for port in (1, 80, 1023):
            with self.assertRaises(SecurityError, msg=port) as ctx:
                core.proxy_target(self.cfg, "ab", port)
            self.assertEqual(ctx.exception.http_status, 403)
        for port in (1024, 65535):
            core.proxy_target(self.cfg, "ab", port)
        with self.assertRaises(NotFoundError):
            core.proxy_target(self.cfg, "ab", 65536)

    def test_stopped_missing_and_foreign_are_not_found(self):
        self.provider.add("fremd")  # läuft in LXD, steht aber nicht in der devc-Datenbank
        with self.assertRaises(NotFoundError):
            core.proxy_target(self.cfg, "fremd", 8000)
        with self.assertRaises(NotFoundError):
            core.proxy_target(self.cfg, "gibts-nicht", 8000)
        with self.assertRaises(NotFoundError):
            core.proxy_target(self.cfg, "Ab;x", 8000)
        core.stop(self.cfg, "ab")
        with self.assertRaises(NotFoundError):
            core.proxy_target(self.cfg, "ab", 8000)


class AuthMixin:
    def auth(self, host=HOST, uri="/", cookie=None, method="GET", proto="https", extra=None):
        headers = {"Host": "127.0.0.1:8740", "X-Forwarded-Host": host, "X-Forwarded-Proto": proto,
                   "X-Forwarded-Method": method, "X-Forwarded-Uri": uri, **(extra or {})}
        if cookie:
            headers["Cookie"] = cookie
        return self.request("GET", "/api/proxy/auth", headers=headers, auth=False, raw=True)[0]


class ProxyAuthTest(AuthMixin, ServerTestCase):
    config_text = PROXY
    request_headers = VIA

    def setUp(self):
        super().setUp()
        self.make()

    def gui_cookie(self):
        response, _ = self.request("POST", "/api/login", {"token": self.token}, headers=VIA, auth=False, raw=True)
        return response.getheader("Set-Cookie").split(";")[0]

    def login(self, host=HOST, uri="/"):
        """Geht den Anmeldeweg durch und liefert das Cookie des Proxy-Namens."""
        to = self.auth(host, uri).getheader("Location")
        response, _ = self.request("GET", to[len("https://devc.dev.example"):], headers={**VIA, "Cookie": self.gui_cookie()}, auth=False, raw=True)
        back = urlsplit(response.getheader("Location"))
        response = self.auth(host, back.path + "?" + back.query)
        return response.getheader("Set-Cookie").split(";")[0]

    def test_unauthenticated_is_sent_to_the_devc_login(self):
        response = self.auth(uri="/app?x=1")
        self.assertEqual(response.status, 302)
        location = urlsplit(response.getheader("Location"))
        self.assertEqual((location.scheme, location.netloc, location.path), ("https", "devc.dev.example", "/api/proxy/login"))
        self.assertEqual(parse_qs(location.query), {"to": ["https://8000-ab.dev.example/app?x=1"]})
        self.assertIsNone(response.getheader("X-Devc-Upstream"))
        # nur Seitenaufrufe werden umgeleitet
        self.assertEqual(self.auth(method="POST").status, 401)

    def test_full_login_flow(self):
        # 1. ohne Anmeldung bei devc: weiter zur Anmeldeseite der GUI, das Ziel bleibt erhalten
        to = "https://8000-ab.dev.example/app?x=1"
        path = "/api/proxy/login?to=" + quote(to, safe="")
        response, _ = self.request("GET", path, headers=VIA, auth=False, raw=True)
        self.assertEqual((response.status, response.getheader("Location")), (302, "/?to=" + quote(to, safe="")))
        # 2. angemeldet: einmaliges Ticket und zurück zum Dienst
        response, _ = self.request("GET", path, headers={**VIA, "Cookie": self.gui_cookie()}, auth=False, raw=True)
        back = urlsplit(response.getheader("Location"))
        self.assertEqual((response.status, back.scheme, back.netloc, back.path), (302, "https", "8000-ab.dev.example", "/app"))
        query = parse_qs(back.query)
        self.assertEqual(query["x"], ["1"])
        ticket = query["devc_ticket"][0]
        # 3. der Proxy-Name tauscht das Ticket gegen sein eigenes Cookie und entfernt den Parameter
        response = self.auth(uri=back.path + "?" + back.query)
        self.assertEqual((response.status, response.getheader("Location")), (302, "https://8000-ab.dev.example/app?x=1"))
        cookie = response.getheader("Set-Cookie")
        self.assertTrue(cookie.startswith("devc_proxy="))
        for part in ("HttpOnly", "Secure", "Path=/", "SameSite=Lax"):
            self.assertIn(part, cookie)
        self.assertNotIn("domain=", cookie.lower())
        self.assertNotIn(ticket, cookie)
        # 4. mit dem Cookie liefert devc das Ziel
        response = self.auth(uri="/app?x=1", cookie=cookie.split(";")[0])
        self.assertEqual((response.status, response.getheader("X-Devc-Upstream")), (200, "10.0.0.5:8000"))
        # das Ticket gilt nur einmal
        response = self.auth(uri=back.path + "?" + back.query)
        self.assertIsNone(response.getheader("Set-Cookie"))

    def test_ticket_parameter_never_reaches_the_container(self):
        cookie = self.login()
        response = self.auth(uri="/a?devc_ticket=irgendwas&x=1", cookie=cookie)
        self.assertEqual((response.status, response.getheader("Location")), (302, "https://8000-ab.dev.example/a?x=1"))
        self.assertIsNone(response.getheader("X-Devc-Upstream"))
        # kein Sprung auf einen fremden Rechner über einen Pfad mit zwei Schrägstrichen
        response = self.auth(uri="//evil.example/?devc_ticket=x", cookie=cookie)
        self.assertEqual(response.getheader("Location"), "https://8000-ab.dev.example//evil.example/")

    def test_cookie_is_bound_to_its_host(self):
        cookie = self.login()
        self.assertEqual(self.auth(cookie=cookie).status, 200)
        self.assertEqual(self.auth(host="9000-ab.dev.example", cookie=cookie).status, 302)

    def test_gui_cookie_and_token_do_not_open_a_container_port(self):
        """Das GUI-Cookie gilt nur für devc.<base_domain>; eine App im Container bekäme es sonst zu sehen."""
        self.assertEqual(self.auth(cookie=self.gui_cookie()).status, 302)
        self.assertEqual(self.auth(extra={"Authorization": f"Bearer {self.token}"}).status, 302)
        self.assertEqual(self.auth(cookie=self.gui_cookie().replace("devc_session", "devc_proxy")).status, 302)

    def test_proxy_cookie_does_not_open_the_gui(self):
        cookie = self.login()
        status, error = self.request("GET", "/api/containers", headers={**VIA, "Cookie": cookie.replace("devc_proxy", "devc_session")}, auth=False)
        self.assertEqual(status, 401)

    def test_stopped_and_foreign_container(self):
        cookie = self.login()
        core.stop(self.cfg, "ab")
        self.server.app.targets.clear()
        response = self.auth(cookie=cookie)
        self.assertEqual(response.status, 404)
        self.provider.add("fremd")
        self.assertEqual(self.auth(host="8000-fremd.dev.example", cookie=self.login("8000-fremd.dev.example")).status, 404)

    def test_public_port_needs_no_login(self):
        """Addon-Link mit `public = true` (SPEC 8.11): für Aufrufer, die den Anmeldeweg nicht mitmachen, etwa API-Clients."""
        self.job("POST", "/api/containers", 'name = "api"\naddons = ["webproxy"]\n\n[data]\ncode = "volume"\n\n[params.webproxy]\nproxy = "3000"\npublic = "8080=API"\n')
        for method in ("GET", "POST", "DELETE"):
            response = self.auth(host="8080-api.dev.example", method=method, uri="/v1/x?y=1")
            self.assertEqual((response.status, response.getheader("X-Devc-Upstream")), (200, "10.0.0.5:8080"), method)
            self.assertIsNone(response.getheader("Set-Cookie"))
        # der andere Port desselben Containers und derselbe Port eines anderen Containers bleiben geschützt
        self.assertEqual(self.auth(host="3000-api.dev.example").status, 302)
        self.assertEqual(self.auth(host="8080-ab.dev.example").status, 302)
        self.assertEqual(self.auth(host="8080-gibtsnicht.dev.example").status, 302)
        # gestoppt: ohne Anmeldung nicht von »unbekannt« unterscheidbar
        core.stop(self.cfg, "api")
        self.server.app.targets.clear()
        self.assertEqual(self.auth(host="8080-api.dev.example").status, 302)

    def test_low_port_is_forbidden(self):
        self.assertEqual(self.auth(host="80-ab.dev.example", cookie=self.login("80-ab.dev.example")).status, 403)

    def test_unknown_names(self):
        for host in ("devc.dev.example", "ab.dev.example", "8000-ab.evil.example", ""):
            self.assertEqual(self.auth(host=host).status, 404, host)

    def test_plain_http_is_refused(self):
        self.assertEqual(self.auth(proto="http").status, 403)

    def test_only_the_local_proxy_may_ask(self):
        from unittest import mock
        from devc import server
        with mock.patch.object(server.Handler, "_peer", lambda handler: "10.88.181.46"):
            self.assertEqual(self.auth().status, 403)

    def test_login_checks_the_target(self):
        cookie = self.gui_cookie()
        for to in ("https://evil.example/", "//evil.example/", "https://devc.dev.example/", "http://8000-ab.dev.example/", ""):
            status, error = self.request("GET", "/api/proxy/login?to=" + quote(to, safe=""), headers={**VIA, "Cookie": cookie}, auth=False)
            self.assertEqual((status, error["error"]), (400, "E_USAGE"), to)

    def test_upstream_is_cached_briefly(self):
        cookie = self.login()
        before = len(self.provider.called("state"))
        for _ in range(5):
            self.assertEqual(self.auth(cookie=cookie).status, 200)
        self.assertLessEqual(len(self.provider.called("state")) - before, 1)

    def test_logout_ends_proxy_sessions(self):
        cookie = self.login()
        self.request("POST", "/api/logout", headers={**VIA, "Cookie": self.gui_cookie(), "X-Devc": "1"}, auth=False)
        self.assertEqual(self.auth(cookie=cookie).status, 302)

    def test_config_endpoint(self):
        status, text = self.request("GET", "/api/proxy/config", headers=VIA)
        self.assertEqual(status, 200)
        self.assertIn("*.dev.example", text)


class LocalProxyAuthTest(AuthMixin, ServerTestCase):
    """Weg A: der Browser kommt über einen eigenen Port und, bei localhost, auch unverschlüsselt."""

    config_text = LOCAL

    def test_redirects_keep_scheme_and_port_of_the_request(self):
        response = self.auth(host="8000-ab.localhost:8080", proto="http", uri="/x")
        self.assertEqual(response.status, 302)
        location = urlsplit(response.getheader("Location"))
        self.assertEqual((location.scheme, location.netloc), ("http", "devc.localhost:8080"))
        self.assertEqual(parse_qs(location.query), {"to": ["http://8000-ab.localhost:8080/x"]})


class NoProxyTest(ServerTestCase):
    def test_endpoints_do_not_exist_without_proxy(self):
        self.assertEqual(self.request("GET", "/api/proxy/auth", auth=False)[0], 404)
        self.assertEqual(self.request("GET", "/api/proxy/login?to=x", auth=False)[0], 404)


class CaddyfileTest(LifecycleTestCase):
    def test_needs_base_domain(self):
        with self.assertRaises(ValidationError) as ctx:
            core.proxy_config(self.cfg)
        self.assertEqual((ctx.exception.details["check"], ctx.exception.details["path"]), ("L14", "server.base_domain"))

    def test_content(self):
        self.write_config(PROXY + "port = 9000\n")
        text = core.proxy_config(self.cfg)["caddyfile"]
        for part in ("devc.dev.example {", "*.dev.example {", "reverse_proxy 127.0.0.1:9000", "forward_auth 127.0.0.1:9000",
                     "uri /api/proxy/auth", "copy_headers X-Devc-Upstream", "reverse_proxy {http.request.header.X-Devc-Upstream}",
                     "header_up -X-Devc-Upstream", "handle_errors 502 503 504 {", "lauscht nichts", 'header_up Cookie "devc_(proxy|session)=[^;]*(; *)?" ""', "tls internal"):
            self.assertIn(part, text)
        self.assertNotIn("http://devc.", text)

    def test_own_certificate(self):
        """Eigene CA als Standard, echte Zertifikate als Option in der Konfiguration."""
        self.write_config(PROXY + 'tls_cert = "/etc/devc/wild.pem"\ntls_key = "/etc/devc/wild.key"\n')
        text = core.proxy_config(self.cfg)["caddyfile"]
        self.assertEqual(text.count("tls /etc/devc/wild.pem /etc/devc/wild.key"), 2)
        self.assertNotIn("tls internal", text)
        self.write_config(PROXY + 'tls_cert = "/etc/devc/wild.pem"\n')
        with self.assertRaises(ValidationError):
            core.proxy_config(self.cfg)
        self.write_config(PROXY + 'tls_cert = "/etc/devc/wild pem"\ntls_key = "/k"\n')
        with self.assertRaises(ValidationError):
            core.proxy_config(self.cfg)

    def test_http_port_only_for_localhost(self):
        self.write_config(PROXY)
        with self.assertRaises(UsageError):
            core.proxy_config(self.cfg, http_port=8080)
        self.write_config(LOCAL)
        text = core.proxy_config(self.cfg, http_port=8080)["caddyfile"]
        self.assertIn("http://devc.localhost:8080 {", text)
        self.assertIn("http://*.localhost:8080 {", text)
        for port in (0, 70000):
            with self.assertRaises(UsageError):
                core.proxy_config(self.cfg, http_port=port)

    def test_info_page(self):
        """Aufruf über die IP-Adresse: Hinweisseite statt leerer Antwort."""
        self.write_config(LOCAL)
        text = core.proxy_config(self.cfg, http_port=8080)["caddyfile"]
        for part in ("\nhttp://:80 {", "\nhttp://:8080 {", 'href="http://devc.localhost:8080/"', "devc token show",
                     "ssh -N -L 8080:{http.request.local.host}:8080", "socat TCP-LISTEN:8080,bind=127.0.0.1,fork,reuseaddr TCP:{http.request.local.host}:8080",
                     "default-src 'none'"):
            self.assertIn(part, text)
        self.assertNotIn("{http.request.host}", text)  # der Host-Kopf kommt vom Aufrufer und gehört nicht in die Seite
        text = core.proxy_config(self.cfg)["caddyfile"]  # ohne unverschlüsselten Port: Tunnel auf 443
        self.assertIn("\nhttp://:80 {", text)
        self.assertNotIn("http://:8080", text)
        self.assertIn('href="https://devc.localhost:8443/"', text)
        self.assertIn("ssh -N -L 8443:{http.request.local.host}:443", text)
        self.write_config(PROXY)  # echte Domain: kein Tunnel, nur Name und Token
        text = core.proxy_config(self.cfg)["caddyfile"]
        self.assertIn('href="https://devc.dev.example/"', text)
        self.assertNotIn("ssh -N", text)
        self.assertIn("devc token show", text)
        self.write_config(LOCAL)
        self.assertEqual(core.proxy_config(self.cfg, http_port=80)["caddyfile"].count("\nhttp://:80 {"), 1)

    def test_cli(self):
        self.write_config(LOCAL)
        code, out, err = run_cli("proxy", "config")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("*.localhost {", out)
        self.assertTrue(out.startswith("#"))
        code, out, err = run_cli("proxy", "config", "--http-port", "8080")
        self.assertIn("http://*.localhost:8080 {", out)
        self.write_config("")
        code, out, err = run_cli("proxy", "config")
        self.assertEqual(code, 5)


del ServerTestCase, LifecycleTestCase


if __name__ == "__main__":
    unittest.main()
