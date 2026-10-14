"""HTTP-API mit FakeProvider: jeder Endpunkt, T-Auth, T-Bind, T-Errors, SSE."""

import gzip
import http.client
import io
import json
import tarfile
import threading
import time
import unittest
from unittest import mock

from devc import config, core, security, server
from devc.errors import EngineError, JobFailedError, SecurityError, UsageError, ValidationError
from tests import fakes
from tests.helpers import run_cli
from tests.test_apply_actions import GOOD, tar_bytes
from tests.test_backup import tgz
from tests.test_lifecycle import SECRET, LifecycleTestCase

MINE = 'id = "mine"\ntitle = "Meins"\nversion = "1.0.0"\n\n[data]\ncode = "volume"\n'
INSTANCE = 'name = "ab"\naddons = []\n\n[data]\ncode = "volume"\n'


class ServerTestCase(LifecycleTestCase):
    options: dict = {}
    config_text = ""  # Inhalt der config.toml, z. B. für den Betrieb hinter einem Proxy
    request_headers: dict = {}

    def setUp(self):
        super().setUp()
        if self.config_text:
            self.write_config(self.config_text)
        core.init(self.cfg)
        self.token = core.token_show(self.cfg)["token"]
        self.server = server.make_server(self.cfg, port=0, **self.options)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None, auth=True, raw=False):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = {**self.request_headers, **(headers or {})}
        if auth and "Authorization" not in headers and "Cookie" not in headers:
            headers["Authorization"] = f"Bearer {self.token}"
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        elif isinstance(body, str):
            body = body.encode()
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        if raw:
            return response, data
        kind = response.getheader("Content-Type", "")
        if kind.startswith("application/json"):
            return response.status, json.loads(data)
        return response.status, data.decode("utf-8", "replace")

    def wait_job(self, job_id):
        for _ in range(400):
            status, job = self.request("GET", f"/api/jobs/{job_id}")
            if job["state"] in ("ok", "failed"):
                return job
            time.sleep(0.02)
        self.fail("Job wurde nicht fertig")

    def job(self, method, path, body=None, expect="ok"):
        status, data = self.request(method, path, body)
        self.assertEqual(status, 202, data)
        job = self.wait_job(data["job"])
        self.assertEqual(job["state"], expect, job["log"])
        return job

    def make(self, name="ab"):
        return self.job("POST", "/api/containers", INSTANCE.replace('"ab"', f'"{name}"'))


