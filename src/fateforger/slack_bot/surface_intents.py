"""One controls-aware interpreter for every Slack proposal surface.

A surface (the planning card, a timeboxing stage card) shows the user a few
controls the host minted. A typed reply is read against exactly those: the
model names a decision the surface allows, or one of the offered option ids,
and nothing else. Deciding *which* option somebody meant is the model's job;
what the rule bans is comparing their words to the labels ourselves.

Nothing in here knows what a draft or a planning session is. The surface
supplies its own schema (extra fields the reply may carry), its own prompt
fragment, and binds the returned decision to its own typed press.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from typing import Annotated, Literal, TypeVar, get_args

from autogen_core.models import ChatCompletionClient, SystemMessage, UserMessage
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    create_model,
)

from fateforger.agents.timeboxing.session_contracts import BlockerOption
from fateforger.core.llm_attribution import llm_attribution

T = TypeVar("T", bound=BaseModel)

#: The decision every surface gets for free when it offers options.
CHOOSE_OPTION = "choose_option"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _clock(value: str) -> str:
    """Normalise a clock time the model wrote to ``HH:MM``.

    Format parsing of the model's own output, not a reading of the user's
    words: the schema asked for a 24-hour clock and "8:30" or "08:30:00Z" is
    one. Any offset is dropped -- the surface's timezone is the contract.
    """

    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value, "%H:%M").time()
    return parsed.replace(tzinfo=None).isoformat(timespec="minutes")


#: A string, not ``datetime.time``: AutoGen ``json.dumps`` the parsed
#: completion for its LLMCallEvent, and a non-JSON leaf breaks every log
#: formatter that sees the call (8/8 draws on 2026-09-02).
Clock = Annotated[
    str,
    AfterValidator(_clock),
    Field(description="24-hour clock time on the surface's day, HH:MM"),
]


class SurfaceView(_StrictModel):
    """What one surface is showing, as the interpreter needs it.

    Built by the surface from durable state -- never from the card on screen,
    which the user may have scrolled back to after the state moved on.
    """

    surface_kind: str = Field(min_length=1)
    display_state: str = Field(min_length=1)
    allowed_decisions: tuple[str, ...]
    offered_options: tuple[BlockerOption, ...] = ()
    open_question: dict[str, str] | None = None
    #: Surface-specific facts merged flat into the prompt (the timeboxing
    #: surface passes the proposed day here).
    context: dict[str, object] = Field(default_factory=dict)


def narrow_schema(base: type[T], options: tuple[BlockerOption, ...]) -> type[T]:
    """Narrow one turn's schema to exactly the answers that were offered.

    Where nothing was offered there is nothing to choose, and the base schema
    cannot express a choice at all.
    """

    if not options:
        return base
    decisions = (
        CHOOSE_OPTION,
        *get_args(base.model_fields["decision"].annotation),
    )
    return create_model(  # type: ignore[call-overload]
        f"{base.__name__}WithOptions",
        __base__=base,
        decision=(Literal[decisions], ...),
        option_id=(
            Literal[tuple(option.option_id for option in options)] | None,
            None,
        ),
    )


GENERIC_PREAMBLE = """You interpret one user reply against a proposal the assistant is showing.
Return only the requested schema.
Choose only a decision listed in allowed_decisions.
When offered_options is present and the user picked one of them, answer with
that option's option_id exactly as given. Accepting, confirming, or agreeing
with the proposal as shown is picking its primary option.
Never invent identifiers; the host owns identity.
"""


class SurfaceIntentInterpreter:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def interpret(
        self,
        *,
        view: SurfaceView,
        user_text: str,
        schema: type[T],
        prompt_fragment: str,
        attribution: tuple[str, str, str],
    ) -> T:
        if not view.allowed_decisions:
            raise ValueError(f"the {view.surface_kind} does not accept another intent")
        narrowed = narrow_schema(schema, view.offered_options)
        allowed = tuple(view.allowed_decisions)
        if view.offered_options and not any(
            item == CHOOSE_OPTION for item in allowed
        ):
            allowed = (*allowed, CHOOSE_OPTION)
        payload: dict[str, object] = {
            "surface": view.surface_kind,
            "display_state": view.display_state,
            "allowed_decisions": list(allowed),
            # The labels and effects are the context the choice needs. An
            # id on its own would ask the model to pick between two names
            # it has never seen.
            "offered_options": [
                {
                    "option_id": option.option_id,
                    "label": option.label,
                    "effect": option.effect,
                }
                for option in view.offered_options
            ],
            "open_question": view.open_question,
            "user_text": user_text,
        }
        payload.update(view.context)
        prompt = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        agent, call_label, key = attribution
        # Awaited straight from the Slack listener, not dispatched through the
        # AutoGen runtime, so without a name the tokens land under "unknown".
        with llm_attribution(agent=agent, call_label=call_label, key=key):
            result = await self.model_client.create(
                [
                    SystemMessage(content=GENERIC_PREAMBLE + prompt_fragment),
                    UserMessage(content=prompt, source="user"),
                ],
                json_output=narrowed,
            )
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            raise ValueError("intent model returned no schema-bound JSON content")
        interpreted = narrowed.model_validate_json(content)
        if not any(interpreted.decision == item for item in allowed):
            raise ValueError(
                f"decision {interpreted.decision!r} is not allowed in "
                f"{view.display_state}"
            )
        return interpreted


__all__ = [
    "CHOOSE_OPTION",
    "Clock",
    "GENERIC_PREAMBLE",
    "SurfaceIntentInterpreter",
    "SurfaceView",
    "narrow_schema",
]
