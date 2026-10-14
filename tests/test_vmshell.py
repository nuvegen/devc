"""VM: beschränktes Terminal und Update von devc (SPEC 8.14)."""

import json
import os
import shutil
import stat
import sys
import time
import zipfile
from unittest import mock

from devc import __main__ as cli
from devc import vm
from devc.terminal import OP_BINARY, encode_frame
from tests import test_terminal
from tests.test_terminal import MASK

MAIN = 'import sys\nif sys.argv[1:] == ["--version"]:\n    print("devc 9.9.9")\nelse:\n    sys.exit(3)\n'


class VmTestCase(test_terminal.TerminalSessionTest):
    """Richtet ein, was sonst vm/install.sh anlegt: Befehlsordner, bashrc, Helfer für das Update."""

    def setUp(self):
        super().setUp()
        self.shell = self.tmp / "vmshell"
        (self.shell / "bin").mkdir(parents=True)
        for tool in ("ls", "cat"):
            os.symlink(shutil.which(tool), self.shell / "bin" / tool)
        (self.shell / "bashrc").write_text(f"PATH={self.shell / 'bin'}\nPS1='vm> '\nunset -f command_not_found_handle\n")
        self.helper = self.tmp / "devc-update"
        self.helper.write_text(f"#!/bin/sh\ncp {self.tmp / 'stage' / 'devc.pyz'} {self.tmp / 'eingerichtet.pyz'}\n")
        self.helper.chmod(self.helper.stat().st_mode | stat.S_IXUSR)
        for name, value in (("SHELL_DIR", self.shell), ("UPDATE_HELPER", self.helper), ("STAGE", self.tmp / "stage" / "devc.pyz"), ("SUDO", [])):
            patcher = mock.patch.object(vm, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # Von der Oberklasse kommen nur die Helfer für WebSockets, nicht ihre Tests.
    locals().update({name: None for name in dir(test_terminal.TerminalSessionTest) if name.startswith("test_")})

    def pyz(self, name="neu.pyz", version='__version__ = "9.9.9"\n', main=MAIN):
        path = self.tmp / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("__main__.py", main)
            if version is not None:
                archive.writestr("devc/__init__.py", version)
        return path

    def vm_jobs(self, kind, count):
        for _ in range(100):
            jobs = [j for j in self.request("GET", "/api/jobs")[1]["jobs"] if j["kind"] == kind and not j.get("container")]
            if len(jobs) >= count:
                break
            time.sleep(0.05)
        return " | ".join(self.request("GET", f"/api/jobs/{j['id']}")[1]["log"] for j in jobs)


class VmTerminalTest(VmTestCase):
    def test_info(self):
        status, info = self.request("GET", "/api/vm")
        self.assertEqual((status, info["terminal"], info["update"], info["allowed"], info["commands"]), (200, True, True, True, ["cat", "ls"]))
        self.assertEqual((info["user"], info["build"]), (vm.user(), None))  # die Tests laufen aus dem Quelltext
        shutil.rmtree(self.shell)
        self.helper.unlink()
        info = self.request("GET", "/api/vm")[1]
        self.assertEqual((info["terminal"], info["update"], info["commands"]), (False, False, []))
        self.assertEqual(self.request("POST", "/api/vm/terminals", {})[1]["error"], "E_CONFLICT")
        self.assertEqual(self.request("POST", "/api/vm/update", self.pyz().read_bytes())[1]["error"], "E_CONFLICT")

    def test_restricted_shell(self):
        # Zwei Anfragen auf einer Verbindung: Der Body der ersten darf nicht stehen bleiben.
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        self.addCleanup(connection.close)
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        connection.request("POST", "/api/vm/terminals", body=json.dumps({"user": "devc"}), headers=headers)
        response = connection.getresponse()
        status, session = response.status, json.loads(response.read())
        connection.request("GET", "/api/vm", headers=headers)
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 200)
        self.assertEqual((status, session["user"], session["number"]), (201, vm.user(), 1))
        self.assertEqual([s["id"] for s in self.request("GET", "/api/vm/terminals")[1]["sessions"]], [session["id"]])
        self.assertEqual(self.request("GET", "/api/containers/ab/terminals")[1]["sessions"], [])  # getrennt von den Containern
        sock, stream, status, _, _ = self.connect(path=f"/api/vm/terminal?session={session['id']}")
        self.assertEqual(status, "HTTP/1.1 101 Switching Protocols")
        frames = self.frames(sock, stream)

        def run(command, needle):
            sock.sendall(encode_frame(OP_BINARY, command.encode() + b"\n", MASK))
            return self.read_until(frames, needle.encode())

        self.read_until(frames, b"vm> ")
        run("ls /etc/hostname", "/etc/hostname\r\n")  # ein erlaubter Befehl
        run("cd /tmp", "cd: restricted")
        run("/bin/sh", "restricted: cannot specify")
        run("python3 -c 1", "command not found")
        run("ls > datei", "restricted: cannot redirect output")
        run("PATH=/usr/bin", "readonly variable")
        run("ls /etc/hostname | cat", "/etc/hostname\r\n")  # Pipes gehen
        # Strg+C erreicht das laufende Programm, die Shell bleibt.
        sock.sendall(encode_frame(OP_BINARY, b"cat\n", MASK))
        time.sleep(0.3)
        sock.sendall(encode_frame(OP_BINARY, b"\x03", MASK))
        self.read_until(frames, b"^C")
        run("ls /etc/hostname", "/etc/hostname\r\n")
        self.assertEqual(self.request("DELETE", f"/api/vm/terminals/{session['id']}")[1], {"closed": session["id"]})
        log = self.vm_jobs("terminal", 2)
        self.assertIn(f"VM-Terminal als {vm.user()} gestartet", log)
        self.assertIn(f"VM-Terminal als {vm.user()} beendet", log)
        self.assertNotIn("hostname", log)  # keine Inhalte
        self.assertEqual(self.provider.called("exec_pty"), [])  # nichts davon läuft in einem Container

    def test_cli(self):
        with mock.patch("os.execve") as execve, mock.patch("os.chdir"):
            cli._run(["vm", "shell"])
        argv, env = execve.call_args[0][1:]
        self.assertEqual(argv[1:], ["--restricted", "--noprofile", "--rcfile", str(self.shell / "bashrc"), "-i"])
        self.assertEqual((env["PATH"], env["LESSSECURE"], env["SYSTEMD_PAGERSECURE"]), (vm.SYSTEM_PATH, "1", "1"))  # die bashrc setzt den PATH und sperrt ihn
        _, result, _ = cli._run(["vm", "info"])
        self.assertEqual(cli.FORMATTERS["vm_info"](result, False), ["devc 1.0.0 (aus dem Quelltext)", "VM-Terminal: eingerichtet – cat ls", "Update: eingerichtet"])