class EndpointTest(ServerTestCase):
    def test_status_and_static(self):
        self.assertEqual(self.request("GET", "/api/status"), (200, {
            "version": "1.0.0", "platform": "lxd", "bind": "127.0.0.1", "insecure": False, "behind_proxy": False,
            "terminal": core.web_file("vendor", "xterm.js").is_file(), "terminal_allowed": True}))
        response, data = self.request("GET", "/", auth=False, raw=True)
        self.assertEqual((response.status, response.getheader("Content-Type")), (200, "text/html; charset=utf-8"))
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        for path in ("/../devc/core.py", "/%2e%2e/%2e%2e/etc/passwd", "/vendor/../../core.py", "/fehlt.js"):
            self.assertEqual(self.request("GET", path, auth=False)[0], 404, path)
        self.assertEqual(self.request("GET", "/app.js", auth=False)[0], 200)

    def test_addons(self):
        status, data = self.request("GET", "/api/addons")
        self.assertEqual((status, len(data["addons"])), (200, 12))
        status, data = self.request("GET", "/api/addons/httpserver")
        self.assertEqual((data["id"], "readme" in data), ("httpserver", True))
        self.assertEqual(self.request("PUT", "/api/addons", tar_bytes(GOOD)), (201, {"id": "demo", "version": "1.2.0"}))
        response, data = self.request("GET", "/api/addons/demo/archive", raw=True)
        self.assertEqual((response.status, response.getheader("Content-Type")), (200, "application/gzip"))
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            self.assertIn("demo/addon.toml", archive.getnames())
        self.assertEqual(self.request("DELETE", "/api/addons/demo")[0], 204)
        self.assertEqual(self.request("DELETE", "/api/addons/demo")[0], 404)
        status, error = self.request("PUT", "/api/addons", tar_bytes(GOOD + [("zwei", None), ("zwei/x", "x")]))
        self.assertEqual((status, error["details"]["check"]), (422, "L16"))

    def test_templates_and_drafts(self):
        self.assertEqual(len(self.request("GET", "/api/templates")[1]["templates"]), 3)
        status, data = self.request("PUT", "/api/templates/mine", MINE)
        self.assertEqual((status, data), (200, {"findings": []}))
        status, data = self.request("GET", "/api/templates/mine")
        self.assertEqual((data["meta"]["origin"], data["text"]), ("own", MINE))
        status, data = self.request("POST", "/api/drafts", {"template": "mine", "name": "ab"})
        self.assertIn('name = "ab"\ntemplate = "mine@1.0.0"', data["text"])
        status, data = self.request("POST", "/api/drafts", {"name": "ab"})
        self.assertIn('addons = ["claude-code"]', data["text"])
        self.assertEqual(self.request("DELETE", "/api/templates/mine")[0], 204)
        self.assertEqual(self.request("DELETE", "/api/templates/pydev")[0], 409)
        status, data = self.request("POST", "/api/validate/instance", INSTANCE.replace("addons = []", 'addons = ["abap"]\n[secrets]\n"git.key" = "x"'))
        self.assertEqual(status, 200)
        self.assertIn("L5", [f["check"] for f in data["findings"]])
        status, data = self.request("POST", "/api/validate/instance", INSTANCE + '[secrets]\n"git.key" = "fehlt"\n')
        self.assertEqual(data["missing_secrets"], ["git.key"])

    def test_container_lifecycle(self):
        job = self.make()
        self.assertEqual((job["kind"], job["container"]), ("create", "ab"))
        self.assertIn("docs     CLAUDE.md", job["log"])
        status, data = self.request("GET", "/api/containers")
        self.assertEqual([(c["name"], c["status"]) for c in data["containers"]], [("ab", "running")])
        self.assertEqual(self.request("GET", "/api/containers/ab")[1]["status"], "running")
        self.assertEqual(self.request("POST", "/api/containers/ab/stop"), (200, {"status": "stopped"}))
        self.assertEqual(self.request("POST", "/api/containers/ab/start"), (200, {"status": "running"}))
        self.assertEqual(self.request("POST", "/api/containers/ab/restart"), (200, {"status": "running"}))
        self.job("DELETE", "/api/containers/ab?keep_data=1")
        self.assertEqual(self.provider.called("delete")[0][1], ("ab", True))
        self.assertEqual(self.request("GET", "/api/containers")[1]["containers"], [])

    def test_config_and_actions(self):
        self.job("POST", "/api/containers", core.draft(self.cfg, "py-tools", "pydev")["text"].replace('[secrets]                    # nur Namen im geteilten Store, nie Werte\n"claude-code.api_key" = "anthropic"\n', ""))
        status, text = self.request("GET", "/api/containers/py-tools/config")
        self.assertIn('name = "py-tools"', text)
        status, data = self.request("PUT", "/api/containers/py-tools/config", text.replace("port = 8000", "port = 9000"))
        self.assertEqual((status, data["findings"]), (200, []))
        status, data = self.request("POST", "/api/containers/py-tools/config/apply?dry_run=1")
        self.assertEqual(data["plan"]["install"], [{"addon": "httpserver", "reason": "Parameter geändert"}])
        self.job("POST", "/api/containers/py-tools/config/apply")
        self.assertEqual(self.request("PUT", "/api/containers/py-tools/config", text.replace("cpu = 4", "cpu = 0"))[0], 422)

        status, data = self.request("GET", "/api/containers/py-tools/addons")
        http_addon = [a for a in data["addons"] if a["id"] == "httpserver"][0]
        self.assertEqual((http_addon["id"], http_addon["actions"][0]["status"], http_addon["links"][0]["url"]),
                         ("httpserver", "stopped", "http://10.0.0.5:9000/"))
        self.job("POST", "/api/containers/py-tools/actions/httpserver.start")
        self.assertEqual(self.request("GET", "/api/containers/py-tools/addons")[1]["addons"][-2]["links"][0]["active"], True)
        self.assertEqual(self.request("POST", "/api/containers/py-tools/actions/httpserver.start")[0], 409)
        self.job("POST", "/api/containers/py-tools/actions/httpserver.stop")
        self.assertEqual(self.request("POST", "/api/containers/py-tools/actions/x.y")[0], 404)

    def test_secrets(self):
        self.job("POST", "/api/containers", core.draft(self.cfg, "py-tools", "pydev")["text"].replace('"claude-code.api_key" = "anthropic"\n', ""))
        self.assertEqual(self.request("PUT", "/api/secrets/anthropic", SECRET)[0], 204)
        status, data = self.request("GET", "/api/secrets")
        self.assertEqual([(s["name"], s["used_by"]) for s in data["secrets"]], [("anthropic", [])])
        self.assertEqual(self.request("PUT", "/api/containers/py-tools/secrets/claude-code.api_key", {"store": "anthropic"})[0], 204)
        status, data = self.request("GET", "/api/containers/py-tools/secrets")
        self.assertEqual([(s["slot"], s["source"]) for s in data["secrets"]], [("claude-code.api_key", "store:anthropic"), ("claude-code.auth_token", "none"), ("claude-code.oauth_token", "none"), ("git.token", "none")])
        self.assertEqual(self.provider.secrets["py-tools"]["/run/devc/secrets/claude-code.api_key"], SECRET)
        self.assertEqual(self.request("PUT", "/api/containers/py-tools/secrets/claude-code.api_key", {"value": "lokal"})[0], 204)
        self.assertEqual(self.request("PUT", "/api/containers/py-tools/secrets/claude-code.api_key", {"value": "a", "store": "b"})[0], 400)
        self.assertEqual(self.request("DELETE", "/api/containers/py-tools/secrets/claude-code.api_key")[0], 204)
        self.assertEqual(self.request("DELETE", "/api/secrets/anthropic")[0], 204)
        self.assertEqual(self.request("DELETE", "/api/secrets/anthropic")[0], 404)
        # nie Werte in Antworten
        for path in ("/api/secrets", "/api/containers/py-tools/secrets", "/api/containers/py-tools", "/api/jobs"):
            self.assertNotIn(SECRET.decode(), json.dumps(self.request("GET", path)[1]))

    def test_snapshots_backups_restore(self):
        self.make()
        self.provider.areas["ab"] = {"/work": tgz({"./a.txt": b"A"}), "/home/dev": tgz({"./.rc": b"B"})}
        self.job("POST", "/api/containers/ab/snapshots", {"label": "s1"})
        self.job("POST", "/api/containers/ab/snapshots", {"full": True})
        status, data = self.request("GET", "/api/containers/ab/snapshots")
        self.assertEqual([s["scope"] for s in data["snapshots"]], ["data", "full"])
        self.job("POST", "/api/containers/ab/snapshots/s1/restore")
        self.assertEqual(self.request("DELETE", "/api/containers/ab/snapshots/s1")[0], 204)
        self.assertEqual(self.request("POST", "/api/containers/ab/snapshots/s1/restore")[0], 404)

        self.job("POST", "/api/containers/ab/backups", {"label": "x", "include_home": False})
        status, data = self.request("GET", "/api/containers/ab/backups")
        file = data["backups"][0]["file"]
        self.assertRegex(file, r"^ab-\d{8}-\d{6}-x\.devc\.tar$")
        response, archive = self.request("GET", f"/api/backups/{file}", raw=True)
        self.assertEqual((response.status, response.getheader("Content-Type")), (200, "application/x-tar"))
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            self.assertEqual(tar.getnames(), ["manifest.toml", "devc.toml", "data/work.tar.gz", "SHA256SUMS"])

        status, data = self.request("POST", "/api/restore/inspect", {"backup": file})
        self.assertEqual((status, data["checks"][0]["ok"], "upload_id" in data), (200, True, False))
        status, data = self.request("POST", "/api/restore/inspect", archive, {"Content-Type": "application/x-tar"})
        upload_id = data["upload_id"]
        self.assertIn('name = "ab"', data["devc_toml"])
        text = data["devc_toml"].replace('name = "ab"', 'name = "neu"')
        self.job("POST", "/api/restore", {"source": {"upload_id": upload_id}, "target": "new", "name": "neu", "devc_toml": text})
        self.assertEqual(gzip.decompress(self.provider.areas["neu"]["/work"]), gzip.decompress(self.provider.areas["ab"]["/work"]))
        self.assertEqual(list(self.cfg.paths.uploads.iterdir()), [])  # Upload nach dem Restore entfernt
        self.job("POST", "/api/restore", {"source": {"backup": file}, "target": "replace", "name": "ab"})
        self.assertEqual(self.request("POST", "/api/restore", {"source": {"upload_id": "u-0000000000000000"}, "target": "new", "name": "xy"})[0], 404)
        self.assertEqual(self.request("POST", "/api/restore", {"source": {"backup": file}, "target": "kaputt", "name": "xy"})[0], 400)
        status, error = self.request("POST", "/api/restore/inspect", archive[:3000] + b"x" * 100 + archive[3100:], {"Content-Type": "application/x-tar"})
        self.assertEqual(status, 422)
        self.assertEqual(list(self.cfg.paths.uploads.iterdir()), [])
        self.assertEqual(self.request("DELETE", f"/api/backups/{file}")[0], 204)
        self.assertEqual(self.request("GET", f"/api/backups/{file}")[0], 404)
        self.assertEqual(self.request("GET", "/api/backups/..%2F..%2Fdevc.sqlite")[0], 404)

    def test_addon_params(self):
        self.make()
        status, error = self.request("PUT", "/api/containers/ab/addons/httpserver/params", {"params": {"port": 8010}})
        self.assertEqual((status, error["error"]), (404, "E_NOT_FOUND"))  # Addon ist in diesem Container nicht aktiv
        status, error = self.request("PUT", "/api/containers/ab/addons/httpserver/params", {"params": {}})
        self.assertEqual((status, error["error"]), (400, "E_USAGE"))

    def test_addon_install(self):
        """Addon über die API ergänzen: wie `devc install`, mit den ersten Einstellungen."""
        self.make()
        for body in ({}, {"addons": []}, {"addons": "webproxy"}, {"addons": ["webproxy"], "params": []}, {"addons": ["webproxy"], "params": {"webproxy": "3000"}}):
            status, error = self.request("POST", "/api/containers/ab/addons", body)
            self.assertEqual((status, error["error"]), (400, "E_USAGE"), body)
        status, error = self.request("POST", "/api/containers/ab/addons", {"addons": ["gibtsnicht"]})
        self.assertEqual(status, 422)
        status, error = self.request("POST", "/api/containers/ab/addons", {"addons": ["webproxy"], "params": {"webproxy": {"proxy": "80"}}})
        self.assertEqual(status, 422)
        self.assertNotIn("webproxy", [a["id"] for a in self.request("GET", "/api/containers/ab/addons")[1]["addons"]])
        self.job("POST", "/api/containers/ab/addons", {"addons": ["webproxy", "packages"], "params": {"webproxy": {"proxy": "3000=Vite"}, "packages": {"recommends": True}}})
        found = {a["id"]: a for a in self.request("GET", "/api/containers/ab/addons")[1]["addons"]}
        # Secrets des neuen Addons kommen mit; ein fremder Slot wird abgelehnt, bevor sich etwas ändert.
        for secrets in ({"git.token": ""}, {"git.token": 3}, []):
            status, error = self.request("POST", "/api/containers/ab/addons", {"addons": ["git"], "secrets": secrets})
            self.assertEqual((status, error["error"]), (400, "E_USAGE"), secrets)
        for slot in ("git.username", "git.fehlt", "claude-code.api_key", "kaputt"):
            status, error = self.request("POST", "/api/containers/ab/addons", {"addons": ["git"], "secrets": {slot: "x"}})
            self.assertEqual((status, error["error"]), (400, "E_USAGE"), slot)
        self.assertNotIn("git", [a["id"] for a in self.request("GET", "/api/containers/ab/addons")[1]["addons"]])
        self.job("POST", "/api/containers/ab/addons", {"addons": ["git"], "params": {"git": {"username": "alex"}}, "secrets": {"git.token": "tok-4711"}})
        listed = {s["slot"]: s["source"] for s in self.request("GET", "/api/containers/ab/secrets")[1]["secrets"]}
        self.assertEqual(listed["git.token"], "local")
        self.assertEqual(self.cfg.paths.secrets.joinpath("ab", "git.token").read_bytes(), b"tok-4711")
        self.assertNotIn("tok-4711", "".join(path.read_text() for path in self.cfg.paths.logs.iterdir()))
        self.assertEqual({p["name"]: p["value"] for p in found["webproxy"]["params"]}["proxy"], "3000=Vite")
        self.assertEqual({p["name"]: p["value"] for p in found["packages"]["params"]}["recommends"], True)

    def test_files(self):
        self.make()
        listing = "d\t4096\t1791500000.5\tsrc\nf\t12\t1791500001.0\tb.txt\nf\t3\t1791500002.0\tA.txt\n"
        self.provider.exec_results.append((["find", "/work"], fakes.ExecResult(0, listing)))
        status, data = self.request("GET", "/api/containers/ab/files?path=/work")
        self.assertEqual((data["path"], [(e["name"], e["type"], e["size"]) for e in data["entries"]]),
                         ("/work", [("src", "dir", 4096), ("A.txt", "file", 3), ("b.txt", "file", 12)]))
        self.assertEqual(self.request("PUT", "/api/containers/ab/files/raw?path=src/neu.txt", b"inhalt")[0], 204)
        write = [c for c in self.provider.called("exec") if c[2]["stdin"] == b"inhalt"][0]
        self.assertEqual((write[1][1][-1], write[2]["user"]), ("/work/src/neu.txt", "dev"))
        self.provider.areas["ab"] = {"/work/src": tgz({"./a.txt": b"A"})}
        self.provider.exec_results.insert(0, (["sh", "-c", 'if [ -d "$1" ]; then echo d; elif [ -f "$1" ]; then echo f; else exit 44; fi', "sh", "/work/src"], fakes.ExecResult(0, "d\n")))
        response, data = self.request("GET", "/api/containers/ab/files/raw?path=src", raw=True)
        self.assertEqual((response.status, response.getheader("Content-Type"), data), (200, "application/gzip", self.provider.areas["ab"]["/work/src"]))
        self.assertIn("src.tar.gz", response.getheader("Content-Disposition"))
        self.assertEqual(self.request("DELETE", "/api/containers/ab/files/raw?path=src/neu.txt")[0], 204)
        self.assertEqual(self.provider.called("exec")[-1][1][1][-1], "/work/src/neu.txt")
        status, error = self.request("DELETE", "/api/containers/ab/files/raw?path=/work")
        self.assertEqual((status, error["error"]), (400, "E_USAGE"))
        self.provider.blobs["ab"] = {"/work/p.tar.gz": tgz({"p/a.txt": b"A"})}
        status, data = self.request("GET", "/api/containers/ab/files/archive?path=p.tar.gz")
        self.assertEqual((status, data["top"], data["count"]), (200, ["p"], 1))
        status, data = self.request("POST", "/api/containers/ab/files/unpack", {"path": "p.tar.gz", "into": "ziel"})
        self.assertEqual((status, data["into"], data["files"], self.provider.unpacked["ab"][0][0]), (200, "/work/ziel", 1, "/work/ziel"))
        status, error = self.request("POST", "/api/containers/ab/files/unpack", {"path": "p.tar.gz", "into": "/etc"})
        self.assertEqual((status, error["error"]), (403, "E_SECURITY"))
        for path in ("/etc/passwd", "../x", "/work/../etc"):
            for method, url in (("GET", "/api/containers/ab/files?path="), ("GET", "/api/containers/ab/files/raw?path="), ("PUT", "/api/containers/ab/files/raw?path="),
                                ("DELETE", "/api/containers/ab/files/raw?path="), ("GET", "/api/containers/ab/files/archive?path=")):
                status, error = self.request(method, url + path, b"x" if method == "PUT" else None)
                self.assertEqual((status, error["error"], error["details"]["check"]), (403, "E_SECURITY", "L13"))

    def test_jobs_and_sse(self):
        job = self.make()
        status, data = self.request("GET", "/api/jobs?container=ab")
        self.assertEqual([j["id"] for j in data["jobs"]], [job["id"]])
        self.assertEqual(self.request("GET", "/api/jobs?container=anderer")[1]["jobs"], [])
        self.assertEqual(self.request("GET", "/api/jobs/j-ffffffffffff")[0], 404)
        response, data = self.request("GET", f"/api/jobs/{job['id']}/events", raw=True)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
        events = [block.split("\n") for block in data.decode().strip().split("\n\n")]
        kinds = [block[0] for block in events]
        self.assertEqual(kinds[-2:], ["event: state", "event: end"])
        self.assertEqual(set(kinds[:-2]), {"event: log"})
        self.assertEqual([block[1][len("data: "):] for block in events[:-2]], job["log"].splitlines())
        self.assertEqual(json.loads(events[-2][1][len("data: "):]), {"state": "ok", "error": None})
        self.assertEqual(self.request("GET", "/api/jobs/j-ffffffffffff/events")[0], 404)

    def test_failing_job_is_reported(self):
        self.provider.stream_rc["install.sh"] = 1
        job = self.job("POST", "/api/containers", INSTANCE.replace("addons = []", 'addons = ["python"]'), expect="failed")
        self.assertEqual(job["error_code"], "E_JOB_FAILED")
        response, data = self.request("GET", f"/api/jobs/{job['id']}/events", raw=True)
        self.assertIn('data: {"state": "failed", "error": "E_JOB_FAILED"}', data.decode())
        self.assertTrue(self.request("GET", "/api/containers")[1]["containers"][0]["incomplete"])

    def test_cli_jobs(self):
        job = self.make()
        code, out, err = run_cli("jobs", "--container", "ab")
        self.assertRegex(out, rf"(?m)^{job['id']}\s+ab\s+create\s+ok\s+-\s+\S+$")
        code, out, err = run_cli("jobs", "--follow", job["id"])
        self.assertEqual((code, out), (0, job["log"]))
        self.assertEqual(run_cli("jobs", "--follow", "j-ffffffffffff")[0], 4)


