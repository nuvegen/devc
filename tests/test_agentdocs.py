"""T-AgentDocs: Block neu, Block ersetzen mit Fremdtext davor/danach, fehlender Marker (SPEC 6.5)."""

import unittest

from devc import addons, agentdocs
from devc.errors import ValidationError
from tests import helpers

DATA = {
    "name": "py-tools", "platform": "lxd", "template": "pydev@1.0.0", "addons": ["python"],
    "data": {"shared": "/srv/dev/py-tools"}, "git": {"remote": "ssh://git@host/py-tools.git"},
    "docs": {"ai": "## Projekt\nQuellen unter /work/src.\n", "readme": "Hallo Nutzer.\n"},
}


class AgentDocsTest(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.catalog = addons.catalog(self.tmp / "leer")

    def entries(self, ids=("base", "python")):
        result = []
        for addon_id in ids:
            addon = self.catalog.get(addon_id)
            result.append((addon, (addon.path / "AI.md").read_text(encoding="utf-8"), addon.values()))
        return result

    def test_block_content_and_order(self):
        block = agentdocs.block(DATA, "ubuntu:24.04", self.entries(), "10.0.0.5")
        lines = block.splitlines()
        self.assertEqual((lines[0], lines[-1]), (agentdocs.BEGIN, agentdocs.END))
        self.assertEqual(lines[1], "# Dev-Container py-tools")
        for fact in ("- Plattform: lxd", "- Image: ubuntu:24.04", "- Template: pydev@1.0.0", "- Arbeitsverzeichnis: /work",
                     "- Shared Folder: /srv/dev/py-tools", "- Git-Remote: ssh://git@host/py-tools.git"):
            self.assertIn(fact, lines)
        self.assertIn("- Du arbeitest als Nutzer `dev` im Container `py-tools`, Arbeitsverzeichnis `/work`.", lines)
        self.assertIn("## Python 3.12", lines)
        order = [block.index(part) for part in ("# Dev-Container", "## Umgebung", "## Python 3.12", "## Projekt", "## Regeln")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(lines[-4:-1], ["- Keine Secrets ins Repository.", "- Secrets liegen nur unter /run/devc/secrets/.",
                                        "- Addon-Aktionen werden vom Nutzer über devc gestartet, nicht selbst nachgebaut."])
        self.assertNotIn("{", block)

    def test_optional_facts_are_left_out(self):
        block = agentdocs.block({"name": "ab", "data": {"code": "volume"}}, "ubuntu:24.04", [])
        for word in ("Template", "Shared Folder", "Git-Remote"):
            self.assertNotIn(word, block)
        self.assertIn("## Regeln", block)

    def test_new_file_is_only_the_block(self):
        block = agentdocs.block(DATA, "ubuntu:24.04", self.entries())
        self.assertEqual(agentdocs.merge(None, block), block)

    def test_replace_keeps_foreign_text(self):
        old = agentdocs.block({"name": "alt", "data": {}}, "ubuntu:22.04", [])
        new = agentdocs.block(DATA, "ubuntu:24.04", self.entries())
        existing = "# Meine Regeln\n\nvorher\n" + old + "\nnachher\n- eigene Regel\n"
        merged = agentdocs.merge(existing, new)
        self.assertEqual(merged, "# Meine Regeln\n\nvorher\n" + new + "\nnachher\n- eigene Regel\n")
        self.assertNotIn("Dev-Container alt", merged)
        self.assertEqual(agentdocs.merge(merged, new), merged)  # idempotent

    def test_broken_markers_change_nothing(self):
        block = agentdocs.block(DATA, "ubuntu:24.04", [])
        cases = [
            "nur eigener Text\n",
            agentdocs.BEGIN + "\nohne Ende\n",
            "ohne Anfang\n" + agentdocs.END + "\n",
            agentdocs.END + "\nverkehrt\n" + agentdocs.BEGIN + "\n",
            block + block,
            "",
        ]
        for existing in cases:
            with self.subTest(existing=existing[:30]):
                with self.assertRaises(ValidationError) as ctx:
                    agentdocs.merge(existing, block)
                self.assertEqual(ctx.exception.code, "E_VALIDATION")

    def test_missing_placeholder_value(self):
        addon = self.catalog.get("abap")
        with self.assertRaises(ValidationError):
            agentdocs.block(DATA, "ubuntu:24.04", [(addon, (addon.path / "AI.md").read_text(encoding="utf-8"), {})])

    def test_block_lists_the_addons_and_points_to_the_info_folder(self):
        block = agentdocs.block(DATA, "ubuntu:24.04", self.entries(), "10.0.0.5")
        python = self.catalog.get("python")
        self.assertIn("## Addons in diesem Container", block)
        self.assertIn(f"- `python` {python.version} – {python.description} → `.devc/addons/python.md`", block)
        self.assertIn("`.devc/README.md`", block)
        self.assertIn("`.devc/config.toml`", block)
        self.assertLess(block.index("## Addons in diesem Container"), block.index("## Regeln"))

    def test_addon_page(self):
        server = self.catalog.get("httpserver")
        texts = [(server.path / name).read_text(encoding="utf-8") for name in ("AI.md", "README.md")]
        page = agentdocs.addon_page(server, texts[0], texts[1], server.values({"port": 8010}), "py-tools", "10.0.0.5", True, {})
        self.assertTrue(page.startswith("# HTTP-Server (`httpserver`)\n"))
        self.assertIn(f"- Version: {server.version}", page)
        self.assertIn("- Stand: aktiv in diesem Container", page)
        self.assertIn("| `port` | `8010` |", page)
        self.assertIn("| `httpserver.start` | Server starten | `python3 -m http.server 8010 --directory /work/out` | läuft im Hintergrund |", page)
        self.assertIn("devc action run py-tools <aktion>", page)
        self.assertIn("auf Port 8010 aus", page)  # AI.md mit eingesetzten Werten
        self.assertIn("## Hinweise für die KI\n\n- Dateien in", page)  # ohne die doppelte Überschrift der AI.md
        self.assertIn("| Im Browser öffnen | Port `8010` im Container | nur solange `httpserver.start` läuft |", page)
        self.assertIn(texts[1].strip().splitlines()[-1], page)  # die Anleitung steht vollständig darin
        # Secrets: nur der Zustand und der Pfad, nie ein Wert.
        claude = self.catalog.get("claude-code")
        page = agentdocs.addon_page(claude, "", "x", claude.values(), "py-tools", None, True, {"claude-code.api_key": True})
        self.assertIn("| `api_key` | Secret, gesetzt: Datei `/run/devc/secrets/claude-code.api_key` |", page)
        self.assertIn("| `oauth_token` | Secret, nicht gesetzt |", page)
        self.assertIn("| `claude-code.chat` | Claude | Chat |", page)
        # Ein Addon, das im Container fehlt: Standardwerte und der Befehl zum Ergänzen.
        page = agentdocs.addon_page(server, texts[0], texts[1], server.values(), "py-tools", None, False, {})
        self.assertIn("- Stand: nicht in diesem Container. Der Nutzer ergänzt es mit `devc install py-tools httpserver`", page)
        self.assertIn("| Name | Standard | Bedeutung |", page)

    def test_index(self):
        python, server = self.catalog.get("python"), self.catalog.get("httpserver")
        text = agentdocs.index(DATA, "ubuntu:24.04", [python], [server], "10.0.0.5", {"git.key": True, "x.y": False})
        self.assertTrue(text.startswith("Hallo Nutzer.\n\n# Container py-tools\n"))
        self.assertIn(f"| `python` | {python.version} | {python.description} | [addons/python.md](addons/python.md) |", text)
        self.assertLess(text.index("## Aktive Addons"), text.index("## Weitere Addons"))
        self.assertIn("| `httpserver` |", text.split("## Weitere Addons")[1])
        self.assertIn("devc install py-tools <addon>", text)
        self.assertIn("| `git.key` | gesetzt | `/run/devc/secrets/git.key` |", text)
        self.assertIn("| `x.y` | nicht gesetzt | – |", text)
        self.assertIn("[config.toml](config.toml)", text)

    def test_config_copy_hides_secrets(self):
        source = ('name = "ab"\n[git]\nremote = "https://nutzer:geheim@git.example/ab.git"\nmirror = "ssh://git@host/ab.git"\n'
                  '[secrets]  # Namen im Store\n"claude-code.api_key" = "anthropic"\ngit.key = "shared:deploy"\n[docs]\nreadme = "a = b"\n')
        text = agentdocs.config_copy(source)
        self.assertTrue(text.startswith("# Kopie der Konfiguration dieses Containers"))
        for secret in ("geheim", "anthropic", "deploy"):
            self.assertNotIn(secret, text)
        self.assertIn('remote = "https://***@git.example/ab.git"', text)
        self.assertIn('mirror = "ssh://git@host/ab.git"', text)  # der Nutzer eines SSH-Remotes ist kein Geheimnis
        self.assertIn('"claude-code.api_key" = "***"', text)
        self.assertIn('git.key = "***"', text)
        self.assertIn('readme = "a = b"', text)  # außerhalb von [secrets] bleibt alles


if __name__ == "__main__":
    unittest.main()
