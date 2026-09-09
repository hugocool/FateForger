"""Standing things, offered as options to one judgement.

Nothing in this package imports Slack. A provider takes an owner and a clock;
the catalog names what comes back; the resolver picks one, or none, or says it
cannot tell. It never says what to *do* -- that is the consumer's own judgement.
"""

from .catalog import Catalog, build_catalog
from .descriptor import GIST_LIMIT, Referent, StandingThing
from .provider import ReferentProvider

__all__ = [
    "GIST_LIMIT",
    "Catalog",
    "Referent",
    "ReferentProvider",
    "StandingThing",
    "build_catalog",
]
