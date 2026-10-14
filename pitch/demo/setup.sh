#!/usr/bin/env bash
# Legt im Testcontainer ein kleines Projekt an, damit Terminal, Git und HTTP-Server etwas zu zeigen haben.
# Läuft als Nutzer dev im Container. Nur für `pitch/screenshots.py`.
set -euo pipefail

git init -q --bare -b main /home/dev/server.git
mkdir -p /work/webshop/src /work/webshop/tests /work/out
cd /work/webshop
git init -q -b main
git remote add origin /home/dev/server.git

cat > src/preise.py <<'PY'
"""Preise im Webshop."""

MWST = 0.19


def brutto(netto: float) -> float:
    """Bruttopreis; ab 100 Euro gibt es 10 Prozent Rabatt."""
    if netto < 100:
        netto = netto * 0.9
    return round(netto * (1 + MWST), 2)
PY
touch src/__init__.py tests/__init__.py
cat > tests/test_preise.py <<'PY'
import unittest

from src.preise import brutto


class PreiseTest(unittest.TestCase):
    def test_ohne_rabatt(self):
        self.assertEqual(brutto(50), 59.5)

    def test_rabatt_ab_100_euro(self):
        self.assertEqual(brutto(100), 107.1)

    def test_rabatt_bei_grossem_betrag(self):
        self.assertEqual(brutto(1000), 1071.0)

    def test_knapp_unter_der_grenze(self):
        self.assertEqual(brutto(99.99), 118.99)

    def test_null(self):
        self.assertEqual(brutto(0), 0.0)

    def test_rundet_auf_cent(self):
        self.assertEqual(brutto(33.33), 39.66)

    def test_kleiner_betrag(self):
        self.assertEqual(brutto(1), 1.19)

    def test_ergebnis_ist_float(self):
        self.assertIsInstance(brutto(10), float)
PY
printf '# Webshop\n\nKleines Beispielprojekt.\n' > README.md
printf '__pycache__/\n' > .gitignore
git add -A && git commit -q -m "Projekt angelegt"
printf '\n## Tests\n\n    python3 -m unittest -q\n' >> README.md
git commit -q -am "README: Tests beschrieben"
git push -q -u origin main
printf 'Versandkosten: 4,90 Euro\n' > versand.txt
git add versand.txt && git commit -q -m "Versandkosten notiert"

# Stand für die Bilder: eine geänderte und eine neue Datei
sed -i 's/if netto < 100:/if netto >= 100:/' src/preise.py
printf 'Rabatt ab 100 Euro geprüft.\n' > notizen.txt

cat > /work/out/index.html <<'HTML'
<!doctype html><meta charset="utf-8"><title>Webshop – Vorschau</title>
<body style="font: 18px system-ui; margin: 3em"><h1>Webshop</h1><p>Vorschau aus dem Container, ausgeliefert vom Addon httpserver.</p></body>
HTML
