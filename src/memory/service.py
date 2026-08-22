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
from memory.read_api import get_session_constraints as _read_session
from memory.read_api import get_suspended_constraints as _read_suspended
from memory.reprojection import ReprojectionReport, reproject, split
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
        write_uid: str | None = None,
    ) -> ObserveOutcome:
        """Record one statement. Safe to retry when `write_uid` is supplied.

        Without `write_uid` the identity of the write is minted here, so a
        caller retrying the same statement produces a second observation that
        is indistinguishable from the user having said it twice — and L1 is
        append-only, so it is permanent. Since evidence is what promotion and
        decay count, a retry loop does not merely add noise: it inflates
        support for whatever failed most often.

        Passing a stable `write_uid` across retries makes this a no-op on the
        second attempt. It also repairs the case that motivated it: if a
        previous attempt appended and then failed during projection, the
        observation is already stored with no constraint linked, and the retry
        adopts that orphan rather than adding another.
        """
        observation = Observation(
            text=text,
            channel=channel,
            provenance=provenance,
            session_id=session_id,
            observed_at=observed_at,
            **({"uid": write_uid} if write_uid else {}),
        )

        if write_uid is not None:
            existing = self._observations.get(write_uid)
            if existing is not None:
                linked = self._constraints.constraint_for_observation(write_uid)
                if linked is not None:
                    constraint = self._constraints.get(linked)
                    return ObserveOutcome(
                        stored=True,
                        constraint_uid=constraint.uid,
                        constraint_name=constraint.name,
                        tier=constraint.tier,
                    )
                # Stored but never projected — the orphan a prior failure left
                # behind. Fall through and finish the projection rather than
                # appending a duplicate of a row that is already here.
                observation = existing

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

    async def reproject(
        self, uid: str | None = None, apply: bool = False
    ) -> ReprojectionReport:
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
            self._observations, self._constraints, self._judge, uid=uid, apply=apply
        )

    async def classify_day(self, events: list[str]) -> str:
        """What kind of day this is, from what is on the calendar.

        Separate from the read path on purpose, and for the same reason
        `resolve_anchor_names` is: deciding that "Vakantie Toscane" means the
        user is on holiday is a judgement about meaning, so it needs a model —
        and the read path must not have one. The caller classifies once, then
        reads structurally as many times as it likes.

        Weekday was standing in for this and is not equal to it. A rule scoped
        Mon-Fri fires on a Tuesday spent on holiday; measured on the real
        store, a vacation Friday returned 30 constraints including commute
        duration and deep-work entry gates.
        """
        return (await self._judge.classify_day(events)).day_type

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

    async def split_constraint(
        self, uid: str, observation_uids: list[str]
    ) -> tuple[str, str]:
        """Separate observations that were wrongly folded into one constraint.

        The counterpart to a merge, which the store could do and could not
        undo. L1 keeps every observation, so the evidence to split was always
        present — what was missing is that the partition of observations into
        constraints is derived state nothing re-derives.

        Mechanical: you name which observations leave, and nothing here judges
        whether they should. Both halves are re-projected from the evidence
        they end up holding, and the original keeps its uid.
        """
        return await split(
            self._observations,
            self._constraints,
            self._judge,
            uid=uid,
            observation_uids=observation_uids,
            anchor_store=self._anchors,
        )

    def get_active_constraints(
        self,
        day: date,
        stage: str | None = None,
        anchor_uids: list[str] | None = None,
        day_type: str | None = None,
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
        return _read(self._constraints, day, stage, reachable=reachable,
            day_type=day_type,
        )

    def get_faded_constraints(
        self, day: date, stage: str | None = None
    ) -> list[ConstraintView]:
        return _read_faded(self._constraints, day, stage)

    def get_session_constraints(
        self, session_id: str, day: date | None = None
    ) -> list[ConstraintView]:
        """What this conversation has established so far.

        The session tier is the structured form of the chat history: it is how
        a planning conversation keeps what the user said several replies ago.
        Nothing could read it back until now — every read filtered to durable —
        so the tier was write-only and the user restating themselves between
        turns was the visible symptom.
        """
        return _read_session(self._constraints, session_id, day)

    def get_suspended_constraints(
        self, day: date, day_type: str | None = None
    ) -> list[ConstraintView]:
        """Rules that are true and deliberately not in force on `day`.

        Separate from faded because the two states have different remedies:
        faded asks *is this still true?*, suspended asserts *this is true, and
        not today*. A planner on a vacation day should be able to say that 21
        working-day rules are suspended rather than behave as though the user
        never had them.
        """
        return _read_suspended(self._constraints, day, day_type=day_type)
