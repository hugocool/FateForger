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
    ValidationError,
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


#: Which fields each decision can fill. Keys are decision names this system
#: minted, not user content -- the pattern rule exempts identifiers we own.
#: A field claimed by no allowed decision is one the model must still emit as
#: null on every call, because strict structured output requires every
#: property. Measured 2026-09-06: 42% of answers were a decision and six nulls.
_FIELDS_BY_DECISION: dict[str, frozenset[str]] = {
    "provide_facts": frozenset({"facts"}),
    "revise": frozenset({"facts", "revision_instruction"}),
    "confirm_planning_day": frozenset({"day_type", "day_offset"}),
    # `facts` here is inert, not needed: the binder builds the suspension fact
    # itself and never reads `interpreted.facts`. It costs nothing because
    # steer_not_today is only ever offered where provide_facts already is.
    "steer_not_today": frozenset({"facts", "constraint_uid"}),
    "restore": frozenset({"constraint_uid"}),
    "deny": frozenset({"assumption_id"}),
    "update_time": frozenset({"selected_time"}),
    "update_time_and_add": frozenset({"selected_time"}),
    CHOOSE_OPTION: frozenset({"option_id"}),
}


#: Everything a ``create_model`` rebuild cannot carry across. Fields and config
#: travel; decorated behaviour does not, because the rebuild has no ``__base__``
#: to inherit it from.
_UNCARRIED_DECORATORS = (
    "validators",
    "field_validators",
    "root_validators",
    "model_validators",
    "field_serializers",
    "model_serializers",
    "computed_fields",
)


def _refuse_to_drop_behaviour(base: type[T]) -> None:
    """Raise rather than silently rebuild a schema without its validators.

    The rebuild carries fields and config; a ``@model_validator`` would be
    dropped and nothing would say so. That is not hypothetical here: the
    sibling ``ArtifactActionMeta`` already enforces "this decision requires
    that field" with exactly such a validator, so one arriving on a turn schema
    is a plausible next change -- and its silent loss would let a malformed turn
    through into the binder. Carrying it correctly means narrowing a validator's
    own field references too; until something needs that, refusing is honest.
    """

    declared = sorted(
        name
        for kind in _UNCARRIED_DECORATORS
        for name in getattr(base.__pydantic_decorators__, kind, {})
    )
    if declared:
        raise TypeError(
            f"{base.__name__} declares {', '.join(declared)}, which narrowing "
            "cannot carry onto the rebuilt schema. Teach _narrow_fields to "
            "carry it before narrowing this schema."
        )


def _narrow_fields(base: type[T], allowed_decisions: tuple[str, ...]) -> type[T]:
    """Narrow to the fields the allowed decisions can fill, and to those decisions.

    Rebuilt without ``__base__``: inheriting the parent would carry its fields
    along, and removal is the whole point. Each surviving field keeps the
    annotation and the ``FieldInfo`` ``base`` declared -- ``Clock``'s validator
    and ``day_offset``'s bounds are correctness, not decoration -- and the new
    model carries ``base``'s own config, so strictness travels too.

    The ``decision`` Literal narrows by the same rule as the fields. A schema
    offering a decision the state disallows is one the model can only waste a
    turn on, and at the date stage it advertised ``revise`` while carrying no
    ``revision_instruction`` to express it.
    """

    retained = {"decision"}
    for decision in allowed_decisions:
        # A lookup over decision names, and only over the ones `base` actually
        # declares: a map entry for a field another surface's schema owns is a
        # no-op here rather than an error.
        retained |= _FIELDS_BY_DECISION.get(decision, frozenset())
    retained &= set(base.model_fields)
    offered = get_args(base.model_fields["decision"].annotation)
    decisions_narrow = set(offered) != set(allowed_decisions)
    if retained == set(base.model_fields) and not decisions_narrow:
        # Nothing to drop, so nothing is rebuilt and nothing can be lost --
        # which is why the decorator guard below belongs on this side of it.
        return base
    _refuse_to_drop_behaviour(base)
    fields: dict[str, object] = {
        # Equality, not `in`: an AST test bans every membership operator in
        # this module, because one over user text would be the banned
        # judgement. The module's other comparisons read the same way.
        name: (info.annotation, info)
        for name, info in base.model_fields.items()
        if any(name == kept for kept in retained)
    }
    if decisions_narrow:
        fields["decision"] = (Literal[tuple(allowed_decisions)], ...)
    return create_model(  # type: ignore[call-overload]
        f"{base.__name__}Narrowed",
        __config__=base.model_config,
        **fields,
    )


