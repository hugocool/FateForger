"""Standing things, offered as options to one judgement.

Nothing in this package imports Slack. A provider takes an owner and a clock;
the catalog names what comes back; the resolver picks one, or none, or says it
cannot tell. It never says what to *do* -- that is the consumer's own judgement.
"""

from .catalog import Catalog, build_catalog
from .descriptor import GIST_LIMIT, Referent, StandingThing
from .provider import ReferentProvider
from .resolver import (
    RESOLVER_PROMPT,
    Ambiguous,
    NoReferent,
    ReferentResolutionError,
    ReferentResolver,
    Resolution,
    Resolved,
)
from .timeboxing import TimeboxingReferentProvider

__all__ = [
    "GIST_LIMIT",
    "RESOLVER_PROMPT",
    "Ambiguous",
    "Catalog",
    "NoReferent",
    "Referent",
    "ReferentProvider",
    "ReferentResolutionError",
    "ReferentResolver",
    "Resolution",
    "Resolved",
    "StandingThing",
    "TimeboxingReferentProvider",
    "build_catalog",
]
