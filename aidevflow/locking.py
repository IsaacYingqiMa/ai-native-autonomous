"""OS-released lock for concurrent observer processes, including Windows."""
from __future__ import annotations

import os
import time
import threading
from functools import wraps
from contextlib import contextmanager
from pathlib import Path

_mutexes: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()
_held = threading.local()


@contextmanager
def writer_lock(control: Path, timeout: float = 5.0):
    """Reentrant within one thread; exclusive across threads and processes."""
    key = str(control.resolve()).casefold() if os.name == "nt" else str(control.resolve())
    with _registry_lock:
        mutex = _mutexes.setdefault(key, threading.RLock())
    if not mutex.acquire(timeout=timeout):
        raise TimeoutError("Another Gate writer is active; retry after it finishes")
    held = getattr(_held, "paths", None)
    if held is None:
        held = _held.paths = set()
    try:
        if key in held:
            yield
        else:
            with observer_lock(control, timeout=timeout, name="gate-write.lock"):
                held.add(key)
                try:
                    yield
                finally:
                    held.remove(key)
    finally:
        mutex.release()


def writes(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with writer_lock(self.control):
            return method(self, *args, **kwargs)
    return locked


@contextmanager
def observer_lock(control: Path, timeout: float = 5.0, *, name: str = "agent-observer.lock"):
    control.mkdir(parents=True, exist_ok=True)
    # Never unlink this file: that would allow two different lock objects.
    if name not in ("agent-observer.lock", "gate-write.lock", "coordinator.lock", "event-log.lock"):
        raise ValueError("Unknown AIDevFlow lock name")
    with (control / name).open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Agent observer lock timed out; retry observation/diagnostic")
                time.sleep(0.025)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
