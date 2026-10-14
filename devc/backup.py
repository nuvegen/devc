"""Backup-Archiv (SPEC 12.1) und tar-Hilfen für Shared Folders auf dem Host."""

import hashlib
import io
import os
import stat
import tarfile
import tomllib
from pathlib import Path

from . import __version__, names, tomlw
from .errors import ValidationError

AREAS = {"work": "/work", "home": "/home/dev"}
MANIFEST = "manifest.toml"
INSTANCE = "devc.toml"
SUMS = "SHA256SUMS"
DATA = {"work": "data/work.tar.gz", "home": "data/home.tar.gz"}
ALLOWED = {MANIFEST, INSTANCE, SUMS, *DATA.values()}
MAX_TEXT = 4 * 1024 * 1024


def _bad(message: str) -> ValidationError:
    return ValidationError.single("L16", "backup", message)


def file_name(name: str, stamp: str, label: str | None = None) -> str:
    """`<name>-<YYYYMMDD-HHMMSS>[-<label>].devc.tar` (SPEC 5.4)."""
    return f"{name}-{stamp}" + (f"-{label}" if label else "") + ".devc.tar"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_text(name: str, template: str, includes: list[str], created_at: str) -> str:
    return tomlw.dumps({
        "devc_version": __version__,
        "created_at": created_at,
        "platform": "lxd",
        "name": name,
        "template": template,
        "includes": includes,
    })


def build(target: Path, staging: Path, manifest: str, instance_text: str, includes: list[str]) -> None:
    """Baut das unkomprimierte tar aus den Dateien in `staging` (data/<area>.tar.gz liegen schon dort)."""
    (staging / MANIFEST).write_text(manifest, encoding="utf-8")
    (staging / INSTANCE).write_text(instance_text, encoding="utf-8")
    entries = [MANIFEST, INSTANCE] + [DATA[area] for area in includes]
    sums = "".join(f"{sha256(staging / entry)}  {entry}\n" for entry in entries)
    (staging / SUMS).write_text(sums, encoding="utf-8")
    tmp = target.with_name(target.name + ".tmp")
    with tarfile.open(tmp, "w") as archive:
        for entry in entries + [SUMS]:
            info = archive.gettarinfo(str(staging / entry), entry)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with open(staging / entry, "rb") as handle:
                archive.addfile(info, handle)
    os.replace(tmp, target)


def inspect(path: Path) -> dict:
    """Prüft Aufbau und SHA256SUMS eines Backups. Liefert manifest, devc_toml, includes, checks."""
    try:
        return _inspect(path)
    except (tarfile.TarError, EOFError, OSError, ValueError, KeyError):
        # beschädigtes oder manipuliertes Archiv: nie als unerwarteter Fehler enden
        raise _bad("Datei ist kein unbeschädigtes devc-Backup") from None


def _inspect(path: Path) -> dict:
    archive = tarfile.open(path, "r:")
    with archive:
        members = {}
        for info in archive.getmembers():
            if info.name not in ALLOWED or not info.isreg():
                raise _bad(f"Unerwarteter Eintrag im Backup: {info.name}")
            if info.name in members:
                raise _bad(f"Doppelter Eintrag im Backup: {info.name}")
            members[info.name] = info
        for required in (MANIFEST, INSTANCE, SUMS):
            if required not in members:
                raise _bad(f"Eintrag fehlt im Backup: {required}")
        if members[MANIFEST].size > MAX_TEXT or members[INSTANCE].size > MAX_TEXT or members[SUMS].size > MAX_TEXT:
            raise _bad("Textdatei im Backup ist unplausibel groß")
        expected = {}
        for line in archive.extractfile(members[SUMS]).read().decode("utf-8", "replace").splitlines():
            digest, sep, entry = line.partition("  ")
            if not sep or len(digest) != 64:
                raise _bad("SHA256SUMS hat ein ungültiges Format")
            expected[entry] = digest
        others = set(members) - {SUMS}
        if set(expected) != others:
            raise _bad("SHA256SUMS passt nicht zu den Einträgen des Backups")
        for entry in sorted(others):
            digest = hashlib.sha256()
            source = archive.extractfile(members[entry])
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != expected[entry]:
                raise _bad(f"Prüfsumme stimmt nicht: {entry}")
        try:
            manifest = tomllib.loads(archive.extractfile(members[MANIFEST]).read().decode("utf-8"))
            instance_text = archive.extractfile(members[INSTANCE]).read().decode("utf-8")
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            raise _bad("manifest.toml oder devc.toml ist nicht lesbar") from None
    includes = manifest.get("includes")
    if not isinstance(includes, list) or any(area not in DATA for area in includes):
        raise _bad("manifest.toml: includes ist ungültig")
    if {DATA[area] for area in includes} != others - {MANIFEST, INSTANCE}:
        raise _bad("manifest.toml: includes passt nicht zu den Datenbereichen im Backup")
    if not names.is_container(manifest.get("name")):
        raise _bad("manifest.toml: name ist ungültig")
    return {
        "manifest": manifest,
        "devc_toml": instance_text,
        "includes": includes,
        "checks": [{"check": "SHA256SUMS", "ok": True, "entries": len(others)}],
    }