def narrow_schema(
    base: type[T],
    options: tuple[BlockerOption, ...],
    *,
    allowed_decisions: tuple[str, ...] | None = None,
) -> type[T]:
    """Narrow one turn's schema to exactly what this state can express.

    Two narrowings, same reason: the model should not be offered a decision the
    state disallows, and should not be made to emit a field no allowed decision
    can fill. `allowed_decisions=None` narrows no fields, so a caller that has
    not opted in keeps the full schema.

    Where nothing was offered there is nothing to choose, and the base schema
    cannot express a choice at all.

    The order matters: the option narrowing adds ``option_id``, so it runs
    first and the field pass then sees a field ``CHOOSE_OPTION`` claims.
    """

    narrowed = base
    if options:
        decisions = (
            CHOOSE_OPTION,
            *get_args(base.model_fields["decision"].annotation),
        )
        narrowed = create_model(  # type: ignore[call-overload]
            f"{base.__name__}WithOptions",
            __base__=base,
            decision=(Literal[decisions], ...),
            option_id=(
                Literal[tuple(option.option_id for option in options)] | None,
                None,
            ),
        )
    if allowed_decisions is None:
        return narrowed
    return _narrow_fields(narrowed, allowed_decisions)


GENERIC_PREAMBLE = """You interpret one user reply against a proposal the assistant is showing.
Return only the requested schema.
Choose only a decision listed in allowed_decisions.
When offered_options is present and the user picked one of them, answer with
that option's option_id exactly as given.
Never invent identifiers; the host owns identity.
"""


class SurfaceIntentError(RuntimeError, ValueError):
    """Reading one reply against one surface failed.

    Interpretation only. It never covers executing the press the reading
    asked for: a store or Slack failure mid-press, after the surface already
    told the user it was acting, is a different failure and gets its own
    words.

    Also a ``ValueError`` because the schema violations it wraps already were
    one, and callers that catch that shape (the timeboxing session's own
    refusals) must keep catching it.
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
            raise SurfaceIntentError(
                f"the {view.surface_kind} does not accept another intent"
            )
        allowed = tuple(view.allowed_decisions)
        if view.offered_options and not any(
            item == CHOOSE_OPTION for item in allowed
        ):
            allowed = (*allowed, CHOOSE_OPTION)
        # After `allowed` is final: a state with options has just gained
        # CHOOSE_OPTION, which is what keeps `option_id` in the schema.
        narrowed = narrow_schema(
            schema, view.offered_options, allowed_decisions=allowed
        )
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
        try:
            # Awaited straight from the Slack listener, not dispatched through
            # the AutoGen runtime, so without a name the tokens land under
            # "unknown".
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
                raise SurfaceIntentError(
                    "intent model returned no schema-bound JSON content"
                )
            interpreted = narrowed.model_validate_json(content)
        except (SurfaceIntentError, ValidationError):
            # A schema violation is already the precise typed reading failure,
            # and the surfaces that bind one read its field errors. It travels
            # as itself; the seam that reports to a user wraps it.
            raise
        except Exception as exc:
            raise SurfaceIntentError(
                f"could not read the reply against the {view.surface_kind}"
            ) from exc
        # Defence in depth, not the gate, and unreachable through `interpret`
        # today: the `decision` Literal on `narrowed` is exactly `allowed`, and
        # `model_validate_json` above is local validation no host can bypass --
        # so a disallowed decision fails there as a `ValidationError` and never
        # arrives here. Kept because the two would have to be derived
        # separately for that to stop being true, and a decision the session
        # cannot honour must not reach a binder silently.
        if not any(interpreted.decision == item for item in allowed):
            raise SurfaceIntentError(
                f"decision {interpreted.decision!r} is not allowed in "
                f"{view.display_state}"
            )
        return interpreted


__all__ = [
    "CHOOSE_OPTION",
    "Clock",
    "GENERIC_PREAMBLE",
    "SurfaceIntentError",
    "SurfaceIntentInterpreter",
    "SurfaceView",
    "narrow_schema",
]
