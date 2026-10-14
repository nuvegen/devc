"""T-L2: Namensmuster aus SPEC 5.4."""

import unittest

from devc import names


class NamesTest(unittest.TestCase):
    def check_all(self, test, good, bad):
        for value in good:
            with self.subTest(good=value):
                self.assertTrue(test(value))
        for value in bad:
            with self.subTest(bad=value):
                self.assertFalse(test(value))

    def test_container(self):
        self.check_all(
            names.is_container,
            ["py-tools", "ab", "a1", "it-py", "a" + "b" * 30],
            ["a", "", "Py", "1ab", "ab-", "a_b", "a b", "ab\n", "a" + "b" * 31, "-ab", "a.b", None, 5],
        )

    def test_id(self):
        self.check_all(names.is_id, ["claude-code", "base", "ab"], ["a", "Base", "a_b", "x" * 32, "9a", ""])

    def test_param(self):
        self.check_all(
            names.is_param,
            ["sap_client", "a", "port", "a" * 32],
            ["", "Port", "a-b", "1a", "_a", "a" * 33, "a.b"],
        )

    def test_store(self):
        self.check_all(
            names.is_store,
            ["git-deploy", "a", "s4_dev", "a" * 64],
            ["", "_shared", "A", "a" * 65, "a/b", "..", "a.b"],
        )

    def test_slot(self):
        self.check_all(
            names.is_slot,
            ["abap.sap_password", "git.key", "claude-code.api_key"],
            ["abap", "abap.", ".x", "abap.sap-password", "a.b.c", "Abap.x", "ab." + "x" * 62, "../x.y", None],
        )
        self.assertEqual(names.split_slot("abap.sap_password"), ("abap", "sap_password"))
        self.assertEqual(names.split_slot("git.key"), ("git", "key"))

    def test_action(self):
        self.assertEqual(names.split_action("httpserver.start"), ("httpserver", "start"))
        for bad in ["httpserver", "httpserver.", "a.b.c", ".start", 3]:
            with self.subTest(bad=bad):
                self.assertIsNone(names.split_action(bad))

    def test_snapshot(self):
        self.check_all(
            names.is_snapshot,
            ["vor-refactoring", "auto-20261010-091200", "1", "a" * 63],
            ["", "-a", "A", "a_b", "a" * 64, "a/b"],
        )
        self.assertTrue(names.is_auto_snapshot("auto-20261010-091200"))
        self.assertFalse(names.is_auto_snapshot("auto-2026-10-10"))

    def test_job_version_size(self):
        self.check_all(names.is_job, ["j-4f1a09c2b7de"], ["j-4f1a09c2b7d", "j-4F1A09C2B7DE", "4f1a09c2b7de", "j-4f1a09c2b7dex"])
        self.check_all(names.is_version, ["1.0.0", "10.20.30"], ["1.0", "1.0.0-rc1", "v1.0.0", "1.0.0\n", ""])
        self.check_all(names.is_size, ["8GiB", "512MiB", "1TiB"], ["8GB", "8 GiB", "GiB", "8gib", "1.5GiB", 8, ""])

    def test_backup_file(self):
        self.check_all(
            names.is_backup_file,
            ["py-tools-20261009-030000.devc.tar", "py-tools-20261009-030000-vor-umbau.devc.tar"],
            ["py-tools.devc.tar", "py-tools-20261009-030000.tar", "../py-tools-20261009-030000.devc.tar", "x/y-20261009-030000.devc.tar"],
        )

    def test_volume_name(self):
        self.assertEqual(names.volume_name("py-tools", "home"), "devc-py-tools-home")

    def test_check_reports_l2(self):
        self.assertIsNone(names.check("container", "py-tools"))
        finding = names.check("container", "Py_Tools", "name")
        self.assertEqual((finding.check, finding.path, finding.severity), ("L2", "name", "error"))
        self.assertIn("Py_Tools", finding.message)
        self.assertEqual(names.check("store", 7).check, "L2")


if __name__ == "__main__":
    unittest.main()
