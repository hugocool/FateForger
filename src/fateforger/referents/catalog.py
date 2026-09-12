"""Gather every provider, name what comes back, say whether it is whole."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from fateforger.core.logging_config import record_error

from .descriptor import Referent, StandingThing
from .provider import ReferentProvider

logger = logging.getLogger(__name__)


class Catalog(BaseModel):
    """What stands, named, plus whether anyone failed to answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    referents: tuple[Referent, ...] = ()
    #: False when a provider raised. A `none` drawn from a partial catalog is
    #: not evidence that nothing stands, so a door that can create must ask
    #: rather than create when this is False.
    complete: bool = True


async def build_catalog(
    providers: Sequence[ReferentProvider],
    *,
    owner_user_id: str,
    as_of: datetime,
    current_thread: tuple[str, str] | None = None,
) -> Catalog:
    """Ask every provider concurrently and name the results `r1`, `r2`, ...

    Ids are minted here and never by a provider, so identity stays with the
    host. `current_thread` is `(channel_id, thread_ts)` for the conversation the
    message arrived in, compared as identifiers this system minted.
    """
    results = await asyncio.gather(
        *(p.standing(owner_user_id=owner_user_id, as_of=as_of) for p in providers),
        return_exceptions=True,
    )

    referents: list[Referent] = []
    complete = True
    for provider, result in zip(providers, results):
        if isinstance(result, BaseException):
            complete = False
            logger.exception(
                "referent provider %s failed for %s",
                provider.agent_type,
                owner_user_id,
                exc_info=result,
            )
            record_error(component="referent_catalog", error_type="provider_failure")
            continue
        for thing in result:
            referents.append(
                Referent(
                    **thing.model_dump(),
                    ref_id=f"r{len(referents) + 1}",
                    is_current_surface=(
                        current_thread is not None
                        and thing.channel_id == current_thread[0]
                        and thing.thread_ts == current_thread[1]
                    ),
                )
            )
    return Catalog(referents=tuple(referents), complete=complete)
