# LLM Extraction and Promotion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract anchors and decide memory tier entirely with an LLM, with zero pattern matching, and serve the resulting constraints to the timeboxing patcher.

**Architecture:** A `Judge` port defines four independent judgements as async methods returning pydantic models. Three implementations satisfy it: an MCP-backed one for the runtime agent path, a direct OpenRouter one for offline corpus work where no agent exists, and a stub for unit tests. Ingest issues the independent judgements concurrently via `asyncio.gather` and applies the results to the append-only store from the previous plan.

**Tech Stack:** Python 3.11, pydantic v2, `asyncio`, stdlib `sqlite3`, pytest 8 + pytest-asyncio (`asyncio_mode = "auto"` is already configured). `httpx` for the OpenRouter implementation — already a project dependency.

**Supersedes:** `2026-08-16-memory-observation-log-and-promotion.md` Tasks 2–5, reverted in commit `191c3f2` for violating the no-pattern-matching rule. Task 1 of that plan (store, identity, models) stands and is the base for this one.

**Spec:** `docs/superpowers/specs/2026-08-16-kg-memory-server-design.md`

## Global Constraints

Read `CLAUDE.md` before writing a line. The first constraint is the reason this plan exists.

- **No keyword matching, string matching, or regex. Ever.** `import re` is banned outright, as are keyword lists, marker lists, stopword lists, substring tests against user content, tokenising by splitting on whitespace or punctuation, and fuzzy string similarity. Any judgement about what user content *means* goes to the LLM. String operations on identifiers the system itself minted — uids, SQL column names, enum values — are not covered by this rule.
- **Independent LLM calls are issued concurrently**, never in sequence. Use `asyncio.gather`. Chain only where a later prompt genuinely needs an earlier answer, and say why in a comment.
- **Extraction model:** `google/gemini-3.6-flash` with `"reasoning": {"effort": "minimal"}`. Do NOT send `{"enabled": false}` — the endpoint rejects it ("Reasoning is mandatory for this endpoint and cannot be disabled") and every request 400s. Verified against the live API 2026-08-16.
- **Two kinds of test, both required.** Unit tests stub the judge — fast, offline, deterministic, asserting the *plumbing*. Eval tests hit OpenRouter with real cases and assert *quality*; mark them `@pytest.mark.slow`. Never assert an exact model output string in a unit test; assert the decision it drove.
- Zero imports from `fateforger.*`. The package stays independently importable.
- **I2 — the observations table is append-only.** No `UPDATE` or `DELETE` against it.
- **I3 — identity is minted, never content-derived.**
- `from __future__ import annotations` at the top of every module. Type annotations on all public functions.
- Tests live in `tests/memory/`. **Never create `tests/memory/__init__.py` or `tests/__init__.py`** — either shadows the `memory` package and breaks every import.
- Secrets come from the environment. Never hardcode or log a key.

## What already exists

From the previous plan's Task 1, at commit `191c3f2`:

- `src/memory/identity.py` — `mint_uid() -> str`
- `src/memory/models.py` — `Channel` (`PLANNING`, `REVIEW`, `CALENDAR`), `Provenance` (`OBSERVED`, `GENERATED`), `Reliability` (`CONFIRMED`, `CORRECTED`, `UNEXAMINED`), `Tier` (`SESSION`, `DURABLE`), `Observation`
- `src/memory/store.py` — `ObservationStore` with `.append`, `.get`, `.all`, `.by_session`
- `tests/memory/test_store.py` — 5 tests, passing

## File Structure

| File | Responsibility |
|---|---|
| `src/memory/judge.py` | The `Judge` port, its four result models, and `StubJudge` |
| `src/memory/ingest.py` | Concurrent orchestration of judgements; applies results to the store |
| `src/memory/openrouter_judge.py` | Direct OpenRouter implementation for offline corpus and eval work |
| `src/memory/mcp_judge.py` | MCP-tool-backed implementation for the runtime agent path |
| `src/memory/read_api.py` | `get_active_constraints` — what the timebox patcher calls |
| `tests/memory/` | One test module per source module, plus `test_eval_extraction.py` for slow evals |

---

### Task 1: The Judge port and its stub

