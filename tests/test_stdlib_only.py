"""SPEC 14.2: kein Import außerhalb der Standardbibliothek."""

import ast
import sys
import unittest

from tests import helpers


def imported_modules(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module.split(".")[0]


class StdlibOnlyTest(unittest.TestCase):
    def test_only_stdlib_imports(self):
        allowed = set(sys.stdlib_module_names) | {"devc"}
        offenders = []
        files = sorted(helpers.PACKAGE.rglob("*.py")) + [helpers.ROOT / "build.py"]
        for file in files:
            if not file.exists():
                continue
            tree = ast.parse(file.read_text(encoding="utf-8"), str(file))
            for line, module in imported_modules(tree):
                if module not in allowed:
                    offenders.append(f"{file.relative_to(helpers.ROOT)}:{line} {module}")
        self.assertEqual(offenders, [])

    def test_detects_third_party(self):
        tree = ast.parse("import requests\nfrom yaml import safe_load\nfrom . import x\nimport os.path\n")
        self.assertEqual([m for _, m in imported_modules(tree)], ["requests", "yaml", "os"])


if __name__ == "__main__":
    unittest.main()
