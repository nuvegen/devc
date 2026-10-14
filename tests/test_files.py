"""Dateien unter /work: löschen, Archiv ansehen und entpacken (SPEC 8.10, L13)."""

import io
import tarfile
import zipfile

from devc import core
from devc.errors import NotFoundError, SecurityError, UsageError
from tests import fakes
from tests.helpers import run_cli
from tests.test_backup import BackupTestCase


def tar_bytes(members, mode="w:gz") -> bytes:
    """members: Liste aus (name, inhalt | None für Ordner | ("link", ziel))."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for name, content in members:
            info = tarfile.TarInfo(name)
            if content is None:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
            elif isinstance(content, tuple):
                info.type, info.linkname = (tarfile.SYMTYPE if content[0] == "link" else tarfile.LNKTYPE), content[1]
                archive.addfile(info)
            else:
                info.size, info.mode = len(content), 0o640
                archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def zip_bytes(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class FileOpsTest(BackupTestCase):
    def put(self, path: str, data: bytes) -> None:
        self.provider.blobs.setdefault("py-tools", {})[path] = data

    def unpacked(self) -> dict:
        """Was beim Container ankam: Zielordner und der Inhalt des tar-Stroms."""
        target, stream = self.provider.unpacked["py-tools"][-1]
        with tarfile.open(fileobj=io.BytesIO(stream)) as archive:
            return {"into": target, "members": {m.name: (m.type, m.linkname if m.issym() else (archive.extractfile(m).read() if m.isfile() else None)) for m in archive}}

    def test_rm(self):
        result = core.file_rm(self.cfg, "py-tools", "src/alt")
        self.assertEqual(result["path"], "/work/src/alt")
        call = self.provider.called("exec")[-1]
        self.assertEqual((call[1][1][-1], call[2]["user"]), ("/work/src/alt", "dev"))
        self.provider.exec_results.insert(0, (["sh", "-c", core._RM_FILE], fakes.ExecResult(44)))
        with self.assertRaises(NotFoundError):
            core.file_rm(self.cfg, "py-tools", "fehlt")

    def test_rm_refuses_work_and_outside(self):
        before = len(self.provider.called("exec"))
        for path, error in (("/work", UsageError), ("", UsageError), ("/etc/passwd", SecurityError), ("../x", SecurityError)):
            with self.subTest(path=path), self.assertRaises(error):
                core.file_rm(self.cfg, "py-tools", path)
        self.assertFalse([c for c in self.provider.called("exec")[before:] if c[1][1][:2] == ["sh", "-c"]])

    def test_archive_list_tar(self):
        self.put("/work/pkg.tar.gz", tar_bytes([("pkg", None), ("pkg/a.txt", b"A"), ("pkg/sub/b.txt", b"BB")]))
        result = core.archive_list(self.cfg, "py-tools", "pkg.tar.gz")
        self.assertEqual((result["path"], result["format"], result["count"], result["size"], result["top"], result["skipped"]),
                         ("/work/pkg.tar.gz", "tar", 3, 3, ["pkg"], []))
        self.assertEqual([(e["name"], e["type"], e["size"]) for e in result["entries"]],
                         [("pkg", "dir", 0), ("pkg/a.txt", "file", 1), ("pkg/sub/b.txt", "file", 2)])

    def test_archive_list_zip_with_many_roots(self):
        self.put("/work/x.zip", zip_bytes({"a.txt": b"A", "docs/b.md": b"B"}))
        result = core.archive_list(self.cfg, "py-tools", "/work/x.zip")
        self.assertEqual((result["format"], result["top"], result["count"]), ("zip", ["a.txt", "docs"], 2))

    def test_archive_skips_unsafe_members(self):
        self.put("/work/b.tar", tar_bytes([("ok.txt", b"1"), ("../raus.txt", b"2"), ("/abs.txt", b"3"), ("l-ok", ("link", "ok.txt")),
                                           ("l-raus", ("link", "../../etc/passwd")), ("l-abs", ("link", "/etc/passwd")), ("hart", ("hard", "ok.txt"))], mode="w"))
        result = core.archive_list(self.cfg, "py-tools", "b.tar")
        self.assertEqual([e["name"] for e in result["entries"]], ["ok.txt", "l-ok"])
        self.assertEqual(sorted(result["skipped"]), ["../raus.txt", "/abs.txt", "hart", "l-abs", "l-raus"])

    def test_unpack_here_and_into_folder(self):
        self.put("/work/in/pkg.tar.gz", tar_bytes([("./pkg/a.txt", b"A"), ("pkg/run.sh", b"#!/bin/sh\n"), ("../raus", b"x")]))
        result = core.archive_unpack(self.cfg, "py-tools", "in/pkg.tar.gz")
        self.assertEqual((result["into"], result["files"], result["skipped"]), ("/work/in", 2, ["../raus"]))
        got = self.unpacked()
        self.assertEqual((got["into"], got["members"]["pkg/a.txt"][1], sorted(got["members"])), ("/work/in", b"A", ["pkg/a.txt", "pkg/run.sh"]))
        self.assertEqual(core.archive_unpack(self.cfg, "py-tools", "in/pkg.tar.gz", into="in/neu")["into"], "/work/in/neu")
        self.assertEqual(self.unpacked()["into"], "/work/in/neu")
        self.assertEqual(self.provider.called("exec")[-1][2]["user"], "dev")

    def test_unpack_zip_keeps_content(self):
        self.put("/work/x.zip", zip_bytes({"a.txt": b"A", "docs/b.md": b"B"}))
        core.archive_unpack(self.cfg, "py-tools", "x.zip", into="x")
        got = self.unpacked()
        self.assertEqual((got["into"], got["members"]["docs/b.md"][1]), ("/work/x", b"B"))

    def test_errors(self):
        self.put("/work/kein.zip", b"das ist kein Archiv")
        self.put("/work/ok.zip", zip_bytes({"a": b"A"}))
        for func, args, error in (
            (core.archive_list, ("kein.zip",), UsageError), (core.archive_unpack, ("kein.zip",), UsageError),
            (core.archive_list, ("fehlt.zip",), NotFoundError), (core.archive_list, ("/etc/x.zip",), SecurityError),
            (core.archive_unpack, ("ok.zip", "/etc"), SecurityError),
        ):
            with self.subTest(func=func.__name__, args=args), self.assertRaises(error):
                func(self.cfg, "py-tools", *args)
        self.assertEqual(self.provider.unpacked, {})

    def test_limits(self):
        self.put("/work/x.zip", zip_bytes({"a.txt": b"A" * 10, "b.txt": b"B"}))
        for name, value in (("ARCHIVE_MAX_BYTES", 5), ("ARCHIVE_MAX_ENTRIES", 1)):
            with self.subTest(limit=name), self.swap(name, value), self.assertRaises(UsageError):
                core.archive_unpack(self.cfg, "py-tools", "x.zip")
        self.assertEqual(self.provider.unpacked, {})

    def swap(self, name, value):
        from unittest import mock
        return mock.patch.object(core, name, value)

    def test_cli(self):
        self.put("/work/pkg.tar.gz", tar_bytes([("pkg/a.txt", b"A")]))
        code, out, err = run_cli("file", "archive", "py-tools", "pkg.tar.gz")
        self.assertEqual(code, 0, err)
        self.assertIn("pkg/a.txt", out)
        code, out, err = run_cli("file", "unpack", "py-tools", "pkg.tar.gz", "--into", "neu")
        self.assertEqual((code, self.unpacked()["into"]), (0, "/work/neu"), err)
        self.assertEqual(run_cli("file", "rm", "py-tools", "pkg.tar.gz")[0], 2)  # ohne Terminal nur mit --yes
        self.assertEqual(run_cli("file", "rm", "py-tools", "pkg.tar.gz", "--yes")[0], 0)
