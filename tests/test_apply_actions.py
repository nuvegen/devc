"""config apply, install/uninstall, T-Actions, Addon-Import/-Export (L16), template save/rm."""

import io
import json
import os
import stat
import sys
import tarfile
import tomllib
import unittest
import zipfile
from unittest import mock

from devc import actions, addons, config, core, secrets
from devc.errors import ConflictError, EngineError, JobFailedError, MissingSecretError, NotFoundError, UsageError, ValidationError
from tests import fakes, helpers
from tests.helpers import run_cli, write_addon
from tests.test_lifecycle import SECRET, LifecycleTestCase


class ContainerTestCase(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.create()
        self.provider.calls.clear()

    def edit(self, old, new, name="py-tools"):
        file = self.cfg.paths.instance_file(name)
        text = file.read_text()
        self.assertIn(old, text)
        file.write_text(text.replace(old, new))

    def installs(self):
        return [call[2]["env"]["DEVC_ADDON"] for call in self.provider.called("exec_stream")]


class ConfigTest(ContainerTestCase):
    def test_show_put_discard(self):
        shown = core.config_show(self.cfg, "py-tools")
        self.assertTrue(shown["applied"])
        self.assertEqual(shown["text"], self.cfg.paths.applied_file("py-tools").read_text())
        new = shown["text"].replace("cpu = 4", "cpu = 2  # weniger")
        result = core.config_put(self.cfg, "py-tools", new)
        self.assertEqual([f["check"] for f in result["findings"]], ["W1"])
        shown = core.config_show(self.cfg, "py-tools")
        self.assertEqual((shown["applied"], "# weniger" in shown["text"]), (False, True))
        core.config_discard(self.cfg, "py-tools")
        self.assertTrue(core.config_show(self.cfg, "py-tools")["applied"])

    def test_put_rejects_invalid_and_keeps_file(self):
        text = core.config_show(self.cfg, "py-tools")["text"]
        for bad in (text.replace("cpu = 4", "cpu = 0"), text.replace('name = "py-tools"', 'name = "anders"'), "name = "):
            with self.subTest(bad=bad[:40]):
                with self.assertRaises(ValidationError):
                    core.config_put(self.cfg, "py-tools", bad)
        self.assertEqual(core.config_show(self.cfg, "py-tools")["text"], text)
        with self.assertRaises(NotFoundError):
            core.config_put(self.cfg, "fehlt", text)

    def test_cli_show_and_edit(self):
        code, out, err = run_cli("config", "show", "py-tools")
        self.assertEqual(out, core.config_show(self.cfg, "py-tools")["text"])
        script = self.tmp / "editor"
        script.write_text(f"#!{sys.executable}\nimport sys\np = sys.argv[-1]\nt = open(p).read()\nopen(p, 'w').write(t.replace('cpu = 4', 'cpu = 3'))\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        with mock.patch.dict(os.environ, {"EDITOR": str(script)}):
            os.environ.pop("VISUAL", None)
            code, out, err = run_cli("config", "edit", "py-tools")
        self.assertEqual(code, 0)
        self.assertIn("devc config apply py-tools", out)
        self.assertIn("cpu = 3", core.config_show(self.cfg, "py-tools")["text"])


class ApplyTest(ContainerTestCase):
    def test_no_change(self):
        result = core.config_apply(self.cfg, "py-tools", dry_run=True)
        self.assertEqual(result["plan"], {"install": [], "uninstall": [], "resources": False, "secrets": False,
                                          "git": False, "files": False, "docs": False, "changed": False})
        self.assertEqual(self.provider.called("exec_stream"), [])
        code, out, err = run_cli("config", "apply", "py-tools", "--dry-run")
        self.assertIn("Keine Unterschiede zum angewendeten Stand\nProbelauf, nichts geändert\n", out)

    def test_dry_run_changes_nothing(self):
        self.edit('version = "3.12"', 'version = "3.13"')
        applied_at = core._store(self.cfg).container("py-tools")["applied_at"]
        result = core.config_apply(self.cfg, "py-tools", dry_run=True)
        self.assertEqual(result["plan"]["install"], [{"addon": "python", "reason": "Parameter geändert"}])
        self.assertEqual([c[0] for c in self.provider.calls if c[0] in ("exec", "exec_stream", "set_limits")], [])
        self.assertEqual(core._store(self.cfg).container("py-tools")["applied_at"], applied_at)
        self.assertEqual(core._store(self.cfg).jobs("py-tools")[0]["kind"], "create")

    def test_param_change_reinstalls_only_that_addon(self):
        self.edit("port = 8000", "port = 9000")
        result = core.config_apply(self.cfg, "py-tools", on_log=self.log.append)
        self.assertEqual(self.installs(), ["httpserver"])
        self.assertEqual(self.provider.called("exec_stream")[0][2]["env"]["DEVC_PARAM_PORT"], "9000")
        self.assertIn("auf Port 9000 aus", self.provider.files["py-tools"]["/work/CLAUDE.md"])
        self.assertTrue(core.config_show(self.cfg, "py-tools")["applied"])
        job = core._store(self.cfg).job(result["job"])
        self.assertEqual((job["kind"], job["state"]), ("apply", "ok"))
        self.assertEqual(self.provider.called("set_limits"), [])
        self.assertFalse(core.config_apply(self.cfg, "py-tools", dry_run=True)["plan"]["changed"])

    def test_resources(self):
        self.edit("cpu = 4", "cpu = 2")
        self.edit('disk = "30GiB"', 'disk = "40GiB"')
        core.config_apply(self.cfg, "py-tools")
        self.assertEqual(self.provider.called("set_limits")[0][1], ("py-tools", 2, "8GiB", "40GiB"))
        self.assertEqual(self.installs(), [])

    def test_new_addon_and_new_version(self):
        self.edit('addons = ["claude-code", "python", "httpserver", "webproxy", "git"]', 'addons = ["claude-code", "python", "httpserver", "webproxy", "git", "node"]')
        write_addon(self.config_dir / "addons", "python", {
            "version": "1.1.0", "install": ["install.sh"],
            "params": [{"name": "version", "type": "choice", "options": ["3.12", "3.13"], "default": "3.12"}],
        }, ai="## Python {version}\n", files={"install.sh": "true\n"})
        plan = core.config_apply(self.cfg, "py-tools", dry_run=True)["plan"]
        self.assertEqual(plan["install"], [{"addon": "python", "reason": "Version 1.0.0 → 1.1.0"}, {"addon": "node", "reason": "neu"}])
        core.config_apply(self.cfg, "py-tools")
        self.assertEqual(self.installs(), ["python", "node"])
        row = core._store(self.cfg).container("py-tools")
        self.assertEqual((row["addons"]["python"], row["addons"]["node"]), ("1.1.0", "1.0.0"))
        self.assertEqual(tomllib.loads(self.provider.files["py-tools"]["/etc/devc/addons.toml"]), row["addons"])

    def test_data_and_image_changes_are_not_applicable(self):
        root = self.tmp / "srv"
        root.mkdir()
        self.write_config(f'allowed_roots = ["{root}"]\n')
        self.edit('code = "volume"', f'shared = "{root}"')
        with self.assertRaises(ValidationError) as ctx:
            core.config_apply(self.cfg, "py-tools")
        self.assertIn("Backup und Restore", ctx.exception.message)
        core.config_discard(self.cfg, "py-tools")
        self.edit('platform = "lxd"', 'platform = "lxd"\nimage = "ubuntu:22.04"')
        with self.assertRaises(ValidationError):
            core.config_apply(self.cfg, "py-tools", dry_run=True)
        self.assertEqual(self.provider.called("exec_stream"), [])

    def test_removed_addon_without_uninstall(self):
        self.edit('addons = ["claude-code", "python", "httpserver", "webproxy", "git"]', 'addons = ["claude-code", "python", "webproxy", "git"]')
        self.edit("[params.httpserver]\nport = 8000\ndir = \"/work/out\"\n", "")
        with self.assertRaises(ValidationError) as ctx:
            core.config_apply(self.cfg, "py-tools")
        self.assertIn("httpserver hat kein uninstall", ctx.exception.message)
        self.assertIn("httpserver", core._store(self.cfg).container("py-tools")["addons"])

    def test_removed_addon_with_uninstall(self):
        write_addon(self.config_dir / "addons", "tool", {"install": ["install.sh"], "uninstall": ["remove.sh"],
                                                         "params": [{"name": "level", "type": "int", "default": 1}]},
                    files={"install.sh": "true\n", "remove.sh": "true\n"})
        core.install(self.cfg, "py-tools", ["tool"], ["tool.level=7"])
        self.assertEqual(core._store(self.cfg).container("py-tools")["addons"]["tool"], "1.0.0")
        self.provider.calls.clear()
        result = core.uninstall(self.cfg, "py-tools", "tool")
        self.assertEqual(result["plan"]["uninstall"], ["tool"])
        stream = self.provider.called("exec_stream")[0]
        self.assertEqual((stream[1][1], stream[2]["cwd"], stream[2]["env"]["DEVC_PARAM_LEVEL"]),
                         (["bash", "-euo", "pipefail", "remove.sh"], "/opt/devc/addons/tool", "7"))
        self.assertIn(["rm", "-rf", "/opt/devc/addons/tool"], [c[1][1] for c in self.provider.called("exec")])
        self.assertNotIn("tool", core._store(self.cfg).container("py-tools")["addons"])
        data = tomllib.loads(core.config_show(self.cfg, "py-tools")["text"])
        self.assertNotIn("tool", data["addons"])
        self.assertNotIn("tool", data.get("params", {}))

    def test_install_validates_before_saving(self):
        before = core.config_show(self.cfg, "py-tools")["text"]
        for addons_, params in ((["abap"], []), (["gibtsnicht"], []), (["node"], ["node.version=99"])):
            with self.subTest(addons=addons_):
                with self.assertRaises(ValidationError):
                    core.install(self.cfg, "py-tools", addons_, params)
        with self.assertRaises(UsageError):
            core.uninstall(self.cfg, "py-tools", "node")
        with self.assertRaises(ValidationError):
            core.uninstall(self.cfg, "py-tools", "python")  # httpserver benötigt python (L5)
        self.assertEqual(core.config_show(self.cfg, "py-tools")["text"], before)

    def test_stopped_container(self):
        core.stop(self.cfg, "py-tools")
        self.edit("port = 8000", "port = 9000")
        with self.assertRaises(ConflictError):
            core.config_apply(self.cfg, "py-tools")
        self.assertFalse(core.config_apply(self.cfg, "py-tools", dry_run=True)["plan"]["install"] == [])

    def test_incomplete_container_resumes_at_step_seven(self):
        self.provider.stream_rc["install.sh"] = 1
        with self.assertRaises(JobFailedError):
            self.create("halb")
        self.provider.stream_rc.clear()
        self.provider.calls.clear()
        plan = core.config_apply(self.cfg, "halb", dry_run=True)["plan"]
        self.assertEqual([i["addon"] for i in plan["install"]], ["base", "claude-code", "python", "httpserver", "webproxy", "git"])
        core.config_apply(self.cfg, "halb")
        self.assertEqual(self.installs(), ["base", "claude-code", "python", "httpserver"])
        self.assertFalse(core.list_containers(self.cfg)["containers"][0]["incomplete"])

    def test_secret_mapping(self):
        secrets.shared_set(self.cfg.paths, "anthropic", b"aus-dem-store")
        core.config_apply(self.cfg, "py-tools")  # Verweis unverändert: lokaler Wert bleibt
        self.assertEqual(secrets.local_get(self.cfg.paths, "py-tools", "claude-code.api_key"), SECRET)
        secrets.shared_set(self.cfg.paths, "zweiter", b"zweiter-wert")
        self.edit('"claude-code.api_key" = "anthropic"', '"claude-code.api_key" = "zweiter"')
        core.config_apply(self.cfg, "py-tools")
        self.assertEqual(self.provider.secrets["py-tools"]["/run/devc/secrets/claude-code.api_key"], b"zweiter-wert")
        self.edit('"claude-code.api_key" = "zweiter"', '"claude-code.api_key" = "zweiter"\n"git.key" = "fehlt"')
        with self.assertRaises(MissingSecretError) as ctx:
            core.config_apply(self.cfg, "py-tools")
        self.assertEqual(ctx.exception.details["slots"], ["git.key"])


class ActionTest(ContainerTestCase):
    """T-Actions: detach-Start, Doppelstart E_CONFLICT, stops, Status, Timeout."""

    def bash(self):
        return [c for c in self.provider.called("exec") if c[1][1][:2] == ["bash", "-lc"]]

    def test_detach_start_status_stop(self):
        result = core.action_run(self.cfg, "py-tools", "httpserver.start", on_log=self.log.append)
        self.assertEqual((result["status"], result["pid"]), ("running", 401))
        call = self.bash()[0]
        self.assertEqual(call[1][1][2], (
            "umask 077; setsid nohup bash -lc 'python3 -m http.server 8000 --directory /work/out' "
            "> /run/devc/actions/httpserver-start.log 2>&1 & echo $! > /run/devc/actions/httpserver-start.pid"
        ))
        self.assertEqual((call[2]["user"], call[2]["cwd"]), ("dev", "/work"))
        self.assertEqual(core._store(self.cfg).action_run("py-tools", "httpserver.start")["pid"], 401)
        listed = {a["id"]: a for a in core.action_list(self.cfg, "py-tools")["addons"]}
        self.assertEqual(listed["httpserver"]["actions"][0]["status"], "running")
        self.assertEqual(listed["httpserver"]["links"], [{"label": "Im Browser öffnen", "url": "http://10.0.0.5:8000/", "active": True, "port": 8000, "path": "/"}])
        self.assertIn("python3 -m http.server", listed["httpserver"]["readme"])

        with self.assertRaises(ConflictError) as ctx:
            core.action_run(self.cfg, "py-tools", "httpserver.start")
        self.assertEqual(ctx.exception.code, "E_CONFLICT")

        result = core.action_run(self.cfg, "py-tools", "httpserver.stop")
        self.assertEqual((result["status"], result["was_running"]), ("stopped", True))
        self.assertIsNone(core._store(self.cfg).action_run("py-tools", "httpserver.start"))
        result = core.action_run(self.cfg, "py-tools", "httpserver.stop", on_log=self.log.append)
        self.assertFalse(result["was_running"])
        self.assertTrue(any("lief nicht" in line for line in self.log))
        listed = [a for a in core.action_list(self.cfg, "py-tools")["addons"] if a["id"] == "httpserver"][0]
        self.assertEqual((listed["actions"][0]["status"], listed["links"][0]["active"]), ("stopped", False))

    def test_stop_script_kills_process_group(self):
        self.assertIn('kill -TERM -- "-$p"', actions._STOP)
        self.assertIn('kill -KILL -- "-$p"', actions._STOP)
        self.assertIn("-lt 50", actions._STOP)  # 50 × 0,1 s = 5 s
        self.assertIn('rm -f "$1"', actions._STOP)

    def test_foreground_action_quotes_and_logs(self):
        self.edit('version = "3.12"', 'version = "3.13"')
        self.provider.exec_results.append((["bash", "-lc"], fakes.ExecResult(0, "erstellt\n", "hinweis\n")))
        result = core.action_run(self.cfg, "py-tools", "python.venv", on_log=self.log.append)
        self.assertEqual(result["status"], "ok")
        call = self.bash()[0]
        self.assertEqual(call[1][1][2], "python3.13 -m venv /work/.venv")
        self.assertEqual(call[2]["timeout"], 300)
        self.assertTrue(any(line.endswith("action   erstellt") for line in self.log))
        self.assertEqual(core._store(self.cfg).jobs("py-tools")[0]["kind"], "action")

    def test_values_are_shell_quoted(self):
        self.edit('dir = "/work/out"', 'dir = "/work/my out; rm -rf /"')
        core.action_run(self.cfg, "py-tools", "httpserver.start")
        self.assertIn("--directory '\"'\"'/work/my out; rm -rf /'\"'\"''", self.bash()[0][1][1][2])

    def test_failure_and_timeout(self):
        self.provider.exec_results.append((["bash", "-lc"], fakes.ExecResult(2, "", "kaputt\n")))
        with self.assertRaises(JobFailedError) as ctx:
            core.action_run(self.cfg, "py-tools", "python.venv")
        self.assertEqual(ctx.exception.code, "E_JOB_FAILED")
        self.assertIn("job", ctx.exception.details)
        self.provider.fail["exec"] = EngineError("lxc exec: Zeitüberschreitung nach 300 s", {"timeout": True})
        with mock.patch.object(core, "_managed", return_value=({}, self.provider.containers["py-tools"])):
            with self.assertRaises(JobFailedError) as ctx:
                core.action_run(self.cfg, "py-tools", "python.venv")
        self.assertIn("Zeitüberschreitung nach 300 s", ctx.exception.message)

    def test_unknown_action_and_stopped_container(self):
        for spec in ("httpserver.fehlt", "node.start", "kaputt", "fehlt.start"):
            with self.subTest(spec=spec):
                with self.assertRaises(NotFoundError):
                    core.action_run(self.cfg, "py-tools", spec)
        core.stop(self.cfg, "py-tools")
        with self.assertRaises(ConflictError) as ctx:
            core.action_run(self.cfg, "py-tools", "httpserver.start")
        self.assertEqual(ctx.exception.message, "Container läuft nicht")
        listed = [a for a in core.action_list(self.cfg, "py-tools")["addons"] if a["id"] == "httpserver"][0]
        self.assertEqual(listed["links"], [{"label": "Im Browser öffnen", "url": None, "active": False, "port": 8000, "path": "/"}])

    def test_detach_takes_container_lock(self):
        with core.locks.container_lock(self.cfg.paths, "py-tools"):
            with self.assertRaises(ConflictError):
                core.action_run(self.cfg, "py-tools", "httpserver.start")
            self.assertEqual(core.action_run(self.cfg, "py-tools", "python.venv")["status"], "ok")

    def test_cli(self):
        code, out, err = run_cli("action", "run", "py-tools", "httpserver.start", "-q")
        self.assertEqual((code, out), (0, ""))
        code, out, err = run_cli("action", "list", "py-tools")
        self.assertIn("httpserver.start [running]  Server starten\n", out)
        self.assertIn("httpserver Link [aktiv]  Im Browser öffnen: http://10.0.0.5:8000/\n", out)
        self.assertEqual(run_cli("action", "run", "py-tools", "httpserver.start")[0], 5)
        code, out, err = run_cli("action", "run", "py-tools", "httpserver.stop")
        self.assertIn("httpserver.stop: stopped\n", out)
        self.assertEqual(run_cli("action", "run", "py-tools", "x.y")[0], 4)


def tar_bytes(entries, gz=True):
    """entries: (name, inhalt | None für Verzeichnis | TarInfo)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz" if gz else "w") as archive:
        for name, content in entries:
            if isinstance(content, tarfile.TarInfo):
                archive.addfile(content)
            elif content is None:
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            else:
                data = content.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith(".sh") else 0o644
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


GOOD = [
    ("demo", None),
    ("demo/addon.toml", 'id = "demo"\ntitle = "Demo"\nversion = "1.2.0"\ninstall = ["install.sh"]\n'),
    ("demo/README.md", "Demo\n"),
    ("demo/AI.md", "## Demo\n"),
    ("demo/install.sh", "true\n"),
]


def special(name, kind, linkname=""):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    return (name, info)


class AddonArchiveTest(LifecycleTestCase):
    """L16, addon import/export/rm."""

    def import_bytes(self, data):
        return core.addon_import_data(self.cfg, data)

    def test_import_tar(self):
        result = self.import_bytes(tar_bytes(GOOD))
        self.assertEqual((result["id"], result["version"], result["replaced"]), ("demo", "1.2.0", False))
        target = self.config_dir / "addons" / "demo"
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["AI.md", "README.md", "addon.toml", "install.sh"])
        self.assertTrue(os.access(target / "install.sh", os.X_OK))
        self.assertEqual(core.addon_show(self.cfg, "demo")["origin"], "own")
        self.assertEqual([p.name for p in (self.config_dir / "addons").iterdir()], ["demo"])  # kein Rest vom Import
        job = core._store(self.cfg).job(result["job"])
        self.assertEqual((job["kind"], job["container"], job["state"]), ("addon-import", None, "ok"))

    def test_import_zip_and_replace(self):
        self.import_bytes(tar_bytes(GOOD))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in GOOD:
                if content is not None:
                    archive.writestr(name, content.replace("1.2.0", "2.0.0"))
        result = self.import_bytes(buffer.getvalue())
        self.assertEqual((result["version"], result["replaced"]), ("2.0.0", True))
        self.assertEqual(core.addon_show(self.cfg, "demo")["version"], "2.0.0")

    def assert_rejected(self, data, check="L16"):
        with self.assertRaises(ValidationError) as ctx:
            self.import_bytes(data)
        self.assertEqual(ctx.exception.details["check"], check)
        addons_dir = self.config_dir / "addons"
        self.assertEqual([p.name for p in addons_dir.iterdir()] if addons_dir.exists() else [], [])
        return ctx.exception

    def test_l16_violations(self):
        cases = {
            "zwei Verzeichnisse": GOOD + [("zwei", None), ("zwei/x", "x")],
            "Datei auf oberster Ebene": GOOD + [("lose.txt", "x")],
            "nur eine Datei": [("addon.toml", "x")],
            "absoluter Pfad": [("/etc/demo/addon.toml", "x")],
            "dotdot": GOOD + [("demo/../../x", "x")],
            "Symlink": GOOD + [special("demo/link", tarfile.SYMTYPE, "/etc/passwd")],
            "Hardlink": GOOD + [special("demo/hard", tarfile.LNKTYPE, "demo/addon.toml")],
            "Gerätedatei": GOOD + [special("demo/dev", tarfile.CHRTYPE)],
            "FIFO": GOOD + [special("demo/fifo", tarfile.FIFOTYPE)],
            "ungültige ID": [("Demo_X", None), ("Demo_X/addon.toml", "x")],
            "leer": [],
        }
        for label, entries in cases.items():
            with self.subTest(case=label):
                self.assert_rejected(tar_bytes(entries))
        self.assert_rejected(b"kein archiv")

    def test_zip_symlink_and_traversal(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("demo/addon.toml", "x")
            link = zipfile.ZipInfo("demo/link")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "/etc/passwd")
        self.assert_rejected(buffer.getvalue())
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("demo/addon.toml", "x")
            archive.writestr("demo/../../raus", "x")
        self.assert_rejected(buffer.getvalue())

    def test_size_limit(self):
        with mock.patch.object(addons, "MAX_UNPACKED", 100):
            self.assert_rejected(tar_bytes(GOOD + [("demo/gross.bin", "x" * 200)]))

    def test_content_checks_follow_l16(self):
        bad = [(n, c.replace("1.2.0", "kaputt") if c else c) for n, c in GOOD]
        self.assert_rejected(tar_bytes(bad), check="L3")
        self.assert_rejected(tar_bytes([e for e in GOOD if e[0] != "demo/AI.md"]), check="L4")
        renamed = [("andere", None)] + [(n.replace("demo/", "andere/"), c) for n, c in GOOD[1:]]
        self.assert_rejected(tar_bytes(renamed), check="L3")

    def test_export_roundtrip(self):
        filename, data = core.addon_archive(self.cfg, "httpserver")
        self.assertEqual(filename, "httpserver-1.1.0.tar.gz")
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            self.assertEqual(sorted(archive.getnames()), [
                "httpserver", "httpserver/AI.md", "httpserver/README.md", "httpserver/addon.toml",
                "httpserver/files", "httpserver/files/index.html", "httpserver/install.sh",
            ])
        result = self.import_bytes(data)
        self.assertEqual(result["id"], "httpserver")
        self.assertEqual(core.addon_show(self.cfg, "httpserver")["origin"], "override")
        out = self.tmp / "out"
        out.mkdir()
        code, stdout, err = run_cli("addon", "export", "abap", "-o", str(out))
        self.assertEqual((code, stdout.strip()), (0, str(out / "abap-1.0.0.tar.gz")))
        self.assertEqual(run_cli("addon", "import", str(out / "abap-1.0.0.tar.gz"), "-q"), (0, "", ""))
        self.assertEqual(run_cli("addon", "import", str(out / "fehlt.tar.gz"))[0], 4)
        self.assertEqual(run_cli("addon", "export", "fehlt")[0], 4)

    def test_rm(self):
        self.import_bytes(tar_bytes(GOOD))
        self.assertEqual(core.addon_rm(self.cfg, "demo"), {"id": "demo"})
        self.assertFalse((self.config_dir / "addons" / "demo").exists())
        with self.assertRaises(NotFoundError):
            core.addon_rm(self.cfg, "demo")
        with self.assertRaises(ConflictError):
            core.addon_rm(self.cfg, "python")  # mitgeliefert
        with self.assertRaises(ValidationError):
            core.addon_rm(self.cfg, "../x")

    def test_rm_addon_in_use(self):
        self.import_bytes(tar_bytes(GOOD))
        self.create("ab", template=None, text='name = "ab"\naddons = ["demo"]\n[data]\ncode = "volume"\n', secret_values={})
        with self.assertRaises(ConflictError) as ctx:
            core.addon_rm(self.cfg, "demo")
        self.assertEqual(ctx.exception.details["containers"], ["ab"])
        self.assertEqual(run_cli("addon", "rm", "demo")[0], 5)
        # eine eigene Überlagerung eines mitgelieferten Addons darf weg: das mitgelieferte bleibt
        write_addon(self.config_dir / "addons", "base", {"version": "9.0.0"})
        self.assertEqual(run_cli("addon", "rm", "base"), (0, "Addon base entfernt\n", ""))


class TemplateSaveTest(ContainerTestCase):
    def test_save_and_reuse(self):
        self.edit("cpu = 4", "cpu = 2")
        result = core.template_save(self.cfg, "py-tools", "mine")
        self.assertEqual([f["check"] for f in result["findings"]], ["W1"])
        text = (self.config_dir / "templates" / "mine.toml").read_text()
        data = tomllib.loads(text)
        self.assertEqual((data["id"], data["version"], data["resources"]["cpu"]), ("mine", "1.0.0", 2))
        for key in ("name", "template", "platform"):
            self.assertNotIn(key, data)
        self.assertIn("# Mitgeliefertes Template", text)  # Kommentare bleiben
        self.assertNotIn("# Entwurf aus", text)
        self.create("neu", template="mine")
        self.assertEqual(self.provider.called("create")[-1][1][0].cpu, 2)
        self.assertEqual(json.loads(run_cli("template", "show", "mine", "--json")[1])["meta"]["origin"], "own")

    def test_save_errors(self):
        with self.assertRaises(NotFoundError):
            core.template_save(self.cfg, "fehlt", "mine")
        with self.assertRaises(ValidationError):
            core.template_save(self.cfg, "py-tools", "Kein_Name")

    def test_rm(self):
        core.template_save(self.cfg, "py-tools", "mine")
        self.assertEqual(run_cli("template", "rm", "mine"), (0, "Template mine entfernt\n", ""))
        self.assertEqual(run_cli("template", "rm", "mine")[0], 4)
        self.assertEqual(run_cli("template", "rm", "pydev")[0], 5)
        self.assertTrue((core.templates_module.BUILTIN / "pydev.toml").exists())
        self.assertEqual(run_cli("template", "save", "py-tools", "kopie", "-q")[0], 0)


if __name__ == "__main__":
    unittest.main()


class ParamsSetTest(ContainerTestCase):
    """Parameter eines Addons im Betrieb ändern: nur dieses Addon, laufende Aktionen starten neu."""

    def starts(self):
        return [c[1][1][2] for c in self.provider.called("exec") if c[1][1][:2] == ["bash", "-lc"] and "setsid nohup" in c[1][1][2]]

    def status(self, addon="httpserver", action="start"):
        entry = [a for a in core.action_list(self.cfg, "py-tools")["addons"] if a["id"] == addon][0]
        return [a["status"] for a in entry["actions"] if a["id"] == action][0]

    def test_sets_params_and_reinstalls_only_that_addon(self):
        result = core.params_set(self.cfg, "py-tools", ["httpserver.port=8010"], on_log=self.log.append)
        self.assertEqual([i["addon"] for i in result["plan"]["install"]], ["httpserver"])
        self.assertEqual(self.installs(), ["httpserver"])
        self.assertEqual(tomllib.loads(self.cfg.paths.instance_file("py-tools").read_text())["params"]["httpserver"]["port"], 8010)
        self.assertEqual(self.starts(), [])  # lief nicht, bleibt gestoppt
        self.assertEqual([c for c in self.provider.called("stop") + self.provider.called("restart")], [])

    def test_running_action_restarts_with_new_params(self):
        core.action_run(self.cfg, "py-tools", "httpserver.start")
        self.provider.calls.clear()
        core.params_set(self.cfg, "py-tools", ["httpserver.port=8010"], on_log=self.log.append)
        self.assertEqual(len(self.starts()), 1)
        self.assertIn("8010", self.starts()[0])
        self.assertEqual(self.status(), "running")
        self.assertIsNotNone(core._store(self.cfg).action_run("py-tools", "httpserver.start"))
        self.assertIn("neu gestartet", "\n".join(self.log))

    def test_other_addon_keeps_running(self):
        core.action_run(self.cfg, "py-tools", "httpserver.start")
        self.provider.calls.clear()
        core.params_set(self.cfg, "py-tools", ["python.version=3.13"])
        self.assertEqual((self.installs(), self.starts(), self.status()), (["python"], [], "running"))

    def test_errors(self):
        before = self.cfg.paths.instance_file("py-tools").read_text()
        for params, error in (([], UsageError), (["httpserver.fehlt=1"], UsageError), (["httpserver.port=viele"], UsageError),
                              (["abap.sap_client=100"], NotFoundError), (["httpserver.port=80"], ValidationError)):
            with self.subTest(params=params), self.assertRaises(error):
                core.params_set(self.cfg, "py-tools", params)
        self.assertEqual(self.cfg.paths.instance_file("py-tools").read_text(), before)
        self.assertEqual(self.installs(), [])

    def test_action_list_describes_params(self):
        entry = [a for a in core.action_list(self.cfg, "py-tools")["addons"] if a["id"] == "httpserver"][0]
        self.assertEqual(entry["description"], "Einfacher HTTP-Server für Dateien aus /work")
        port = [p for p in entry["params"] if p["name"] == "port"][0]
        self.assertEqual((port["type"], port["value"], port["default"], port["min"], port["max"]), ("int", 8000, 8000, 1024, 65535))
        claude = [a for a in core.action_list(self.cfg, "py-tools")["addons"] if a["id"] == "claude-code"][0]
        self.assertEqual([(p["name"], p["type"], "value" in p) for p in claude["params"] if p["type"] == "secret"],
                         [("oauth_token", "secret", False), ("api_key", "secret", False), ("auth_token", "secret", False)])
