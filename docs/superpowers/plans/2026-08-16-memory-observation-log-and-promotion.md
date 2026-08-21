# Memory: Observation Log and Promotion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the write half of the KG memory server — an immutable observation log with minted identity, plus the promotion decision that moves durable preferences out of the session tier.

**Architecture:** A cleanroom package `src/memory/` with zero imports from `fateforger.*`. Observations are appended to SQLite and never mutated; identity is minted, never derived from content. Promotion from session to durable runs on two complementary paths — anchor recurrence for things observed, and source-channel assertion for things declared. Anchors come from both observation text and the calendar. Nothing in this plan reads a graph or performs traversal; that is a later plan, blocked on #137.

**Tech Stack:** Python 3.11, pydantic v2, stdlib `sqlite3`, pytest 8. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-16-kg-memory-server-design.md`
**Map:** GitHub #133. Related tickets: #145 (write path), #136 (operator surface), #137 (blocked, not this plan).

## Global Constraints

- **Zero imports from `fateforger.*`.** This package must be independently importable. A test asserts it.
- **I2 — L1 is append-only.** No `UPDATE` or `DELETE` against the observations table, ever. Corrections are new rows.
- **I3 — Identity is minted, never content-derived.** `uid` is a random opaque token. Nothing may hash name, description, necessity or scope to produce identity.
- **I5 — Every write that changes state is compare-and-swap.** Never blind replay of a prior payload.
- **C9 — Every apparent signal needs a machine-replay filter.** 48% of restatements in the real corpus occur under one minute apart; raw counts overstate evidence by roughly 2×.
- **Provenance is mandatory on every observation.** `observed` (from the user's world) versus `generated` (produced by a rule's own output). Loop 2 must never learn from `generated`.
- Python 3.11 target. Type annotations on all public functions. `from __future__ import annotations` at the top of every module.
- Tests live under `tests/memory/`. Do not add to `tests/unit/`.

## File Structure

| File | Responsibility |
|---|---|
| `src/memory/__init__.py` | Public exports only |
| `src/memory/identity.py` | Minting opaque uids (I3) |
| `src/memory/models.py` | `Channel`, `Provenance`, `Reliability`, `Tier`, `Observation`, `Proposal` |
| `src/memory/store.py` | Append-only SQLite observation log |
| `src/memory/replay_filter.py` | Machine-replay detection at ingest (C9) |
| `src/memory/anchors.py` | Anchor extraction and recurrence counting |
| `src/memory/calendar_anchors.py` | Anchor extraction from calendar events |
| `src/memory/promotion.py` | The two promotion paths and the decision |
| `tests/memory/` | One test module per source module |

---

### Task 1: Make `src/memory/` importable, then build identity, models and the store

**Read this before starting.** `import memory` will not resolve without the first two steps.
The repo declares only `{include = "fateforger", from = "src"}` in `[tool.poetry] packages`,
and — verified on 2026-08-16 — the active venv's `fateforger.pth` points at a *git worktree*
(`.worktrees/tmbx-journal-level1/src`), not this checkout's `src/`. Adding `pythonpath` to the
pytest config makes the tests independent of install state, which is what we want regardless.

`pyproject.toml` may have uncommitted changes from another session sharing this working tree.
Re-read it before editing and add only the two lines below.

**Files:**
- Modify: `pyproject.toml` (`[tool.poetry] packages`, `[tool.pytest.ini_options]`)
- Create: `src/memory/__init__.py`
- Create: `src/memory/identity.py`
- Create: `src/memory/models.py`
- Create: `src/memory/store.py`
- Create: `tests/memory/__init__.py`
- Test: `tests/memory/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `mint_uid() -> str`; enums `Channel`, `Provenance`, `Reliability`, `Tier`; `Observation` model; `ObservationStore(db_path: str)` with `.append(obs: Observation) -> str`, `.get(uid: str) -> Observation | None`, `.all() -> list[Observation]`, `.by_session(session_id: str) -> list[Observation]`.

- [ ] **Step 0a: Declare the package**

In `pyproject.toml`, under `[tool.poetry]`, add the `memory` include alongside the existing ones:

```toml
packages = [
    {include = "fateforger", from = "src"},
    {include = "memory", from = "src"},
    {include = "scripts"}
]
```

- [ ] **Step 0b: Put `src` on the test path**

In `pyproject.toml`, under `[tool.pytest.ini_options]`, add one line (pytest 8 supports it):

```toml
pythonpath = ["src"]
```

Verify: `python -m pytest --collect-only tests/memory/ 2>&1 | tail -3` should report a
collection error about the missing test file, **not** about a missing `memory` module.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/memory/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/identity.py
from __future__ import annotations

import uuid


def mint_uid() -> str:
    """Mint an opaque identity.

    I3: identity is never derived from content. Editing an observation's text
    must not change its uid, and two observations with identical content must
    receive different uids.
    """
    return uuid.uuid4().hex
```

```python
# src/memory/models.py
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from memory.identity import mint_uid


