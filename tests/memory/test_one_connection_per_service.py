"""The four stores share one connection, which #186 needs before anything else.

`MemoryService.observe` appends an observation through one store and projects a
constraint through another. On separate connections to the same file there is
no transaction that can span the two, and there cannot be — so a crash between
them leaves an observation with no constraint derived from it.

Sharing does not by itself make ingest atomic: commit boundaries still live
inside the individual stores. It is what makes the boundary expressible, which
it was not before, and #186 says so: "atomic ingest needs the stores
consolidated onto one connection first."
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from memory.anchor_store import AnchorStore
from memory.constraint_store import ConstraintStore
from memory.store import ObservationStore


def test_a_service_gives_all_four_the_same_connection(tmp_path):
    """The property #186 asks for, asserted on identity rather than on count.

    The enforceable-kind registry is the fourth (#212). A promotion writes the
    rule through one store and the kind row through this one, so it is inside
    the span that has to be able to become one transaction.
    """
    from memory.service import MemoryService

    class _Judge:
        pass

    service = MemoryService(str(tmp_path / "m.db"), _Judge())
    conns = {
        id(service._observations._conn),
        id(service._constraints._conn),
        id(service._anchors._conn),
        id(service._kinds._conn),
    }
    assert len(conns) == 1, "the stores still hold separate connections"

    locks = {
        id(service._observations._lock),
        id(service._constraints._lock),
        id(service._anchors._lock),
        id(service._kinds._lock),
    }
    assert len(locks) == 1, "one connection guarded by more than one lock"


def test_a_store_opened_alone_still_owns_its_connection(tmp_path):
    """Read paths and tools construct these singly and must keep working."""
    a = ObservationStore(str(tmp_path / "m.db"))
    b = ConstraintStore(str(tmp_path / "m.db"))
    assert a._conn is not b._conn


def test_a_connection_without_its_lock_is_refused(tmp_path):
    """Sharing one and not the other is the bug the sharing exists to avoid.

    This build's SQLite is multithread mode: one connection may cross threads
    only if uses never overlap. Two stores serialising on two different locks
    over one connection is exactly that overlap, and it would fail rarely and
    unreproducibly — so it is refused at construction instead.
    """
    conn = sqlite3.connect(str(tmp_path / "m.db"), check_same_thread=False)
    with pytest.raises(ValueError) as excinfo:
        ObservationStore(str(tmp_path / "m.db"), conn=conn)
    assert "not at all" in str(excinfo.value)

    with pytest.raises(ValueError):
        ObservationStore(str(tmp_path / "m.db"), lock=threading.RLock())


def test_a_shared_connection_sees_a_siblings_write(tmp_path):
    """The point of sharing, stated as behaviour rather than as identity."""
    conn = sqlite3.connect(str(tmp_path / "m.db"), check_same_thread=False)
    lock = threading.RLock()
    observations = ObservationStore(str(tmp_path / "m.db"), conn=conn, lock=lock)
    anchors = AnchorStore(str(tmp_path / "m.db"), conn=conn, lock=lock)

    from memory.anchor import Anchor

    uid = anchors.upsert(Anchor(name="gym"))
    # Same connection, so this is visible without a commit crossing processes.
    assert observations._conn.execute(
        "SELECT COUNT(*) FROM anchors WHERE uid = ?", (uid,)
    ).fetchone()[0] == 1