def area_size(path: Path, area: str) -> int:
    """Größe von data/<area>.tar.gz im Backup."""
    with tarfile.open(path, "r:") as archive:
        return archive.getmember(DATA[area]).size


def extract_area(path: Path, area: str, target, on_progress=None) -> None:
    """Schreibt data/<area>.tar.gz aus dem Backup in das Dateiobjekt `target`.

    `on_progress(bytes_bisher)` meldet den Stand.
    """
    done = 0
    with tarfile.open(path, "r:") as archive:
        source = archive.extractfile(DATA[area])
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            target.write(chunk)
            done += len(chunk)
            if on_progress is not None:
                on_progress(done)


# --- Shared Folder auf dem Host --------------------------------------------


def tar_size(directory: Path) -> int:
    """Geschätzte Größe des unkomprimierten tar eines Verzeichnisses: je Eintrag ein Kopf, Dateien auf 512 Bytes gerundet."""
    total = 0
    for root, dirs, files in os.walk(directory):
        total += 512 * (len(dirs) + len(files))
        for name in files:
            try:
                info = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += (info.st_size + 511) // 512 * 512
    return total + 10240


def pack_directory(directory: Path, target: Path, on_progress=None) -> None:
    """tar.gz des Inhalts eines Host-Verzeichnisses (Shared Folder).

    `on_progress(bytes_bisher, bytes_geschätzt)` meldet den Stand im unkomprimierten tar.
    """
    total = tar_size(directory) if on_progress is not None else 0

    def report(info):
        if on_progress is not None:
            on_progress(archive.offset, total)
        return info

    with tarfile.open(target, "w:gz") as archive:
        for entry in sorted(directory.iterdir()):
            archive.add(str(entry), entry.name, filter=report)


def clear_directory(directory: Path) -> None:
    """Leert ein Host-Verzeichnis, ohne Symlinks zu folgen. Nur nach Bestätigung aufrufen (SPEC 11.5)."""
    for root, dirs, files in os.walk(directory, topdown=False):
        for name in files:
            os.unlink(os.path.join(root, name))
        for name in dirs:
            full = os.path.join(root, name)
            if os.path.islink(full):
                os.unlink(full)
            else:
                os.rmdir(full)


def unpack_directory(source: Path, directory: Path) -> None:
    """Entpackt ein tar.gz in ein Host-Verzeichnis, mit Pfadprüfungen wie L16.

    Symlinks sind als Inhalt eines Projekts erlaubt, dürfen aber nicht als Weg nach außen dienen.
    """
    root = directory.resolve()
    with tarfile.open(source, "r:gz") as archive:
        for info in archive:
            parts = [part for part in info.name.split("/") if part not in ("", ".")]
            if info.name.startswith("/") or ".." in parts or not parts:
                raise _bad(f"Unzulässiger Pfad im Snapshot: {info.name}")
            target = root.joinpath(*parts)
            if not target.parent.resolve().is_relative_to(root):
                raise _bad(f"Pfad verlässt den Shared Folder: {info.name}")
            if info.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, info.mode & 0o777 or 0o755)
            elif info.isreg():
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(info) as data, open(target, "wb") as handle:
                    for chunk in iter(lambda: data.read(1024 * 1024), b""):
                        handle.write(chunk)
                os.chmod(target, info.mode & 0o777)
                os.utime(target, (info.mtime, info.mtime))
            elif info.issym():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(info.linkname, target)
            else:
                raise _bad(f"Nur Dateien, Verzeichnisse und Symlinks sind erlaubt: {info.name}")


def set_name(text: str, name: str) -> str:
    """Setzt `name = "…"` im Kopf einer devc.toml (für Restore unter neuem Namen)."""
    import re

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^\s*\[", line):
            break
        if re.match(r"^\s*name\s*=", line):
            lines[index] = f'name = "{name}"'
            return "\n".join(lines) + "\n"
    return f'name = "{name}"\n' + text
