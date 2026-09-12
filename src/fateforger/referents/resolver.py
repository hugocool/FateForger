"""One judgement: which standing thing, if any, is this message about.

It never says what to *do* with the answer. That is a second judgement, run by
whichever consumer holds the state's own allowed decisions -- and keeping them
apart is what stops a resolution becoming a write with no human in between.
The enforcement is the return type: no outcome here has a field for an action.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from autogen_core.models import ChatCompletionClient, SystemMessage, UserMessage
from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from fateforger.core.llm_attribution import llm_attribution

from .catalog import Catalog
from .descriptor import Referent


class ReferentResolutionError(RuntimeError, ValueError):
    """Reading one message against one catalog failed.

    Also a ValueError because the schema violations it wraps already were one.
    """


class _Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: False when a provider failed. Carried on every outcome because a `none`
    #: -- and equally a confident `Resolved` -- drawn from a partial catalog is
    #: weaker evidence than one drawn from a whole one.
    catalog_complete: bool = True


class Resolved(_Outcome):
    referent: Referent


class Ambiguous(_Outcome):
    candidates: tuple[Referent, ...]


class NoReferent(_Outcome):
    pass


Resolution = Resolved | Ambiguous | NoReferent


#: The wording is measured. It moved 74/112 -> 95/112 draws on the frozen
#: fixture, entirely on the last sentence, which is the distinction between
#: continuing a day's plan and wanting something new scheduled. Changing this
#: text means re-running tests/evals/test_eval_referent_resolver.py.
RESOLVER_PROMPT = """You route one message a user just typed to a scheduling assistant.
The user has some *standing things*: plans for a particular day that already exist and can be continued.
Decide which standing thing, if any, this message is about.

A message is about a standing thing when it continues, changes, questions, or ends THAT day's plan --
including a question about what that plan says.
A message is about NONE of them when it asks for something new that no listed plan covers: a fact,
an errand, a reminder, or planning a day that is not listed. Wanting something scheduled is not the
same as continuing an existing plan for a day.
Choose "ambiguous" only when the message is clearly about one of the listed plans but two or more fit equally.

Judge by meaning. Never invent identifiers. Return only JSON.
Answer with {"decision": "<ref_id>" | "none" | "ambiguous", "why": "<one short sentence>"}."""


class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    why: str = ""


def _narrowed(catalog: Catalog) -> type[_Answer]:
    """Offer the model exactly the ids the host minted, and nothing else."""
    ids = tuple(r.ref_id for r in catalog.referents)
    return create_model(  # type: ignore[call-overload]
        "_NarrowedAnswer",
        __base__=_Answer,
        decision=(Literal[(*ids, "none", "ambiguous")], ...),
    )


class ReferentResolver:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self._model_client = model_client

    async def resolve(
        self, *, catalog: Catalog, message: str, as_of: datetime
    ) -> Resolution:
        if not catalog.referents:
            # Nothing to choose between. Asking anyway would spend a round trip
            # to be told what the query already said.
            return NoReferent(catalog_complete=catalog.complete)

        payload = {
            "now": as_of.strftime("%Y-%m-%d %H:%M (%A)"),
            "standing_things": [r.describe(as_of) for r in catalog.referents],
            "message": message,
        }
        schema = _narrowed(catalog)
        try:
            with llm_attribution(
                agent="referent_resolver", call_label="resolve", key="referents"
            ):
                result = await self._model_client.create(
                    [
                        SystemMessage(content=RESOLVER_PROMPT),
                        UserMessage(
                            content=json.dumps(payload, ensure_ascii=False),
                            source="user",
                        ),
                    ],
                    json_output=schema,
                )
            content = getattr(result, "content", None)
            if not isinstance(content, str):
                raise ReferentResolutionError(
                    "the resolver model returned no schema-bound JSON content"
                )
            answer = schema.model_validate_json(content)
        except ReferentResolutionError:
            raise
        except ValidationError as exc:
            raise ReferentResolutionError(
                f"the resolver model answered outside its schema: {exc}"
            ) from exc

        if answer.decision == "none":
            return NoReferent(catalog_complete=catalog.complete)
        if answer.decision == "ambiguous":
            return Ambiguous(
                catalog_complete=catalog.complete, candidates=catalog.referents
            )
        chosen = next(
            (r for r in catalog.referents if r.ref_id == answer.decision), None
        )
        if chosen is None:  # pragma: no cover - the schema should prevent it
            raise ReferentResolutionError(
                f"the resolver named an id the host did not mint: {answer.decision!r}"
            )
        return Resolved(catalog_complete=catalog.complete, referent=chosen)
