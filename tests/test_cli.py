"""CLI-Gerüst: Befehle wie Tabelle SPEC 8.9, Fehlerausgabe wie SPEC 8.1."""

import json
import unittest
from unittest import mock

from devc import __main__ as cli
from devc import __version__, core, errors
from tests import helpers
from tests.helpers import run_cli

# Tabelle SPEC 8.9: Befehl -> Beispielaufruf
COMMANDS = {
    "init": ["init"],
    "doctor": ["doctor", "--cleanup"],
    "token show": ["token", "show"],
    "token rotate": ["token", "rotate"],
    "addon list": ["addon", "list"],
    "addon show": ["addon", "show", "base"],
    "addon validate": ["addon", "validate", "examples/addons/base"],
    "addon import": ["addon", "import", "a.tar.gz"],
    "addon export": ["addon", "export", "base", "-o", "base.tar.gz"],
    "addon rm": ["addon", "rm", "base"],
    "template list": ["template", "list"],
    "template show": ["template", "show", "pydev"],
    "template edit": ["template", "edit", "pydev"],
    "template validate": ["template", "validate", "pydev.toml"],
    "template save": ["template", "save", "py-tools", "mine"],
    "template rm": ["template", "rm", "mine"],
    "create": [
        "create", "py-tools", "--template", "pydev", "--edit", "--addon", "node", "--addon", "abap",
        "--param", "python.version=3.13", "--secret", "claude-code.api_key=@key", "--shared", "/srv/dev/x",
        "--git", "ssh://git@host/x.git", "--image", "ubuntu:24.04", "--cpu", "2", "--memory", "4GiB",
        "--disk", "20GiB", "--no-start", "--yes",
    ],
    "list": ["list"],
    "info": ["info", "py-tools"],
    "start": ["start", "py-tools"],
    "stop": ["stop", "py-tools"],
    "restart": ["restart", "py-tools"],
    "rm": ["rm", "py-tools", "--keep-data", "--yes"],
    "shell": ["shell", "py-tools", "--root"],
    "exec": ["exec", "py-tools", "--root", "--", "ls", "-la"],
    "install": ["install", "py-tools", "node", "abap", "--param", "node.version=22"],
    "uninstall": ["uninstall", "py-tools", "node"],
    "vm info": ["vm", "info"],
    "vm shell": ["vm", "shell"],
    "vm update": ["vm", "update", "devc.pyz"],
    "docs": ["docs", "py-tools"],
    "config show": ["config", "show", "py-tools"],
    "config edit": ["config", "edit", "py-tools"],
    "config apply": ["config", "apply", "py-tools", "--dry-run"],
    "config set": ["config", "set", "py-tools", "httpserver.port=8010"],
    "action list": ["action", "list", "py-tools"],
    "action run": ["action", "run", "py-tools", "httpserver.start"],
    "tile list": ["tile", "list", "py-tools"],
    "tile show": ["tile", "show", "py-tools", "info.status"],
    "tile panel": ["tile", "panel", "py-tools", "git.repos"],
    "secret set": ["secret", "set", "py-tools", "abap.sap_password=-"],
    "secret list": ["secret", "list", "py-tools"],
    "secret rm": ["secret", "rm", "py-tools", "abap.sap_password"],
    "snapshot create": ["snapshot", "create", "py-tools", "--label", "vor-umbau", "--full"],
    "snapshot list": ["snapshot", "list", "py-tools"],
    "snapshot restore": ["snapshot", "restore", "py-tools", "vor-umbau", "--yes"],
    "snapshot rm": ["snapshot", "rm", "py-tools", "vor-umbau"],
    "backup": ["backup", "py-tools", "-o", "/tmp", "--label", "x", "--no-home", "--no-work"],
    "restore": ["restore", "a.devc.tar", "--name", "neu", "--edit", "--yes"],
    "push": ["push", "py-tools", "datei", "ziel"],
    "pull": ["pull", "py-tools", "quelle"],
    "file rm": ["file", "rm", "py-tools", "alt.txt", "--yes"],
    "file archive": ["file", "archive", "py-tools", "a.zip"],
    "file unpack": ["file", "unpack", "py-tools", "a.zip", "--into", "neu"],
    "serve": [
        "serve", "--bind", "127.0.0.1", "--port", "8740", "--allow-insecure-bind",
        "--allow-insecure-terminal", "--tls-cert", "c.pem", "--tls-key", "k.pem", "--dev",
    ],
    "jobs": ["jobs", "--container", "py-tools", "--follow", "j-4f1a09c2b7de"],
    "proxy config": ["proxy", "config", "--http-port", "8080"],
    "help list": ["help", "list"],
    "help show": ["help", "show", "user"],
}


