"""SQLite-Zustand (SPEC 6.6). LXD bleibt die Wahrheit über Existenz und Laufstatus."""

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_V1 = """
CREATE TABLE containers (
  name TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  created_at TEXT NOT NULL,
  applied_at TEXT,
  addons_json TEXT NOT NULL
);
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  container TEXT,
  kind TEXT NOT NULL,
  state TEXT NOT NULL,
  error_code TEXT,
  started_at TEXT, finished_at TEXT
);
CREATE TABLE snapshots (
  container TEXT NOT NULL, name TEXT NOT NULL,
  created_at TEXT NOT NULL, scope TEXT NOT NULL,
  PRIMARY KEY (container, name)
);
CREATE TABLE action_runs (
  container TEXT NOT NULL, action TEXT NOT NULL,
  started_at TEXT NOT NULL, pid INTEGER
);
"""

# Migrationen vorwärts; Index + 1 ist die Schema-Version nach der Migration.
MIGRATIONS = [lambda db: db.executescript(SCHEMA_V1)]


def iso(moment) -> str:
    """ISO 8601 UTC mit Z."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            # Das Anlegen sperren: Sonst legen zwei gleichzeitige erste Zugriffe (das Dashboard stellt mehrere
            # Anfragen auf einmal) die Tabellen doppelt an. Eine Transaktion reicht dafür nicht, weil
            # `executescript` in den Migrationen sie beendet; darum eine Sperre auf dem Ordner der Datenbank.
            if not self._ready(db):
                lock = os.open(self.path.parent, os.O_RDONLY)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    if not self._ready(db):
                        self._migrate(db)
                        db.commit()
                finally:
                    os.close(lock)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _ready(db) -> bool:
        """Wahr, wenn das Schema schon aktuell ist; dann braucht der Zugriff keine Sperre."""
        try:
            row = db.execute("SELECT v FROM schema_version").fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None and row["v"] == len(MIGRATIONS)

    @staticmethod
    def _migrate(db) -> None:
        db.execute("CREATE TABLE IF NOT EXISTS schema_version (v INTEGER NOT NULL)")
        row = db.execute("SELECT v FROM schema_version").fetchone()
        version = row["v"] if row else 0
        for index in range(version, len(MIGRATIONS)):
            MIGRATIONS[index](db)
        if row is None:
            db.execute("INSERT INTO schema_version (v) VALUES (?)", (len(MIGRATIONS),))
        elif version != len(MIGRATIONS):
            db.execute("UPDATE schema_version SET v = ?", (len(MIGRATIONS),))

    def schema_version(self) -> int:
        with self._db() as db:
            return db.execute("SELECT v FROM schema_version").fetchone()["v"]

    # --- containers -----------------------------------------------------

    @staticmethod
    def _container(row) -> dict:
        data = dict(row)
        data["addons"] = json.loads(data.pop("addons_json"))
        return data

    def container_add(self, name: str, platform: str, created_at: str) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO containers (name, platform, created_at, applied_at, addons_json) VALUES (?, ?, ?, NULL, '{}')",
                (name, platform, created_at),
            )

    def container(self, name: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM containers WHERE name = ?", (name,)).fetchone()
        return self._container(row) if row else None

    def containers(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM containers ORDER BY name").fetchall()
        return [self._container(row) for row in rows]

    def container_set_addons(self, name: str, addons: dict) -> None:
        with self._db() as db:
            db.execute("UPDATE containers SET addons_json = ? WHERE name = ?", (json.dumps(addons, sort_keys=True), name))

    def container_set_applied(self, name: str, applied_at: str | None) -> None:
        with self._db() as db:
            db.execute("UPDATE containers SET applied_at = ? WHERE name = ?", (applied_at, name))

    def container_remove(self, name: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM containers WHERE name = ?", (name,))
            db.execute("DELETE FROM snapshots WHERE container = ?", (name,))
            db.execute("DELETE FROM action_runs WHERE container = ?", (name,))

    # --- jobs -----------------------------------------------------------

    def job_add(self, job_id: str, container: str | None, kind: str, state: str, started_at: str | None) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO jobs (id, container, kind, state, started_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, container, kind, state, started_at),
            )

    def job_update(self, job_id: str, state: str, error_code: str | None = None, started_at=None, finished_at=None) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE jobs SET state = ?, error_code = ?, started_at = COALESCE(?, started_at), finished_at = ? WHERE id = ?",
                (state, error_code, started_at, finished_at, job_id),
            )

    def job(self, job_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def jobs(self, container: str | None = None, limit: int = 200) -> list[dict]:
        query = "SELECT * FROM jobs"
        args: tuple = ()
        if container is not None:
            query += " WHERE container = ?"
            args = (container,)
        query += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
        with self._db() as db:
            return [dict(row) for row in db.execute(query, args + (limit,)).fetchall()]

    # --- snapshots ------------------------------------------------------

    def snapshot_add(self, container: str, name: str, created_at: str, scope: str) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO snapshots (container, name, created_at, scope) VALUES (?, ?, ?, ?)",
                (container, name, created_at, scope),
            )

    def snapshots(self, container: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM snapshots WHERE container = ? ORDER BY created_at, name", (container,)).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self, container: str, name: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM snapshots WHERE container = ? AND name = ?", (container, name)).fetchone()
        return dict(row) if row else None

    def snapshot_remove(self, container: str, name: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM snapshots WHERE container = ? AND name = ?", (container, name))

    # --- action_runs ----------------------------------------------------

    def action_run_set(self, container: str, action: str, started_at: str, pid: int | None) -> None:
        with self._db() as db:
            db.execute("DELETE FROM action_runs WHERE container = ? AND action = ?", (container, action))
            db.execute(
                "INSERT INTO action_runs (container, action, started_at, pid) VALUES (?, ?, ?, ?)",
                (container, action, started_at, pid),
            )

    def action_run(self, container: str, action: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM action_runs WHERE container = ? AND action = ?", (container, action)).fetchone()
        return dict(row) if row else None

    def action_run_remove(self, container: str, action: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM action_runs WHERE container = ? AND action = ?", (container, action))
