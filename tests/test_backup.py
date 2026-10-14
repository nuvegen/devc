"""T-Backup, Snapshots, push/pull mit L13."""

import gzip
import hashlib
import io
import os
import tarfile
import tomllib
import unittest
from unittest import mock

from devc import __main__ as cli
from devc import backup, core, jobs, locks, secrets
from devc.errors import ConflictError, JobFailedError, MissingSecretError, NotFoundError, SecurityError, UsageError, ValidationError
from tests import fakes
from tests.helpers import run_cli
from tests.test_apply_actions import ContainerTestCase
from tests.test_lifecycle import SECRET


def tgz(files: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class BackupTestCase(ContainerTestCase):
    def setUp(self):
        super().setUp()
        self.provider.areas["py-tools"] = {"/work": tgz({"./src/a.py": b"print(1)\n"}), "/home/dev": tgz({"./.bashrc": b"# rc\n"})}

    def backup(self, **kwargs):
        return core.backup(self.cfg, "py-tools", **kwargs)


class ArchiveTest(BackupTestCase):
    def test_layout_and_sums(self):
        result = self.backup(label="vor-umbau")
        self.assertRegex(os.path.basename(result["file"]), r"^py-tools-\d{8}-\d{6}-vor-umbau\.devc\.tar$")
        self.assertEqual(os.path.dirname(result["file"]), str(self.cfg.paths.backups))
        with tarfile.open(result["file"], "r:") as archive:  # unkomprimiert
            self.assertEqual(archive.getnames(), ["manifest.toml", "devc.toml", "data/work.tar.gz", "data/home.tar.gz", "SHA256SUMS"])
            read = {name: archive.extractfile(name).read() for name in archive.getnames()}
        manifest = tomllib.loads(read["manifest.toml"].decode())
        self.assertEqual({k: manifest[k] for k in ("devc_version", "platform", "name", "template", "includes")},
                         {"devc_version": "1.0.0", "platform": "lxd", "name": "py-tools", "template": "pydev@1.0.0", "includes": ["work", "home"]})
        self.assertRegex(manifest["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(read["devc.toml"].decode(), self.cfg.paths.instance_file("py-tools").read_text())
        # gepackt wird in devc, nicht im Container: der Inhalt ist gleich, die gzip-Bytes sind es nicht
        self.assertEqual(gzip.decompress(read["data/work.tar.gz"]), gzip.decompress(self.provider.areas["py-tools"]["/work"]))
        self.assertEqual(read["data/work.tar.gz"][:10], b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff")  # ohne Zeit und Namen
        sums = dict(reversed(line.split("  ")) for line in read["SHA256SUMS"].decode().splitlines())
        self.assertEqual(set(sums), {"manifest.toml", "devc.toml", "data/work.tar.gz", "data/home.tar.gz"})
        for name, digest in sums.items():
            self.assertEqual(hashlib.sha256(read[name]).hexdigest(), digest)
        # als root, Daten über stdout
        dump = [c for c in self.provider.called("exec") if c[1][1][:2] == ["tar", "-C"]]
        self.assertEqual([(c[1][1], c[2]["user"]) for c in dump], [(["tar", "-C", "/work", "-cf", "-", "."], "root"), (["tar", "-C", "/home/dev", "-cf", "-", "."], "root")])
        self.assertEqual(os.listdir(self.cfg.paths.backups), [os.path.basename(result["file"])])  # kein Rest
        job = core._store(self.cfg).job(result["job"])
        self.assertEqual((job["kind"], job["state"]), ("backup", "ok"))

    def test_no_secret_in_backup(self):
        result = self.backup()
        with open(result["file"], "rb") as handle:
            self.assertNotIn(SECRET, handle.read())

    def test_partial_and_output_dir(self):
        out = self.tmp / "out"
        out.mkdir()
        result = self.backup(output=str(out), no_home=True)
        self.assertEqual(result["includes"], ["work"])
        with tarfile.open(result["file"]) as archive:
            self.assertEqual(archive.getnames(), ["manifest.toml", "devc.toml", "data/work.tar.gz", "SHA256SUMS"])
        self.assertEqual(backup.inspect(result["file"])["includes"], ["work"])
        with self.assertRaises(NotFoundError):
            self.backup(output=str(self.tmp / "fehlt"))
        with self.assertRaises(ValidationError):
            self.backup(label="Nicht Gut")

    def test_stopped_container_is_started_and_stopped_again(self):
        core.stop(self.cfg, "py-tools")
        self.provider.calls.clear()
        self.backup()
        order = [c[0] for c in self.provider.calls if c[0] in ("start", "stop", "exec")]
        self.assertEqual((order[0], order[-1]), ("start", "stop"))
        self.assertEqual(self.provider.containers["py-tools"].status, "stopped")

    def test_shared_folder_is_packed_on_host(self):
        root = self.tmp / "srv"
        (root / "sh" / "src").mkdir(parents=True)
        (root / "sh" / "src" / "a.txt").write_text("vom host")
        self.write_config(f'allowed_roots = ["{root}"]\n')
        self.create("sh", template=None, text=f'name = "sh"\naddons = []\n[data]\nshared = "{root}/sh"\n', secret_values={})
        self.provider.calls.clear()
        result = core.backup(self.cfg, "sh")
        with tarfile.open(result["file"]) as archive:
            work = archive.extractfile("data/work.tar.gz").read()
        with tarfile.open(fileobj=io.BytesIO(work)) as inner:
            self.assertEqual(inner.extractfile("src/a.txt").read(), b"vom host")
        self.assertEqual([c[1][1][2] for c in self.provider.called("exec") if c[1][1][:2] == ["tar", "-C"]], ["/home/dev"])

    def test_list_and_rm(self):
        first = os.path.basename(self.backup()["file"])
        listed = core.backup_list(self.cfg, "py-tools")["backups"]
        self.assertEqual([(b["file"], b["container"]) for b in listed], [(first, "py-tools")])
        self.assertEqual(core.backup_list(self.cfg, "anderer")["backups"], [])
        self.assertEqual(core.backup_file(self.cfg, first).name, first)
        for bad in ("../x.devc.tar", "fehlt-20260101-000000.devc.tar", "x"):
            with self.assertRaises(NotFoundError):
                core.backup_file(self.cfg, bad)
        core.backup_rm(self.cfg, first)
        self.assertEqual(core.backup_list(self.cfg)["backups"], [])


class InspectTest(BackupTestCase):
    def rebuild(self, change):
        """Schreibt das Backup mit verändertem Inhalt neu; `change(entries)` bearbeitet {name: bytes}."""
        source = self.backup()["file"]
        with tarfile.open(source) as archive:
            entries = {name: archive.extractfile(name).read() for name in archive.getnames()}
        extra = change(entries)
        extra = extra if isinstance(extra, list) else []  # dict.pop liefert den entfernten Inhalt
        target = self.tmp / "manipuliert.devc.tar"
        with tarfile.open(target, "w") as archive:
            for name, content in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            for info in extra:
                archive.addfile(info)
        return target

    def test_valid(self):
        result = backup.inspect(self.backup()["file"])
        self.assertEqual((result["manifest"]["name"], result["includes"], result["checks"][0]["ok"]), ("py-tools", ["work", "home"], True))
        self.assertIn('name = "py-tools"', result["devc_toml"])

    def test_manipulated_archives(self):
        def link(entries):
            info = tarfile.TarInfo("data/link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            return [info]

        cases = {
            "Daten verändert": lambda e: e.__setitem__("data/work.tar.gz", e["data/work.tar.gz"] + b"x"),
            "devc.toml verändert": lambda e: e.__setitem__("devc.toml", e["devc.toml"] + b"\n# x"),
            "Eintrag entfernt": lambda e: e.pop("data/home.tar.gz"),
            "SHA256SUMS fehlt": lambda e: e.pop("SHA256SUMS"),
            "fremder Eintrag": lambda e: e.__setitem__("../../raus", b"x"),
            "zusätzliche Datei": lambda e: e.__setitem__("data/extra.tar.gz", b"x"),
            "Symlink": link,
            "SHA256SUMS kaputt": lambda e: e.__setitem__("SHA256SUMS", b"keine summen\n"),
        }
        for label, change in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValidationError) as ctx:
                    backup.inspect(self.rebuild(change))
                self.assertEqual(ctx.exception.code, "E_VALIDATION")

    def test_not_a_backup(self):
        file = self.tmp / "x.devc.tar"
        file.write_bytes(b"kein tar")
        with self.assertRaises(ValidationError):
            backup.inspect(file)
        with self.assertRaises(NotFoundError):
            core.restore_inspect(self.cfg, str(self.tmp / "fehlt"))

    def test_restore_rejects_manipulated_archive_before_changes(self):
        target = self.rebuild(lambda e: e.__setitem__("data/work.tar.gz", b"x"))
        self.provider.calls.clear()
        with self.assertRaises(ValidationError):
            core.restore(self.cfg, str(target), replace="py-tools")
        self.assertEqual(self.provider.called("delete"), [])
        self.assertIn("py-tools", self.provider.containers)


class ProgressTest(BackupTestCase):
    """SPEC 8.8: Stand langer Schritte als eigenes Ereignis, nicht im Log."""

    def setUp(self):
        super().setUp()
        self.seen = []
        core.set_progress_hook(self.seen.append)
        self.addCleanup(core.set_progress_hook, None)

    def test_backup_reports_estimate(self):
        size = len(gzip.decompress(self.provider.areas["py-tools"]["/work"]))
        self.provider.exec_results.insert(0, (["sh", "-c", core._AREA_SIZE, "sh", "/work"], fakes.ExecResult(0, f"{size * 2}\n")))
        result = self.backup(on_log=self.log.append)
        work = [p for p in self.seen if p and p["label"] == "Code (/work) sichern"]
        self.assertEqual((work[0]["done"], work[0]["total"], work[0]["percent"]), (0, size * 2, 0))
        self.assertEqual((work[-1]["done"], work[-1]["percent"]), (size, 50))
        home = [p for p in self.seen if p and p["label"] == "Home (/home/dev) sichern"]
        self.assertEqual((home[0]["total"], home[0]["percent"]), (None, None))  # Schätzung gescheitert: nur Bytes
        self.assertIsNone(self.seen[-1])  # am Ende ist der Stand weg
        self.assertEqual(self.seen.count(None), 2)
        self.assertFalse(any("sichern" in line and "%" in line for line in self.log))  # nichts davon im Log
        job_log = self.cfg.paths.job_log(result["job"])
        self.assertFalse(jobs.progress_file(job_log).exists())
        self.assertEqual([e for e, _ in core.job_events(self.cfg, result["job"])].count("progress"), 0)

    def test_estimate_never_shows_100(self):
        self.provider.exec_results.insert(0, (["sh", "-c", core._AREA_SIZE], fakes.ExecResult(0, "10\n")))  # viel zu klein geschätzt
        self.backup()
        self.assertEqual(max(p["percent"] for p in self.seen if p), 99)

    def test_restore_is_exact(self):
        file = self.backup()["file"]
        self.seen.clear()
        core.restore(self.cfg, file, new_name="kopie", secret_values={"claude-code.api_key": SECRET})
        work = [p for p in self.seen if p and p["label"] == "Code (/work) wiederherstellen"]
        self.assertEqual((work[0]["done"], work[0]["percent"]), (0, 0))
        self.assertEqual((work[-1]["done"], work[-1]["percent"]), (backup.area_size(file, "work"), 100))
        self.assertTrue(any(p and p["label"] == "Home (/home/dev) wiederherstellen" for p in self.seen))
        self.assertIsNone(self.seen[-1])

    def test_failed_dump_leaves_no_progress(self):
        self.provider.exec_results.insert(0, (["tar", "-C", "/work"], fakes.ExecResult(2, "", "tar: kaputt")))
        self.provider.areas["py-tools"].pop("/work")
        original = self.provider._simulate_data
        self.provider._simulate_data = lambda name, argv, stdin, stdout: None if argv[:3] == ["tar", "-C", "/work"] else original(name, argv, stdin, stdout)
        with self.assertRaises(JobFailedError) as ctx:
            self.backup()
        self.assertIn("tar: kaputt", ctx.exception.message)
        self.assertIsNone(self.seen[-1])
        self.assertEqual([f for f in os.listdir(self.cfg.paths.logs) if f.endswith(".progress")], [])

    def test_running_job_is_active_only_with_lock(self):
        """Die GUI zeigt einen laufenden Job nach einem Seitenwechsel wieder an; ein liegen gebliebener zählt nicht."""
        store = core._store(self.cfg)
        store.job_add("j-0123456789ab", "py-tools", "backup", "running", "2026-10-12T00:00:00Z")
        self.assertIs(core.jobs(self.cfg, "py-tools")["jobs"][0]["active"], False)
        with locks.container_lock(self.cfg.paths, "py-tools"):
            self.assertIs(core.jobs(self.cfg, "py-tools")["jobs"][0]["active"], True)
        self.backup()
        self.assertNotIn("active", [j for j in core.jobs(self.cfg, "py-tools")["jobs"] if j["state"] == "ok"][0])

    def test_events_carry_progress(self):
        job_id = "j-0123456789ab"
        store = core._store(self.cfg)
        self.cfg.paths.logs.mkdir(parents=True, exist_ok=True)
        log_file = self.cfg.paths.job_log(job_id)
        job = jobs.Job(job_id, "backup", "py-tools", log_file, core.clock)
        store.job_add(job_id, "py-tools", "backup", "running", "2026-10-12T00:00:00Z")
        job.log("backup", "los")
        job.progress("Code (/work) sichern", 512, 2048)
        stream = core.job_events(self.cfg, job_id)
        self.assertEqual(next(stream)[0], "log")
        self.assertEqual(next(stream), ("progress", {"label": "Code (/work) sichern", "done": 512, "total": 2048, "percent": 25, "exact": False}))
        job.progress("Code (/work) sichern", 600, 2048)  # gleiche Prozentzahl, gleich danach: keine neue Meldung
        job.progress_end()
        self.assertEqual(next(stream), ("progress", None))
        store.job_update(job_id, "ok", finished_at="2026-10-12T00:00:01Z")
        self.assertEqual([e for e, _ in stream], ["state", "end"])


class RestoreTest(BackupTestCase):
    def test_new_container_with_changed_config(self):
        file = self.backup()["file"]
        text = backup.set_name(backup.inspect(file)["devc_toml"], "kopie").replace("cpu = 4", "cpu = 2")
        self.provider.calls.clear()
        result = core.restore(self.cfg, file, new_name="kopie", text=text, secret_values={"claude-code.api_key": SECRET}, on_log=self.log.append)
        self.assertEqual((result["name"], result["status"], result["replaced"], result["includes"]), ("kopie", "running", False, ["work", "home"]))
        self.assertEqual(self.provider.called("create")[0][1][0].cpu, 2)
        self.assertEqual({k: gzip.decompress(v) for k, v in self.provider.areas["kopie"].items()},
                         {k: gzip.decompress(v) for k, v in self.provider.areas["py-tools"].items()})
        restores = [c for c in self.provider.called("exec") if c[1][1][:2] == ["sh", "-c"] and c[1][1][2] == core._RESTORE_AREA]
        self.assertEqual([(c[1][1][4], c[2]["user"]) for c in restores], [("/work", "root"), ("/home/dev", "root")])
        self.assertIn('find "$1" -mindepth 1 -delete', core._RESTORE_AREA)
        # Daten vor den Addons: install-Skripte laufen auf den wiederhergestellten Daten
        order = [c[0] for c in self.provider.calls]
        last_restore = max(i for i, c in enumerate(self.provider.calls) if c in restores)
        self.assertLess(last_restore, order.index("exec_stream"))
        self.assertIn("# Dev-Container kopie", self.provider.files["kopie"]["/work/CLAUDE.md"])
        self.assertEqual(core._store(self.cfg).job(result["job"])["kind"], "restore")
        self.assertIn("py-tools", self.provider.containers)  # Original bleibt

    def test_default_text_gets_new_name_and_needs_secrets(self):
        file = self.backup()["file"]
        with self.assertRaises(MissingSecretError):
            core.restore(self.cfg, file, new_name="kopie")
        secrets.shared_set(self.cfg.paths, "anthropic", b"store-wert")
        core.restore(self.cfg, file, new_name="kopie")
        self.assertIn('name = "kopie"', self.cfg.paths.instance_file("kopie").read_text())
        with self.assertRaises(ConflictError):
            core.restore(self.cfg, file, new_name="kopie")

    def test_replace_keeps_volumes_and_secrets(self):
        file = self.backup(no_home=True)["file"]
        self.provider.areas["py-tools"]["/work"] = b"neuerer stand"
        self.provider.calls.clear()
        result = core.restore(self.cfg, file, replace="py-tools")
        self.assertTrue(result["replaced"])
        self.assertEqual(self.provider.called("delete")[0][1], ("py-tools", True))
        self.assertNotEqual(self.provider.areas["py-tools"]["/work"], b"neuerer stand")
        self.assertEqual(self.provider.secrets["py-tools"]["/run/devc/secrets/claude-code.api_key"], SECRET)
        self.assertEqual(len(core.list_containers(self.cfg)["containers"]), 1)

    def test_usage(self):
        file = self.backup()["file"]
        for kwargs in ({}, {"new_name": "a1", "replace": "py-tools"}):
            with self.assertRaises(UsageError):
                core.restore(self.cfg, file, **kwargs)
        with self.assertRaises(NotFoundError):
            core.restore(self.cfg, str(self.tmp / "fehlt"), new_name="a1")
        with self.assertRaises(NotFoundError):
            core.restore(self.cfg, file, replace="gibtsnicht")

    def test_cli(self):
        code, out, err = run_cli("backup", "py-tools", "-q")
        self.assertEqual(code, 0)
        file = out.strip()
        with mock.patch.object(cli, "_interactive", return_value=False):
            self.assertEqual(run_cli("restore", file, "--replace", "py-tools")[0], 2)  # ohne Bestätigung nichts
            self.assertEqual(self.provider.called("delete"), [])
            code, out, err = run_cli("restore", file, "--replace", "py-tools", "--yes", "-q")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(run_cli("restore", file, "--name", "neu", "--yes")[0], 5)  # Secret fehlt


class SnapshotTest(BackupTestCase):
    def test_create_list_restore_rm(self):
        result = core.snapshot_create(self.cfg, "py-tools", label="vor-umbau")
        self.assertEqual((result["snapshot"], result["scope"]), ("vor-umbau", "data"))
        self.assertEqual([c[1] for c in self.provider.called("volume_snapshot")], [("py-tools", "work", "vor-umbau"), ("py-tools", "home", "vor-umbau")])
        auto = core.snapshot_create(self.cfg, "py-tools", full=True)
        self.assertRegex(auto["snapshot"], r"^auto-\d{8}-\d{6}$")
        self.assertEqual(self.provider.called("full_snapshot")[0][1], ("py-tools", auto["snapshot"]))
        listed = core.snapshot_list(self.cfg, "py-tools")["snapshots"]
        self.assertEqual([(s["name"], s["scope"], s["auto"]) for s in listed], [("vor-umbau", "data", False), (auto["snapshot"], "full", True)])
        with self.assertRaises(ConflictError):
            core.snapshot_create(self.cfg, "py-tools", label="vor-umbau")
        with self.assertRaises(ValidationError):
            core.snapshot_create(self.cfg, "py-tools", label="Nicht Gut")

        self.provider.calls.clear()
        core.snapshot_restore(self.cfg, "py-tools", auto["snapshot"])
        order = [c[0] for c in self.provider.calls if c[0] in ("stop", "full_restore", "volume_restore", "start")]
        self.assertEqual(order, ["stop", "full_restore", "volume_restore", "volume_restore", "start"])
        self.assertEqual(self.provider.secrets["py-tools"]["/run/devc/secrets/claude-code.api_key"], SECRET)  # nach dem Start wieder da
        with self.assertRaises(NotFoundError):
            core.snapshot_restore(self.cfg, "py-tools", "fehlt")

        core.snapshot_rm(self.cfg, "py-tools", auto["snapshot"])
        self.assertEqual(self.provider.called("full_snapshot_delete")[0][1], ("py-tools", auto["snapshot"]))
        self.assertEqual(len(self.provider.called("volume_snapshot_delete")), 2)
        self.assertEqual(len(core.snapshot_list(self.cfg, "py-tools")["snapshots"]), 1)
        with self.assertRaises(NotFoundError):
            core.snapshot_rm(self.cfg, "py-tools", auto["snapshot"])

    def test_shared_folder_uses_tar_and_replaces_host_folder(self):
        root = self.tmp / "srv"
        work = root / "sh"
        (work / "src").mkdir(parents=True)
        (work / "src" / "a.txt").write_text("alt")
        outside = self.tmp / "aussen.txt"
        outside.write_text("bleibt")
        self.write_config(f'allowed_roots = ["{root}"]\n')
        self.create("sh", template=None, text=f'name = "sh"\naddons = []\n[data]\nshared = "{work}"\n', secret_values={})
        (work / "link").symlink_to(outside)
        core.snapshot_create(self.cfg, "sh", label="s1")
        archive = self.cfg.paths.snapshot_dir("sh", "s1") / "work.tar.gz"
        self.assertTrue(archive.is_file())
        self.assertEqual([c[1] for c in self.provider.called("volume_snapshot") if c[1][0] == "sh"], [("sh", "home", "s1")])
        (work / "src" / "a.txt").write_text("neu")
        (work / "zusatz.txt").write_text("kommt weg")
        core.snapshot_restore(self.cfg, "sh", "s1")
        self.assertEqual((work / "src" / "a.txt").read_text(), "alt")
        self.assertFalse((work / "zusatz.txt").exists())
        self.assertTrue((work / "link").is_symlink())
        self.assertEqual(outside.read_text(), "bleibt")  # dem Symlink wird beim Leeren nicht gefolgt
        core.snapshot_rm(self.cfg, "sh", "s1")
        self.assertFalse(archive.exists())

    def test_unpack_rejects_escaping_paths(self):
        target = self.tmp / "ziel"
        target.mkdir()
        (self.tmp / "aussen").mkdir()
        (target / "raus").symlink_to(self.tmp / "aussen")
        for name in ("../x", "/abs/x", "raus/x"):
            with self.subTest(name=name):
                file = self.tmp / "s.tar.gz"
                file.write_bytes(tgz({name: b"x"}))
                with self.assertRaises(ValidationError):
                    backup.unpack_directory(file, target)
        self.assertEqual(os.listdir(self.tmp / "aussen"), [])

    def test_cli_restore_needs_confirmation(self):
        run_cli("snapshot", "create", "py-tools", "--label", "s1", "-q")
        with mock.patch.object(cli, "_interactive", return_value=False):
            self.assertEqual(run_cli("snapshot", "restore", "py-tools", "s1")[0], 2)
        self.assertEqual(self.provider.called("volume_restore"), [])
        self.assertEqual(run_cli("snapshot", "restore", "py-tools", "s1", "--yes", "-q"), (0, "", ""))
        code, out, err = run_cli("snapshot", "list", "py-tools")
        self.assertRegex(out, r"(?m)^s1\s+\S+\s+Daten$")
        self.assertEqual(run_cli("snapshot", "rm", "py-tools", "s1")[0], 0)


class FilesTest(BackupTestCase):
    """L13."""

    def test_push(self):
        local = self.tmp / "a.txt"
        local.write_text("x")
        local.chmod(0o640)
        self.assertEqual(core.push(self.cfg, "py-tools", str(local))["dest"], "/work/a.txt")
        self.assertEqual(core.push(self.cfg, "py-tools", str(local), "src/b.txt")["dest"], "/work/src/b.txt")
        self.assertEqual(core.push(self.cfg, "py-tools", str(local), "src/")["dest"], "/work/src/a.txt")
        call = self.provider.called("push")[0]
        self.assertEqual((call[1], call[2]), (("py-tools", str(local), "/work/a.txt"), {"mode": "0640", "uid": 1000, "gid": 1000}))
        self.assertEqual(self.provider.called("exec")[0][1][1], ["realpath", "-m", "/work/a.txt"])

    def test_paths_outside_work_are_security_errors(self):
        local = self.tmp / "a.txt"
        local.write_text("x")
        self.provider.realpaths["/work/link/x"] = "/etc/x"  # Symlink im Container zeigt nach außen
        for dest in ("../etc/passwd", "/etc/passwd", "a/../../x", "link/x", "/workx/a"):
            with self.subTest(dest=dest):
                with self.assertRaises(SecurityError) as ctx:
                    core.push(self.cfg, "py-tools", str(local), dest)
                self.assertEqual((ctx.exception.code, ctx.exception.details["check"]), ("E_SECURITY", "L13"))
                with self.assertRaises(SecurityError):
                    core.pull(self.cfg, "py-tools", dest)
        self.assertEqual(self.provider.called("push"), [])
        self.assertEqual(self.provider.called("pull"), [])

    def test_pull_file_and_directory(self):
        self.provider.exec_results.append((["sh", "-c", 'if [ -d "$1" ]; then echo d; elif [ -f "$1" ]; then echo f; else exit 44; fi', "sh", "/work/src/a.py"], fakes.ExecResult(0, "f\n")))
        target = self.tmp / "geholt.py"
        result = core.pull(self.cfg, "py-tools", "src/a.py", str(target))
        self.assertEqual((result["kind"], self.provider.called("pull")[0][1]), ("file", ("py-tools", "/work/src/a.py", str(target))))
        self.provider.exec_results.insert(0, (["sh", "-c", 'if [ -d "$1" ]; then echo d; elif [ -f "$1" ]; then echo f; else exit 44; fi', "sh", "/work"], fakes.ExecResult(0, "d\n")))
        out = self.tmp / "work.tar.gz"
        result = core.pull(self.cfg, "py-tools", "/work", str(out))
        self.assertEqual(result["kind"], "directory")
        self.assertEqual(out.read_bytes(), self.provider.areas["py-tools"]["/work"])
        self.provider.exec_results.insert(0, (["sh", "-c"], fakes.ExecResult(44)))
        with self.assertRaises(NotFoundError):
            core.pull(self.cfg, "py-tools", "fehlt")

    def test_errors(self):
        with self.assertRaises(NotFoundError):
            core.push(self.cfg, "py-tools", str(self.tmp / "fehlt"))
        core.stop(self.cfg, "py-tools")
        local = self.tmp / "a.txt"
        local.write_text("x")
        with self.assertRaises(ConflictError):
            core.push(self.cfg, "py-tools", str(local))


if __name__ == "__main__":
    unittest.main()
