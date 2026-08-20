# src/memory/service.py
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from memory.constraint import ConstraintView
from memory.anchor_store import AnchorStore
from memory.anchoring import resolve_anchors
from memory.constraint_store import ConstraintStore
from memory.ingest import ingest
from memory.judge import Judge
from memory.models import Channel, Observation, Provenance, Tier
from memory.projection import project
from memory.read_api import get_active_constraints as _read
from memory.read_api import get_faded_constraints as _read_faded
from memory.reprojection import ReprojectionReport, reproject
from memory.store import ObservationStore


class ObserveOutcome(BaseModel):
    """What happened to one statement, in terms a host can display."""

    stored: bool
    suppressed_as: str | None = None
    constraint_uid: str | None = None
    constraint_name: str | None = None
    tier: Tier | None = None


class MemoryService:
    """One session-shaped entry point over the whole pipeline.

    Owns both stores on a single sqlite file (the schemas do not collide) and
    a judge. `observe` is the write path: ingest, then — only if the
    observation was stored — projection into a constraint. The read path
    delegates to `read_api` and stays synchronous: no judge, no await.
    """

    def __init__(self, db_path: str, judge: Judge) -> None:
        self._observations = ObservationStore(db_path)
        self._constraints = ConstraintStore(db_path)
        self._anchors = AnchorStore(db_path)
        self._judge = judge

    async def observe(
        self,
        text: str,
        *,
        channel: Channel,
        session_id: str | None,
        observed_at: datetime,
        provenance: Provenance = Provenance.OBSERVED,
    ) -> ObserveOutcome:
        observation = Observation(
            text=text,
            channel=channel,
            provenance=provenance,
            session_id=session_id,
            observed_at=observed_at,
        )
        result = await ingest(observation, self._judge, self._observations)
        if not result.stored:
            return ObserveOutcome(stored=False, suppressed_as=result.suppressed_as)
        constraint = await project(
            observation, result, self._judge, self._constraints, self._anchors
        )
        return ObserveOutcome(
            stored=True,
            constraint_uid=constraint.uid,
            constraint_name=constraint.name,
            tier=constraint.tier,
        )

    async def reproject(self, uid: str | None = None) -> ReprojectionReport:
        """Re-derive constraints from the observations that produced them.

        The entry point invariant I4 needs: without it, a judgement
        improvement reaches only constraints created after it shipped, so a
        store stays frozen at the taxonomy of the run that made it.

        Explicit rather than automatic. It re-asks the model once per
        observation in the store, and it rewrites derived state in place —
        neither belongs in a request path, and the report it returns is the
        only record of what moved.
        """
        return await reproject(
            self._observations, self._constraints, self._judge, uid=uid
        )

    async def resolve_anchor_names(self, names: list[str]) -> list[str]:
        """Anchor uids for names the caller pulled off a calendar or a plan.

        Separate from the read path on purpose. Deciding that "Hockey
        practice" is the `hockey` anchor is a judgement about meaning, so it
        needs a model — and the read path must not have one, because callers
        hold it inside a planning loop and the same day read twice would
        otherwise answer differently.

        So a host calls this once when it knows the day's events, then reads
        as many times as it likes with the uids it got back.
        """
        return await resolve_anchors(names, self._anchors, self._judge)

    def get_active_constraints(
        self,
        day: date,
        stage: str | None = None,
        anchor_uids: list[str] | None = None,
    ) -> list[ConstraintView]:
        """Rules applying on `day`, optionally narrowed to the day's anchors.

        With `anchor_uids`, the taxonomy is walked from those anchors and only
        rules they reach are returned — plus every unanchored rule, which is
        about the shape of the day rather than a thing in it. Still no model:
        the walk is a graph traversal and the filter is set membership over
        uids this system minted.
        """
        reachable = None
        if anchor_uids:
            reachable = self._anchors.constraints_reachable_from(anchor_uids)
            reachable |= {
                c.uid
                for c in self._constraints.durable()
                if not self._anchors.anchors_for(c.uid)
            }
        return _read(self._constraints, day, stage, reachable=reachable)

    def get_faded_constraints(
        self, day: date, stage: str | None = None
    ) -> list[ConstraintView]:
        return _read_faded(self._constraints, day, stage)