Everything else depends on this shape. No LLM call happens here — this task defines what asking looks like.

**Files:**
- Create: `src/memory/judge.py`
- Test: `tests/memory/test_judge.py`

**Interfaces:**
- Consumes: `Observation` from `memory.models`.
- Produces: result models `AnchorJudgement` (`anchors: list[str]`), `TierJudgement` (`tier: Tier`, `is_declaration: bool`, `rationale: str`), `MetaJudgement` (`is_meta: bool`, `rationale: str`), `DedupJudgement` (`duplicate_of: str | None`, `rationale: str`); protocol `Judge` with async `anchors`, `tier`, `meta`, `dedup`; `StubJudge` implementing it from canned answers.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_judge.py
from __future__ import annotations

from datetime import datetime, timezone

from memory.judge import (
    AnchorJudgement,
    DedupJudgement,
    Judge,
    MetaJudgement,
    StubJudge,
    TierJudgement,
)
from memory.models import Channel, Observation, Provenance, Tier

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


def test_stub_returns_its_canned_anchor_answer():
    judge = StubJudge(anchors={"eat oats before gym": ["oats", "gym"]})
    result = await_sync(judge.anchors(_obs("eat oats before gym")))
    assert isinstance(result, AnchorJudgement)
    assert result.anchors == ["oats", "gym"]


def test_stub_returns_its_canned_tier_answer():
    judge = StubJudge(tiers={"eat oats before gym": Tier.DURABLE})
    result = await_sync(judge.tier(_obs("eat oats before gym")))
    assert isinstance(result, TierJudgement)
    assert result.tier is Tier.DURABLE


def test_stub_defaults_are_conservative():
    """An unstubbed question must not silently promote or suppress."""
    judge = StubJudge()
    assert await_sync(judge.anchors(_obs("anything"))).anchors == []
    assert await_sync(judge.tier(_obs("anything"))).tier is Tier.SESSION
    assert await_sync(judge.meta(_obs("anything"))).is_meta is False
    assert await_sync(judge.dedup(_obs("anything"), [])).duplicate_of is None


def test_stub_records_what_it_was_asked():
    """Ingest tests need to assert which questions were put to the model."""
    judge = StubJudge()
    obs = _obs("eat oats before gym")
    await_sync(judge.anchors(obs))
    await_sync(judge.meta(obs))
    assert judge.calls == [("anchors", obs.uid), ("meta", obs.uid)]


def test_stub_satisfies_the_protocol():
    assert isinstance(StubJudge(), Judge)


def await_sync(coro):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_judge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.judge'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/judge.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from memory.models import Observation, Tier


class AnchorJudgement(BaseModel):
    """The recurring kinds of thing an observation mentions.

    An observation carries n anchors, not one: a calendar block titled
    "hockey/running" is genuinely two.
    """

    anchors: list[str] = Field(default_factory=list)


class TierJudgement(BaseModel):
    """Whether this belongs in durable memory or dies with the session."""

    tier: Tier = Tier.SESSION
    is_declaration: bool = False
    rationale: str = ""


class MetaJudgement(BaseModel):
    """Whether this describes the interaction rather than the user's life."""

    is_meta: bool = False
    rationale: str = ""


class DedupJudgement(BaseModel):
    """Which earlier observation, if any, this one restates."""

    duplicate_of: str | None = None
    rationale: str = ""


@runtime_checkable
class Judge(Protocol):
    """The only way this package learns what an observation means.

    Four independent questions. Implementations must not answer any of them
    with pattern matching; see CLAUDE.md.
    """

    async def anchors(self, observation: Observation) -> AnchorJudgement: ...

    async def tier(self, observation: Observation) -> TierJudgement: ...

    async def meta(self, observation: Observation) -> MetaJudgement: ...

    async def dedup(
        self, observation: Observation, recent: list[Observation]
    ) -> DedupJudgement: ...


