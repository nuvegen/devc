"""store.py (SPEC 6.6), locks.py und jobs.py (SPEC 8.8)."""

import datetime
import re
import unittest

from devc import config, jobs, locks
from devc.errors import ConflictError, InternalError, JobFailedError
from devc.store import Store, iso
from tests import helpers

NOW = datetime.datetime(2026, 10, 10, 7, 11, 40, tzinfo=datetime.timezone.utc)


class StoreTest(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store(self.data_dir / "devc.sqlite")

    def test_first_access_from_several_threads(self):
        # Das Dashboard stellt mehrere Anfragen auf einmal; auf einer neuen Datenbank dürfen sie sich nicht stören.
        import threading

        for round_ in range(25):
            errors = []

            def work(path=self.data_dir / f"neu-{round_}.sqlite"):
                try:
                    errors.append(Store(path).schema_version())
                except Exception as error:  # noqa: BLE001 - jeder Fehler zählt
                    errors.append(repr(error))

            threads = [threading.Thread(target=work) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [1] * 6)

    def test_schema(self):
        self.assertEqual(self.store.schema_version(), 1)
        self.assertEqual(Store(self.data_dir / "devc.sqlite").schema_version(), 1)  # zweites Öffnen migriert nicht erneut
        with self.store._db() as db:
            tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(tables, {"containers", "jobs", "snapshots", "action_runs", "schema_version"})

    def test_containers(self):
        self.store.container_add("py-tools", "lxd", iso(NOW))
        self.assertEqual(self.store.container("py-tools"), {
            "name": "py-tools", "platform": "lxd", "created_at": "2026-10-10T07:11:40Z", "applied_at": None, "addons": {},
        })
        self.store.container_set_addons("py-tools", {"base": "1.0.0"})
        self.store.container_set_applied("py-tools", iso(NOW))
        row = self.store.container("py-tools")
        self.assertEqual((row["addons"], row["applied_at"]), ({"base": "1.0.0"}, "2026-10-10T07:11:40Z"))
        self.store.container_add("ab", "lxd", iso(NOW))
        self.assertEqual([c["name"] for c in self.store.containers()], ["ab", "py-tools"])
        self.assertIsNone(self.store.container("fehlt"))

    def test_remove_cascades(self):
        self.store.container_add("py-tools", "lxd", iso(NOW))
        self.store.snapshot_add("py-tools", "s1", iso(NOW), "data")
        self.store.action_run_set("py-tools", "httpserver.start", iso(NOW), 42)
        self.store.container_remove("py-tools")
        self.assertIsNone(self.store.container("py-tools"))
        self.assertEqual(self.store.snapshots("py-tools"), [])
        self.assertIsNone(self.store.action_run("py-tools", "httpserver.start"))

    def test_jobs(self):
        self.store.job_add("j-000000000001", "py-tools", "create", "running", iso(NOW))
        self.store.job_add("j-000000000002", None, "addon-import", "queued", None)
        self.store.job_update("j-000000000001", "failed", "E_JOB_FAILED", finished_at=iso(NOW))
        job = self.store.job("j-000000000001")
        self.assertEqual((job["state"], job["error_code"], job["started_at"], job["finished_at"]),
                         ("failed", "E_JOB_FAILED", "2026-10-10T07:11:40Z", "2026-10-10T07:11:40Z"))
        self.assertEqual([j["id"] for j in self.store.jobs("py-tools")], ["j-000000000001"])
        self.assertEqual(len(self.store.jobs()), 2)

    def test_snapshots_and_action_runs(self):
        self.store.snapshot_add("py-tools", "s1", iso(NOW), "full")
        self.assertEqual(self.store.snapshot("py-tools", "s1")["scope"], "full")
        self.store.snapshot_remove("py-tools", "s1")
        self.assertIsNone(self.store.snapshot("py-tools", "s1"))
        self.store.action_run_set("py-tools", "a.start", iso(NOW), 1)
        self.store.action_run_set("py-tools", "a.start", iso(NOW), 2)
        self.assertEqual(self.store.action_run("py-tools", "a.start")["pid"], 2)
        self.store.action_run_remove("py-tools", "a.start")
        self.assertIsNone(self.store.action_run("py-tools", "a.start"))


class LockTest(helpers.TempEnvTestCase):
    def test_lock_is_exclusive_per_container(self):
        paths = config.load().paths
        with locks.container_lock(paths, "py-tools"):
            with self.assertRaises(ConflictError) as ctx:
                with locks.container_lock(paths, "py-tools"):
                    self.fail("zweiter Lock darf nicht gelingen")
            self.assertEqual((ctx.exception.code, ctx.exception.message), ("E_CONFLICT", "Container ist beschäftigt"))
            with locks.container_lock(paths, "anderer"):
                pass
        with locks.container_lock(paths, "py-tools"):
            pass
        self.assertTrue(paths.lock_file("py-tools").exists())

    def test_released_after_exception(self):
        paths = config.load().paths
        with self.assertRaises(RuntimeError):
            with locks.container_lock(paths, "py-tools"):
                raise RuntimeError
        with locks.container_lock(paths, "py-tools"):
            pass


class JobTest(helpers.TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.config = config.load()
        self.store = Store(self.config.paths.db)
        self.lines = []

    def run_job(self, func, job_id="j-4f1a09c2b7de"):
        return jobs.run(self.config, self.store, "create", "py-tools", func, lambda: NOW, self.lines.append, job_id)

    def test_new_id(self):
        self.assertRegex(jobs.new_id(), r"^j-[0-9a-f]{12}$")
        self.assertNotEqual(jobs.new_id(), jobs.new_id())

    def test_ok(self):
        def work(job):
            job.log("addon", "python: install.sh ok")
            job.warn("docs", "W4 Hinweis")
            return {"x": 1}

        result, job = self.run_job(work)
        self.assertEqual((result, job.id, job.warnings), ({"x": 1}, "j-4f1a09c2b7de", ["W4 Hinweis"]))
        row = self.store.job(job.id)
        self.assertEqual((row["state"], row["error_code"], row["kind"], row["container"]), ("ok", None, "create", "py-tools"))
        log = self.config.paths.job_log(job.id).read_text().splitlines()
        self.assertEqual(log, self.lines)
        for line in log:
            self.assertRegex(line, r"^\[\d\d:\d\d:\d\d\] (\w+ *) ")
        self.assertRegex(log[0], r"^\[\d\d:\d\d:\d\d\] addon    python: install\.sh ok$")
        self.assertTrue(log[-1].endswith("done     fertig"))

    def test_multiline_log(self):
        result, job = self.run_job(lambda job: job.log("addon", "eins\nzwei"))
        self.assertEqual(len([line for line in self.lines if " addon " in line]), 2)

    def test_devc_error_marks_failed_and_carries_job(self):
        def work(job):
            raise JobFailedError("Addon python: install.sh endete mit Exit 1")

        with self.assertRaises(JobFailedError) as ctx:
            self.run_job(work)
        self.assertEqual(ctx.exception.details["job"], "j-4f1a09c2b7de")
        row = self.store.job("j-4f1a09c2b7de")
        self.assertEqual((row["state"], row["error_code"]), ("failed", "E_JOB_FAILED"))
        self.assertIsNotNone(row["finished_at"])
        self.assertRegex(self.lines[-1], r"error    E_JOB_FAILED: Addon python")

    def test_unexpected_error_hides_message(self):
        def work(job):
            raise RuntimeError("geheim-4711")

        with self.assertRaises(InternalError) as ctx:
            self.run_job(work)
        self.assertNotIn("geheim-4711", ctx.exception.message + "".join(self.lines))
        self.assertEqual(self.store.job("j-4f1a09c2b7de")["error_code"], "E_INTERNAL")

    def test_phases(self):
        self.assertEqual(len(jobs.PHASES), 14)
        self.assertTrue(all(re.fullmatch(r"[a-z]{1,8}", phase) for phase in jobs.PHASES))


if __name__ == "__main__":
    unittest.main()
