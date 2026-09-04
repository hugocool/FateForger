# src/memory/judge.py
from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from memory.models import DecayClass, Observation, Tier


class AnchorJudgement(BaseModel):
    """The recurring kinds of thing an observation mentions.

    An observation carries n anchors, not one: a calendar block titled
    "hockey/running" is genuinely two.
    """

    anchors: list[str] = Field(default_factory=list)


class TierJudgement(BaseModel):
    """Whether this belongs in durable memory or dies with the session."""

    tier: Tier = Tier.SESSION
    label: str
    rationale: str = ""

    # Applicability rides along on this judgement rather than getting its own
    # call: deciding durability already requires reading the whole statement,
    # so the scoping words are in front of the model anyway. Raw fields rather
    # than the Applicability value object, so this port stays free of any
    # import from the constraint layer above it.
    start_date: date | None = None
    end_date: date | None = None
    days_of_week: list[int] = Field(default_factory=list)  # 0=Mon .. 6=Sun

    # Seed vocabulary — see DecayClass. Default PERMANENT because the safe
    # failure is a rule that never fades, not one that vanishes unasked.
    decay_class: DecayClass = DecayClass.PERMANENT


class AnchorResolution(BaseModel):
    """One extracted anchor name, mapped to an anchor that already exists."""

    name: str
    anchor_uid: str | None = None


class AnchorResolutions(BaseModel):
    """Every anchor name in one observation, resolved together.

    Deliberately one question rather than one per name, which is the exception
    to the parallelise rule and needs its reason stated: the names are not
    independent. "gym" and "the gym" appearing in one observation is a single
    anchor, and asking about each alone against the same snapshot of existing
    anchors would have both answered "new" and mint two.
    """

    resolutions: list[AnchorResolution] = Field(default_factory=list)


class NecessityJudgement(BaseModel):
    """Whether breaking this rule ruins the day or merely worsens it.

    A bool rather than the Necessity enum, so this port stays free of any
    import from the constraint layer above it — the same reason applicability
    rides as raw fields on TierJudgement.
    """

    is_binding: bool = False
    rationale: str = ""


class MetaJudgement(BaseModel):
    """Whether this describes the interaction rather than the user's life."""

    is_meta: bool = False
    rationale: str = ""


class DedupJudgement(BaseModel):
    """Which earlier observation, if any, this one restates."""

    duplicate_of: str | None = None
    rationale: str = ""


class CanonicaliseJudgement(BaseModel):
    """Which existing constraint, if any, a new observation expresses."""

    constraint_uid: str | None = None
    rationale: str = ""


class DayJudgement(BaseModel):
    """What kind of day this is, read off the calendar.

    `day_type` is drawn from a system-minted vocabulary, so comparing it at
    read time is set membership rather than a judgement about meaning. The
    judgement is here, once, before the read path — which is the same
    position anchor resolution occupies and for the same reason.
    """

    day_type: str = "working"
    rationale: str = ""


class RequiresBlockJudgement(BaseModel):
    """Which registered kind of block, if any, this statement says must be on
    the day (#212).

    `slug` is one of the kinds the caller offered or None -- a closed choice
    over identifiers this system minted, verified by the transport before it
    is returned. Deciding that "end-of-day closure block" is the `planning`
    kind is a judgement about meaning and stays with the model; deciding that
    the answer is *one of the offered words* is set membership and stays here.
    """

    slug: str | None = None
    rationale: str = ""


@runtime_checkable
class AnchorLike(Protocol):
    """The shape resolve_anchors needs from an existing anchor.

    Structural rather than importing Anchor, so the port stays free of
    knowledge about the layer above it.
    """

    uid: str
    name: str


@runtime_checkable
class ConstraintLike(Protocol):
    """The shape canonicalise needs from a candidate.

    Structural rather than importing Constraint, so the port stays free of
    knowledge about the layer above it.
    """

    uid: str
    name: str
    description: str