class Channel(str, Enum):
    """Where an observation arrived from. Carries a durability prior."""

    PLANNING = "planning"
    REVIEW = "review"
    CALENDAR = "calendar"


class Provenance(str, Enum):
    """Whether this came from the user's world or from a rule's own output.

    GENERATED observations must never feed the learning loop: a rule that
    emits a calendar block would otherwise observe its own output as evidence.
    """

    OBSERVED = "observed"
    GENERATED = "generated"


class Reliability(str, Enum):
    """Three values, not two. Silence is not evidence."""

    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    UNEXAMINED = "unexamined"


class Tier(str, Enum):
    SESSION = "session"
    DURABLE = "durable"


class Observation(BaseModel):
    uid: str = Field(default_factory=mint_uid)
    text: str
    channel: Channel
    provenance: Provenance
    session_id: str | None = None
    observed_at: datetime
    anchors: list[str] = Field(default_factory=list)
```

```python
# src/memory/store.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from memory.models import Channel, Observation, Provenance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    uid          TEXT PRIMARY KEY,
    text         TEXT NOT NULL,
    channel      TEXT NOT NULL,
    provenance   TEXT NOT NULL,
    session_id   TEXT,
    observed_at  TEXT NOT NULL,
    anchors      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_obs_session ON observations(session_id);