class AuthTest(ServerTestCase):
    """T-Auth."""

    def login(self):
        response, _ = self.request("POST", "/api/login", {"token": self.token}, auth=False, raw=True)
        self.assertEqual(response.status, 204)
        cookie = response.getheader("Set-Cookie")
        for part in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(part, cookie)
        self.assertNotIn("Secure", cookie)  # kein TLS
        return cookie.split(";")[0]

    def test_bearer(self):
        self.assertEqual(self.request("GET", "/api/status")[0], 200)
        for headers in ({}, {"Authorization": "Bearer falsch"}, {"Authorization": f"Basic {self.token}"}, {"Authorization": "Bearer "}):
            status, error = self.request("GET", "/api/status", headers=headers, auth=False)
            self.assertEqual((status, error["error"]), (401, "E_AUTH"))

    def test_every_api_route_needs_auth(self):
        public = [(method, pattern.pattern) for method, pattern, _, is_public in server.ROUTES if is_public]
        # Die beiden Proxy-Pfade prüfen Ticket bzw. Cookie selbst (SPEC 8.11) und antworten ohne Proxy-Betrieb mit 404.
        self.assertEqual(public, [("POST", "^/api/login$"), ("GET", "^/api/proxy/auth$"), ("GET", "^/api/proxy/login$")])
        for method, path in (("GET", "/api/containers"), ("GET", "/api/secrets"), ("POST", "/api/containers"),
                             ("DELETE", "/api/containers/ab"), ("GET", "/api/jobs"), ("PUT", "/api/secrets/x"),
                             ("GET", "/api/containers/ab/terminal"), ("GET", "/api/backups/x")):
            self.assertEqual(self.request(method, path, b"x" if method in ("POST", "PUT") else None, auth=False)[0], 401, path)

    def test_login_and_cookie(self):
        self.assertEqual(self.request("POST", "/api/login", {"token": "falsch"}, auth=False)[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {}, auth=False)[0], 401)
        cookie = self.login()
        self.assertEqual(self.request("GET", "/api/status", headers={"Cookie": cookie})[0], 200)
        # ändernde Anfrage mit Cookie braucht X-Devc: 1
        status, error = self.request("PUT", "/api/secrets/abc", b"wert", headers={"Cookie": cookie})
        self.assertEqual((status, error["error"]), (401, "E_AUTH"))
        self.assertEqual(self.request("PUT", "/api/secrets/abc", b"wert", headers={"Cookie": cookie, "X-Devc": "1"})[0], 204)
        self.assertEqual(self.request("POST", "/api/logout", headers={"Cookie": cookie, "X-Devc": "1"})[0], 204)
        self.assertEqual(self.request("GET", "/api/status", headers={"Cookie": cookie})[0], 401)
        self.assertEqual(self.request("GET", "/api/status", headers={"Cookie": "devc_session=erfunden"})[0], 401)

    def test_expired_session(self):
        now = [1000.0]
        self.server.app.sessions = security.Sessions(clock=lambda: now[0])
        cookie = self.login()
        now[0] += security.SESSION_SECONDS - 1
        self.assertEqual(self.request("GET", "/api/status", headers={"Cookie": cookie})[0], 200)
        now[0] += 2
        self.assertEqual(self.request("GET", "/api/status", headers={"Cookie": cookie})[0], 401)

    def test_token_rotation_takes_effect(self):
        old = self.token
        new = core.token_rotate(self.cfg)["token"]
        self.assertEqual(self.request("GET", "/api/status", headers={"Authorization": f"Bearer {old}"})[0], 401)
        self.assertEqual(self.request("GET", "/api/status", headers={"Authorization": f"Bearer {new}"})[0], 200)

    def test_host_header(self):
        for host, expected in ((f"localhost:{self.port}", 200), ("127.0.0.1", 200), (f"[::1]:{self.port}", 200),
                               ("evil.example", 403), (f"evil.example:{self.port}", 403), ("", 403)):
            status, data = self.request("GET", "/api/status", headers={"Host": host})
            self.assertEqual(status, expected, host)
        status, error = self.request("GET", "/", headers={"Host": "evil.example"}, auth=False)
        self.assertEqual((status, error["error"]), (403, "E_SECURITY"))

    def test_configured_allowed_host(self):
        self.assertEqual(security.allowed_hosts(["devbox.lan"]), {"devbox.lan", "localhost", "127.0.0.1", "[::1]"})
        self.assertEqual(security.host_of("DevBox.lan:8740"), "devbox.lan")
        self.assertEqual(security.host_of("[::1]:8740"), "[::1]")