class CliTest(helpers.TempEnvTestCase):
    def call(self, func, *argv):
        """Ruft die CLI mit ersetztem Use-Case auf; liefert (exit, stdout, stderr, mock)."""
        use_case = mock.Mock(return_value={"ok": True})
        if func in cli.ADAPTERS:  # Befehle mit Editor oder Rückfrage laufen über einen Adapter
            patcher = mock.patch.dict(cli.ADAPTERS, {func: use_case})
        else:
            patcher = mock.patch.object(core, func, use_case)
        with patcher:
            code, out, err = run_cli(*argv)
        return code, out, err, use_case

    def test_commands_match_spec_table(self):
        cli.build_parser()
        self.assertEqual(sorted(cli.COMMANDS), sorted(COMMANDS))

    def test_every_command_has_a_use_case(self):
        cli.build_parser()
        for command, func in cli.COMMANDS.items():
            with self.subTest(command=command):
                self.assertTrue(callable(cli.ADAPTERS.get(func) or getattr(core, func, None)))

    def test_every_command_parses_and_dispatches(self):
        cli.build_parser()
        for command, argv in COMMANDS.items():
            with self.subTest(command=command):
                # --json: die Textausgabe je Befehl prüfen die Tests der Befehle selbst
                code, out, err, use_case = self.call(cli.COMMANDS[command], "--json", *argv)
                self.assertEqual((code, err, json.loads(out)), (0, "", {"ok": True}))
                use_case.assert_called_once()

    def test_help_lists_all_commands(self):
        code, out, err = run_cli("--help")
        self.assertEqual(code, 0)
        for command in {name.split()[0] for name in COMMANDS}:
            self.assertRegex(out, rf"(?m)^\s+{command}\s")
        for group in {name.split()[0] for name in COMMANDS if " " in name}:
            code, out, err = run_cli(group, "--help")
            self.assertEqual(code, 0)
            for name in (n for n in COMMANDS if n.startswith(group + " ")):
                self.assertRegex(out, rf"(?m)^\s+{name.split()[1]}\s")

    def test_version(self):
        code, out, err = run_cli("--version")
        self.assertEqual((code, out.strip()), (0, f"devc {__version__}"))

    def test_pending_command_is_internal_error(self):
        def pending(config, **kwargs):
            raise NotImplementedError

        with mock.patch.object(core, "info", pending):
            code, out, err = run_cli("info", "py-tools")
            self.assertEqual((code, out), (1, ""))
            self.assertRegex(err, r"^Fehler \[E_INTERNAL\]: ")
            code, out, err = run_cli("--json", "info", "py-tools")
        self.assertEqual((code, err), (1, ""))
        self.assertEqual(json.loads(out)["error"], "E_INTERNAL")

    def test_unexpected_exception_hides_message(self):
        use_case = mock.Mock(side_effect=RuntimeError("geheim-4711"))
        with mock.patch.object(core, "info", use_case):
            code, out, err = run_cli("info", "py-tools")
        self.assertEqual(code, 1)
        self.assertIn("E_INTERNAL", err)
        self.assertNotIn("geheim-4711", out + err)

    def test_usage_errors(self):
        cases = [
            [],
            ["gibtsnicht"],
            ["addon"],
            ["info"],
            ["create", "x", "--template", "pydev", "--from", "devc.toml"],
            ["create", "x", "--cpu", "viele"],
            ["restore", "a.devc.tar"],
            ["restore", "a.devc.tar", "--name", "a", "--replace", "b"],
            ["secret", "set", "py-tools"],
            ["secret", "set", "--shared", "a", "b"],
            ["secret", "list"],
            ["secret", "list", "--shared", "py-tools"],
            ["secret", "rm", "--shared"],
            ["exec", "py-tools"],
            ["exec", "py-tools", "--"],
            ["list", "--unbekannt"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                code, out, err = run_cli(*argv)
                self.assertEqual((code, out), (2, ""))
                self.assertRegex(err, r"^Fehler \[E_USAGE\]: ")
                code, out, err = run_cli("--json", *argv)
                self.assertEqual((code, err), (2, ""))
                self.assertEqual(json.loads(out)["error"], "E_USAGE")

    def test_error_codes_map_to_exit(self):
        cases = [
            (errors.JobFailedError("x", job="j-4f1a09c2b7de"), 1),
            (errors.EngineError("lxc fehlt"), 3),
            (errors.NotFoundError("Container fehlt: py-tools"), 4),
            (errors.ConflictError("Container ist beschäftigt"), 5),
            (errors.MissingSecretError(["abap.sap_password"]), 5),
            (errors.SecurityError("Pfad außerhalb der Allowlist"), 6),
        ]
        for error, exit_code in cases:
            with self.subTest(code=error.code):
                with mock.patch.object(core, "info", mock.Mock(side_effect=error)):
                    code, out, err = run_cli("info", "py-tools")
                    self.assertEqual((code, out), (exit_code, ""))
                    self.assertEqual(err, f"Fehler [{error.code}]: {error.message}\n")
                    code, out, err = run_cli("info", "py-tools", "--json")
                self.assertEqual((code, err), (exit_code, ""))
                self.assertEqual(json.loads(out), error.to_dict())

    def test_validation_prints_one_line_per_finding(self):
        error = errors.ValidationError(
            [errors.Finding("L5", "addons", "Addon abap benötigt node"), errors.Finding("L2", "name", "Containername ungültig")]
        )
        with mock.patch.object(core, "info", mock.Mock(side_effect=error)):
            code, out, err = run_cli("info", "py-tools")
        self.assertEqual((code, out), (5, ""))
        self.assertEqual(err, "L5 addons: Addon abap benötigt node\nL2 name: Containername ungültig\n")

    def test_invalid_config_is_validation_error(self):
        self.write_config('platform = "docker"\n')
        code, out, err = run_cli("list")
        self.assertEqual(code, 5)
        self.assertTrue(err.startswith("L1 platform: "))

    def test_json_result_and_global_option_positions(self):
        for argv in (["--json", "list"], ["list", "--json"]):
            with self.subTest(argv=argv):
                code, out, err, use_case = self.call("list_containers", *argv)
                self.assertEqual((code, json.loads(out)), (0, {"ok": True}))
        other = self.tmp / "c.toml"
        other.write_text('[lxd]\nproject = "p2"\n', encoding="utf-8")
        for argv in (["--json", "--config", str(other), "list"], ["list", "--config", str(other), "--json"]):
            with self.subTest(argv=argv):
                code, out, err, use_case = self.call("list_containers", *argv)
                self.assertEqual(use_case.call_args.args[0].lxd["project"], "p2")

    def test_arguments_reach_use_case(self):
        code, out, err, use_case = self.call("create", "--json", *COMMANDS["create"])
        kwargs = use_case.call_args.kwargs
        self.assertEqual(kwargs["name"], "py-tools")
        self.assertEqual(kwargs["addons"], ["node", "abap"])
        self.assertEqual(kwargs["params"], ["python.version=3.13"])
        self.assertEqual(kwargs["secrets"], ["claude-code.api_key=@key"])
        self.assertEqual((kwargs["cpu"], kwargs["no_start"], kwargs["yes"], kwargs["from_file"]), (2, True, True, None))
        self.assertNotIn("json", kwargs)
        self.assertNotIn("func", kwargs)

        code, out, err, use_case = self.call("exec_command", "exec", "py-tools", "--", "ls", "--json", "-v")
        self.assertEqual(use_case.call_args.kwargs, {"name": "py-tools", "root": False, "argv": ["ls", "--json", "-v"]})
        self.assertEqual(out, "")

        for argv in (["exec", "py-tools", "--root", "--", "id", "--root"], ["exec", "--root", "py-tools", "--", "id", "--root"]):
            code, out, err, use_case = self.call("exec_command", *argv)
            self.assertEqual(use_case.call_args.kwargs, {"name": "py-tools", "root": True, "argv": ["id", "--root"]})
        self.assertEqual(run_cli("exec", "py-tools", "--falsch", "--", "id")[0], 2)

        code, out, err, use_case = self.call("secret_set", "--json", "secret", "set", "--shared", "anthropic=@key")
        self.assertEqual(use_case.call_args.kwargs, {"shared": True, "name": None, "spec": "anthropic=@key"})
        code, out, err, use_case = self.call("secret_set", "--json", "secret", "set", "py-tools", "abap.sap_password")
        self.assertEqual(use_case.call_args.kwargs, {"shared": False, "name": "py-tools", "spec": "abap.sap_password"})
        code, out, err, use_case = self.call("secret_list", "secret", "list", "--shared", "--json")
        self.assertEqual(use_case.call_args.kwargs, {"shared": True, "name": None})


if __name__ == "__main__":
    unittest.main()
