"""What one standing thing looks like to the judge.

Every field here is minted by this system -- a session key, a status, a date, a
block title the planner wrote. Nothing is the user's prose, which is why the
whole descriptor may be handed to a model without any of it being *matched*.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

#: How many gist entries reach the model. A cap, not a ranking: the provider
#: supplies the plan's own order and the tail is dropped, because a twenty-block
#: day would otherwise crowd out the other candidates.
GIST_LIMIT = 12


class StandingThing(BaseModel):
    """One thing a provider says is standing, before the host names it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    agent_type: str
    kind: str
    day: date | None
    status: str
    never_used: bool
    last_activity: datetime
    accepts: tuple[str, ...]
    #: The plan's own block titles WITH their times. Half the resolving power is
    #: the clock: a title alone cannot answer "push the investor call prep
    #: later". Present to be READ by a model and never matched, sorted or
    #: filtered on by code -- `test_referents_never_match_gist` guards that.
    gist: tuple[str, ...] = ()
    #: Opaque locators the provider copies rather than interprets. A provider
    #: whose things live elsewhere leaves them None and loses only the link and
    #: `is_current_surface`.
    channel_id: str | None = None
    thread_ts: str | None = None


class Referent(StandingThing):
    """A standing thing the catalog has named, as the judge sees it."""

    #: Host-minted. Providers never set this; `build_catalog` does, so identity
    #: stays with the host exactly as on every other surface in this repo.
    ref_id: str
    #: This candidate IS the surface the message arrived in. Structural -- a
    #: thread id compared to a thread id -- and it exists because "cancel THAT
    #: session" points away from where the speaker is.
    is_current_surface: bool = False

    def describe(self, now: datetime) -> dict:
        """The candidate as JSON for the prompt. Reproducible from `now`."""
        hours = round((now - self.last_activity).total_seconds() / 3600, 1)
        described: dict = {
            "ref_id": self.ref_id,
            "kind": self.kind,
            "day": (
                f"{self.day.isoformat()} ({self.day.strftime('%A')})"
                if self.day is not None
                else "no day locked yet"
            ),
            "status": self.status,
            "opened_automatically_never_used": self.never_used,
            "last_activity": f"{hours}h ago",
            "accepts": list(self.accepts),
            "is_the_conversation_this_message_arrived_in": self.is_current_surface,
        }
        if self.gist:
            described["plan_contains"] = list(self.gist[:GIST_LIMIT])
        return described