"""


class ObservationStore:
    """Append-only log of observations.

    I2: there is deliberately no update or delete. A correction is a new row.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def append(self, obs: Observation) -> str:
        self._conn.execute(
            "INSERT INTO observations "
            "(uid, text, channel, provenance, session_id, observed_at, anchors) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                obs.uid,
                obs.text,
                obs.channel.value,
                obs.provenance.value,
                obs.session_id,
                obs.observed_at.isoformat(),
                json.dumps(obs.anchors),
            ),
        )
        self._conn.commit()
        return obs.uid

    def get(self, uid: str) -> Observation | None:
        row = self._conn.execute(
            "SELECT * FROM observations WHERE uid = ?", (uid,)
        ).fetchone()
        return self._row_to_obs(row) if row else None

    def all(self) -> list[Observation]:
        rows = self._conn.execute(
            "SELECT * FROM observations ORDER BY observed_at"
        ).fetchall()
        return [self._row_to_obs(r) for r in rows]

    def by_session(self, session_id: str) -> list[Observation]:
        rows = self._conn.execute(
            "SELECT * FROM observations WHERE session_id = ? ORDER BY observed_at",
            (session_id,),
        ).fetchall()
        return [self._row_to_obs(r) for r in rows]

    @staticmethod
    def _row_to_obs(row: sqlite3.Row) -> Observation:
        return Observation(
            uid=row["uid"],
            text=row["text"],
            channel=Channel(row["channel"]),
            provenance=Provenance(row["provenance"]),
            session_id=row["session_id"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
            anchors=json.loads(row["anchors"]),
        )
```

```python
# src/memory/__init__.py
from __future__ import annotations

from memory.identity import mint_uid
from memory.models import (
    Channel,
    Observation,
    Provenance,
    Reliability,
    Tier,
)
from memory.store import ObservationStore

__all__ = [
    "Channel",
    "Observation",
    "ObservationStore",
    "Provenance",
    "Reliability",
    "Tier",
    "mint_uid",
]
```

```python
# tests/memory/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/memory/test_store.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/memory/ tests/memory/
git commit -m "feat(memory): append-only observation store with minted identity"
```

---

### Task 2: Machine-replay filter at ingest

The real corpus shows 48% of restatements occurring under one minute apart, with byte-identical text. Counting those as evidence overstates it by roughly 2×. This filter runs at ingest so the log never accumulates them.

**Files:**
- Create: `src/memory/replay_filter.py`
- Modify: `src/memory/store.py` (add `append_filtered`)
- Test: `tests/memory/test_replay_filter.py`

**Interfaces:**
- Consumes: `Observation` from Task 1.
- Produces: `normalize(text: str) -> str`; `is_machine_replay(candidate: Observation, recent: list[Observation], window_seconds: int = 60) -> bool`; `ObservationStore.append_filtered(obs: Observation, window_seconds: int = 60) -> str | None` returning `None` when suppressed.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_replay_filter.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory.models import Channel, Observation, Provenance
from memory.replay_filter import is_machine_replay, normalize
from memory.store import ObservationStore

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str, offset_s: int = 0, session_id: str = "s1") -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id=session_id,
        observed_at=T0 + timedelta(seconds=offset_s),
    )


def test_normalize_collapses_case_and_punctuation():
    assert normalize("Work Window: 14:30 to 21:30!") == "work window 14:30 to 21:30"


def test_identical_text_within_window_is_replay():
    prior = _obs("work window today is 14:30 to 21:30")
    cand = _obs("work window today is 14:30 to 21:30", offset_s=30)
    assert is_machine_replay(cand, [prior]) is True


def test_identical_text_outside_window_is_not_replay():
    prior = _obs("work window today is 14:30 to 21:30")
    cand = _obs("work window today is 14:30 to 21:30", offset_s=3600)
    assert is_machine_replay(cand, [prior]) is False


def test_different_session_is_not_replay():
    prior = _obs("work window today is 14:30 to 21:30", session_id="s1")
    cand = _obs("work window today is 14:30 to 21:30", offset_s=30, session_id="s2")
    assert is_machine_replay(cand, [prior]) is False


def test_different_text_within_window_is_not_replay():
    prior = _obs("work window today is 14:30 to 21:30")
    cand = _obs("daily one thing is facet extraction", offset_s=30)
    assert is_machine_replay(cand, [prior]) is False


def test_append_filtered_suppresses_replay(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    first = store.append_filtered(_obs("wake at 07:00"))
    second = store.append_filtered(_obs("wake at 07:00", offset_s=20))
    assert first is not None
    assert second is None
    assert len(store.all()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/memory/test_replay_filter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.replay_filter'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/replay_filter.py
from __future__ import annotations

import re

from memory.models import Observation

_PUNCT = re.compile(r"[^a-z0-9: ]+")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Times are preserved."""
    lowered = text.lower().strip()
    stripped = _PUNCT.sub(" ", lowered)
    return _WS.sub(" ", stripped).strip()


def is_machine_replay(
    candidate: Observation,
    recent: list[Observation],
    window_seconds: int = 60,
) -> bool:
    """True when this observation is the same text re-emitted by machinery.

    The real corpus shows byte-identical user turns repeated within seconds
    (retry loops, resubmits, duplicate logging). Those are not evidence.
    Restatement across sessions, or after the window, is genuine and kept.
    """
    key = normalize(candidate.text)
    for prior in recent:
        if prior.session_id != candidate.session_id:
            continue
        delta = abs((candidate.observed_at - prior.observed_at).total_seconds())
        if delta <= window_seconds and normalize(prior.text) == key:
            return True
    return False
```

```python
# append to src/memory/store.py, inside class ObservationStore
    def append_filtered(
        self, obs: Observation, window_seconds: int = 60
    ) -> str | None:
        """Append unless this is machine replay. Returns None when suppressed."""
        from memory.replay_filter import is_machine_replay

        recent = self.by_session(obs.session_id) if obs.session_id else []
        if is_machine_replay(obs, recent, window_seconds=window_seconds):
            return None
        return self.append(obs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/memory/ -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/memory/replay_filter.py src/memory/store.py tests/memory/test_replay_filter.py
git commit -m "feat(memory): machine-replay filter at ingest"
```

---

### Task 3: Anchor extraction and recurrence counting

An anchor is durable when it recurs across many *sessions* — not many rows. In the real store, `wake` recurs across 59 threads, `commute` 41, `oats` 17, while `hospital` reaches 3.

**Files:**
- Create: `src/memory/anchors.py`
- Test: `tests/memory/test_anchors.py`

**Interfaces:**
- Consumes: `Observation` from Task 1.
- Produces: `extract_anchors(text: str) -> set[str]`; `AnchorVocabulary` with `.from_observations(observations: list[Observation], threshold: int = 6) -> AnchorVocabulary`, `.recurrence(anchor: str) -> int`, `.is_durable(anchor: str) -> bool`, `.durable() -> set[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_anchors.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory.anchors import AnchorVocabulary, extract_anchors
from memory.models import Channel, Observation, Provenance

T0 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


def _obs(text: str, session_id: str, provenance=Provenance.OBSERVED) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=provenance,
        session_id=session_id,
        observed_at=T0 + timedelta(days=int(session_id[1:])),
    )


def test_extract_drops_stopwords_and_short_tokens():
    got = extract_anchors("The user must wake up at 07:00 for the commute")
    assert "wake" in got
    assert "commute" in got
    assert "the" not in got
    assert "up" not in got


def test_recurrence_counts_distinct_sessions_not_rows():
    """Ten rows in one session is one session's worth of evidence."""
    obs = [_obs("wake at 07:00", "s1") for _ in range(10)]
    vocab = AnchorVocabulary.from_observations(obs, threshold=2)
    assert vocab.recurrence("wake") == 1
    assert vocab.is_durable("wake") is False


def test_anchor_becomes_durable_at_threshold():
    obs = [_obs("wake at 07:00", f"s{i}") for i in range(6)]
    vocab = AnchorVocabulary.from_observations(obs, threshold=6)
    assert vocab.recurrence("wake") == 6
    assert vocab.is_durable("wake") is True


def test_generated_observations_are_excluded():
    """A rule's own output must not make its anchor look durable."""
    obs = [
        _obs("pre-gym oats", f"s{i}", provenance=Provenance.GENERATED)
        for i in range(10)
    ]
    vocab = AnchorVocabulary.from_observations(obs, threshold=2)
    assert vocab.recurrence("oats") == 0
    assert vocab.is_durable("oats") is False


def test_durable_returns_the_vocabulary():
    obs = [_obs("wake at 07:00", f"s{i}") for i in range(6)]
    obs += [_obs("hospital visit", f"h{i}") for i in range(2)]
    vocab = AnchorVocabulary.from_observations(obs, threshold=6)
    assert vocab.durable() == {"wake"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/memory/test_anchors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.anchors'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/anchors.py
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from memory.models import Observation, Provenance

STOPWORDS = frozenset(
    """
    the a an of to for is are be user and in at on with we do dont want need
    have has can will would should could not no yes ok so but if then just
    also very really much more most some any all this that these those from
    into one two use used usually their its only block blocks time must
    always never each every day daily set preference constraint duration
    schedule scheduling
    """.split()
)

_TOKEN = re.compile(r"[a-z][a-z0-9]{2,}")


def extract_anchors(text: str) -> set[str]:
    """Content words that could name a recurring kind of thing."""
    return {t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS}


@dataclass
class AnchorVocabulary:
    """Anchors scored by how many distinct sessions mention them.

    Recurrence counts sessions, never rows: ten mentions inside one
    conversation is one session's worth of evidence, and the real corpus is
    roughly half machine duplication.
    """

    threshold: int = 6
    _sessions: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    @classmethod
    def from_observations(
        cls, observations: list[Observation], threshold: int = 6
    ) -> AnchorVocabulary:
        vocab = cls(threshold=threshold)
        for obs in observations:
            if obs.provenance is not Provenance.OBSERVED:
                continue
            session = obs.session_id or obs.uid
            for anchor in extract_anchors(obs.text) | set(obs.anchors):
                vocab._sessions[anchor].add(session)
        return vocab

    def recurrence(self, anchor: str) -> int:
        return len(self._sessions.get(anchor, ()))

    def is_durable(self, anchor: str) -> bool:
        return self.recurrence(anchor) >= self.threshold

    def durable(self) -> set[str]:
        return {a for a in self._sessions if self.is_durable(a)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/memory/ -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add src/memory/anchors.py tests/memory/test_anchors.py
git commit -m "feat(memory): anchor vocabulary from session recurrence"
```

---

### Task 4: Calendar as an anchor source

Measured on the real store: an anchor vocabulary built from constraint text alone scores `gym` at **0** despite `Oats −2h before gym` being one of the most locked-in rules. Gym, hockey and meetings live in the calendar. An anchor vocabulary that ignores the calendar systematically misses the anchors that work so well they are never discussed.

**Files:**
- Create: `src/memory/calendar_anchors.py`
- Test: `tests/memory/test_calendar_anchors.py`

**Interfaces:**
- Consumes: `Channel`, `Observation`, `Provenance` from Task 1; `extract_anchors` from Task 3.
- Produces: `CalendarEvent` model with fields `title: str`, `start: datetime`, `day: date`; `observations_from_events(events: list[CalendarEvent]) -> list[Observation]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_calendar_anchors.py
from __future__ import annotations

from datetime import datetime, timezone

from memory.anchors import AnchorVocabulary
from memory.calendar_anchors import CalendarEvent, observations_from_events
from memory.models import Channel, Provenance


def _ev(title: str, day: int) -> CalendarEvent:
    return CalendarEvent(
        title=title, start=datetime(2026, 3, day, 18, 0, tzinfo=timezone.utc)
    )


def test_event_becomes_a_calendar_channel_observation():
    obs = observations_from_events([_ev("Gym", 9)])
    assert len(obs) == 1
    assert obs[0].channel is Channel.CALENDAR
    assert obs[0].provenance is Provenance.OBSERVED
    assert "gym" in obs[0].anchors


def test_one_event_can_carry_multiple_anchors():
    """Real calendar contains a block literally titled 'hockey/running'."""
    obs = observations_from_events([_ev("hockey/running", 17)])
    assert {"hockey", "running"} <= set(obs[0].anchors)


def test_each_day_is_a_distinct_session_for_recurrence():
    events = [_ev("Gym", d) for d in range(2, 10)]
    vocab = AnchorVocabulary.from_observations(
        observations_from_events(events), threshold=6
    )
    assert vocab.recurrence("gym") == 8
    assert vocab.is_durable("gym") is True


def test_calendar_recovers_gym_which_text_alone_misses():
    """The measured failure: gym scores 0 from constraint text alone."""
    vocab_text_only = AnchorVocabulary.from_observations([], threshold=6)
    assert vocab_text_only.is_durable("gym") is False

    events = [_ev("Gym", d) for d in range(2, 10)]
    vocab_with_calendar = AnchorVocabulary.from_observations(
        observations_from_events(events), threshold=6
    )
    assert vocab_with_calendar.is_durable("gym") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/memory/test_calendar_anchors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.calendar_anchors'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/calendar_anchors.py
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from memory.anchors import extract_anchors
from memory.models import Channel, Observation, Provenance


class CalendarEvent(BaseModel):
    title: str
    start: datetime

    @property
    def day(self) -> date:
        return self.start.date()


def observations_from_events(events: list[CalendarEvent]) -> list[Observation]:
    """Turn calendar events into observations on the CALENDAR channel.

    Each day is its own session, so recurrence counts days rather than
    events: eight gym sessions across eight days is eight units of evidence,
    while eight blocks on one day is one.

    An event carries n anchors, not one. The real calendar contains a block
    titled 'hockey/running', which is genuinely two.
    """
    out: list[Observation] = []
    for event in events:
        out.append(
            Observation(
                text=event.title,
                channel=Channel.CALENDAR,
                provenance=Provenance.OBSERVED,
                session_id=f"cal:{event.day.isoformat()}",
                observed_at=event.start,
                anchors=sorted(extract_anchors(event.title)),
            )
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/memory/ -v`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add src/memory/calendar_anchors.py tests/memory/test_calendar_anchors.py
git commit -m "feat(memory): calendar as a first-class anchor source"
```

---

### Task 5: Promotion — the two paths

Recurrence catches what the user does; assertion catches what the user decides. Measured: the recurrence path alone reaches recall 0.85, and every one of its false negatives is a policy declaration.

**Files:**
- Create: `src/memory/promotion.py`
- Test: `tests/memory/test_promotion.py`

**Interfaces:**
- Consumes: `Channel`, `Observation`, `Tier` from Task 1; `AnchorVocabulary`, `extract_anchors` from Task 3.
- Produces: `PromotionReason` enum with members `ANCHOR_RECURRENCE`, `ASSERTION`, `NONE`; `PromotionDecision` model with `tier: Tier`, `reason: PromotionReason`, `matched_anchors: list[str]`; `decide(observation: Observation, vocabulary: AnchorVocabulary) -> PromotionDecision`; `is_meta_level(text: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_promotion.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory.anchors import AnchorVocabulary
from memory.models import Channel, Observation, Provenance, Tier
from memory.promotion import PromotionReason, decide, is_meta_level

T0 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


def _obs(text: str, channel=Channel.PLANNING, session_id="s0") -> Observation:
    return Observation(
        text=text,
        channel=channel,
        provenance=Provenance.OBSERVED,
        session_id=session_id,
        observed_at=T0,
    )


def _vocab(anchor: str, sessions: int = 8) -> AnchorVocabulary:
    obs = [
        Observation(
            text=f"{anchor} mention",
            channel=Channel.PLANNING,
            provenance=Provenance.OBSERVED,
            session_id=f"s{i}",
            observed_at=T0 + timedelta(days=i),
        )
        for i in range(sessions)
    ]
    return AnchorVocabulary.from_observations(obs, threshold=6)


def test_rule_on_a_durable_anchor_promotes():
    """Oats-before-gym is stated once, but gym is durable."""
    decision = decide(_obs("eat oats two hours before gym"), _vocab("gym"))
    assert decision.tier is Tier.DURABLE
    assert decision.reason is PromotionReason.ANCHOR_RECURRENCE
    assert "gym" in decision.matched_anchors


def test_rule_on_an_ephemeral_anchor_stays_session():
    decision = decide(_obs("hospital visit at 14:00"), _vocab("gym"))
    assert decision.tier is Tier.SESSION
    assert decision.reason is PromotionReason.NONE


def test_review_channel_promotes_regardless_of_anchor():
    """Policy declarations never recur; the channel is the signal."""
    decision = decide(
        _obs(
            "never schedule more than two parallel strategic outcomes",
            channel=Channel.REVIEW,
        ),
        _vocab("gym"),
    )
    assert decision.tier is Tier.DURABLE
    assert decision.reason is PromotionReason.ASSERTION


def test_meta_level_rows_are_rejected_even_on_review_channel():
    decision = decide(
        _obs(
            "the user wants to begin the timeboxing session immediately",
            channel=Channel.REVIEW,
        ),
        _vocab("gym"),
    )
    assert decision.tier is Tier.SESSION
    assert decision.reason is PromotionReason.NONE


def test_is_meta_level_detects_interaction_talk():
    assert is_meta_level("the user wants to begin the timeboxing session immediately")
    assert is_meta_level("the activity must adhere to a timeboxing format")
    assert not is_meta_level("eat oats two hours before gym")


def test_generated_observations_never_promote():
    obs = Observation(
        text="pre-gym oats",
        channel=Channel.CALENDAR,
        provenance=Provenance.GENERATED,
        session_id="s0",
        observed_at=T0,
    )
    decision = decide(obs, _vocab("gym"))
    assert decision.tier is Tier.SESSION
    assert decision.reason is PromotionReason.NONE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/memory/test_promotion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.promotion'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/promotion.py
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from memory.anchors import AnchorVocabulary, extract_anchors
from memory.models import Channel, Observation, Provenance, Tier

META_MARKERS = (
    "timeboxing session",
    "timeboxing format",
    "timeboxing methodology",
    "begin the session",
    "start the session",
)


class PromotionReason(str, Enum):
    ANCHOR_RECURRENCE = "anchor_recurrence"
    ASSERTION = "assertion"
    NONE = "none"


class PromotionDecision(BaseModel):
    tier: Tier
    reason: PromotionReason
    matched_anchors: list[str] = Field(default_factory=list)


def is_meta_level(text: str) -> bool:
    """True for statements about the interaction rather than about the day.

    The real store contains rows such as "the user wants to begin the
    timeboxing session immediately". These are not preferences about a life.
    """
    lowered = text.lower()
    return any(marker in lowered for marker in META_MARKERS)


def decide(
    observation: Observation, vocabulary: AnchorVocabulary
) -> PromotionDecision:
    """Decide which tier an observation belongs in.

    Two complementary paths. Recurrence promotes a rule when *its anchor* is
    durable, which catches what the user does. Assertion promotes on the
    source channel, which catches what the user decides — policy
    declarations never recur, because declaring something once is how
    declarations work.
    """
    session = PromotionDecision(tier=Tier.SESSION, reason=PromotionReason.NONE)

    if observation.provenance is not Provenance.OBSERVED:
        return session
    if is_meta_level(observation.text):
        return session

    if observation.channel is Channel.REVIEW:
        return PromotionDecision(
            tier=Tier.DURABLE, reason=PromotionReason.ASSERTION
        )

    candidates = extract_anchors(observation.text) | set(observation.anchors)
    matched = sorted(a for a in candidates if vocabulary.is_durable(a))
    if matched:
        return PromotionDecision(
            tier=Tier.DURABLE,
            reason=PromotionReason.ANCHOR_RECURRENCE,
            matched_anchors=matched,
        )
    return session
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/memory/ -v`
Expected: 26 passed

- [ ] **Step 5: Commit**

```bash
git add src/memory/promotion.py tests/memory/test_promotion.py
git commit -m "feat(memory): promotion via anchor recurrence and channel assertion"
```

---

### Task 6: Proposals with three-valued reliability

The agent proposes a tier; the user may change it; not looking must never be recorded as agreement. This is the data model behind the ambient surface, not the surface itself.

**Files:**
- Modify: `src/memory/models.py` (add `Proposal`)
- Modify: `src/memory/store.py` (add proposals table and methods)
- Modify: `src/memory/__init__.py` (export `Proposal`)
- Test: `tests/memory/test_proposals.py`

**Interfaces:**
- Consumes: `Observation`, `Reliability`, `Tier` from Task 1; `PromotionDecision` from Task 5.
- Produces: `Proposal` model with `uid`, `observation_uid`, `proposed_tier`, `reason`, `reliability`, `final_tier`, `proposed_at`; `ObservationStore.propose(observation_uid: str, decision: PromotionDecision, at: datetime) -> Proposal`; `.confirm(proposal_uid: str) -> Proposal`; `.correct(proposal_uid: str, corrected_tier: Tier) -> Proposal`; `.proposals() -> list[Proposal]`; `.evidence_proposals() -> list[Proposal]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_proposals.py
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.models import Channel, Observation, Provenance, Reliability, Tier
from memory.promotion import PromotionDecision, PromotionReason
from memory.store import ObservationStore

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _setup(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    obs = Observation(
        text="eat oats two hours before gym",
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )
    store.append(obs)
    decision = PromotionDecision(
        tier=Tier.DURABLE,
        reason=PromotionReason.ANCHOR_RECURRENCE,
        matched_anchors=["gym"],
    )
    return store, obs, decision


def test_new_proposal_is_unexamined(tmp_path):
    store, obs, decision = _setup(tmp_path)
    proposal = store.propose(obs.uid, decision, at=T0)
    assert proposal.reliability is Reliability.UNEXAMINED
    assert proposal.proposed_tier is Tier.DURABLE
    assert proposal.final_tier is Tier.DURABLE


def test_confirm_records_active_agreement(tmp_path):
    store, obs, decision = _setup(tmp_path)
    proposal = store.propose(obs.uid, decision, at=T0)
    confirmed = store.confirm(proposal.uid)
    assert confirmed.reliability is Reliability.CONFIRMED
    assert confirmed.final_tier is Tier.DURABLE


def test_correct_changes_tier_and_records_correction(tmp_path):
    store, obs, decision = _setup(tmp_path)
    proposal = store.propose(obs.uid, decision, at=T0)
    corrected = store.correct(proposal.uid, Tier.SESSION)
    assert corrected.reliability is Reliability.CORRECTED
    assert corrected.final_tier is Tier.SESSION
    assert corrected.proposed_tier is Tier.DURABLE


def test_unexamined_is_not_evidence(tmp_path):
    """Silence must never count as agreement."""
    store, obs, decision = _setup(tmp_path)
    store.propose(obs.uid, decision, at=T0)
    assert len(store.proposals()) == 1
    assert store.evidence_proposals() == []


def test_confirmed_and_corrected_are_evidence(tmp_path):
    store, obs, decision = _setup(tmp_path)
    a = store.propose(obs.uid, decision, at=T0)
    b = store.propose(obs.uid, decision, at=T0)
    store.confirm(a.uid)
    store.correct(b.uid, Tier.SESSION)
    assert len(store.evidence_proposals()) == 2


def test_confirm_is_compare_and_swap(tmp_path):
    """I5: a second decision on an already-decided proposal is rejected."""
    store, obs, decision = _setup(tmp_path)
    proposal = store.propose(obs.uid, decision, at=T0)
    store.confirm(proposal.uid)
    with pytest.raises(ValueError, match="already decided"):
        store.correct(proposal.uid, Tier.SESSION)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/memory/test_proposals.py -v`
Expected: FAIL with `ImportError: cannot import name 'Proposal'` or `AttributeError: 'ObservationStore' object has no attribute 'propose'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/memory/models.py
class Proposal(BaseModel):
    """A tier assignment the agent proposed and the user may change.

    reliability has three values because "did not object" is not agreement.
    Only CONFIRMED and CORRECTED are evidence.
    """

    uid: str = Field(default_factory=mint_uid)
    observation_uid: str
    proposed_tier: Tier
    reason: str
    reliability: Reliability = Reliability.UNEXAMINED
    final_tier: Tier
    proposed_at: datetime
```

```python
# add to _SCHEMA in src/memory/store.py
CREATE TABLE IF NOT EXISTS proposals (
    uid              TEXT PRIMARY KEY,
    observation_uid  TEXT NOT NULL,
    proposed_tier    TEXT NOT NULL,
    reason           TEXT NOT NULL,
    reliability      TEXT NOT NULL,
    final_tier       TEXT NOT NULL,
    proposed_at      TEXT NOT NULL
);
```

```python
# append to src/memory/store.py, inside class ObservationStore
    def propose(
        self, observation_uid: str, decision: PromotionDecision, at: datetime
    ) -> Proposal:
        proposal = Proposal(
            observation_uid=observation_uid,
            proposed_tier=decision.tier,
            reason=decision.reason.value,
            final_tier=decision.tier,
            proposed_at=at,
        )
        self._conn.execute(
            "INSERT INTO proposals "
            "(uid, observation_uid, proposed_tier, reason, reliability, "
            " final_tier, proposed_at) VALUES (?,?,?,?,?,?,?)",
            (
                proposal.uid,
                proposal.observation_uid,
                proposal.proposed_tier.value,
                proposal.reason,
                proposal.reliability.value,
                proposal.final_tier.value,
                proposal.proposed_at.isoformat(),
            ),
        )
        self._conn.commit()
        return proposal

    def confirm(self, proposal_uid: str) -> "Proposal":
        return self._decide(proposal_uid, Reliability.CONFIRMED, None)

    def correct(self, proposal_uid: str, corrected_tier: Tier) -> "Proposal":
        return self._decide(proposal_uid, Reliability.CORRECTED, corrected_tier)

    def _decide(
        self,
        proposal_uid: str,
        reliability: Reliability,
        corrected_tier: Tier | None,
    ) -> "Proposal":
        """I5: compare-and-swap. Only an UNEXAMINED proposal may be decided."""
        row = self._conn.execute(
            "SELECT * FROM proposals WHERE uid = ?", (proposal_uid,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no such proposal: {proposal_uid}")
        if row["reliability"] != Reliability.UNEXAMINED.value:
            raise ValueError(f"proposal {proposal_uid} already decided")
        final = corrected_tier.value if corrected_tier else row["final_tier"]
        cur = self._conn.execute(
            "UPDATE proposals SET reliability = ?, final_tier = ? "
            "WHERE uid = ? AND reliability = ?",
            (reliability.value, final, proposal_uid, Reliability.UNEXAMINED.value),
        )
        if cur.rowcount != 1:
            raise ValueError(f"proposal {proposal_uid} already decided")
        self._conn.commit()
        return self._row_to_proposal(
            self._conn.execute(
                "SELECT * FROM proposals WHERE uid = ?", (proposal_uid,)
            ).fetchone()
        )

    def proposals(self) -> list["Proposal"]:
        rows = self._conn.execute(
            "SELECT * FROM proposals ORDER BY proposed_at"
        ).fetchall()
        return [self._row_to_proposal(r) for r in rows]

    def evidence_proposals(self) -> list["Proposal"]:
        """Only actively confirmed or corrected proposals are evidence."""
        return [
            p for p in self.proposals() if p.reliability is not Reliability.UNEXAMINED
        ]

    @staticmethod
    def _row_to_proposal(row: sqlite3.Row) -> "Proposal":
        from memory.models import Proposal

        return Proposal(
            uid=row["uid"],
            observation_uid=row["observation_uid"],
            proposed_tier=Tier(row["proposed_tier"]),
            reason=row["reason"],
            reliability=Reliability(row["reliability"]),
            final_tier=Tier(row["final_tier"]),
            proposed_at=datetime.fromisoformat(row["proposed_at"]),
        )
```

Replace the imports at the top of `src/memory/store.py` with:

```python
from memory.models import (
    Channel,
    Observation,
    Provenance,
    Proposal,
    Reliability,
    Tier,
)
from memory.promotion import PromotionDecision
```

No import cycle results: `promotion` imports from `anchors` and `models`, neither of which
imports `store`. Remove the now-redundant local `from memory.models import Proposal` lines
inside `propose` and `_row_to_proposal`.

Add `"Proposal"` to `__all__` and the import block in `src/memory/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/memory/ -v`
Expected: 32 passed

- [ ] **Step 5: Commit**

```bash
git add src/memory/ tests/memory/test_proposals.py
git commit -m "feat(memory): proposals with three-valued reliability"
```

---

### Task 7: Regression harness against the real store

The design's numbers came from `data/admonish.db`. This task pins them so a future change that breaks promotion is caught. The test skips when the database is absent so CI stays green on a clean checkout.

**Files:**
- Create: `tests/memory/test_real_corpus.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: nothing. This is a characterisation test.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_real_corpus.py
from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from datetime import datetime, timezone

import pytest

from memory.anchors import AnchorVocabulary
from memory.models import Channel, Observation, Provenance, Tier
from memory.promotion import PromotionReason, decide

DB = pathlib.Path(__file__).resolve().parents[2] / "data" / "admonish.db"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="real corpus not present")


def _load() -> list[Observation]:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name, description, scope, thread_ts, created_at "
        "FROM timeboxing_constraints"
    ).fetchall()
    out = []
    for r in rows:
        try:
            when = datetime.fromisoformat(str(r["created_at"]).replace("Z", ""))
        except ValueError:
            when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        out.append(
            Observation(
                text=f"{r['name'] or ''} {r['description'] or ''}".strip(),
                channel=Channel.PLANNING,
                provenance=Provenance.OBSERVED,
                session_id=r["thread_ts"] or f"none:{r['scope']}",
                observed_at=when,
            )
        )
    return out


def test_wake_is_a_durable_anchor_in_the_real_corpus():
    """Measured: `wake` recurs across 59 threads."""
    vocab = AnchorVocabulary.from_observations(_load(), threshold=6)
    assert vocab.is_durable("wake") is True
    assert vocab.recurrence("wake") >= 20


def test_hospital_is_not_a_durable_anchor():
    """Measured: `hospital` reaches 3 threads. Ephemeral."""
    vocab = AnchorVocabulary.from_observations(_load(), threshold=6)
    assert vocab.is_durable("hospital") is False


def test_promotion_recovers_wake_up_time_which_the_old_system_lost():
    """The core defect: `wake up time` was extracted 22 times, never promoted."""
    observations = _load()
    vocab = AnchorVocabulary.from_observations(observations, threshold=6)
    obs = Observation(
        text="Wake up time: the user wakes at 07:00",
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="new",
        observed_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )
    decision = decide(obs, vocab)
    assert decision.tier is Tier.DURABLE
    assert decision.reason is PromotionReason.ANCHOR_RECURRENCE


def test_promotion_recall_on_known_profile_rules_is_at_least_point_eight():
    """Measured recall of the anchor-recurrence path: 0.85."""
    observations = _load()
    vocab = AnchorVocabulary.from_observations(observations, threshold=6)

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT DISTINCT name, description FROM timeboxing_constraints "
        "WHERE scope = 'PROFILE'"
    ).fetchall()

    promoted = 0
    for r in rows:
        obs = Observation(
            text=f"{r['name'] or ''} {r['description'] or ''}".strip(),
            channel=Channel.PLANNING,
            provenance=Provenance.OBSERVED,
            session_id="probe",
            observed_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        )
        if decide(obs, vocab).tier is Tier.DURABLE:
            promoted += 1

    recall = promoted / len(rows)
    assert recall >= 0.80, f"recall regressed to {recall:.2f}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/memory/test_real_corpus.py -v`
Expected: FAIL (or SKIP if `data/admonish.db` is absent — in that case run against a copy and confirm the assertions hold before proceeding)

- [ ] **Step 3: Make it pass**

No new source code. If `test_promotion_recall_on_known_profile_rules_is_at_least_point_eight` fails, the fix is in `STOPWORDS` in `src/memory/anchors.py` — the measured 0.85 used a stopword list that removes generic scheduling vocabulary (`block`, `time`, `duration`, `schedule`) while keeping domain nouns (`wake`, `commute`, `oats`, `gym`, `lunch`). Adjust the list, not the threshold.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/memory/ -v`
Expected: 36 passed (or 32 passed, 4 skipped without the corpus)

- [ ] **Step 5: Commit**

```bash
git add tests/memory/test_real_corpus.py
git commit -m "test(memory): characterisation tests against the real corpus"
```

---

## Not in this plan

Deliberately excluded, with reasons:

- **Traversal and the graph.** Blocked on #137's conditionality-encoding fork (path intersection versus trigger predicates). Retrieval was measured cheap — two deterministic hops — so it is not the risk.
- **Decay.** Needs the taxonomy to inherit rates from, which needs #137.
- **Loop 2, the gate, IB3/BBNR.** Needs decay and the taxonomy. Lands in the weekly review.
- **The MCP server frontend.** The package is importable in-process first; the MCP binding is a thin wrapper once the verb list settles on #134.
- **Migration of the 1,662 existing rows.** Open question in the spec.
- **The ambient UI.** Task 6 builds its data model; the surface itself is #136.
