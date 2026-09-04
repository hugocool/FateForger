# Brief: brainstorm the stage-card grammar (#266) with Hugo

You are picking up **#266** — *one card grammar and one steer route for the five-stage session UX on the harness*. Hugo will brainstorm it with you. A sibling session (`admonish-1-a2`) is writing the **Stage 1 elicitation spec** in parallel; Stage 1 is the first consumer of whatever you decide. This brief is what you need to not re-litigate settled things and to meet that spec in the middle.

## How to run it

- Invoke `superpowers:brainstorming` and follow it. **One question per message.** Hugo asked for this explicitly and it holds even when a topic needs five questions — split them.
- This is a genuinely visual topic. When a question is about *layout* — which sections, where the controls sit, receipt vs live card — offer the visual companion **then**, as its own message, not up front. Text questions (which state lives where, what a control means) stay in the terminal.
- Lead every choice with your recommendation and the reason. Hugo pushes back on recommendations and is usually right to; when he does, steelman the option you dismissed before defending yours. He caught this session doing exactly that today.
- End with a spec at `docs/superpowers/specs/2026-09-04-stage-card-grammar-design.md`, committed. Not a plan — the plan comes from `writing-plans` after he approves the spec.

## What #266 is deciding

Read the ticket. It has three shapes already written up — **A** section grammar, **B** artifacts render themselves, **C** card as a view over the snapshot — and four cross-cutting decisions. Do not restate them to Hugo; he wrote them. Start from *which of the three, and why*, and use the Stage 1 contract below as the first test any shape has to pass.

`docs/superpowers/specs/2026-09-03-stage-ux-port-design.md` is increment A's spec (stage identity, Back, Cancel — landed in #273). Read it first; you are designing increment B's surface on top of it, and #276 records one ladder inconsistency it left behind that the grammar should not inherit.

## Already decided — do not reopen

These came out of the Stage 1 brainstorm this morning and are the constraints your grammar must serve. Reopening them costs Hugo the same conversation twice.

- **Stage 1 is user-ended.** The agent *proposes* to close when its gate is met; the **Next** control is offered *only* on that outcome (`GateMet`). When the gate is not met, there is no Next — the card states what would meet it. Consent to advance is Hugo's **next message**, never a timer.
- **Forcing past an unmet gate is a user-filed assumption**, not a bypass. "Just assume a normal working day" files a `PlannerAssumption` for the open cell; it appears in the card's *decided* list, and it is reversible.
- **Button ≡ typed reply, through `SurfaceIntentInterpreter`.** The surface offers a closed `allowed_decisions` set plus option ids; the model picks; nothing anywhere compares Hugo's words to labels. Every control you design must have a typed equivalent that lands on the same intent — this is the existing pattern, extend it, do not fork it.
- **Constraints are grouped by anchor.** The memory server mints anchors from Hugo's own statements (29 exist: `gym`, `dinner`, `deep work`, `sleep`, `commute`, `lunch`, `nature reservation`, `finance`…). The group headings on a card are anchor names — dynamic, never an authored list. Necessity (`must`/`should`) and applicability (every day / day-specific / suspended today) are tags on a row, not groups.
- **Per-rule steering is session-scoped by default.** *Not today* → a session fact that supersedes; restore = re-state. *Always* → promotion through `memory_observe`, and it **asks first** because it is the one irreversible write. *This is wrong* → routes to the memory server's correction path when map B lands.
- **Where the loop runs is being spiked, not decided** (#283–#286). Your grammar must not assume the kernel or the harness owns the probe text. It renders `AwaitingUser`, `GateMet`, facts and assumptions from the snapshot — whoever produced them.

## The Stage 1 content contract your grammar has to render

This is the section the sibling spec commits under **"Content contract"**. Treat it as the acceptance test for whichever shape wins:

1. **Context** — active constraints grouped by anchor, each row with necessity and applicability tags; unanchored rules in their own group (CLAUDE.md: *unanchored and unreachable are different things*); suspended-today rules shown as suspended with the reason (day type).
2. **Decided** — facts stated this session and assumptions filed (by the planner *or* by Hugo), each with a deny control and a `ref` so it can be steered by reference.
3. **Asking** — at most one open probe, with `why_needed`, and options as buttons when the answer set is closed (≤ 4), free text otherwise.
4. **Gate** — one line, always present: either *"still need: …"* or *"that's what I know to ask about a working Tuesday — anything else, or shall I plan?"*
5. **Controls** — Back, Cancel always; **Next only on `GateMet`**; every button with a typed equivalent.

If a shape cannot render all five without per-stage special casing, that is a finding against the shape.

## Invariants the harness already has — keep every one

From #266 itself; each caught a real defect and none existed in legacy:

- revision/digest binding on every press (`artifact_action_value`) — a stale press is decidable
- `PendingBlocker` — the question that was asked is the question the answer is for
- the requirement catalog with owner/hard/resolution/question (`readiness.py`)
- `PlannerAssumption.invalidated_by`
- day-type override buttons; honest `calendar_backend` / `durable` reporting
- the **decomposition discipline**: a result posts as a *new* message and the pressed card shrinks to a receipt. Legacy grew one message until Slack refused to edit it (`msg_too_long`). Shape C is the one most likely to walk back into that; #266 already says so.

## What legacy had that must not be ported

`constraint_template` coverage (a stub, tested to be absent), the Stage 5 auto-commit short-circuit, the `Background` notes. Hugo will recognise them and will not want them.

## Coordination with the sibling spec

- The Stage 1 spec lands at `docs/superpowers/specs/2026-09-04-stage1-elicitation-design.md`. Its **Content contract** section is the five items above; read it there when it is committed rather than from this brief, in case it moved.
- Your spec should expose **one rendering interface** — a typed model the Stage 1 spec can target (a `SessionMessage`-like object, or per-artifact `to_card()`, depending on the shape you pick) — and name it explicitly. The Stage 1 spec will target that name. That is the seam; keep it narrow.
- When you have a draft interface, message `admonish-1-a2` (`SendMessage`). It will message you when the content contract is committed. Neither of you decides the other's half.
- The shared checkout is `main`, clean, at `a71f213` or later. Your spec is a docs commit; do not touch `src/`. If you need to sketch, sketch in the spec.

## Hugo, as observed today

- Dislikes hardcoded lists and magic constants on principle, not just in code — a card with an authored section list will get the same reaction as a keyword list.
- Wants the spec to be what the system reasons against, and wants to be told *what the gate needs* rather than asked a question with no context.
- Wants "induce free association" — the surface should make it easy to add a thought, not just answer the one being asked. That is a design input for the *asking* section.
- Will say "b?" with a question mark when choosing. Take it as a choice, and note the uncertainty in the spec's rationale.
- Prefers a working example over a description. If two shapes are close, a rendered mock of Stage 1 in each, side by side, settles it faster than prose — that is the moment for the visual companion.
