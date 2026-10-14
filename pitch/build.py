#!/usr/bin/env python3
"""Baut die Präsentation als eine einzige Datei und als Archiv. Nur Standardbibliothek.

    python3 pitch/build.py

Ergebnis:
    dist/devc-pitch.html   alles in einer Datei, die Bilder sind eingebettet
    dist/devc-pitch.zip    diese Datei und die Quellen (index.html, img/, README.md)
"""
import base64
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE.parent / "dist"
IMAGE = re.compile(r'src="(img/[A-Za-z0-9._-]+\.png)"')


def single_page() -> str:
    text = (HERE / "index.html").read_text(encoding="utf-8")

    def embed(match: re.Match) -> str:
        data = (HERE / match.group(1)).read_bytes()
        return 'src="data:image/png;base64,' + base64.b64encode(data).decode("ascii") + '"'

    return IMAGE.sub(embed, text)


def main() -> int:
    DIST.mkdir(exist_ok=True)
    page = DIST / "devc-pitch.html"
    page.write_text(single_page(), encoding="utf-8")
    archive = DIST / "devc-pitch.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(page, "devc-pitch/devc-pitch.html")
        for file in [HERE / "index.html", HERE / "README.md", *sorted((HERE / "img").glob("*.png"))]:
            bundle.write(file, "devc-pitch/quellen/" + str(file.relative_to(HERE)))
    for file in (page, archive):
        print(f"{file.relative_to(HERE.parent)}  {file.stat().st_size // 1024} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
