"""SPEC 14.2: kein shell=True, kein os.system, kein subprocess mit Befehls-String."""

import ast
import unittest

from tests import helpers

SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}
FORBIDDEN = {
    ("os", "system"),
    ("os", "popen"),
    ("subprocess", "getoutput"),
    ("subprocess", "getstatusoutput"),
}


def _is_string_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp):
        return _is_string_expr(node.left) or _is_string_expr(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr in ("format", "join") and _is_string_expr(node.func.value)
    return False


def violations(source: str, filename: str = "<src>") -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source, filename)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None)
            for alias in node.names:
                if (module, alias.name) in FORBIDDEN:
                    found.append(f"{filename}:{node.lineno} import {module}.{alias.name}")
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "shell" and not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            ):
                found.append(f"{filename}:{node.lineno} shell=")
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if (func.value.id, func.attr) in FORBIDDEN:
                found.append(f"{filename}:{node.lineno} {func.value.id}.{func.attr}")
            if func.value.id == "subprocess" and func.attr in SUBPROCESS_CALLS:
                first = node.args[0] if node.args else None
                for keyword in node.keywords:
                    if keyword.arg == "args":
                        first = keyword.value
                if first is not None and _is_string_expr(first):
                    found.append(f"{filename}:{node.lineno} subprocess.{func.attr} mit String")
    return found


class NoShellTest(unittest.TestCase):
    def test_package_is_clean(self):
        found = []
        for file in sorted(helpers.PACKAGE.rglob("*.py")):
            found += violations(file.read_text(encoding="utf-8"), str(file.relative_to(helpers.ROOT)))
        self.assertEqual(found, [])

    def test_detects_violations(self):
        bad = [
            "import subprocess\nsubprocess.run(['ls'], shell=True)",
            "import subprocess\nsubprocess.run('ls -l')",
            "import subprocess\nsubprocess.Popen(f'ls {x}')",
            "import subprocess\nsubprocess.check_output('ls ' + x)",
            "import subprocess\nsubprocess.run(args=' '.join(a))",
            "import subprocess\nsubprocess.run(cmd, shell=flag)",
            "import os\nos.system('ls')",
            "from os import system",
            "import subprocess\nsubprocess.getoutput('ls')",
        ]
        for source in bad:
            with self.subTest(source=source):
                self.assertTrue(violations(source))

    def test_accepts_argument_lists(self):
        good = "import subprocess\nsubprocess.run(['lxc', 'list'], shell=False)\nsubprocess.run(argv)"
        self.assertEqual(violations(good), [])


if __name__ == "__main__":
    unittest.main()
