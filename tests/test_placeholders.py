"""T-Placeholder: Ersetzung, Escapes, shlex.quote, URL-Quote, unbekannter Name (SPEC 7.3)."""

import shlex
import unittest

from devc import placeholders
from devc.placeholders import PlaceholderError, names_in, parse, render


class PlaceholderTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(
            parse("a {port} b {{x}} {dir}"),
            [("text", "a "), ("name", "port"), ("text", " b "), ("text", "{"), ("text", "x"), ("text", "}"), ("text", " "), ("name", "dir")],
        )
        self.assertEqual(parse(""), [])
        self.assertEqual(names_in("{a} {b} {a}"), ["a", "b"])

    def test_escapes(self):
        self.assertEqual(render("awk '{{print $1}}' {f}", {"f": "x"}), "awk '{print $1}' x")
        self.assertEqual(render("{{{f}}}", {"f": "x"}), "{x}")

    def test_syntax_errors(self):
        for bad in ["{", "}", "a { b", "{Port}", "{1a}", "{a-b}", "{}", "{a b}", "{" + "a" * 33 + "}", "x}y", "{a}}"]:
            with self.subTest(bad=bad):
                with self.assertRaises(PlaceholderError):
                    parse(bad)

    def test_run_quotes_every_value(self):
        values = {"port": 8000, "dir": "/work/my out; rm -rf /", "flag": True, "off": False, "empty": ""}
        text = render("serve {port} --directory {dir} {flag} {off} {empty}", values, "run")
        self.assertEqual(shlex.split(text), ["serve", "8000", "--directory", "/work/my out; rm -rf /", "true", "false", ""])
        self.assertEqual(render("echo {v}", {"v": "$(id) `id` 'x'"}, "run"), "echo " + shlex.quote("$(id) `id` 'x'"))

    def test_url_quotes_except_container_ip(self):
        values = {"container_ip": "10.0.0.5", "port": 8000, "path": "a b/c?d=e&f"}
        self.assertEqual(
            render("http://{container_ip}:{port}/{path}", values, "url"),
            "http://10.0.0.5:8000/a%20b%2Fc%3Fd%3De%26f",
        )

    def test_plain_is_unchanged(self):
        self.assertEqual(render("Host `{h}` ok={b}", {"h": "a b&c", "b": True}), "Host `a b&c` ok=true")

    def test_missing_value(self):
        for values in ({}, {"x": None}):
            with self.assertRaises(PlaceholderError):
                render("{x}", values)

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            render("x", {}, "html")

    def test_system_names(self):
        self.assertEqual(placeholders.SYSTEM_NAMES, ("container", "container_ip", "workdir", "user"))


if __name__ == "__main__":
    unittest.main()