class StubJudge:
    """Canned answers for unit tests. Records what it was asked.

    Defaults are deliberately conservative: an unstubbed question never
    promotes to durable and never suppresses as meta, so a test that forgets
    to stub fails loudly rather than passing for the wrong reason.
    """

    def __init__(
        self,
        anchors: dict[str, list[str]] | None = None,
        tiers: dict[str, Tier] | None = None,
        metas: dict[str, bool] | None = None,
        duplicates: dict[str, str] | None = None,
    ) -> None:
        self._anchors = anchors or {}
        self._tiers = tiers or {}
        self._metas = metas or {}
        self._duplicates = duplicates or {}
        self.calls: list[tuple[str, str]] = []

    async def anchors(self, observation: Observation) -> AnchorJudgement:
        self.calls.append(("anchors", observation.uid))
        return AnchorJudgement(anchors=self._anchors.get(observation.text, []))

    async def tier(self, observation: Observation) -> TierJudgement:
        self.calls.append(("tier", observation.uid))
        return TierJudgement(
            tier=self._tiers.get(observation.text, Tier.SESSION)
        )

    async def meta(self, observation: Observation) -> MetaJudgement:
        self.calls.append(("meta", observation.uid))
        return MetaJudgement(is_meta=self._metas.get(observation.text, False))

    async def dedup(
        self, observation: Observation, recent: list[Observation]
    ) -> DedupJudgement:
        self.calls.append(("dedup", observation.uid))
        return DedupJudgement(
            duplicate_of=self._duplicates.get(observation.text)
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v`
Expected: 10 passed (5 new + 5 existing)

- [ ] **Step 5: Commit**

```bash
git add src/memory/judge.py tests/memory/test_judge.py
git commit -m "feat(memory): Judge port with four independent judgements and a test stub"
```

---

### Task 2: Concurrent ingest

The four judgements are independent, so they go out together. Sequential calls are the usual reason someone reaches back for a pattern to "save a round-trip"; concurrency is what makes the rule affordable.

**Files:**
- Create: `src/memory/ingest.py`
- Test: `tests/memory/test_ingest.py`

**Interfaces:**
- Consumes: `Judge`, the four judgement models, `ObservationStore`, `Observation`, `Tier`.
- Produces: `IngestResult` (`stored: bool`, `uid: str | None`, `tier: Tier`, `anchors: list[str]`, `suppressed_as: str | None`); `async ingest(observation, judge, store) -> IngestResult`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_ingest.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from memory.ingest import ingest
from memory.judge import StubJudge
from memory.models import Channel, Observation, Provenance, Tier
from memory.store import ObservationStore

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str, session_id: str = "s1") -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id=session_id,
        observed_at=T0,
    )


async def test_ingest_stores_and_applies_the_anchor_judgement(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge(anchors={"eat oats before gym": ["oats", "gym"]})
    result = await ingest(_obs("eat oats before gym"), judge, store)
    assert result.stored is True
    assert result.anchors == ["oats", "gym"]
    assert store.get(result.uid).anchors == ["oats", "gym"]


async def test_ingest_applies_the_tier_judgement(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge(tiers={"never work after 21:00": Tier.DURABLE})
    result = await ingest(_obs("never work after 21:00"), judge, store)
    assert result.tier is Tier.DURABLE


async def test_meta_level_observations_are_not_stored(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge(metas={"begin the timeboxing session now": True})
    result = await ingest(_obs("begin the timeboxing session now"), judge, store)
    assert result.stored is False
    assert result.suppressed_as == "meta"
    assert store.all() == []


async def test_duplicates_are_not_stored(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    first = _obs("wake at 07:00")
    store.append(first)
    judge = StubJudge(duplicates={"wake at 07:00": first.uid})
    result = await ingest(_obs("wake at 07:00"), judge, store)
    assert result.stored is False
    assert result.suppressed_as == "duplicate"
    assert len(store.all()) == 1


async def test_generated_provenance_is_never_judged_or_stored(tmp_path):
    """A rule's own output must not re-enter as evidence, and must not
    cost an LLM call to reject."""
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge()
    obs = Observation(
        text="pre-gym oats",
        channel=Channel.CALENDAR,
        provenance=Provenance.GENERATED,
        session_id="s1",
        observed_at=T0,
    )
    result = await ingest(obs, judge, store)
    assert result.stored is False
    assert result.suppressed_as == "generated"
    assert judge.calls == []


async def test_the_four_judgements_are_issued_concurrently(tmp_path):
    """Four sequential round-trips is the failure this guards against."""
    store = ObservationStore(str(tmp_path / "m.db"))

    class SlowJudge(StubJudge):
        async def anchors(self, observation):
            await asyncio.sleep(0.05)
            return await super().anchors(observation)

        async def tier(self, observation):
            await asyncio.sleep(0.05)
            return await super().tier(observation)

        async def meta(self, observation):
            await asyncio.sleep(0.05)
            return await super().meta(observation)

        async def dedup(self, observation, recent):
            await asyncio.sleep(0.05)
            return await super().dedup(observation, recent)

    loop = asyncio.get_event_loop()
    start = loop.time()
    await ingest(_obs("eat oats before gym"), SlowJudge(), store)
    elapsed = loop.time() - start
    assert elapsed < 0.15, f"judgements appear sequential: {elapsed:.3f}s"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.ingest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/ingest.py
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from memory.judge import Judge
from memory.models import Observation, Provenance, Tier
from memory.store import ObservationStore


class IngestResult(BaseModel):
    stored: bool
    uid: str | None = None
    tier: Tier = Tier.SESSION
    anchors: list[str] = Field(default_factory=list)
    suppressed_as: str | None = None


async def ingest(
    observation: Observation, judge: Judge, store: ObservationStore
) -> IngestResult:
    """Judge an observation and append it unless it should be suppressed.

    The four judgements are independent, so they are issued concurrently:
    one round-trip of latency rather than four. Nothing here inspects the
    observation's text — every decision about meaning comes from the judge.
    """
    if observation.provenance is not Provenance.OBSERVED:
        # A rule's own output must never re-enter as evidence, and rejecting
        # it costs no LLM call.
        return IngestResult(stored=False, suppressed_as="generated")

    recent = (
        store.by_session(observation.session_id) if observation.session_id else []
    )
    anchor_j, tier_j, meta_j, dedup_j = await asyncio.gather(
        judge.anchors(observation),
        judge.tier(observation),
        judge.meta(observation),
        judge.dedup(observation, recent),
    )

    if meta_j.is_meta:
        return IngestResult(stored=False, suppressed_as="meta")
    if dedup_j.duplicate_of is not None:
        return IngestResult(stored=False, suppressed_as="duplicate")

    observation.anchors = anchor_j.anchors
    uid = store.append(observation)
    return IngestResult(
        stored=True, uid=uid, tier=tier_j.tier, anchors=anchor_j.anchors
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v`
Expected: 16 passed (6 new + 10 existing)

- [ ] **Step 5: Commit**

```bash
git add src/memory/ingest.py tests/memory/test_ingest.py
git commit -m "feat(memory): concurrent ingest applying four independent judgements"
```

---

### Task 3: OpenRouter judge

The real model. Used for offline corpus work and eval tests; the MCP implementation in Task 4 covers the runtime agent path.

**Files:**
- Create: `src/memory/openrouter_judge.py`
- Test: `tests/memory/test_openrouter_judge.py`
- Test: `tests/memory/test_eval_extraction.py`

**Interfaces:**
- Consumes: `Judge` and its four result models, `Observation`.
- Produces: `OpenRouterJudge(api_key, base_url, model="google/gemini-3.6-flash", client=None)` satisfying `Judge`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_openrouter_judge.py
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from memory.judge import Judge
from memory.models import Channel, Observation, Provenance, Tier
from memory.openrouter_judge import OpenRouterJudge

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


def _mock(payload: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(payload)}}
                ]
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_satisfies_the_protocol():
    assert isinstance(
        OpenRouterJudge(api_key="k", base_url="https://example.invalid"), Judge
    )