class VmUpdateTest(VmTestCase):
    def test_upload_is_checked_staged_and_installed(self):
        new = self.pyz()
        status, result = self.request("POST", "/api/vm/update", new.read_bytes())
        self.assertEqual((status, result["started"], result["to"]), (202, True, {"version": "9.9.9", "build": vm.digest(new)}))
        self.assertEqual(result["from"], {"version": "1.0.0", "build": None})
        self.assertEqual((self.tmp / "eingerichtet.pyz").read_bytes(), new.read_bytes())  # der Helfer hat die bereitgelegte Datei geholt
        self.assertEqual(list(self.cfg.paths.uploads.glob("*")), [])
        self.assertIn(f"Update von devc gestartet: 1.0.0 (Quelltext) → 9.9.9 ({vm.digest(new)})", self.vm_jobs("update", 1))

    def test_wrong_files_are_refused(self):
        cases = {
            "kein Zip": b"kein zip",
            "ohne devc": self.pyz("a.pyz", version=None).read_bytes(),
            "ohne Version": self.pyz("b.pyz", version="x = 1\n").read_bytes(),
            "startet nicht": self.pyz("c.pyz", main="import sys\nsys.exit(1)\n").read_bytes(),
        }
        for label, body in cases.items():
            status, error = self.request("POST", "/api/vm/update", body)
            self.assertEqual((status, error["error"]), (400, "E_USAGE"), label)
        self.assertFalse((self.tmp / "eingerichtet.pyz").exists())
        with mock.patch.object(vm, "MAX_UPDATE", 10):
            self.assertEqual(self.request("POST", "/api/vm/update", b"x" * 11)[1]["error"], "E_USAGE")

    def test_same_build_and_failing_helper(self):
        new = self.pyz()
        with mock.patch.object(vm, "BUILD", vm.digest(new)):
            status, error = self.request("POST", "/api/vm/update", new.read_bytes())
        self.assertEqual((status, error["error"]), (409, "E_CONFLICT"))
        self.helper.write_text("#!/bin/sh\necho oben >&2\necho 'sudo: ein Passwort ist notwendig' >&2\nexit 1\n")
        status, error = self.request("POST", "/api/vm/update", new.read_bytes())
        self.assertEqual((status, error["error"]), (502, "E_ENGINE"))
        self.assertIn("ein Passwort ist notwendig", error["message"])

    def test_cli(self):
        new = self.pyz()
        func, result, _ = cli._run(["vm", "update", str(new)])
        self.assertEqual(cli.FORMATTERS[func](result, False), [f"Update gestartet: devc 9.9.9 ({vm.digest(new)}). Der Dienst startet gleich neu."])
        with self.assertRaises(Exception) as caught:
            cli._run(["vm", "update", str(self.tmp / "fehlt.pyz")])
        self.assertEqual(caught.exception.code, "E_USAGE")


class VmDisabledTest(VmTestCase):
    options = {"bind": "0.0.0.0", "allow_insecure_bind": True}

    def test_same_gate_as_terminal(self):
        info = self.request("GET", "/api/vm")[1]
        self.assertEqual((info["terminal"], info["update"], info["allowed"]), (False, False, False))
        for method, path in (("GET", "terminals"), ("POST", "terminals"), ("DELETE", "terminals/abcd"), ("GET", "terminal?session=ab"), ("POST", "update")):
            self.assertEqual(self.request(method, f"/api/vm/{path}", b"x" if path == "update" else None)[1]["error"], "E_SECURITY", path)


if __name__ == "__main__":
    import unittest

    unittest.main()
