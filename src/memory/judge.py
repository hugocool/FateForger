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