async def test_anchors_parses_structured_output():
    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=_mock({"anchors": ["oats", "gym"]}),
    )
    result = await judge.anchors(_obs("eat oats two hours before gym"))
    assert result.anchors == ["oats", "gym"]


async def test_tier_parses_structured_output():
    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=_mock(
            {"tier": "durable", "is_declaration": True, "rationale": "policy"}
        ),
    )
    result = await judge.tier(_obs("never work after 21:00"))
    assert result.tier is Tier.DURABLE
    assert result.is_declaration is True


async def test_a_malformed_response_fails_loudly():
    """A silent fallback is how a wrong answer becomes permanent."""
    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=_mock({"unexpected": "shape"}),
    )
    import pytest

    with pytest.raises(ValueError, match="could not parse"):
        await judge.anchors(_obs("anything"))


async def test_request_carries_the_pinned_model_and_minimal_reasoning():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"anchors": []})}}]},
        )

    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await judge.anchors(_obs("anything"))
    assert captured["model"] == "google/gemini-3.6-flash"
    # "enabled": False is rejected by this endpoint; "minimal" is the floor.
    assert captured.get("reasoning", {}).get("effort") == "minimal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_openrouter_judge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.openrouter_judge'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/openrouter_judge.py
from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from memory.judge import (
    AnchorJudgement,
    DedupJudgement,
    MetaJudgement,
    TierJudgement,
)
from memory.models import Observation

