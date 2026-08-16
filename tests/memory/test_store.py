# tests/memory/test_store.py
from __future__ import annotations

from datetime import datetime, timezone

from memory.models import Channel, Observation, Provenance
from memory.store import ObservationStore


def _obs(text: str = "wake up at 07:00", **kw) -> Observation:
    defaults = dict(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc),
        anchors=["wake"],
    )
    defaults.update(kw)
    return Observation(**defaults)


def test_append_returns_uid_and_get_round_trips(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    obs = _obs()
    uid = store.append(obs)
    assert uid == obs.uid
    got = store.get(uid)
    assert got is not None
    assert got.text == "wake up at 07:00"
    assert got.channel is Channel.PLANNING
    assert got.provenance is Provenance.OBSERVED
    assert got.anchors == ["wake"]


def test_identity_is_not_content_derived(tmp_path):
    """I3: two observations with identical content get different uids."""
    store = ObservationStore(str(tmp_path / "m.db"))
    a = _obs()
    b = _obs()
    assert a.uid != b.uid
    store.append(a)
    store.append(b)
    assert len({o.uid for o in store.all()}) == 2


def test_store_is_append_only(tmp_path):
    """I2: there is no update or delete on the public surface."""
    store = ObservationStore(str(tmp_path / "m.db"))
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")


def test_by_session_filters(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    store.append(_obs(session_id="s1"))
    store.append(_obs(session_id="s2"))
    assert len(store.by_session("s1")) == 1


def test_package_does_not_import_fateforger():
    """Global constraint: cleanroom package."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "memory"
    for path in root.rglob("*.py"):
        assert "fateforger" not in path.read_text(), f"{path} imports fateforger"
