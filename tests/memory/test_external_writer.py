"""A second process writes the store while a long-lived connection is open.

Three sessions relied on this behaviour on 2026-08-22 while deciding whether a
migration could write to `data/memory.db` under a running MCP server, and it was
settled by measuring once in a scratch directory. That measurement is the thing
this file replaces: the store's connections are opened once in `__init__` and
held for the life of the process, so whether they observe an outside write is a
property of how they are opened, and a future change to that could not fail any
existing test.

What the stores actually do: one `sqlite3.connect` per store, held on `self._conn`,
`isolation_level` never set. So reads run in autocommit and take no snapshot, and
an external commit is visible to the next statement. Setting `isolation_level=None`
with an explicit `BEGIN`, or holding a read transaction open across calls, would
break that silently — the store would keep answering, from a frozen view.

That last part rests on a driver default that nobody chose. `isolation_level`
appears nowhere in `src/memory/` or its tests, all three stores pass only
`check_same_thread=False`, and `constraint_store.py` commits explicitly in two
places and via `with self._conn:` in a third — transaction handling here grew
rather than being designed. So these tests assert the *behaviour* and never the
setting: tidying the transaction model is allowed, and should keep them green.
If it does not, that is the signal to re-check these two facts rather than to
restore the default.

The second test pins the case that is NOT safe, and that would have bitten a
wipe: replacing the file leaves the open connection attached to the unlinked
inode. What happens then is deliberately asserted loosely, because measuring it
showed it is not deterministic — the read either raises `disk I/O error` or
answers from the old pages, depending on what is still cached. Only one thing is
guaranteed either way, and it is the thing that matters: the replacement's
contents never become visible. Replace a store and you must restart whatever
holds it open.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from memory.store import ObservationStore


def _row_values(db: Path) -> tuple[str, str, str]:
    """Borrow channel/provenance/anchors from a real row so enums stay valid."""
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT channel, provenance, anchors FROM observations LIMIT 1"
        ).fetchone()
    finally:
        conn.close()


def _seed(db: Path) -> None:
    store = ObservationStore(str(db))
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO observations (uid, text, channel, provenance, session_id, "
        "observed_at, anchors) VALUES ('seed', 'seed row', 'planning', "
        "'observed', 'seed', '2026-08-22T00:00:00Z', '[]')"
    )
    conn.commit()
    conn.close()
    del store


def _external_insert(db: Path, uid: str) -> None:
    """Write from a genuinely separate process, as a migration script would."""
    channel, provenance, anchors = _row_values(db)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);"
            "c.execute('INSERT INTO observations (uid, text, channel, provenance,"
            " session_id, observed_at, anchors) VALUES (?,?,?,?,?,?,?)',"
            "(sys.argv[2],'external write',sys.argv[3],sys.argv[4],'external',"
            "'2026-08-22T00:00:00Z',sys.argv[5]));c.commit();c.close()",
            str(db),
            uid,
            channel,
            provenance,
            anchors,
        ],
        check=True,
    )


def test_external_write_is_visible_without_reconnecting(tmp_path: Path) -> None:
    db = tmp_path / "memory.db"
    _seed(db)

    store = ObservationStore(str(db))  # held open, as the MCP server holds it
    before = len(store.all())

    _external_insert(db, "external-1")

    assert len(store.all()) == before + 1, (
        "the long-lived connection did not see a committed external write; "
        "a snapshot is being held open somewhere"
    )


def test_replacing_the_file_leaves_the_connection_on_the_old_inode(
    tmp_path: Path,
) -> None:
    """The failure mode that reads as success: no error, just a dead store."""
    db = tmp_path / "memory.db"
    _seed(db)

    store = ObservationStore(str(db))
    before = len(store.all())

    replacement = tmp_path / "replacement.db"
    shutil.copyfile(db, replacement)
    _external_insert(replacement, "only-in-replacement")
    db.unlink()
    replacement.rename(db)

    # Either outcome is allowed; both mean the same thing operationally. But an
    # allowed-either-way test can pass without exercising anything, so both
    # branches first prove the setup was real: the replacement is on disk, under
    # the original name, and it does contain the row the held connection must
    # not be showing.
    def _setup_was_real() -> None:
        fresh = sqlite3.connect(db)
        try:
            assert fresh.execute(
                "SELECT count(*) FROM observations WHERE uid = ?",
                ("only-in-replacement",),
            ).fetchone()[0] == 1, "the file was never actually replaced"
        finally:
            fresh.close()

    try:
        after = len(store.all())
    except sqlite3.OperationalError:
        _setup_was_real()  # the loud version: the unlinked file went out from under it
        return
    _setup_was_real()
    assert after == before, (
        "the connection picked up the replacement file, which SQLite does not "
        "promise; if this starts passing differently, re-check the restart advice"
    )
