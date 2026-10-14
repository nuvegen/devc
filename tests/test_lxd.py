"""T-LXD: für jede Zeile aus SPEC 8.3 die exakte Argumentliste, gegen ein gefälschtes lxc."""

import os
import unittest

from devc.errors import EngineError, NotFoundError
from devc.providers.base import CreateSpec, Provider
from devc.providers.lxd import LxdProvider
from tests import fakes, helpers

P = ["--project", "devcontainers"]
SERVER = {"auth": "trusted", "environment": {"server_version": "6.9", "kernel_features": {"idmapped_mounts": "true"}}}
PROFILE_URL = "/1.0/profiles/default?project=devcontainers"
FULL_PROFILE = {"devices": {"root": {"type": "disk"}, "eth0": {"type": "nic"}}}
MANAGED = {
    "features.images": "false", "features.profiles": "true", "features.storage.volumes": "true",
    "features.networks": "false", "restricted": "true", "restricted.snapshots": "allow",
    "restricted.devices.disk": "managed",
}


def instance(name, status="Running", ip="10.119.122.20"):
    network = None
    if status == "Running":
        network = {
            "lo": {"addresses": [{"family": "inet", "address": "127.0.0.1", "scope": "local"}]},
            "eth0": {"addresses": [
                {"family": "inet6", "address": "fd42::1", "scope": "global"},
                {"family": "inet", "address": ip, "scope": "global"},
            ]},
        }
    return {"name": name, "status": status, "created_at": "2026-10-10T07:00:00Z", "state": {"network": network}}


