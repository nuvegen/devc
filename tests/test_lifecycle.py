"""create, list, info, start/stop, rm, exec, docs und Secrets gegen den FakeProvider, inkl. T-Secrets."""

import datetime
import io
import json
import os
import sys
import tomllib
import unittest
from unittest import mock

from devc import __main__ as cli
from devc import config, core, secrets
from devc.errors import ConflictError, JobFailedError, MissingSecretError, NotFoundError, SecurityError, UsageError, ValidationError
from tests import fakes, helpers
from tests.helpers import run_cli

SECRET = b"geheim-4711-nicht-loggen"
STORE_SECRET = b"store-0815-nicht-loggen"


class LifecycleTestCase(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.provider = fakes.FakeProvider()
        ids = iter(f"j-{n:012x}" for n in range(1, 1000))
        start = datetime.datetime(2026, 10, 10, 7, 0, 0, tzinfo=datetime.timezone.utc)
        ticks = iter(start + datetime.timedelta(seconds=n) for n in range(1, 1000000))
        # Uhr und IDs sind injizierbar (SPEC 12): jede Abfrage rückt die Uhr um eine Sekunde vor
        for target, value in (("_provider", lambda cfg: self.provider), ("sleep", lambda s: None),
                              ("new_job_id", lambda: next(ids)), ("clock", lambda: next(ticks))):
            patcher = mock.patch.object(core, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.log = []

    @property
    def cfg(self):
        return config.load()

    def create(self, name="py-tools", **kwargs):
        kwargs.setdefault("template", "pydev")
        kwargs.setdefault("secret_values", {"claude-code.api_key": SECRET})
        return core.create(self.cfg, name, on_log=self.log.append, **kwargs)

    def execs(self, user=None):
        return [call for call in self.provider.called("exec") if user is None or call[2]["user"] == user]


class CreateTest(LifecycleTestCase):
    def test_full_flow(self):
        result = self.create()
        self.assertEqual((result["name"], result["status"], result["job"]), ("py-tools", "running", "j-000000000001"))
        methods = [call[0] for call in self.provider.calls]
        self.assertLess(methods.index("create"), methods.index("start"))
        spec = self.provider.called("create")[0][1][0]
        self.assertEqual((spec.name, spec.image, spec.cpu, spec.memory, spec.disk, spec.shared),
                         ("py-tools", "ubuntu:24.04", 4, "8GiB", "30GiB", None))

        # Schritt 3: devc.toml als Text, Eintrag in containers
        text = self.cfg.paths.instance_file("py-tools").read_text()
        self.assertTrue(text.startswith("# Entwurf aus pydev@1.0.0"))
        self.assertIn("# Mitgeliefertes Template", text)
        row = core._store(self.cfg).container("py-tools")
        self.assertEqual(row["addons"], {"base": "1.1.0", "claude-code": "1.9.0", "python": "1.0.0", "httpserver": "1.1.0", "webproxy": "1.0.0", "git": "1.1.0"})
        self.assertIsNotNone(row["applied_at"])

        # Schritt 5: Laufzeitverzeichnisse als root
        self.assertIn(["install", "-d", "-o", "1000", "-g", "1000", "-m", "0700", "/run/devc", "/run/devc/secrets", "/run/devc/actions"],
                      [call[1][1] for call in self.execs("root")])
        # Schritt 6: Secret über stdin
        self.assertEqual(self.provider.secrets["py-tools"], {"/run/devc/secrets/claude-code.api_key": SECRET})

        # Schritt 7: Reihenfolge und Umgebung der install-Skripte
        streams = self.provider.called("exec_stream")
        self.assertEqual([(c[2]["cwd"], c[1][1]) for c in streams], [
            ("/opt/devc/addons/base", ["bash", "-euo", "pipefail", "install.sh"]),
            ("/opt/devc/addons/claude-code", ["bash", "-euo", "pipefail", "install.sh"]),
            ("/opt/devc/addons/python", ["bash", "-euo", "pipefail", "install.sh"]),
            ("/opt/devc/addons/httpserver", ["bash", "-euo", "pipefail", "install.sh"]),
        ])
        self.assertTrue(all(c[2]["user"] == "root" for c in streams))
        self.assertEqual(streams[1][2]["env"], {
            "DEVC_CONTAINER": "py-tools", "DEVC_ADDON": "claude-code", "DEVC_ADDON_DIR": "/opt/devc/addons/claude-code",
            "DEVC_WORKDIR": "/work", "DEVC_USER": "dev", "DEBIAN_FRONTEND": "noninteractive",
            "DEVC_PARAM_PROVIDER": "anthropic", "DEVC_PARAM_ANTHROPIC_MODEL": "",
            "DEVC_PARAM_CHANNEL": "stable", "DEVC_PARAM_BASE_URL": "", "DEVC_PARAM_MODEL": "", "DEVC_PARAM_CHAT_MODEL": "", "DEVC_PARAM_CHAT_UPDATES": "true", "DEVC_SECRET_API_KEY": "/run/devc/secrets/claude-code.api_key",
        })
        self.assertEqual(streams[3][2]["env"]["DEVC_PARAM_PORT"], "8000")
        self.assertEqual(streams[0][2]["env"]["DEVC_PARAM_SUDO"], "false")
        self.assertEqual(tomllib.loads(self.provider.files["py-tools"]["/etc/devc/addons.toml"]), row["addons"])

        # Schritt 10: Agent-Doku
        files = self.provider.files["py-tools"]
        self.assertIn("# Dev-Container py-tools", files["/work/CLAUDE.md"])
        self.assertIn("## Python 3.12", files["/work/CLAUDE.md"])
        self.assertEqual(files["/work/AGENTS.md"], "-> CLAUDE.md")
        self.assertIn("→ `.devc/addons/httpserver.md`", files["/work/CLAUDE.md"])
        self.assertTrue(files["/work/.devc/README.md"].startswith("Python-Container mit Claude Code."))
        # Infoverzeichnis: Übersicht, eine Seite je Addon des Katalogs, Kopie der Konfiguration ohne Geheimnisse
        self.assertIn("| `httpserver` |", files["/work/.devc/README.md"].split("## Weitere Addons")[0])
        self.assertIn("| `node` |", files["/work/.devc/README.md"].split("## Weitere Addons")[1])
        self.assertIn("| `claude-code.api_key` | gesetzt | `/run/devc/secrets/claude-code.api_key` |", files["/work/.devc/README.md"])
        pages = sorted(path.rsplit("/", 1)[1] for path in files if path.startswith("/work/.devc/addons/"))
        self.assertEqual(pages, sorted(f"{a.id}.md" for a in core.catalog(self.cfg).sorted()))
        self.assertIn("- Stand: aktiv in diesem Container", files["/work/.devc/addons/httpserver.md"])
        self.assertIn("| `port` | `8000` |", files["/work/.devc/addons/httpserver.md"])
        self.assertIn("- Stand: nicht in diesem Container", files["/work/.devc/addons/node.md"])
        copy = files["/work/.devc/config.toml"]
        self.assertIn('name = "py-tools"', copy)
        self.assertIn('"claude-code.api_key" = "***"', copy)
        for path, text in files.items():
            if path.startswith("/work/"):
                self.assertNotIn(SECRET.decode(), text, path)
                self.assertNotIn(STORE_SECRET.decode(), text, path)

        # Job und Log
        job = core._store(self.cfg).job(result["job"])
        self.assertEqual((job["kind"], job["state"], job["container"]), ("create", "ok", "py-tools"))
        self.assertEqual(self.cfg.paths.job_log(result["job"]).read_text().splitlines(), self.log)
        phases = [line.split()[1] for line in self.log]
        for phase in ("template", "create", "mount", "secrets", "addon", "docs", "done"):
            self.assertIn(phase, phases)

    def test_addon_copy_archive_and_files(self):
        self.create("abap-rap", template=None, from_file=None, text=(
            'name = "abap-rap"\naddons = ["node", "abap"]\n[data]\ncode = "volume"\n'
            '[params.abap]\nsap_host = "s4.example"\nsap_client = "100"\nsap_user = "DEV"\n'
        ), secret_values={})
        scripts = [call[1][1] for call in self.execs("root")]
        copy = [a for a in scripts if a[:2] == ["sh", "-c"] and "tar -xzf - -C" in a[2]]
        self.assertEqual([a[-1] for a in copy], ["/opt/devc/addons/base", "/opt/devc/addons/node", "/opt/devc/addons/abap"])
        archive = [a for a in scripts if a[:2] == ["sh", "-c"] and 'tar -xzf "$1" -C "$2"' in a[2]]
        self.assertEqual([a[-2:] for a in archive], [["/opt/devc/addons/abap/files/faq.tar.gz", "/FAQ"]])
        # Das Addon-Archiv geht über stdin und enthält die Dateien des Addons
        import tarfile
        stdin = [c[2]["stdin"] for c in self.execs("root") if c[1][1][-1] == "/opt/devc/addons/abap"][0]
        with tarfile.open(fileobj=io.BytesIO(stdin), mode="r:gz") as tar:
            self.assertEqual(sorted(m.name for m in tar.getmembers()),
                             ["AI.md", "README.md", "addon.toml", "files", "files/faq.tar.gz", "install.sh"])
        env = self.provider.called("exec_stream")[-1][2]["env"]
        self.assertEqual((env["DEVC_PARAM_SAP_HOST"], env["DEVC_PARAM_SAP_PORT"], env["DEVC_PARAM_SAP_SSL"]), ("s4.example", "443", "true"))
        self.assertNotIn("DEVC_SECRET_SAP_PASSWORD", env)  # Slot nicht befüllt
        self.assertIn("Zielsystem: `s4.example`, Mandant `100`, Benutzer `DEV`", self.provider.files["abap-rap"]["/work/CLAUDE.md"])

    def test_addon_files_respect_overwrite(self):
        self.create()
        installs = [c[1][1] for c in self.execs("root") if c[1][1][:2] == ["sh", "-c"] and c[1][1][2] == core._INSTALL_FILE]
        self.assertEqual([a[4:] for a in installs], [["/opt/devc/addons/httpserver/files/index.html", "/work/out/index.html", "0644", "1000", "false"]])

    def test_instance_files_git_and_docs(self):
        text = (
            'name = "ab"\naddons = []\n[data]\ncode = "volume"\n[git]\nremote = "ssh://git@git.example:2222/ab.git"\n'
            '[[files]]\ndest = "/work/.abaplint.json"\noverwrite = false\ncontent = "{}"\n'
            '[[files]]\ndest = "/home/dev/.note"\nmode = "0600"\ncontent = "x"\n'
        )
        self.provider.add("ab")
        self.provider.files["ab"] = {"/work/.abaplint.json": "ALT", "/home/dev/.ssh/config": "Host eigen\n  User ich\n"}
        self.provider.containers.clear()
        self.create("ab", template=None, text=text, secret_values={"git.key": SECRET})
        files = self.provider.files["ab"]
        self.assertEqual((files["/work/.abaplint.json"], files["/home/dev/.note"]), ("ALT", "x"))
        self.assertEqual(files["/home/dev/.ssh/config"], (
            "Host eigen\n  User ich\n# devc:begin\nHost git.example\n  IdentityFile /run/devc/secrets/git.key\n  IdentitiesOnly yes\n# devc:end\n"
        ))
        self.assertIn("- Git-Remote: ssh://git@git.example:2222/ab.git", files["/work/CLAUDE.md"])
        self.assertIn("/run/devc/secrets/git.key", self.provider.secrets["ab"])
        # kein git init, kein push
        for call in self.provider.calls:
            self.assertNotIn("push", " ".join(map(str, call[1][1])) if call[0].startswith("exec") else "")

    def test_git_host_parsing(self):
        self.assertEqual(core.git_host("ssh://git@git.example:2222/x.git"), "git.example")
        self.assertEqual(core.git_host("git@github.com:team/x.git"), "github.com")
        for remote in ("https://git.example/x.git", "ssh://git@[GIT-HOST]/x.git", "git@-oProxyCommand=x:y", "ssh://git@host name/x"):
            with self.subTest(remote=remote):
                self.assertIsNone(core.git_host(remote))

    def test_existing_agents_md_gives_w4(self):
        text = 'name = "ab"\naddons = []\n[data]\ncode = "volume"\n'
        self.provider.files["ab"] = {"/work/AGENTS.md": "eigene Datei"}
        result = self.create("ab", template=None, text=text, secret_values={})
        self.assertEqual(self.provider.files["ab"]["/work/AGENTS.md"], "eigene Datei")
        self.assertTrue(any(w.startswith("W4") for w in result["warnings"]))

    def test_claude_md_without_markers_fails_and_keeps_file(self):
        text = 'name = "ab"\naddons = []\n[data]\ncode = "volume"\n'
        self.provider.files["ab"] = {"/work/CLAUDE.md": "meine Regeln\n"}
        with self.assertRaises(ValidationError) as ctx:
            self.create("ab", template=None, text=text, secret_values={})
        self.assertEqual(self.provider.files["ab"]["/work/CLAUDE.md"], "meine Regeln\n")
        self.assertEqual(ctx.exception.details["job"], "j-000000000001")

    def test_failing_install_keeps_container_incomplete(self):
        self.provider.stream_rc["install.sh"] = 1
        with self.assertRaises(JobFailedError) as ctx:
            self.create()
        self.assertEqual((ctx.exception.code, ctx.exception.details["job"]), ("E_JOB_FAILED", "j-000000000001"))
        self.assertIn("py-tools", self.provider.containers)  # keine Transaktion
        listed = core.list_containers(self.cfg)["containers"][0]
        self.assertEqual((listed["name"], listed["incomplete"], listed["addons"]), ("py-tools", True, {}))
        self.assertEqual(core._store(self.cfg).job("j-000000000001")["state"], "failed")
        self.assertFalse(listed["installing"])
        with core.locks.container_lock(self.cfg.paths, "py-tools"):
            # Solange eine Operation den Lock hält, läuft die Einrichtung noch: nicht »unvollständig« melden.
            self.assertTrue(core.list_containers(self.cfg)["containers"][0]["installing"])
            self.assertTrue(core.info(self.cfg, "py-tools")["installing"])
        self.assertFalse(core.info(self.cfg, "py-tools")["installing"])

    def test_complete_container_is_never_installing(self):
        self.create()
        with core.locks.container_lock(self.cfg.paths, "py-tools"):
            listed = core.list_containers(self.cfg)["containers"][0]
            self.assertEqual((listed["incomplete"], listed["installing"]), (False, False))

    def test_options_are_merged(self):
        root = self.tmp / "srv"
        (root / "py-tools").mkdir(parents=True)
        self.write_config(f'allowed_roots = ["{root}"]\n')
        self.create(shared=str(root / "py-tools"), params=["httpserver.port=9000"], cpu=2, addons=["node"])
        spec = self.provider.called("create")[0][1][0]
        self.assertEqual((spec.cpu, spec.shared), (2, str(root / "py-tools")))
        data = tomllib.loads(self.cfg.paths.instance_file("py-tools").read_text())
        self.assertEqual((data["addons"][-1], data["params"]["httpserver"]["port"]), ("node", 9000))
        self.assertIn(f"- Shared Folder: {root}/py-tools", self.provider.files["py-tools"]["/work/CLAUDE.md"])

    def test_no_start_stops_after_provisioning(self):
        result = self.create(no_start=True)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(self.provider.containers["py-tools"].status, "stopped")
        self.assertIsNotNone(core._store(self.cfg).container("py-tools")["applied_at"])

    def assert_nothing_created(self):
        self.assertEqual(self.provider.called("create"), [])
        self.assertEqual(core._store(self.cfg).containers(), [])
        self.assertFalse(self.cfg.paths.containers.exists() and any(self.cfg.paths.containers.iterdir()))

    def test_validation_errors_change_nothing(self):
        cases = [
            (dict(name="Py_Tools"), ValidationError),
            (dict(template="fehlt"), NotFoundError),
            (dict(addons=["abap"]), ValidationError),
            (dict(params=["httpserver.port=80"]), ValidationError),
            (dict(template="pydev", from_file="x.toml"), UsageError),
            (dict(shared="/gibt/es/nicht"), ValidationError),
            (dict(secret_values={"abap.sap_password": SECRET, "claude-code.api_key": SECRET}), UsageError),
            (dict(text='name = "anders"\n[data]\ncode = "volume"\n', template=None), ValidationError),
        ]
        for kwargs, error in cases:
            with self.subTest(kwargs=kwargs):
                name = kwargs.pop("name", "py-tools")
                with self.assertRaises(error):
                    self.create(name, **kwargs)
                self.assert_nothing_created()

    def test_shared_outside_allowed_roots_is_security_error(self):
        outside = self.tmp / "fremd"
        outside.mkdir()
        with self.assertRaises(SecurityError):
            self.create(shared=str(outside))
        self.assert_nothing_created()

    def test_name_taken(self):
        self.create()
        with self.assertRaises(ConflictError):
            self.create()
        self.provider.add("fremd")
        with self.assertRaises(ConflictError):
            self.create("fremd")

    def test_missing_secret(self):
        with self.assertRaises(MissingSecretError) as ctx:
            self.create(secret_values={})
        self.assertEqual((ctx.exception.code, ctx.exception.details["slots"]), ("E_MISSING_SECRET", ["claude-code.api_key"]))
        self.assert_nothing_created()

    def test_secret_from_store_and_prompt(self):
        secrets.shared_set(self.cfg.paths, "anthropic", STORE_SECRET)
        self.create(secret_values={})
        self.assertEqual(self.provider.secrets["py-tools"]["/run/devc/secrets/claude-code.api_key"], STORE_SECRET)
        self.assertEqual(secrets.local_list(self.cfg.paths, "py-tools")[0]["source"], "store:anthropic")
        secrets.shared_remove(self.cfg.paths, "anthropic")
        asked = []
        self.create("zwei", secret_values={}, ask_secret=lambda slot: asked.append(slot) or SECRET)
        self.assertEqual(asked, ["claude-code.api_key"])
        self.assertEqual(self.provider.secrets["zwei"]["/run/devc/secrets/claude-code.api_key"], SECRET)

    def test_busy_container(self):
        with core.locks.container_lock(self.cfg.paths, "py-tools"):
            with self.assertRaises(ConflictError) as ctx:
                self.create()
        self.assertEqual(ctx.exception.message, "Container ist beschäftigt")


class ManageTest(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.create()
        self.provider.calls.clear()

    def test_list_marks_missing_and_foreign(self):
        self.create("weg")
        del self.provider.containers["weg"]
        self.provider.add("fremd", "stopped")
        listed = {c["name"]: c for c in core.list_containers(self.cfg)["containers"]}
        self.assertEqual({k: v["status"] for k, v in listed.items()}, {"py-tools": "running", "weg": "missing", "fremd": "foreign"})
        self.assertEqual((listed["py-tools"]["ip"], listed["py-tools"]["template"]), ("10.0.0.5", "pydev@1.0.0"))
        code, out, err = run_cli("list")
        self.assertRegex(out, r"(?m)^py-tools\s+running\s+10\.0\.0\.5\s+pydev@1\.0\.0\s+base, claude-code, git, httpserver, python, webproxy$")
        self.assertRegex(out, r"(?m)^fremd\s+foreign")

    def test_info_and_w2(self):
        data = core.info(self.cfg, "py-tools")
        self.assertEqual((data["status"], data["image"], data["resources"]["cpu"], data["findings"]), ("running", "ubuntu:24.04", 4, []))
        helpers.write_addon(self.config_dir / "addons", "python", {"version": "1.1.0"})
        findings = core.info(self.cfg, "py-tools")["findings"]
        self.assertEqual([(f["check"], f["severity"]) for f in findings], [("W2", "warning")])
        code, out, err = run_cli("info", "py-tools")
        self.assertIn("Status:     running\n", out)
        self.assertIn("W2 addons: Addon python: installiert 1.0.0, verfügbar 1.1.0\n", out)

    def test_foreign_and_unknown_are_locked(self):
        self.provider.add("fremd")
        for func in (core.start, core.stop, core.restart, core.info, core.docs):
            with self.subTest(func=func.__name__):
                with self.assertRaises(ConflictError):
                    func(self.cfg, "fremd")
                with self.assertRaises(NotFoundError):
                    func(self.cfg, "gibtsnicht")
        with self.assertRaises(ConflictError):
            core.rm(self.cfg, "fremd")
        with self.assertRaises(ConflictError):
            core.exec_command(self.cfg, "fremd", ["id"])
        self.assertEqual([c[0] for c in self.provider.calls if c[0] in ("start", "stop", "delete", "exec")], [])

    def test_stop_start_pushes_runtime_again(self):
        self.assertEqual(core.stop(self.cfg, "py-tools"), {"name": "py-tools", "status": "stopped"})
        self.provider.secrets.clear()
        self.assertEqual(core.start(self.cfg, "py-tools"), {"name": "py-tools", "status": "running"})
        self.assertEqual(self.provider.secrets["py-tools"]["/run/devc/secrets/claude-code.api_key"], SECRET)
        self.assertEqual(self.execs("root")[0][1][1][:2], ["install", "-d"])
        core.restart(self.cfg, "py-tools")
        self.assertEqual([c[0] for c in self.provider.calls if c[0] in ("start", "stop")], ["stop", "start", "stop", "start"])
        self.assertEqual(run_cli("stop", "py-tools"), (0, "py-tools: stopped\n", ""))

    def test_exec(self):
        self.provider.exec_results.append((["sh", "-c", "exit 3"], fakes.ExecResult(3, "aus\n", "fehler\n")))
        self.assertEqual(core.exec_command(self.cfg, "py-tools", ["sh", "-c", "exit 3"]), {"rc": 3, "stdout": "aus\n", "stderr": "fehler\n"})
        self.assertEqual(self.provider.called("exec")[-1][2]["user"], "dev")
        self.assertEqual(run_cli("exec", "py-tools", "--", "sh", "-c", "exit 3"), (3, "aus\n", "fehler\n"))
        core.exec_command(self.cfg, "py-tools", ["id"], root=True)
        self.assertEqual(self.provider.called("exec")[-1][2]["user"], "root")
        core.stop(self.cfg, "py-tools")
        with self.assertRaises(ConflictError) as ctx:
            core.exec_command(self.cfg, "py-tools", ["id"])
        self.assertEqual(ctx.exception.message, "Container läuft nicht")

    def test_shell_execs_lxc(self):
        with mock.patch.object(os, "execvp") as execvp:
            core.shell(self.cfg, "py-tools", root=True)
        execvp.assert_called_once()
        self.assertEqual(execvp.call_args.args[0], "lxc")
        self.assertEqual(self.provider.called("shell_argv")[0][2]["user"], "root")

    def test_docs_regenerates_block(self):
        files = self.provider.files["py-tools"]
        files["/work/CLAUDE.md"] = "# Eigenes\n" + files["/work/CLAUDE.md"] + "danach\n"
        file = self.cfg.paths.instance_file("py-tools")
        file.write_text(file.read_text().replace("Python-Quellen unter /work/src", "Quellen jetzt unter /work/lib"))
        result = core.docs(self.cfg, "py-tools")
        self.assertEqual(result["name"], "py-tools")
        text = files["/work/CLAUDE.md"]
        self.assertTrue(text.startswith("# Eigenes\n<!-- devc:begin -->"))
        self.assertTrue(text.endswith("<!-- devc:end -->\ndanach\n"))
        self.assertIn("Quellen jetzt unter /work/lib", text)

    def test_rm(self):
        result = core.rm(self.cfg, "py-tools")
        self.assertEqual(self.provider.called("delete")[0][1], ("py-tools", False))
        self.assertIsNone(core._store(self.cfg).container("py-tools"))
        self.assertFalse(self.cfg.paths.container_dir("py-tools").exists())
        self.assertFalse(self.cfg.paths.container_secrets("py-tools").exists())
        self.assertEqual(core._store(self.cfg).job(result["job"])["kind"], "delete")
        self.assertTrue(self.cfg.paths.job_log("j-000000000001").exists())  # Logs bleiben
        with self.assertRaises(NotFoundError):
            core.rm(self.cfg, "py-tools")

    def test_rm_keep_data_and_missing_container(self):
        del self.provider.containers["py-tools"]  # in LXD schon weg
        core.rm(self.cfg, "py-tools", keep_data=True)
        self.assertEqual(self.provider.called("delete")[0][1], ("py-tools", True))
        self.assertEqual(core.list_containers(self.cfg)["containers"], [])

    def test_rm_cli_needs_confirmation(self):
        with mock.patch.object(cli, "_interactive", return_value=False):
            code, out, err = run_cli("rm", "py-tools")
        self.assertEqual(code, 2)
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch("builtins.input", return_value="n"):
            self.assertEqual(run_cli("rm", "py-tools")[0], 2)
        self.assertEqual(self.provider.called("delete"), [])
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch("builtins.input", return_value="j"):
            self.assertEqual(run_cli("rm", "py-tools", "-q"), (0, "", ""))
        self.create()
        code, out, err = run_cli("rm", "py-tools", "--yes", "--keep-data")
        self.assertEqual(code, 0)
        self.assertIn("Container py-tools gelöscht", out)


class SecretTest(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.create()

    def test_store_files_are_private(self):
        secrets.shared_set(self.cfg.paths, "anthropic", STORE_SECRET)
        paths = self.cfg.paths
        self.assertEqual(paths.secrets.stat().st_mode & 0o777, 0o700)
        for directory in (paths.shared_secrets, paths.container_secrets("py-tools")):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            for file in directory.iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600, file)
        self.assertEqual((paths.shared_secrets / "anthropic").read_bytes(), STORE_SECRET)  # Rohbytes

    def test_shared_set_list_rm(self):
        core.secret_set(self.cfg, shared=True, target="anthropic", value=STORE_SECRET)
        core.secret_set(self.cfg, name="py-tools", target="claude-code.api_key", from_store="anthropic")
        listed = core.secret_list(self.cfg, shared=True)["secrets"]
        self.assertEqual([(s["name"], s["used_by"]) for s in listed], [("anthropic", ["py-tools/claude-code.api_key"])])
        self.assertEqual(self.provider.secrets["py-tools"]["/run/devc/secrets/claude-code.api_key"], STORE_SECRET)
        # Eine Store-Änderung wirkt erst nach erneutem secret set
        core.secret_set(self.cfg, shared=True, target="anthropic", value=b"neu")
        self.assertEqual(secrets.local_get(self.cfg.paths, "py-tools", "claude-code.api_key"), STORE_SECRET)
        core.secret_rm(self.cfg, shared=True, target="anthropic")
        self.assertEqual(core.secret_list(self.cfg, shared=True), {"secrets": []})
        with self.assertRaises(NotFoundError):
            core.secret_rm(self.cfg, shared=True, target="anthropic")
        with self.assertRaises(NotFoundError):
            core.secret_set(self.cfg, name="py-tools", target="claude-code.api_key", from_store="anthropic")

    def test_container_list_and_rm(self):
        listed = core.secret_list(self.cfg, name="py-tools")["secrets"]
        self.assertEqual([(s["slot"], s["source"], s["required"]) for s in listed], [("claude-code.api_key", "local", False), ("claude-code.auth_token", "none", False), ("claude-code.oauth_token", "none", False), ("git.token", "none", False)])
        core.secret_rm(self.cfg, name="py-tools", target="claude-code.api_key")
        self.assertEqual(core.secret_list(self.cfg, name="py-tools")["secrets"][0]["source"], "none")
        # danach folgt nur noch das Nachziehen des Infoverzeichnisses
        self.assertIn(["rm", "-f", "/run/devc/secrets/claude-code.api_key"], [call[1][1] for call in self.provider.called("exec")])
        with self.assertRaises(NotFoundError):
            core.secret_rm(self.cfg, name="py-tools", target="claude-code.api_key")

    def test_set_validates_target(self):
        cases = [
            (dict(name="py-tools", target="abap.sap_password", value=b"x"), NotFoundError),
            (dict(name="py-tools", target="../../etc/passwd", value=b"x"), ValidationError),
            (dict(name="fehlt", target="claude-code.api_key", value=b"x"), NotFoundError),
            (dict(shared=True, target="../x", value=b"x"), ValidationError),
            (dict(shared=True, target="ok", value=None), UsageError),
            (dict(shared=True, target="ok", from_store="anders"), UsageError),
            (dict(name="py-tools", target="claude-code.api_key", value=b"x", from_store="y"), UsageError),
        ]
        for kwargs, error in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(error):
                    core.secret_set(self.cfg, **kwargs)

    def test_set_on_stopped_container_is_pushed_at_start(self):
        core.stop(self.cfg, "py-tools")
        result = core.secret_set(self.cfg, name="py-tools", target="claude-code.api_key", value=b"neuer-wert")
        self.assertFalse(result["pushed"])
        core.start(self.cfg, "py-tools")
        self.assertEqual(self.provider.secrets["py-tools"]["/run/devc/secrets/claude-code.api_key"], b"neuer-wert")

    def test_cli_sources(self):
        key = self.tmp / "key"
        key.write_bytes(STORE_SECRET)
        self.assertEqual(run_cli("secret", "set", "--shared", f"anthropic=@{key}"), (0, "Store-Eintrag anthropic gesetzt\n", ""))
        code, out, err = run_cli("secret", "set", "py-tools", "claude-code.api_key=shared:anthropic")
        self.assertEqual(code, 0)
        self.assertIn("in den laufenden Container geschrieben", out)
        stdin = mock.Mock(buffer=io.BytesIO(b"aus-stdin"), isatty=lambda: False)
        with mock.patch.object(sys, "stdin", stdin):
            self.assertEqual(run_cli("secret", "set", "py-tools", "claude-code.api_key=-")[0], 0)
        self.assertEqual(secrets.local_get(self.cfg.paths, "py-tools", "claude-code.api_key"), b"aus-stdin")
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch("getpass.getpass", return_value="getippt"):
            self.assertEqual(run_cli("secret", "set", "py-tools", "claude-code.api_key")[0], 0)
        self.assertEqual(secrets.local_get(self.cfg.paths, "py-tools", "claude-code.api_key"), b"getippt")
        with mock.patch.object(cli, "_interactive", return_value=False):
            self.assertEqual(run_cli("secret", "set", "py-tools", "claude-code.api_key")[0], 5)
        self.assertEqual(run_cli("secret", "set", "py-tools", "claude-code.api_key=klartext")[0], 2)
        self.assertEqual(run_cli("secret", "set", "py-tools", f"claude-code.api_key=@{self.tmp}/fehlt")[0], 4)
        code, out, err = run_cli("secret", "list", "py-tools")
        self.assertRegex(out, r"(?m)^claude-code\.api_key\s+local\s+\S+\s+nein$")
        code, out, err = run_cli("secret", "list", "--shared")
        self.assertRegex(out, r"(?m)^anthropic\s+\S+\s+-$")
        self.assertEqual(run_cli("secret", "rm", "--shared", "anthropic")[0], 0)


class NoSecretLeakTest(LifecycleTestCase):
    """T-Secrets: der Testwert erscheint in keiner Ausgabe, keinem Log und keinem Argument."""

    def haystack(self, outputs):
        parts = list(outputs)
        cfg = self.cfg
        for root in (cfg.paths.state, cfg.paths.config_file.parent):
            for file in root.rglob("*"):
                if file.is_file() and cfg.paths.secrets not in file.parents:
                    parts.append(file.read_bytes().decode("utf-8", "replace"))
        for method, args, kwargs in self.provider.calls:
            kwargs = {k: v for k, v in kwargs.items() if k != "stdin"}
            parts.append(repr((method, args, kwargs)))
            stdin = dict(self.provider.calls[0][2]).get("stdin")
        # stdin darf den Wert nur beim Push-Befehl tragen
        for method, args, kwargs in self.provider.calls:
            stdin = kwargs.get("stdin")
            if stdin and (SECRET in stdin or STORE_SECRET in stdin):
                self.assertEqual(args[1][2], secrets._PUSH)
        for files in self.provider.files.values():
            parts += list(files.values())
        return "\n".join(parts)

    def test_secret_change_updates_the_info_folder(self):
        key = self.tmp / "key"
        key.write_bytes(SECRET)
        self.assertEqual(run_cli("create", "py-tools", "--addon", "claude-code", "--yes")[0], 0)
        files = self.provider.files["py-tools"]
        self.assertIn("| `claude-code.api_key` | nicht gesetzt | – |", files["/work/.devc/README.md"])
        before = dict(files)
        self.assertEqual(run_cli("secret", "set", "py-tools", f"claude-code.api_key=@{key}")[0], 0)
        self.assertIn("| `claude-code.api_key` | gesetzt | `/run/devc/secrets/claude-code.api_key` |", files["/work/.devc/README.md"])
        self.assertIn("| `api_key` | Secret, gesetzt: Datei", files["/work/.devc/addons/claude-code.md"])
        # Nur die Übersicht und die Seite dieses Addons ändern sich; der Wert steht nirgends.
        changed = sorted(path for path in files if path.startswith("/work/") and files[path] != before.get(path))
        self.assertEqual(changed, ["/work/.devc/README.md", "/work/.devc/addons/claude-code.md"])
        self.assertFalse([path for path in changed if SECRET.decode() in files[path]])
        self.assertEqual(run_cli("secret", "rm", "py-tools", "claude-code.api_key")[0], 0)
        self.assertIn("| `claude-code.api_key` | nicht gesetzt | – |", files["/work/.devc/README.md"])
        self.assertIn("| `api_key` | Secret, nicht gesetzt |", files["/work/.devc/addons/claude-code.md"])

    def test_secret_never_leaks(self):
        key = self.tmp / "key"
        key.write_bytes(SECRET)
        store_key = self.tmp / "store-key"
        store_key.write_bytes(STORE_SECRET)
        outputs = []

        def cli_run(*argv):
            code, out, err = run_cli(*argv)
            outputs.extend([out, err])
            return code

        self.assertEqual(cli_run("secret", "set", "--shared", f"anthropic=@{store_key}"), 0)
        self.assertEqual(cli_run("create", "py-tools", "--template", "pydev", "--yes", "--secret", f"claude-code.api_key=@{key}", "-v"), 0)
        self.assertEqual(cli_run("create", "zwei", "--template", "pydev", "--yes", "--json"), 0)
        for argv in (["secret", "list", "py-tools"], ["secret", "list", "py-tools", "--json"], ["secret", "list", "--shared", "--json"],
                     ["info", "py-tools", "--json"], ["list", "--json"], ["stop", "py-tools"], ["start", "py-tools"],
                     ["docs", "py-tools"], ["secret", "set", "zwei", f"claude-code.api_key=@{key}"]):
            self.assertEqual(cli_run(*argv), 0, argv)
        # Fehlerpfade
        self.provider.stream_rc["install.sh"] = 1
        self.assertEqual(cli_run("create", "drei", "--template", "pydev", "--yes", "--secret", f"claude-code.api_key=@{key}"), 1)
        self.provider.fail["exec"] = RuntimeError(SECRET.decode())
        self.assertEqual(cli_run("secret", "set", "py-tools", f"claude-code.api_key=@{key}", "--json"), 1)
        del self.provider.fail["exec"]

        self.assertEqual(self.provider.secrets["py-tools"]["/run/devc/secrets/claude-code.api_key"], SECRET)
        self.assertEqual(self.provider.secrets["zwei"]["/run/devc/secrets/claude-code.api_key"], SECRET)
        haystack = self.haystack(outputs)
        self.assertGreater(len(haystack), 5000)
        for value in (SECRET.decode(), STORE_SECRET.decode()):
            self.assertNotIn(value, haystack)

    def test_install_env_has_only_secret_paths(self):
        self.create()
        for call in self.provider.called("exec_stream"):
            for value in call[2]["env"].values():
                self.assertNotIn(SECRET.decode(), value)


if __name__ == "__main__":
    unittest.main()
