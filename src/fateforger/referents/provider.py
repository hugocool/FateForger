"""The whole seam: an owner, a clock, and what stands.

One method. No Slack event, no channel, no focus manager, no route in scope --
which is what lets the routing rung, a door that creates sessions, and the eval
all be consumers of the same catalog.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from .descriptor import StandingThing


@runtime_checkable
class ReferentProvider(Protocol):
    """Something that knows what one user currently has standing."""

    #: Whose things these are. Travels onto every descriptor.
    agent_type: str

    async def standing(
        self, *, owner_user_id: str, as_of: datetime
    ) -> Sequence[StandingThing]:
        """What stands for this owner AT `as_of`.

        `as_of` is explicit and required rather than read from a clock inside.
        The store keeps no history, so a descriptor otherwise reads *current*
        state while claiming to describe an earlier moment -- which is how two
        runs of one spike drew different candidate sets forty minutes apart.
        """
        ...
