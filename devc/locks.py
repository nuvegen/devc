"""Container-Locks per fcntl.flock, nicht blockierend (SPEC 8.8)."""

import fcntl
import os
from contextlib import contextmanager

from .errors import ConflictError


@contextmanager
def container_lock(paths, name: str):
    """Hält `locks/<name>.lock`; ist der Container belegt: E_CONFLICT."""
    paths.locks.mkdir(parents=True, exist_ok=True)
    fd = os.open(paths.lock_file(name), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConflictError("Container ist beschäftigt", {"container": name}) from None
        yield
    finally:
        os.close(fd)  # gibt den Lock frei; die Lock-Datei bleibt liegen


def is_locked(paths, name: str) -> bool:
    """Ob gerade eine Operation den Lock des Containers hält. Nimmt den Lock selbst nur kurz und geteilt."""
    try:
        fd = os.open(paths.lock_file(name), os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False
