#!/usr/bin/env python3
"""Erzeugt dist/devc.pyz per zipapp: `python3 dist/devc.pyz --help` läuft ohne Repository."""

import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP = {"__pycache__"}


def build(target: Path | None = None) -> Path:
    target = target or ROOT / "dist" / "devc.pyz"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="devc-build-") as staging:
        package = Path(staging) / "devc"
        shutil.copytree(ROOT / "devc", package, ignore=lambda directory, names: [n for n in names if n in SKIP or n.endswith(".pyc")])
        # Eigenes __main__: zipapps `main=`-Variante würde den Rückgabewert und damit den Exit-Code verwerfen.
        # Lizenz und Hinweise gehören zu jeder Weitergabe (Apache-2.0, Abschnitt 4).
        for name in ("LICENSE", "NOTICE"):
            shutil.copy2(ROOT / name, Path(staging) / name)
        (Path(staging) / "__main__.py").write_text("import sys\n\nfrom devc.__main__ import main\n\nsys.exit(main())\n", encoding="utf-8")
        zipapp.create_archive(staging, target, interpreter="/usr/bin/env python3", compressed=True)
    return target


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
