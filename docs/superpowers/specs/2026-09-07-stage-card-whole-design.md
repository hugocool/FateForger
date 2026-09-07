# The stage card shows what it is asking you to approve

**Tickets:** #344 (render), #267 (provenance), #259 (the planner's question).
**Map:** C (#157). **Based on:** `feat/stage1-elicitation-loop` (PR #359); merges after it.
**Spikes:** nine Block Kit variants in `#ff-e2e`, thread `1788780130.551149`, judged by Hugo 2026-09-07.
**Session behind it:** `C0AA6HC1RJL:1788626829.487149`, planning Sunday 2026-09-06 — committed 8 blocks at 00:04:37 and was corrected at 00:06:54.

## The defect, in three parts

**The artifact is unreadable.** `map_outcome` sets `body=skeleton.markdown`; the renderer emits it into a `mrkdwn` section, so `#`, `##` and `-` print literally. `to_mrkdwn()` exists — built for #179 against a real parser — and `timeboxing_cards.py` never calls it.

**The artifact is last.** Order is `header → context → decided → body`, so the day sits under a paragraph of reasoning and up to ten `Decided` bullets. Approval binds `artifact_digest`: what is being approved is the least prominent thing on the card.

**Nothing says where a block came from, and the planner's questions are discarded.** On Sunday a sci-fi reading block arrived from a stored rule the user never invoked that day, indistinguishable from what he had asked for. Separately, `open_questions` is not a field the kernel reads (#259), so a planner that has a question has nowhere to put it.

## What the spikes settled

**Slack collapses a long message behind "Show more".** One section holding the whole day collapsed after ~5 lines — the user had to click to see the day they were approving. A `header` block plus one section per group renders whole. Measured across five variants; the only one that collapsed was the all-long-sections variant.

**Slack has no headings.** `to_mrkdwn` renders every level as plain bold, correctly: Slack has one bold weight. The `header` **block** is the only larger-text affordance, `plain_text` only. **Two visual levels is the ceiling** — `header` for the day, bold for each group.

**Context and Decided fold without a button.** As `context` blocks after the nav they stay readable and under the threshold, and need no interactivity. This retires the `_+N more_` cap, which rendered as text and was not clickable.

**Provenance only informs when it varies.** A card where every line reads `you said` is noise, and it was the first thing cut. Nothing is marked when it came from the user; only rules and guesses are named. Every marker is then, by construction, something to argue with.

**Name the rule, not the category.** `from memory` does not say *which* rule to argue with. `(Sleep schedule)` does.

## Decisions

| fork | choice | why |
| --- | --- | --- |
| payload shape | **structured groups and items** | provenance per item cannot live in flat markdown without the rule name being unverified model text; #267's own "done" section asked for one declared shape |
| who composes the label | **the renderer, from the stored name** | the read path calls no model, and a paraphrased name is not one the user can refer to (#379) |
| rule identity | **uid, verified against the day's active constraints** | never act on a model-supplied identifier; #330 was a judge mistyping a uid by one character |
| provenance phrasing | **style A — trailing, italic, quietest** | chosen against B (`— per X`, competes with the content) and C (`, because X`, buries the name) |
| `must` marked? | **no** | Hugo's call; revisit only if a `must` is ever silently dropped |
| assumptions | **inline only** | the guess is marked where the guess is; `Decided` returns to being only what the user supplied. Sunday put four placements in one bullet, none attached to the block it decided (#263) |
| skeleton questions | **below the artifact, not replacing it** | a placement question is unanswerable without the placement on screen |
| blocking | **non-blocking by default; the planner may escalate one** | mirrors the catalog's hard/soft split; keeps the user ending the stage without letting a real collision be clicked past |

## Design

### Payload — `session_contracts.py`

```python
class SkeletonItem(_StrictModel):
    text: str = Field(min_length=1)
    source: Literal["user", "rule", "assumed", "calendar"]
    #: Set iff source is "rule". The uid of the constraint that placed this.
    rule_uid: str | None = None

class SkeletonGroup(_StrictModel):
    name: str = Field(min_length=1)
    items: list[SkeletonItem] = Field(min_length=1)

class SkeletonPayload(_StrictModel):
    day_label: str = Field(min_length=1)
    groups: list[SkeletonGroup] = Field(min_length=1)
    reasoning: str = ""
```

A `rule_uid` not among the day's active constraint uids fails the turn by name — `TurnFailed(code="unknown_rule_uid")` — rather than being drawn. Comparing a returned uid against uids this system minted is set membership over its own identifiers, explicitly outside the no-matching rule.

`markdown` is gone. An artifact stored under the old shape is refused loudly, as `_StrictModel` already does for `blocks` — consistent with #267's existing stance that a payload failing here predates the contract and must not be drawn as an empty day. **A session mid-flight across the deploy loses its skeleton and must re-draft**; that is the accepted cost and is stated so nobody discovers it in a live session.

### The question — `session_contracts.py`, `adaptive_timeboxing.py`

`UserBlockerDraft` gains `blocking: bool = False`. The kernel branches on it:

- `blocking=True` → `AwaitingUser`, exactly as today: the ladder stops.
- `blocking=False` → `AwaitingApproval(artifact)` carrying an optional `question: Asking | None`.

So one outcome can present an artifact *and* a question. `Proceed` stays live on the non-blocking path and means "approve, question unanswered" — which the card says in as many words.

### Renderer — `timeboxing_cards.py`

```
stage line          (context block)
day                 (header block)
groups              (one section each)
question            (section + option buttons, when present)
nav
context · decided   (context blocks, small)
```

The renderer composes mrkdwn from typed items, so **there is no CommonMark left to convert on this path**. `to_mrkdwn()` is still needed for `reasoning`, which is model free text, and for any other model-authored body. The `artifact_markdown` field from the earlier draft of this design is unnecessary: structure replaced it.

Line composition, style A:

```
• {text}                      source == "user"
• {text}  _({rule name})_     source == "rule"
• {text}  _(my guess)_        source == "assumed"
• {text}  _(on your calendar)_  source == "calendar"
```

`Decided` lists only user-supplied facts, uncapped, in a `context` block.

### Planner

Its instructions are generated from the Pydantic models rather than restated — the #158 decision: import the symbols, never copy. It is told to set `source` per item, to give `rule_uid` whenever a placement is owed to a rule it was given, and that it may raise **at most one question per turn** — a second one waits for the next draft. `blocking` is set on it only when proceeding would produce a plan the planner believes is wrong; the ordinary case is `blocking=False`.

## Tests, each broken on purpose before it is trusted

- A `rule_uid` not among the day's active constraints fails the turn; the card is not drawn.
- `source="user"` renders with no marker; `"rule"` renders the stored name, not the uid and not a paraphrase.
- Ordering: the day's `header` block precedes every group; every group precedes context and decided.
- A non-blocking question renders with `Proceed` still present; a blocking one renders without it.
- An old `markdown`-shaped payload raises rather than drawing an empty day.
- `Decided` contains no assumption.
- A group name or item text containing `*` or `_` survives without becoming formatting.

## Risks accepted

**A third heading level has nowhere to go.** Fine while the skeleton is day → group → item.

**The planner must now attribute.** An item owed to a rule but sent as `source="user"` is invisible — the card simply shows no marker. Only an eval catches that, and it belongs with #267's quality work rather than here.

**Reversing #267's markdown choice.** Made deliberately: flat markdown cannot carry verifiable provenance. Recorded here so it is not re-derived.

## Out of scope

Label register (#379) — the stored name becomes user-facing here and a third of the corpus reads like a spec heading. Fixed at the prompt, never at render.