class ErrorTest(ServerTestCase):
    """T-Errors über die API: Status und Fehlerobjekt je Code (CLI-Seite: tests/test_cli.py)."""

    def assert_error(self, result, status, code):
        self.assertEqual((result[0], result[1]["error"]), (status, code))
        self.assertEqual(set(result[1]), {"error", "message", "details"})
        self.assertTrue(result[1]["message"])

    def test_each_code(self):
        self.make()
        self.provider.add("fremd")
        with mock.patch.object(core, "info", side_effect=RuntimeError("geheim-4711")):
            result = self.request("GET", "/api/containers/ab")
            self.assert_error(result, 500, "E_INTERNAL")
            self.assertNotIn("geheim-4711", json.dumps(result[1]))
        with mock.patch.object(core, "info", side_effect=JobFailedError("Schritt schlug fehl", job="j-000000000001")):
            result = self.request("GET", "/api/containers/ab")
            self.assert_error(result, 500, "E_JOB_FAILED")
            self.assertEqual(result[1]["details"], {"job": "j-000000000001"})
        self.assert_error(self.request("POST", "/api/drafts", b"{kein json"), 400, "E_USAGE")
        self.provider.fail["list"] = EngineError("lxc list: kaputt")
        self.assert_error(self.request("GET", "/api/containers"), 502, "E_ENGINE")
        del self.provider.fail["list"]
        self.assert_error(self.request("GET", "/api/containers/fehlt"), 404, "E_NOT_FOUND")
        self.assert_error(self.request("POST", "/api/containers/fremd/start"), 409, "E_CONFLICT")
        result = self.request("PUT", "/api/templates/mine", MINE.replace('"1.0.0"', '"1"'))
        self.assert_error(result, 422, "E_VALIDATION")
        self.assertEqual(result[1]["details"], {"check": "L9", "path": "version"})
        result = self.request("POST", "/api/containers", INSTANCE.replace('"ab"', '"zwei"') + '[secrets]\n"git.key" = "fehlt"\n')
        self.assert_error(result, 422, "E_MISSING_SECRET")
        self.assertEqual(result[1]["details"], {"slots": ["git.key"]})
        self.assert_error(self.request("GET", "/api/containers/ab/files?path=/etc"), 403, "E_SECURITY")
        self.assert_error(self.request("GET", "/api/containers", auth=False), 401, "E_AUTH")

    def test_unknown_paths_and_methods(self):
        self.assert_error(self.request("GET", "/api/gibtsnicht"), 404, "E_NOT_FOUND")
        self.assert_error(self.request("DELETE", "/api/status"), 400, "E_USAGE")
        self.assert_error(self.request("POST", "/irgendwas", b""), 404, "E_NOT_FOUND")

    def test_uploads_need_content_length(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.putrequest("PUT", "/api/secrets/abc")
        connection.putheader("Authorization", f"Bearer {self.token}")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual((response.status, json.loads(response.read())["error"]), (400, "E_USAGE"))
        connection.close()
        with mock.patch.object(server, "MAX_UPLOAD", 10):
            self.assert_error(self.request("PUT", "/api/addons", b"x" * 11), 400, "E_USAGE")
        with mock.patch.object(server, "MAX_TEXT", 10):
            self.assert_error(self.request("PUT", "/api/templates/mine", MINE), 400, "E_USAGE")

    def test_validation_error_before_job_is_synchronous(self):
        status, error = self.request("POST", "/api/containers", INSTANCE.replace("addons = []", 'addons = ["abap"]'))
        self.assertEqual((status, error["error"]), (422, "E_VALIDATION"))
        self.assertEqual(self.request("GET", "/api/jobs")[1]["jobs"], [])
        self.assertEqual(self.request("POST", "/api/containers", "name = ")[0], 422)


class BindTest(LifecycleTestCase):
    """T-Bind: alle Zeilen der Tabelle SPEC 11.2."""

    def test_loopback(self):
        for bind in ("127.0.0.1", "localhost", "::1", "127.0.0.2"):
            mode = security.bind_mode(bind, tls=False)
            self.assertEqual((mode.loopback, mode.insecure, mode.terminal, mode.check_host), (True, False, True, True), bind)
        self.assertFalse(security.bind_mode("127.0.0.1", tls=False, terminal=False).terminal)

    def test_non_loopback_with_tls(self):
        mode = security.bind_mode("0.0.0.0", tls=True)
        self.assertEqual((mode.loopback, mode.insecure, mode.terminal, mode.check_host), (False, False, True, False))

    def test_non_loopback_without_tls_is_refused(self):
        for bind in ("0.0.0.0", "::", "192.168.1.10", "devbox.lan"):
            with self.assertRaises(SecurityError) as ctx:
                security.bind_mode(bind, tls=False)
            self.assertEqual((ctx.exception.code, ctx.exception.exit_code, ctx.exception.details["check"]), ("E_SECURITY", 6, "L14"))
            self.assertIn("--allow-insecure-bind", ctx.exception.message)

    def test_insecure_bind_disables_terminal(self):
        mode = security.bind_mode("0.0.0.0", tls=False, allow_insecure_bind=True)
        self.assertEqual((mode.insecure, mode.terminal, mode.check_host), (True, False, False))
        warning = security.insecure_warning("0.0.0.0", 8740, mode.terminal)
        self.assertTrue(warning.startswith("WARNUNG:"))
        self.assertGreater(len(warning.splitlines()), 1)
        self.assertIn("ausgeschaltet", warning)

    def test_insecure_terminal_flag(self):
        mode = security.bind_mode("0.0.0.0", tls=False, allow_insecure_bind=True, allow_insecure_terminal=True)
        self.assertEqual((mode.insecure, mode.terminal), (True, True))
        self.assertIn("EINGESCHALTET", security.insecure_warning("0.0.0.0", 8740, mode.terminal))
        self.assertFalse(security.bind_mode("0.0.0.0", False, True, True, terminal=False).terminal)

    def test_cli_exit_code(self):
        """`devc serve --bind 0.0.0.0` ohne Flag endet mit Exit 6."""
        core.init(self.cfg)
        code, out, err = run_cli("serve", "--bind", "0.0.0.0", "--port", "0")
        self.assertEqual((code, out), (6, ""))
        self.assertTrue(err.startswith("Fehler [E_SECURITY]: "))

    def test_insecure_server_reports_status_and_blocks_terminal(self):
        core.init(self.cfg)
        srv = server.make_server(self.cfg, bind="0.0.0.0", port=0, allow_insecure_bind=True)
        self.addCleanup(srv.server_close)
        self.assertEqual((srv.app.mode.insecure, srv.app.mode.terminal), (True, False))

    def test_tls_files(self):
        core.init(self.cfg)
        self.assertFalse(security.check_tls_files("", ""))
        cert = self.tmp / "c.pem"
        cert.write_text("x")
        for args in ((str(cert), ""), ("", str(cert)), (str(cert), str(self.tmp / "fehlt.pem"))):
            with self.assertRaises(ValidationError) as ctx:
                security.check_tls_files(*args)
            self.assertEqual(ctx.exception.details["check"], "L14")
        with self.assertRaises(ValidationError):
            server.make_server(self.cfg, bind="0.0.0.0", port=0, tls_cert=str(cert), tls_key=str(self.tmp / "fehlt.pem"))

    def test_serve_needs_token_and_valid_port(self):
        with self.assertRaises(Exception) as ctx:
            server.make_server(self.cfg, port=0)
        self.assertEqual(ctx.exception.code, "E_NOT_FOUND")
        core.init(self.cfg)
        with self.assertRaises(UsageError):
            server.make_server(self.cfg, port=70000)


if __name__ == "__main__":
    unittest.main()