class LxdTestCase(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.lxc = fakes.FakeLxc(self.tmp / "bin")
        self.addCleanup(self.lxc.close)

    def provider(self, **kwargs):
        options = dict(project="devcontainers", pool="default", network="lxdbr0", uid=1000, gid=1000)
        options.update(kwargs)
        return LxdProvider(**options)

    def assert_calls(self, expected):
        self.assertEqual(self.lxc.calls(), expected)


class LxdCommandTest(LxdTestCase):
    def test_implements_interface(self):
        self.assertIsInstance(self.provider(), Provider)

    def test_project_create_managed(self):
        self.lxc.on(["project", "list"], [{"name": "default", "config": {}}])
        self.lxc.on(["query", PROFILE_URL], {"devices": {}})
        steps = self.provider().init_project()
        self.assert_calls([
            ["project", "list", "--format", "json"],
            ["project", "create", "devcontainers",
             "-c", "features.images=false", "-c", "features.profiles=true",
             "-c", "features.storage.volumes=true", "-c", "features.networks=false",
             "-c", "restricted=true", "-c", "restricted.snapshots=allow",
             "-c", "restricted.devices.disk=managed"],
            ["query", PROFILE_URL],
            P + ["profile", "device", "add", "default", "root", "disk", "path=/", "pool=default"],
            P + ["profile", "device", "add", "default", "eth0", "nic", "network=lxdbr0", "name=eth0"],
        ])
        self.assertEqual(len(steps), 3)

    def test_project_create_with_roots_and_idmap(self):
        self.lxc.on(["project", "list"], [])
        self.lxc.on(["query", PROFILE_URL], FULL_PROFILE)
        self.provider(allowed_roots=["/srv/dev", "/data"], shared_mode="idmap", uid=1001, gid=1002).init_project()
        self.assertEqual(self.lxc.calls()[1], [
            "project", "create", "devcontainers",
            "-c", "features.images=false", "-c", "features.profiles=true",
            "-c", "features.storage.volumes=true", "-c", "features.networks=false",
            "-c", "restricted=true", "-c", "restricted.snapshots=allow",
            "-c", "restricted.devices.disk=allow",
            "-c", "restricted.devices.disk.paths=/srv/dev,/data",
            "-c", "restricted.idmap.uid=1001", "-c", "restricted.idmap.gid=1002",
        ])

    def test_init_is_idempotent(self):
        self.lxc.on(["project", "list"], [{"name": "devcontainers", "config": MANAGED}])
        self.lxc.on(["query", PROFILE_URL], FULL_PROFILE)
        self.assertEqual(self.provider().init_project(), [])
        self.assert_calls([["project", "list", "--format", "json"], ["query", PROFILE_URL]])

    def test_init_syncs_restrictions(self):
        self.lxc.on(["project", "list"], [{"name": "devcontainers", "config": MANAGED}])
        self.lxc.on(["query", PROFILE_URL], FULL_PROFILE)
        self.provider(allowed_roots=["/srv/dev"]).init_project()
        self.assertEqual(self.lxc.calls()[1:3], [
            ["project", "set", "devcontainers", "restricted.devices.disk.paths", "/srv/dev"],
            ["project", "set", "devcontainers", "restricted.devices.disk", "allow"],
        ])
        self.lxc.reset()
        allow = dict(MANAGED, **{"restricted.devices.disk": "allow", "restricted.devices.disk.paths": "/srv/dev"})
        self.lxc.on(["project", "list"], [{"name": "devcontainers", "config": allow}])
        self.lxc.on(["query", PROFILE_URL], FULL_PROFILE)
        self.provider().init_project()
        # nie disk=allow ohne Pfadliste: erst auf managed stellen, dann die Pfadliste entfernen
        self.assertEqual(self.lxc.calls()[1:3], [
            ["project", "set", "devcontainers", "restricted.devices.disk", "managed"],
            ["project", "unset", "devcontainers", "restricted.devices.disk.paths"],
        ])

    def test_init_allows_snapshots_on_existing_project(self):
        old = {k: v for k, v in MANAGED.items() if k != "restricted.snapshots"}
        self.lxc.on(["project", "list"], [{"name": "devcontainers", "config": old}])
        self.lxc.on(["query", PROFILE_URL], FULL_PROFILE)
        steps = self.provider().init_project()
        self.assertEqual(self.lxc.calls()[1], ["project", "set", "devcontainers", "restricted.snapshots", "allow"])
        self.assertEqual(len(steps), 1)

    def test_create_with_volumes(self):
        self.provider().create(CreateSpec("py-tools", "ubuntu:24.04", 4, "8GiB", "30GiB"))
        self.assert_calls([
            P + ["storage", "volume", "create", "default", "devc-py-tools-work"],
            P + ["storage", "volume", "create", "default", "devc-py-tools-home"],
            P + ["init", "ubuntu:24.04", "py-tools", "-c", "limits.cpu=4", "-c", "limits.memory=8GiB", "-d", "root,size=30GiB"],
            P + ["config", "device", "add", "py-tools", "work", "disk", "pool=default", "source=devc-py-tools-work", "path=/work"],
            P + ["config", "device", "add", "py-tools", "home", "disk", "pool=default", "source=devc-py-tools-home", "path=/home/dev"],
        ])

    def test_create_reuses_kept_volumes(self):
        self.lxc.on(["storage", "volume", "create"], stderr="Error: Volume by that name already exists\n", rc=1)
        self.provider().create(CreateSpec("py-tools", "ubuntu:24.04", 4, "8GiB", "30GiB"))
        self.assertEqual(len(self.lxc.calls()), 5)
        self.lxc.reset()
        self.lxc.on(["storage", "volume", "create"], stderr="Error: Pool is full\n", rc=1)
        with self.assertRaises(EngineError):
            self.provider().create(CreateSpec("py-tools", "ubuntu:24.04", 4, "8GiB", "30GiB"))

    def test_create_with_shared_folder_shift(self):
        self.provider().create(CreateSpec("py-tools", "ubuntu:24.04", 2, "4GiB", "20GiB", shared="/srv/dev/py-tools"))
        self.assert_calls([
            P + ["storage", "volume", "create", "default", "devc-py-tools-home"],
            P + ["init", "ubuntu:24.04", "py-tools", "-c", "limits.cpu=2", "-c", "limits.memory=4GiB", "-d", "root,size=20GiB"],
            P + ["config", "device", "add", "py-tools", "work", "disk", "source=/srv/dev/py-tools", "path=/work", "shift=true"],
            P + ["config", "device", "add", "py-tools", "home", "disk", "pool=default", "source=devc-py-tools-home", "path=/home/dev"],
        ])

    def test_create_with_shared_folder_idmap(self):
        self.provider(shared_mode="idmap", uid=1001).create(
            CreateSpec("py-tools", "ubuntu:24.04", 2, "4GiB", "20GiB", shared="/srv/dev/py-tools")
        )
        self.assertEqual(self.lxc.calls()[2:4], [
            P + ["config", "device", "add", "py-tools", "work", "disk", "source=/srv/dev/py-tools", "path=/work"],
            P + ["config", "set", "py-tools", "raw.idmap", "both 1001 1000"],
        ])

    def test_exec(self):
        self.lxc.on(["exec", "py-tools"], stdout="hallo\n", stderr="warn\n", rc=7)
        result = self.provider().exec(
            "py-tools", ["bash", "-lc", "echo 'a b'"], user="root", cwd="/tmp", env={"DEVC_ADDON": "base", "X": "1 2"}, stdin=b"eingabe"
        )
        self.assertEqual((result.rc, result.stdout, result.stderr), (7, "hallo\n", "warn\n"))
        record = self.lxc.records()[0]
        self.assertEqual(record["argv"], P + [
            "exec", "py-tools", "--user", "0", "--group", "0", "--cwd", "/tmp",
            "--env", "HOME=/root", "--env", "DEVC_ADDON=base", "--env", "X=1 2",
            "--", "bash", "-lc", "echo 'a b'",
        ])
        self.assertEqual(record["stdin"], "eingabe")

    def test_exec_defaults_to_dev_in_work(self):
        self.provider().exec("py-tools", ["true"])
        self.assert_calls([P + ["exec", "py-tools", "--user", "1000", "--group", "1000", "--cwd", "/work", "--env", "HOME=/home/dev", "--", "true"]])

    def test_exec_stream(self):
        self.lxc.on(["exec", "py-tools"], stdout="eins\nzwei\n", rc=3)
        lines = []
        rc = self.provider().exec_stream("py-tools", ["make"], on_line=lines.append)
        self.assertEqual((rc, lines), (3, ["eins", "zwei"]))

    def test_pty_argv(self):
        self.assertEqual(self.provider().shell_argv("py-tools", user="root"), ["lxc"] + P + [
            "exec", "py-tools", "-t", "--user", "0", "--group", "0", "--cwd", "/work", "--env", "HOME=/root", "--", "bash", "-l",
        ])

    def test_exec_pty(self):
        self.lxc.on(["exec", "py-tools"], stdout="aus dem pty\n")
        provider = self.provider()
        pid, master = provider.exec_pty("py-tools", ["bash", "-l"])
        self.addCleanup(os.close, master)
        data = b""
        while b"\n" not in data:
            data += os.read(master, 1024)
        self.assertEqual(provider.pty_wait(pid, timeout=10), 0)
        self.assertIsNone(provider.pty_wait(pid))
        self.assertIn(b"aus dem pty", data)
        self.assertEqual(self.lxc.calls()[0][:5], P + ["exec", "py-tools", "-t"])
        # Der Dienst hat kein TERM, das lxc weiterreichen könnte; ohne TERM scheitern clear, top und nano.
        call = self.lxc.calls()[0]
        self.assertEqual(call[call.index("--env") + 2:call.index("--")], ["--env", "TERM=xterm-256color"])

    def test_list_and_ip(self):
        self.lxc.on(["list"], [instance("py-tools"), instance("py", "Stopped")])
        states = self.provider().list()
        self.assert_calls([P + ["list", "--format", "json"]])
        self.assertEqual([(s.name, s.status, s.ip) for s in states], [("py-tools", "running", "10.119.122.20"), ("py", "stopped", None)])
        self.assertEqual(states[0].created, "2026-10-10T07:00:00Z")

    def test_state_matches_exact_name(self):
        self.lxc.on(["list"], [instance("py-tools"), instance("py", "Stopped")])
        provider = self.provider()
        self.assertEqual(provider.state("py").status, "stopped")
        self.assert_calls([P + ["list", "py", "--format", "json"]])
        with self.assertRaises(NotFoundError):
            provider.state("py-t")

    def test_start_stop(self):
        provider = self.provider()
        provider.start("py-tools")
        provider.stop("py-tools")
        self.assert_calls([P + ["start", "py-tools"], P + ["stop", "py-tools", "--timeout", "30"]])

    def test_stop_forces_after_timeout(self):
        self.lxc.on(["stop", "py-tools", "--timeout"], stderr="Error: timed out\n", rc=1)
        self.provider().stop("py-tools")
        self.assertEqual(self.lxc.calls()[1], P + ["stop", "py-tools", "--force"])

    def test_stop_already_stopped(self):
        self.lxc.on(["stop"], stderr="Error: The instance is already stopped\n", rc=1)
        self.provider().stop("py-tools")
        self.assertEqual(len(self.lxc.calls()), 1)

    def test_delete(self):
        self.provider().delete("py-tools", keep_data=False)
        self.assert_calls([
            P + ["delete", "py-tools", "--force"],
            P + ["storage", "volume", "delete", "default", "devc-py-tools-work"],
            P + ["storage", "volume", "delete", "default", "devc-py-tools-home"],
        ])

    def test_delete_keep_data(self):
        self.provider().delete("py-tools", keep_data=True)
        self.assert_calls([P + ["delete", "py-tools", "--force"]])

    def test_delete_without_code_volume(self):
        self.lxc.on(["devc-py-tools-work"], stderr="Error: Storage volume not found\n", rc=1)
        self.provider().delete("py-tools", keep_data=False)
        self.assertEqual(len(self.lxc.calls()), 3)

    def test_set_limits(self):
        self.provider().set_limits("py-tools", 2, "4GiB", "40GiB")
        self.assert_calls([
            P + ["config", "set", "py-tools", "limits.cpu=2", "limits.memory=4GiB"],
            P + ["config", "device", "set", "py-tools", "root", "size=40GiB"],
        ])

    def test_push_pull(self):
        provider = self.provider()
        provider.push("py-tools", "/tmp/a.txt", "/work/a.txt", mode="0600", uid=0, gid=0)
        provider.pull("py-tools", "/work/b.txt", "/tmp/b.txt")
        self.assert_calls([
            P + ["file", "push", "/tmp/a.txt", "py-tools/work/a.txt", "--uid", "0", "--gid", "0", "--mode", "0600", "--create-dirs"],
            P + ["file", "pull", "py-tools/work/b.txt", "/tmp/b.txt"],
        ])

    def test_volume_snapshots(self):
        self.lxc.on(["list"], [instance("py-tools")])
        provider = self.provider()
        provider.volume_snapshot("py-tools", "work", "vor-umbau")
        provider.volume_restore("py-tools", "home", "vor-umbau")
        provider.volume_snapshot_delete("py-tools", "work", "vor-umbau")
        self.assert_calls([
            P + ["storage", "volume", "snapshot", "default", "devc-py-tools-work", "vor-umbau"],
            P + ["list", "py-tools", "--format", "json"],
            P + ["stop", "py-tools", "--timeout", "30"],
            P + ["storage", "volume", "restore", "default", "devc-py-tools-home", "vor-umbau"],
            P + ["start", "py-tools"],
            P + ["storage", "volume", "delete", "default", "devc-py-tools-work/vor-umbau"],
        ])

    def test_volume_restore_keeps_stopped_container_stopped(self):
        self.lxc.on(["list"], [instance("py-tools", "Stopped")])
        self.provider().volume_restore("py-tools", "work", "s1")
        self.assertEqual(self.lxc.calls()[1:], [P + ["storage", "volume", "restore", "default", "devc-py-tools-work", "s1"]])

    def test_full_snapshot_of_running_container(self):
        self.lxc.on(["list"], [instance("py-tools")])
        self.provider().full_snapshot("py-tools", "s1")
        self.assert_calls([
            P + ["list", "py-tools", "--format", "json"],
            P + ["stop", "py-tools", "--timeout", "30"],
            P + ["snapshot", "py-tools", "s1"],
            P + ["start", "py-tools"],
        ])

    def test_full_restore_of_running_container(self):
        self.lxc.on(["list"], [instance("py-tools")])
        self.provider().full_restore("py-tools", "s1")
        self.assertEqual(self.lxc.calls()[1:], [
            P + ["stop", "py-tools", "--timeout", "30"],
            P + ["restore", "py-tools", "s1"],
            P + ["start", "py-tools"],
        ])

    def test_full_snapshot_and_restore_of_stopped_container(self):
        self.lxc.on(["list"], [instance("py-tools", "Stopped")])
        provider = self.provider()
        provider.full_snapshot("py-tools", "s1")
        provider.full_restore("py-tools", "s1")
        self.assert_calls([
            P + ["list", "py-tools", "--format", "json"],
            P + ["snapshot", "py-tools", "s1"],
            P + ["list", "py-tools", "--format", "json"],
            P + ["restore", "py-tools", "s1"],
        ])

    def test_full_restore_error_restarts_container(self):
        self.lxc.on(["list"], [instance("py-tools")])
        self.lxc.on(["restore"], stderr="Error: Snapshot kaputt\n", rc=1)
        with self.assertRaises(EngineError):
            self.provider().full_restore("py-tools", "f1")
        self.assertEqual(self.lxc.calls()[-1], P + ["start", "py-tools"])

    def test_volumes(self):
        self.lxc.on(["storage", "volume", "list"], [
            {"name": "devc-py-tools-work", "type": "custom"},
            {"name": "devc-py-tools-work/s1", "type": "custom"},
            {"name": "devc-py-tools-home", "type": "custom"},
            {"name": "fremd", "type": "custom"},
            {"name": "py-tools", "type": "container"},
        ])
        volumes = self.provider().volumes()
        self.assert_calls([P + ["storage", "volume", "list", "default", "--format", "json"]])
        self.assertEqual([(v.name, v.snapshots) for v in volumes], [("devc-py-tools-home", ()), ("devc-py-tools-work", ("s1",))])


class LxdErrorTest(LxdTestCase):
    def test_engine_error(self):
        self.lxc.on(["start"], stderr="Error: Failed to run: something broke\n", rc=1)
        with self.assertRaises(EngineError) as ctx:
            self.provider().start("py-tools")
        self.assertEqual(ctx.exception.code, "E_ENGINE")
        self.assertIn("something broke", ctx.exception.message)

    def test_error_line_is_preferred_over_hint(self):
        self.lxc.on(["start"], stderr='Error: Failed starting device "work": kaputt\nTry `lxc info --show-log x` for more info\n', rc=1)
        with self.assertRaises(EngineError) as ctx:
            self.provider().start("py-tools")
        self.assertIn('Failed starting device "work": kaputt', ctx.exception.message)

    def test_not_found(self):
        self.lxc.on(["start"], stderr="Error: Instance not found\n", rc=1)
        with self.assertRaises(NotFoundError):
            self.provider().start("py-tools")

    def test_missing_binary(self):
        with self.assertRaises(EngineError):
            self.provider(lxc=str(self.tmp / "gibtsnicht")).list()

    def test_bad_json(self):
        self.lxc.on(["list"], stdout="kein json")
        with self.assertRaises(EngineError):
            self.provider().list()

    def test_exec_timeout(self):
        script = self.lxc.path.read_text()
        self.lxc.path.write_text(script + "\nimport time\ntime.sleep(5)\n")
        with self.assertRaises(EngineError) as ctx:
            self.provider().exec("py-tools", ["sleep", "5"], timeout=0.5)
        self.assertTrue(ctx.exception.details.get("timeout"))


class LxdCheckTest(LxdTestCase):
    def prepare(self, server=SERVER, projects=None, profile=FULL_PROFILE):
        if projects is None:
            projects = [{"name": "devcontainers", "config": MANAGED}]
        self.lxc.on(["query", PROFILE_URL], profile)
        self.lxc.on(["query", "/1.0"], server)
        self.lxc.on(["project", "list"], projects)
        self.lxc.on(["storage", "list"], [{"name": "default"}])
        self.lxc.on(["network", "list"], [{"name": "lxdbr0", "managed": True}, {"name": "eth0", "managed": False}])

    def severities(self, findings):
        return {f.path: f.severity for f in findings}

    def test_all_ok(self):
        self.prepare()
        findings = self.provider().check()
        self.assertEqual(self.severities(findings), {"lxc": "ok", "project": "ok", "pool": "ok", "network": "ok", "shift": "ok"})
        # nur lesende Aufrufe
        for call in self.lxc.calls():
            self.assertIn(call[0] if call[0] != "--project" else call[2], ("query", "project", "storage", "network"))
            self.assertIn("list" if "list" in call else "query", call)

    def test_missing_project(self):
        self.prepare(projects=[{"name": "default", "config": {}}])
        findings = self.provider().check()
        self.assertEqual(self.severities(findings)["project"], "error")
        self.assertIn(["storage", "list", "--format", "json"], self.lxc.calls())

    def test_restriction_mismatch_and_profile(self):
        self.prepare(profile={"devices": {"root": {}}})
        findings = self.provider(allowed_roots=["/srv/dev"]).check()
        project = [f for f in findings if f.path == "project"]
        self.assertEqual([f.severity for f in project], ["warning", "error"])
        self.assertIn("eth0", project[1].message)

    def test_missing_pool_and_network(self):
        self.prepare()
        severities = self.severities(self.provider(pool="tank", network="eth0").check())
        self.assertEqual((severities["pool"], severities["network"]), ("error", "error"))

    def test_no_shift(self):
        server = {"auth": "trusted", "environment": {"kernel_features": {"idmapped_mounts": "false"}}}
        self.prepare(server=server)
        self.assertEqual(self.severities(self.provider().check())["shift"], "warning")
        self.assertEqual(self.severities(self.provider(shared_mode="idmap").check())["shift"], "ok")

    def test_shift_with_shared_folders_is_warned(self):
        self.prepare(projects=[{"name": "devcontainers", "config": dict(MANAGED, **{
            "restricted.devices.disk": "allow", "restricted.devices.disk.paths": "/srv/dev"})}])
        findings = [f for f in self.provider(allowed_roots=["/srv/dev"]).check() if f.path == "shift"]
        self.assertEqual([f.severity for f in findings], ["warning"])
        self.assertIn("idmap", findings[0].message)

    def test_unreachable(self):
        self.lxc.on(["query", "/1.0"], stderr="Error: Get http://unix.socket/1.0: permission denied\n", rc=1)
        findings = self.provider().check()
        self.assertEqual([(f.path, f.severity) for f in findings], [("lxc", "error")])

    def test_untrusted(self):
        self.prepare(server={"auth": "untrusted", "environment": {}})
        self.assertEqual([(f.path, f.severity) for f in self.provider().check()], [("lxc", "error")])


if __name__ == "__main__":
    unittest.main()