ANCHOR_PROMPT = """\
You label the recurring kinds of thing a statement mentions.

An "anchor" names a kind of activity or entity that recurs in someone's life:
gym, hockey, commute, lunch, dinner, sleep, deep work. It is not a time, a
date, a duration, or a one-off proper noun.

Return every anchor the statement mentions. A statement can mention several,
or none. Prefer the general kind over the specific instance: "Hockey Game
(incl. warmup)" mentions hockey.

Respond with JSON only: {"anchors": ["...", "..."]}\
"""

TIER_PROMPT = """\
You decide whether a statement belongs in long-term memory.

"durable" means it will still be true next month: a standing preference,
rule, or fact about how this person lives. "session" means it is about today
only: a specific appointment, a one-off adjustment, today's plan.

Also say whether it is a declaration — a rule the person is stating outright
("I never take meetings before 13:00") rather than a fact you inferred from
what they happened to mention.

Respond with JSON only:
{"tier": "durable"|"session", "is_declaration": true|false, "rationale": "..."}\
"""

META_PROMPT = """\
You detect statements about the tool rather than about the person's life.

A statement is meta if it describes the planning conversation itself — how
the session should run, what format to use, that it should start now. It is
NOT meta merely because it mentions a session: "gym session at 18:00" is
about the person's life.

Respond with JSON only: {"is_meta": true|false, "rationale": "..."}\
"""

DEDUP_PROMPT = """\
You decide whether a new statement says the same thing as an earlier one.

Return the id of the earlier statement it duplicates, or null if it says
something new. Rewording, reordering, or adding detail to the same underlying
point counts as a duplicate. A different rule about the same topic does not.

Respond with JSON only: {"duplicate_of": "<id>"|null, "rationale": "..."}\
"""


