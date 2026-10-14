"""T-TOML: Rundlauf des TOML-Writers (SPEC 7.1)."""

import datetime
import tomllib
import unittest

from devc import tomlw
from tests import helpers


class TomlwTest(unittest.TestCase):
    def roundtrip(self, data):
        text = tomlw.dumps(data)
        self.assertEqual(tomllib.loads(text), data, text)
        return text

    def test_scalars(self):
        self.roundtrip({"s": "text", "i": 42, "neg": -7, "zero": 0, "t": True, "f": False, "empty": ""})

    def test_big_int(self):
        self.roundtrip({"big": 2**63 - 1, "small": -(2**63)})

    def test_string_escapes(self):
        self.roundtrip(
            {
                "quote": 'a "b" c',
                "backslash": "C:\\pfad\\datei",
                "tab": "a\tb",
                "control": "a\x00b\x1fc\x7fd\bx\fy",
                "cr": "a\rb",
                "unicode": "Größe – »ß« ✓ 🎉",
                "hash": "kein # Kommentar",
                "single": "it's",
            }
        )

    def test_multiline_strings(self):
        text = self.roundtrip(
            {
                "a": "zeile 1\nzeile 2\n",
                "b": "\nbeginnt mit Umbruch",
                "c": 'drei """ Anführungszeichen\nund \\ Backslash',
                "d": "endet mit Anführungszeichen\"\n\"",
                "e": "\n",
                "f": "crlf\r\nzeile",
                "g": "leerraum am ende \\\n  weiter",
            }
        )
        self.assertIn('a = """\nzeile 1\nzeile 2\n"""', text)

    def test_lists_of_scalars(self):
        self.roundtrip({"strs": ["a", "b"], "ints": [1, 2, 3], "bools": [True, False], "empty": [], "mixed": ["a", 1, True]})

    def test_nested_tables(self):
        self.roundtrip({"a": {"b": {"c": 1}, "x": "y"}, "empty": {}, "deep": {"only": {"sub": {}}}})

    def test_table_arrays(self):
        self.roundtrip(
            {
                "name": "x",
                "files": [{"dest": "/work/a", "content": "1\n2\n"}, {"dest": "/work/b", "overwrite": False}, {}],
                "outer": {"inner": [{"k": 1, "sub": {"z": True}, "more": [{"q": "r"}]}]},
            }
        )

    def test_scalars_before_tables(self):
        # Ein Skalar nach einer Tabelle im dict darf nicht in der Tabelle landen.
        self.roundtrip({"table": {"a": 1}, "list": [{"b": 2}], "late": "wert"})

    def test_quoted_keys(self):
        text = self.roundtrip(
            {
                "secrets": {"claude-code.api_key": "anthropic", "git.key": "git-deploy"},
                "mit leer": 1,
                "": 2,
                'q"uote': 3,
                "ümlaut": 4,
                "params": {"a.b": {"c d": [{"e.f": 1}]}},
            }
        )
        self.assertIn('"claude-code.api_key" = "anthropic"', text)

    def test_empty(self):
        self.assertEqual(tomlw.dumps({}), "")

    def test_unsupported_values(self):
        bad = [
            {"f": 1.5},
            {"d": datetime.date(2026, 10, 10)},
            {"dt": datetime.datetime(2026, 10, 10, 9, 0)},
            {"t": datetime.time(9, 0)},
            {"n": None},
            {"l": [1.5]},
            {"nested": [[1, 2]]},
            {"mixed": [{"a": 1}, 2]},
            {"t": {"deep": {"f": 0.1}}},
            {"tuple": (1, 2)},
            {"bytes": b"x"},
            {1: "int-key"},
        ]
        for data in bad:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    tomlw.dumps(data)

    def test_root_must_be_dict(self):
        with self.assertRaises(ValueError):
            tomlw.dumps([1])

    def test_examples_roundtrip(self):
        files = sorted(helpers.EXAMPLES.rglob("*.toml"))
        self.assertTrue(files)
        for file in files:
            with self.subTest(file=file.name):
                self.roundtrip(tomllib.loads(file.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
