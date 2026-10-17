"""Per-patient mutual exclusion shared by every path that creates an AI report.

The automatic worker (a thread with its own session) and the manual route (the
request thread) both follow "look for an identical pending report, call the
model, store". Without a shared lock the manual request can run its look-up
while the worker is still between the model call and its commit, and both end
up storing a report for the same input.

The API runs as a single process (a deploy test enforces it), so an in-process
registry of locks is enough. Callers must still re-check the database inside
the lock: the lock orders the two paths, the look-up tells the second one that
the first already stored its report.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager


class _Entry:
    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        # Re-entrant: an inline worker may run inside the thread that holds it.
        self.lock = threading.RLock()
        self.users = 0


_guard = threading.Lock()
_entries: dict[uuid.UUID, _Entry] = {}


@contextmanager
def patient_lock(patient_id: uuid.UUID) -> Iterator[None]:
    """Hold the lock of one patient. Entries are dropped when nobody uses them."""

    with _guard:
        entry = _entries.get(patient_id)
        if entry is None:
            entry = _entries[patient_id] = _Entry()
        entry.users += 1
    try:
        with entry.lock:
            yield
    finally:
        with _guard:
            entry.users -= 1
            if entry.users == 0:
                _entries.pop(patient_id, None)


def users(patient_id: uuid.UUID) -> int:
    """How many callers hold or wait for the lock of a patient (for tests)."""

    with _guard:
        entry = _entries.get(patient_id)
        return entry.users if entry is not None else 0
