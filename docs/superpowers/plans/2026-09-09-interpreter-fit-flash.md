# Fit the interpreter to the flash pin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The surface interpreter asks the cheap pin a smaller question and gives it the discriminator it is missing, so the flash pin holds the cases it currently drops.

**Principle (Hugo, 2026-09-09):** *the cheapest, fastest model wherever it will work; escalate only when empirical testing justifies it.* The 2026-09-06 bench measured the escalation as currently justified — flash loses 4 judgement cases against pro's 0. This plan removes the two reasons it loses that we can measure today, so the escalation stops being justified and the default can flip back down.

**Tickets:** #440 (schema carries only usable fields) · #406 (the discriminator), partial — see Scope.

## Scope, and what is blocked

The four cases flash loses sit across two unmerged branches:

| case | prompt lives | eval lives | in scope |
|---|---|---|---|
| `planning_card::test_a_non_press_is_none[plan tomorrow for me]` | this branch | this branch | **yes** |
| `planning_card::test_a_non_press_is_none[later]` | this branch | this branch | **yes** |
| `timebox_question::test_a_revision_after_commit_is_still_a_revision` | this branch | **#328 only** | no |
| `timebox_question::test_a_fact_after_commit_is_still_a_fact` | this branch | **#328 only** | no |

`tests/integration/test_eval_timebox_question.py` exists only on `feat/asked-not-started` (PR #328, open), and it imports `AskQuestion`/`Asked`/the `no_session` state that also exist only there — so its cases cannot be measured against a prompt change made here. **Do not edit `_TIMEBOX_PROMPT_FRAGMENT_BASE` in this plan**: changing a prompt you cannot measure is the thing CLAUDE.md's resampling rule exists to stop. The timebox half is a follow-up once #328 merges.

This branch is stacked on `feat/336-interpreter-tier-one` (PR #409, open), which is where the `intent_interpreter` row lives.

## Global Constraints

- **No keyword/string/regex matching on user content.** The field-narrowing map in Task 1 is over decision names this system minted, which the rule exempts explicitly.
- **Never pin `temperature`.** **Never assert an exact model output string in a unit test** — assert the decision it drove.
- **Evals sample n≥8 and assert on the rate.** A prompt fix validated by one passing call has not been validated. A case that starts passing must be resampled, and the break-it check must still fail.
- **An agent never changes a model pin.** `.env` untouched. This plan changes prompts and schemas, not pins; whether the default flips to flash is Hugo's call on the bench this produces.
- **Worktree:** `.worktrees/interpreter-fit-flash`, branch `feat/406-interpreter-fit-flash`. Every pytest run is `PYTHONPATH=src ../../.venv/bin/python -m pytest …` from that directory. `.env` is present and gitignored — never in `git status`.
- **Suite before done:** `PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q`, expected all green.
- **Commits:** `<type>(<scope>): <lowercase sentence> (#NNN)` ending `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`. Name files; never `git add -A`.

---

## Task 1: The schema carries only the fields its allowed decisions can use (#440)

**Files:**
- Modify: `src/fateforger/slack_bot/surface_intents.py` (`narrow_schema`)
- Test: `tests/unit/test_surface_intent_schema_narrowing.py` (create)

**Interfaces:**
- Produces: `narrow_schema(base, options, allowed_decisions)` — a third parameter. Existing two-arg callers must keep working, so the parameter is keyword-only with a default of `None` meaning "narrow nothing away".
- Consumes: nothing from other tasks.

**Why it is worth doing:** measured over 1,624 bench draws, 42% of answers are a decision and six nulls — 144 characters of wire for 23 of information, because strict structured-output mode requires every property to be present. Fewer fields is also a smaller job for the cheap pin, which is this plan's point.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_surface_intent_schema_narrowing.py`:

```python
"""A state's schema carries `decision` and the fields its decisions can fill.

Strict structured output requires every property to be emitted, so a field the
state cannot use is a null the model must produce on every call -- 42% of
answers in the 2026-09-06 bench were a decision and six nulls.
"""

from __future__ import annotations

import pytest

from fateforger.slack_bot.surface_intents import narrow_schema
from fateforger.slack_bot.timeboxing_intents import InterpretedTimeboxTurn


def _fields(allowed: tuple[str, ...]) -> set[str]:
    return set(narrow_schema(InterpretedTimeboxTurn, (), allowed_decisions=allowed).model_fields)


def test_a_state_that_can_only_classify_carries_one_field() -> None:
    assert _fields(("start", "question", "cancel")) == {"decision"}


def test_a_state_that_extracts_carries_facts() -> None:
    assert _fields(("provide_facts", "question")) == {"decision", "facts"}


def test_revise_carries_its_instruction_and_facts() -> None:
    assert _fields(("revise", "question")) == {"decision", "facts", "revision_instruction"}


def test_confirming_a_day_carries_the_day_fields_only() -> None:
    assert _fields(("confirm_planning_day", "cancel")) == {"decision", "day_type", "day_offset"}


def test_steering_carries_the_uid_and_the_suspension_fact() -> None:
    assert _fields(("steer_not_today", "restore")) == {"decision", "facts", "constraint_uid"}


def test_denying_carries_the_assumption_id() -> None:
    assert _fields(("deny", "advance")) == {"decision", "assumption_id"}


def test_omitting_the_argument_narrows_nothing_away() -> None:
    # Every existing caller passes two arguments; none may lose a field.
    assert set(narrow_schema(InterpretedTimeboxTurn, ()).model_fields) == set(
        InterpretedTimeboxTurn.model_fields
    )


def test_every_field_is_claimed_by_some_decision() -> None:
    """The guard: a field no decision claims would be silently dropped from
    every state, and a decision that gains a field must claim it here."""
    from fateforger.slack_bot.surface_intents import _FIELDS_BY_DECISION

    claimed = {"decision"} | {f for fields in _FIELDS_BY_DECISION.values() for f in fields}
    assert set(InterpretedTimeboxTurn.model_fields) <= claimed
```

- [ ] **Step 2: Run it, watch it fail**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_surface_intent_schema_narrowing.py -q`
Expected: FAIL — `narrow_schema() got an unexpected keyword argument 'allowed_decisions'`.

- [ ] **Step 3: Extend `narrow_schema`**

In `surface_intents.py`, above `narrow_schema`:

```python
#: Which fields each decision can fill. Keys are decision names this system
#: minted, not user content -- the pattern rule exempts identifiers we own.
#: A field claimed by no allowed decision is one the model must still emit as
#: null on every call, because strict structured output requires every
#: property. Measured 2026-09-06: 42% of answers were a decision and six nulls.
_FIELDS_BY_DECISION: dict[str, frozenset[str]] = {
    "provide_facts": frozenset({"facts"}),
    "revise": frozenset({"facts", "revision_instruction"}),
    "confirm_planning_day": frozenset({"day_type", "day_offset"}),
    "steer_not_today": frozenset({"facts", "constraint_uid"}),
    "restore": frozenset({"constraint_uid"}),
    "deny": frozenset({"assumption_id"}),
    "update_time": frozenset({"selected_time"}),
    "update_time_and_add": frozenset({"selected_time"}),
    CHOOSE_OPTION: frozenset({"option_id"}),
}
```

Then give `narrow_schema` the parameter and the field narrowing:

```python
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
    """
```

Keep the existing decision-narrowing body. After it, when `allowed_decisions` is not None, rebuild the model with only the surviving fields — `decision` plus the union of `_FIELDS_BY_DECISION` over the allowed decisions, intersected with what `base` actually declares (a planning schema has `selected_time` and no `facts`; a timeboxing one the reverse). Use `pydantic.create_model` with the retained fields copied from `base.model_fields`, mirroring how the decision narrowing already rebuilds. Preserve each field's annotation, default and `Field` metadata — `Clock`'s validator and the `day_offset` bounds must survive.

- [ ] **Step 4: Pass, then wire the one caller**

Run the new test file → PASS.

In `SurfaceIntentInterpreter.interpret`, pass the allowed decisions it already computed:
`narrowed = narrow_schema(schema, view.offered_options, allowed_decisions=allowed)` — placed **after** `allowed` is finalised (it appends `CHOOSE_OPTION` when options are offered), so a state with options keeps `option_id`.

- [ ] **Step 5: Run every eval's unit-level suite and the package suite**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit -k "surface or intent or planning or timeboxing" -q` → PASS.
Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q` → all green.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/slack_bot/surface_intents.py tests/unit/test_surface_intent_schema_narrowing.py
git commit -m "feat(slack): a state's schema carries only the fields its decisions can fill (#440)

Strict structured output requires every property, so a field no allowed
decision can use is a null the model emits on every call -- 42% of answers in
the 2026-09-06 bench were a decision and six nulls, 144 characters of wire for
23 of information. narrow_schema already narrowed the decision enum; it now
narrows the fields by the same rule.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: The planning card is told what day it is (#406, planning half)

**Files:**
- Modify: `src/fateforger/slack_bot/planning_surface.py` (`planning_view`; possibly `PLANNING_PROMPT_FRAGMENT`, but only if Step 4 proves it necessary)
- Modify: `src/fateforger/slack_bot/planning.py` (the one caller of `planning_view`)
- Test: `tests/unit/test_planning_surface.py`, `tests/integration/test_eval_planning_card_intent.py`

**Interfaces:** consumes Task 1's narrowed schema (planning states keep `selected_time` and `option_id` and drop nothing else — confirm in the report).

**The defect, corrected 2026-09-09.** An earlier draft of this task blamed the prompt for never defining `none`. That is true but it is not the cause. The cause is that **the request contains no current date or time**, so the question is not answerable from what the model is given. This is the entire payload for the failing case:

```json
{"surface":"planning_card","display_state":"draft",
 "allowed_decisions":["update_time","update_time_and_add","none","choose_option"],
 "offered_options":[{"option_id":"add_to_calendar","label":"Add to calendar",
   "effect":"adds the session to the calendar at Thu 3 Sep 10:38–11:08 as shown"}],
 "open_question":null,"user_text":"plan tomorrow for me",
 "proposal":{"title":"Daily planning session","day":"Thu 3 Sep","start":"10:38",
   "end":"11:08","timezone":"Europe/Amsterdam","status":"not added yet"}}
```

Is `Thu 3 Sep` tomorrow? Nothing here says. If today is Wednesday the user and the card agree and pressing *add* is defensible; if today is Thursday the user is naming a different day and it is plainly not a press. The pro pin defaults to caution and the flash pin defaults to agreement — **neither is reasoning, because the evidence is absent.** The same fact is missing for "later", "tonight", "saturday" and "not today".

The timeboxing surface dodged this deliberately: it passes the proposed day and asks for a `day_offset` measured *from that day*, so it never needs to know today (`_proposed_day_context`, and the comment there explaining why a model naming a date directly was the 2026-08-29 incident). The planning card has no such dodge and was simply never given the fact.

**So: give it the fact first, and only add prose if the fact is not enough.** Adding a clause to compensate for a missing input would be teaching the model to guess well rather than letting it know.

- [ ] **Step 1: Baseline the two cases on the flash pin, before any change**

```
cp ../../.env .env   # if absent
set -a; source .env; set +a
LLM_MODEL_INTENT_INTERPRETER="$OPENROUTER_DEFAULT_MODEL_FLASH" \
LLM_REASONING_EFFORT_INTENT_INTERPRETER=minimal \
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/integration/test_eval_planning_card_intent.py -m slow -q -s -p no:cacheprovider
```

Record every case's count. Expect both `test_a_non_press_is_none` cases below 7/8. **If they pass, stop and report** — the premise is wrong.

- [ ] **Step 2: `now` becomes an argument, never a clock read**

`planning_view(draft)` gains a keyword-only `now: datetime` with **no default**. It must be passed in, not read from `datetime.now()` inside the view: a view that reads the clock produces an eval that passes on Wednesdays and fails on Thursdays, which is precisely the weekday-dependent failure that sat red in `test_planning_reminder_suppression.py` for this whole line of work.

Add to the context dict, rendered in the draft's own timezone:

```python
"now": {
    "date": local_now.date().isoformat(),
    "weekday": local_now.strftime("%A"),
    "time": local_now.strftime("%H:%M"),
},
```

Update `PlanningCoordinator._interpret_reply` (the one production caller) to pass `now=datetime.now(timezone.utc)`. Update existing unit tests in `tests/unit/test_planning_surface.py` that call `planning_view` to pass a fixed `now`, and add one asserting the rendered `now` block is in the draft's timezone and matches the datetime given — not the wall clock.

- [ ] **Step 3: Resample on the flash pin, with the fact and no new prose**

Re-run Step 1's command. Record every case. **This is the measurement the task exists for:** if both `test_a_non_press_is_none` cases now reach ≥ 7/8, the fix is the fact and the task is done — go to Step 5. Note in the report what the counts were before and after.

- [ ] **Step 4: Only if Step 3 falls short — the smallest clause that closes the gap**

If a case is still below 7/8 *with* the date available, then and only then add prose, and make it about the relation the model now has the inputs to check, not a list of phrasings:

```python
The card proposes one event on one day. A reply naming a different day from
the proposal's -- compare it against `now` -- is a request, not agreement with
what is shown; answer `none`. Only an explicit acceptance, or a clock time, is
a press.
```

Split the fragment as the timebox one was — `_PLANNING_PROMPT_FRAGMENT_BASE` plus the clause — and add the break-it case below. Resample; every other case must hold its Step 3 count. If `test_consent_is_the_add_press` regresses, the clause is too broad: narrow and resample, never trade one case for another.

```python
@pytest.mark.parametrize("text", ["plan tomorrow for me", "later"])
async def test_break_it_without_the_day_clause_a_non_press_becomes_a_press(text, monkeypatch):
    """A discriminator that passes without its discriminating sentence is not one."""
    import fateforger.slack_bot.planning_surface as ps
    monkeypatch.setattr(ps, "PLANNING_PROMPT_FRAGMENT", ps._PLANNING_PROMPT_FRAGMENT_BASE)
    results = await _presses(text)
    assert _count(results, kind=None) < THRESHOLD, _report(results)
```

Confirm `_presses` reads `PLANNING_PROMPT_FRAGMENT` as a module global at call time so the monkeypatch lands.

- [ ] **Step 5: Confirm the pro pin did not regress**

Both the context and any clause are shared. Re-run the file with the row's defaults (omit the two env overrides) and confirm every case holds its pre-change count.

- [ ] **Step 6: Package suite, then commit**

Message depends on which step fixed it. If Step 3 alone:

```bash
git add src/fateforger/slack_bot/planning_surface.py src/fateforger/slack_bot/planning.py tests/unit/test_planning_surface.py
git commit -m "feat(slack): the planning card tells the interpreter what day it is (#406)

'plan tomorrow for me' under a card proposing Thu 3 Sep was unanswerable: the
payload carried the proposal's day and no current date, so nothing in the
request said whether Thursday was tomorrow. The pro pin defaulted to caution
and the flash pin to agreement; neither was reasoning. now is passed in, never
read from the clock inside the view, so the eval cannot start depending on the
weekday it runs on.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## What this plan does not do

- Touch `_TIMEBOX_PROMPT_FRAGMENT_BASE`. Its two failing cases can only be measured by an eval that exists on #328; changing an unmeasurable prompt is the failure CLAUDE.md's resampling rule names. Follow-up once #328 merges.
- Change any `.env` pin or the `intent_interpreter` row's defaults. Whether the default flips to flash is Hugo's call on a bench that shows all four cases held — and only two of the four can be shown here.
- Re-run the full six-configuration bench. Task 2's targeted eval runs are the evidence for this change; the full bench belongs to the flip decision.

## Self-review

**Coverage:** #440 → Task 1. #406's planning half → Task 2, with the timebox half explicitly scoped out above and the reason stated. **Placeholders:** two verification instructions (that `create_model` preserves `Field` metadata; that `_presses` reads the fragment at call time) are checks against real code, each with the fallback named. **Type consistency:** `narrow_schema(..., allowed_decisions=)` and `_FIELDS_BY_DECISION` named identically in Task 1's test, code and Task 2's interface note; `_PLANNING_PROMPT_FRAGMENT_BASE` introduced and consumed within Task 2.