class OpenRouterJudge:
    """Judge backed directly by OpenRouter.

    Used for offline corpus passes and eval tests, where no agent and so no
    MCP tool is present. The runtime agent path uses McpJudge instead.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "google/gemini-3.6-flash",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client or httpx.AsyncClient()

    async def _ask(self, system: str, user: str) -> dict[str, Any]:
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                # Extraction is term typing, not deliberation, so reasoning is
                # held to the floor. Note: this endpoint REJECTS
                # {"enabled": False} with "Reasoning is mandatory for this
                # endpoint and cannot be disabled" — verified against the live
                # API on 2026-08-16. "minimal" is the lowest accepted setting.
                "reasoning": {"effort": "minimal"},
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"could not parse judge response: {content!r}") from exc

    @staticmethod
    def _build(model_cls, payload: dict[str, Any]):
        try:
            return model_cls.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(
                f"could not parse judge response into {model_cls.__name__}: {payload!r}"
            ) from exc

    async def anchors(self, observation: Observation) -> AnchorJudgement:
        payload = await self._ask(ANCHOR_PROMPT, observation.text)
        if "anchors" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(AnchorJudgement, payload)

    async def tier(self, observation: Observation) -> TierJudgement:
        payload = await self._ask(TIER_PROMPT, observation.text)
        if "tier" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(TierJudgement, payload)

    async def meta(self, observation: Observation) -> MetaJudgement:
        payload = await self._ask(META_PROMPT, observation.text)
        if "is_meta" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(MetaJudgement, payload)

    async def dedup(
        self, observation: Observation, recent: list[Observation]
    ) -> DedupJudgement:
        if not recent:
            return DedupJudgement()
        candidates = "\n".join(f"{o.uid}: {o.text}" for o in recent)
        user = f"New statement:\n{observation.text}\n\nEarlier statements:\n{candidates}"
        payload = await self._ask(DEDUP_PROMPT, user)
        if "duplicate_of" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(DedupJudgement, payload)
```

- [ ] **Step 4: Write the eval test**

```python
# tests/memory/test_eval_extraction.py
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from memory.models import Channel, Observation, Provenance, Tier
from memory.openrouter_judge import OpenRouterJudge

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    ),
]

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _judge() -> OpenRouterJudge:
    return OpenRouterJudge(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
    )


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="eval",
        observed_at=T0,
    )


async def test_finds_gym_which_pattern_matching_scored_at_zero():
    """The measured failure of the discarded implementation."""
    result = await _judge().anchors(_obs("Eat oats 2 hours before going to the gym"))
    assert "gym" in [a.lower() for a in result.anchors]


async def test_a_real_preference_mentioning_a_session_is_not_meta():
    """The marker list would have suppressed this permanently."""
    result = await _judge().meta(_obs("Gym Session — user goes to the gym at 18:00"))
    assert result.is_meta is False


async def test_interaction_chatter_is_meta():
    result = await _judge().meta(
        _obs("The user wants to begin the timeboxing session immediately")
    )
    assert result.is_meta is True


async def test_a_standing_rule_is_durable_and_a_declaration():
    result = await _judge().tier(_obs("I never schedule meetings before 13:00"))
    assert result.tier is Tier.DURABLE
    assert result.is_declaration is True


async def test_todays_appointment_is_session_scoped():
    result = await _judge().tier(_obs("Hockey game today at 11:45 at VVV"))
    assert result.tier is Tier.SESSION
```

- [ ] **Step 5: Run both test files**

Run unit tests: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v -m "not slow"`
Expected: 21 passed (5 new + 16 existing)

Run evals with the key loaded from `.env`:
```bash
cd /Users/hugoevers/VScode-projects/admonish-1/.worktrees/memory-observation-log
set -a && . /Users/hugoevers/VScode-projects/admonish-1/.env && set +a
/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_eval_extraction.py -v -m slow
```
Expected: 5 passed. **If any eval fails, that is a prompt defect — report it, do not weaken the assertion.**

- [ ] **Step 6: Commit**

```bash
git add src/memory/openrouter_judge.py tests/memory/test_openrouter_judge.py tests/memory/test_eval_extraction.py
git commit -m "feat(memory): OpenRouter judge with prompts and eval tests"
```

---

### Task 4: Register the slow marker

`-m slow` needs the marker declared or pytest's `--strict-markers` rejects it.

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Check whether the marker already exists**

Run: `grep -n "slow" pyproject.toml`
The repo's `[tool.pytest.ini_options] markers` list already contains `slow: marks tests as slow (deselect with '-m "not slow"')`. If it is present, this task is already satisfied — record that and skip to the commit step with no change.

- [ ] **Step 2: Verify strict markers accept it**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -m "not slow" -q`
Expected: 21 passed, no marker warnings or errors.

- [ ] **Step 3: Commit only if a change was needed**

```bash
git add pyproject.toml && git commit -m "chore: declare slow marker for eval tests"
```

---

## Not in this plan

- **`McpJudge`** — the runtime agent binding. Same `Judge` protocol, backed by the shared MCP tool. Deferred until the tool's surface is settled with the timebox session, so it is written against something real rather than guessed.
- **`get_active_constraints`** — the read call the patcher consumes. Needs `McpJudge` and the promotion/anchor-recurrence layer first.
- **Promotion, proposals, decay, the gate** — all rebuilt on top of judge output in a following plan.
- **The corpus characterisation run** — extracting over all 1,662 historical rows with `google/gemini-3.6-flash:batch` to produce a measured recall figure comparable to the discarded baseline at tag `baseline/keyword-matching-discarded`.