@runtime_checkable
class Judge(Protocol):
    """The only way this package learns what an observation means.

    Six independent questions. Implementations must not answer any of them
    with pattern matching; see CLAUDE.md.
    """

    async def anchors(self, observation: Observation) -> AnchorJudgement: ...

    async def tier(self, observation: Observation) -> TierJudgement: ...

    async def necessity(self, observation: Observation) -> NecessityJudgement: ...

    async def requires_block(
        self, observation: Observation, kinds: list[str]
    ) -> RequiresBlockJudgement: ...

    async def resolve_anchors(
        self, names: list[str], candidates: list[AnchorLike]
    ) -> AnchorResolutions: ...

    async def classify_day(self, events: list[str]) -> DayJudgement: ...

    async def meta(self, observation: Observation) -> MetaJudgement: ...

    async def dedup(
        self, observation: Observation, recent: list[Observation]
    ) -> DedupJudgement: ...

    async def resolve_anchors(
        self, names: list[str], candidates: list[AnchorLike]
    ) -> AnchorResolutions:
        self.calls.append(("resolve_anchors", ",".join(names)))
        return AnchorResolutions(
            resolutions=[
                AnchorResolution(name=n, anchor_uid=self._anchor_uids.get(n))
                for n in names
            ]
        )

    async def canonicalise(
        self, observation: Observation, candidates: list[ConstraintLike]
    ) -> CanonicaliseJudgement: ...


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
        canonical: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        days_of_week: dict[str, list[int]] | None = None,
        start_dates: dict[str, date] | None = None,
        end_dates: dict[str, date] | None = None,
        decay_classes: dict[str, DecayClass] | None = None,
        bindings: dict[str, bool] | None = None,
        anchor_uids: dict[str, str] | None = None,
        day_type: str = "working",
        requires_blocks: dict[str, str] | None = None,
    ) -> None:
        self._anchors = anchors or {}
        self._tiers = tiers or {}
        self._metas = metas or {}
        self._duplicates = duplicates or {}
        self._canonical = canonical or {}
        self._labels = labels or {}
        self._days_of_week = days_of_week or {}
        self._start_dates = start_dates or {}
        self._end_dates = end_dates or {}
        self._decay_classes = decay_classes or {}
        self._bindings = bindings or {}
        self._anchor_uids = anchor_uids or {}
        self._day_type = day_type
        self._requires_blocks = requires_blocks or {}
        self.calls: list[tuple[str, str]] = []

    async def anchors(self, observation: Observation) -> AnchorJudgement:
        self.calls.append(("anchors", observation.uid))
        return AnchorJudgement(anchors=self._anchors.get(observation.text, []))

    async def tier(self, observation: Observation) -> TierJudgement:
        self.calls.append(("tier", observation.uid))
        return TierJudgement(
            tier=self._tiers.get(observation.text, Tier.SESSION),
            label=self._labels.get(observation.text, observation.text),
            start_date=self._start_dates.get(observation.text),
            end_date=self._end_dates.get(observation.text),
            days_of_week=self._days_of_week.get(observation.text, []),
            decay_class=self._decay_classes.get(
                observation.text, DecayClass.PERMANENT
            ),
        )

    async def necessity(self, observation: Observation) -> NecessityJudgement:
        self.calls.append(("necessity", observation.uid))
        return NecessityJudgement(
            is_binding=self._bindings.get(observation.text, False)
        )

    async def requires_block(
        self, observation: Observation, kinds: list[str]
    ) -> RequiresBlockJudgement:
        self.calls.append(("requires_block", observation.uid))
        slug = self._requires_blocks.get(observation.text)
        return RequiresBlockJudgement(slug=slug if slug in kinds else None)

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

    async def resolve_anchors(
        self, names: list[str], candidates: list[AnchorLike]
    ) -> AnchorResolutions:
        self.calls.append(("resolve_anchors", ",".join(names)))
        return AnchorResolutions(
            resolutions=[
                AnchorResolution(name=n, anchor_uid=self._anchor_uids.get(n))
                for n in names
            ]
        )

    async def classify_day(self, events: list[str]) -> DayJudgement:
        self.calls.append(("classify_day", ",".join(events)))
        return DayJudgement(day_type=self._day_type)

    async def canonicalise(
        self, observation: Observation, candidates: list[ConstraintLike]
    ) -> CanonicaliseJudgement:
        self.calls.append(("canonicalise", observation.uid))
        return CanonicaliseJudgement(
            constraint_uid=self._canonical.get(observation.text)
        )
