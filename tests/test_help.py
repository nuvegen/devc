"""Hilfe in der App (SPEC 8.12): drei Dokumente über CLI, API und GUI."""

import unittest

from devc import core
from devc.errors import NotFoundError
from tests.helpers import PACKAGE, ROOT, run_cli
from tests.test_lifecycle import LifecycleTestCase
from tests.test_server import ServerTestCase

SOURCES = {"user": "HILFE.md", "technical": "ANLEITUNG.md", "ai": "KI.md"}


class HelpTest(LifecycleTestCase):
    def test_list(self):
        topics = core.help_list(self.cfg)["topics"]
        self.assertEqual([t["id"] for t in topics], ["user", "technical", "ai"])
        for topic in topics:
            self.assertTrue(topic["title"] and topic["summary"], topic)

    def test_show(self):
        for topic_id in SOURCES:
            topic = core.help_show(self.cfg, topic_id)
            self.assertEqual(topic["id"], topic_id)
            self.assertTrue(topic["text"].startswith("# "), topic_id)
            self.assertGreater(len(topic["text"]), 1500, topic_id)

    def test_unknown_topic(self):
        for topic_id in ("gibts-nicht", "../config", "", "user.md"):
            with self.assertRaises(NotFoundError, msg=topic_id):
                core.help_show(self.cfg, topic_id)

    def test_package_copies_match_docs(self):
        """Wie builtin/ und examples/: gepflegt wird unter docs/, das Paket trägt eine Kopie."""
        for topic_id, name in SOURCES.items():
            self.assertEqual((PACKAGE / "help" / f"{topic_id}.md").read_bytes(), (ROOT / "docs" / name).read_bytes(), name)

    def test_cli(self):
        code, out, err = run_cli("help", "list")
        self.assertEqual((code, err), (0, ""))
        for topic_id in SOURCES:
            self.assertRegex(out, rf"(?m)^{topic_id}\s")
        code, out, err = run_cli("help", "show", "ai")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# "))
        self.assertEqual(run_cli("help", "show", "nix")[0], 4)

    def test_documents_name_no_internal_build_files(self):
        """Nutzer haben build/ nicht; die Hilfe darf nicht dorthin verweisen."""
        for topic_id in ("user", "ai"):
            self.assertNotIn("build/", core.help_show(self.cfg, topic_id)["text"], topic_id)


class HelpApiTest(ServerTestCase):
    def test_endpoints(self):
        status, data = self.request("GET", "/api/help")
        self.assertEqual((status, [t["id"] for t in data["topics"]]), (200, ["user", "technical", "ai"]))
        status, data = self.request("GET", "/api/help/user")
        self.assertEqual((status, data["id"]), (200, "user"))
        self.assertIn("# ", data["text"])
        self.assertEqual(self.request("GET", "/api/help/nix")[0], 404)
        self.assertEqual(self.request("GET", "/api/help", auth=False)[0], 401)


del LifecycleTestCase, ServerTestCase


if __name__ == "__main__":
    unittest.main()
