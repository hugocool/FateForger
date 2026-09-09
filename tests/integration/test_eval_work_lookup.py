# tests/integration/test_eval_work_lookup.py
"""Quality of the work lookup against the live model (Task 4, #-work-links).

`tests/unit/test_work_lookup.py` stubs the model and proves the plumbing. This
is the other half CLAUDE.md requires: the same question put to a real model,
resampled, asserted on the rate. One draw tests the model's luck.

The candidate rows below are the live board's 12 current-sprint Ready tickets
as `TaskBoard.list_tasks("current_sprint_ready")` returned them on 2026-09-08,
in that order — the order is part of the question (see `build_prompt`), so
they are stored in it rather than sorted here. Only the four fields the prompt
renders are kept; the rest of a candidate is filler that never reaches a model.

Since #401 `resolve_work` takes `TaskCandidate` rows off the `TaskSource` port
rather than `TaskRow` rows off the board, so this file builds candidates from
the same four facts under the port's names — `external_id`, `number`, `label`,
`summary`. **The prompt text and the request shape did not change**, which is
what makes the rates below still describe the current prompt:
`tests/unit/test_work_lookup.py` pins the rendered line byte for byte, and the
snapshot here is the same twelve rows in the same order it always was.

Measured 2026-09-08 on the flash pin at `reasoning: minimal`, 8 draws per case.
The fourth case is why this file exists. Before the prompt distinguished
naming an ITEM of work from naming a TOPIC to spend time on, it read as:

    message                                          answer      rate
    "the next finance ticket ..."                    #427        8/8
    "get the DNS and the holding page done today"    #457 #458   8/8
    "book a dentist appointment tomorrow"            none        8/8
    "serious c2f work in the morning, gym ..."       #500        5/8   <-- wrong
                                                     other sets  3/8

That 5/3 split is the coin-flip signature: the prompt named a category without
giving the model anything to key off. Attaching one C2F ticket to a block Hugo
meant as a broad C2F session is a wrong answer that reads as a right one on
the card, so it is the failure worth an eval.

Measured again after the fix, 2026-09-08, same pin and shape. Every case
clears the threshold; the rates below pool several sweeps:

    case                                     answer      rate
    "the next finance ticket ..."            #427        109/112
    "get the DNS and the holding page ..."   #457 #458   16/16
    "book a dentist appointment tomorrow"    none        16/16
    "serious c2f work in the morning ..."    none        16/16
    "finish the Auth0 M2M registration"      #500        16/16
    "block out the afternoon for portal ..." none        15/15

The finance case is the one that is not flat. Over 112 draws it answered #427
109 times, none once, and #500 twice — and both #500 draws fell in a single
batch of eight, which is 6/8 and would have failed this file. So a red run on
that case alone is a resample away from a green one and is not by itself
evidence that the prompt moved: re-run before believing it, and only treat a
repeated dip as a finding.

**Quoted cases and held-out cases.** The four above are worded from the
prompt's own examples, which makes them regression pins and nothing more: they
catch the discriminator being deleted or weakened, but a rule that merely
recognised those four sentences would pass them too. Each side therefore also
carries a case whose wording appears nowhere in the prompt — "finish the Auth0
M2M registration" for singling out an item, "block out the afternoon for
portal stuff" for naming a topic. The second is the hard direction: the
snapshot holds three PORTAL tickets, so the empty answer has to survive an
area with obvious candidates in it. Both are marked `held-out` in the test ids.

**The request shape this measured, which is part of the number.** One user
turn carrying the whole prompt, `reasoning: {"effort": "minimal"}`,
`response_format: {"type": "json_object"}`, no temperature, on the flash pin.
`resolve_work` requires none of that: its parser deliberately accepts JSON
wrapped in prose, which this shape never produces, so the prose path is not
exercised here. A caller that sends a different shape — a system/user split, a
transport without `response_format`, a different reasoning effort — invalidates
these rates and must re-measure rather than cite them.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter

import httpx
import pytest

from fateforger.agents.tasks.task_source import TaskCandidate
from fateforger.agents.timeboxing.work_lookup import resolve_work

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    ),
]

# The pin is the decision record (CLAUDE.md); the env var wins so a bench run
# can point this at something else without editing a test.
MODEL = os.environ.get("OPENROUTER_DEFAULT_MODEL_FLASH", "openai/gpt-oss-120b:nitro")

# Enough draws to tell a robust judgement from a coin flip, few enough to stay
# runnable: at n=8 a genuine 50/50 clears 7 in only 3.5% of runs.
SAMPLES = 8
THRESHOLD = 7

# (page_id, task number, name, summary) — the live rows, in board order.
BOARD: list[tuple[str, int, str, str]] = [
    (
        "33628174-6a47-8149-b76f-c9b9ef278302",
        500,
        "Register C2F as Auth0 M2M app in iam-data-synchronizer (replaces CAS service account)",
        "URGENT: Acceptatie1 switches to Auth0 on April 22. Register C2F as an M2M application in gerimedica/iam-data-synchronizer with audience gerimedica-ext and client_credentials grant. This replaces the C",
    ),
    (
        "33628174-6a47-81a0-b362-c10a964975b4",
        501,
        "Map ysis-daily MariaDB schema from dump → frontend field mapping",
        "MariaDB dump of ysis-daily received (fake data, real schema). Goal: map DB tables/columns to frontend field paths already documented in the RabbitMQ field mapping resource. Understand which tables CDC",
    ),
    (
        "33428174-6a47-8162-a321-e377e7837ce9",
        497,
        "Follow up k8s: check RD-594 reply + send productie SA manifest to Mattia",
        "Tomorrow-morning infra follow-up packet: check RD-594 for the gm-ai kubeconfig reply, send the productie SA manifest to Mattia, and explicitly validate the permission boundary for C2F-owned workloads:",
    ),
    (
        "33428174-6a47-8189-b570-ff17f566b008",
        496,
        "Validate ysis-core access + find Kafka publisher pattern",
        "Clone ysis-core, locate the existing Kafka publisher pattern, and reduce the Gerimedica ingest design to concrete implementation facts: which classes / modules would publish the live event stream, whe",
    ),
    (
        "33428174-6a47-81f1-932e-f5e05ca03279",
        495,
        "Explore gm-iac repo structure and secret management",
        "Clone gm-iac and turn it into a deployment decision memo: identify manifest format (Helm/Kustomize/raw), namespace layout, secret management approach (Vault / External Secrets / plain k8s), where C2F ",
    ),
    (
        "32928174-6a47-8147-91dd-fecc655b191d",
        457,
        "PORTAL-00 — Namecheap → Cloudflare DNS + subdomain setup",
        "Move c2f.ai DNS to Cloudflare. Configure subdomains: c2f.ai, api.c2f.ai, auth.c2f.ai, docs.c2f.ai. Unlocks all other PORTAL tickets.",
    ),
    (
        "32928174-6a47-8148-b963-d026a6649b38",
        458,
        "PORTAL-01 — Temp landing page (c2f.ai holding page)",
        "Static Cloudflare Pages deploy for c2f.ai while portal is in progress. Brand-consistent, no external deps, email capture optional.",
    ),
    (
        "32928174-6a47-815f-a628-d98af83c0f52",
        464,
        "PORTAL-07 — Workbench launch surface + project runtime handoff",
        "Expose the taxonomy workbench as a full-screen project-scoped app launched from the portal. Pass project/runtime context and auth cleanly; no in-tab embed or Next.js rewrite required for first release",
    ),
    (
        "32528174-6a47-81eb-a81b-d2f208890cca",
        427,
        "Verify VPB 2024 aangifte — deadline was June 1, 2025",
        "VPB aangifte 2024 was due June 1, 2025. Unclear if filed. If not filed, this is an overdue regulatory obligation with potential fines and interest. Check MijnBelastingdienst Zakelijk immediately.",
    ),
    (
        "31e28174-6a47-811d-a82a-e49c72e9ac8d",
        404,
        "KG-02 — Model 4 frame induction and closed-question compilation experiment",
        "Run the first controlled KG experiment after KG-01 gates exist: induce a stable Model 4 FrameSchema on one facet, annotate snippets with evidence, compile a closed-question tree, then run one second-f",
    ),
    (
        "2cd28174-6a47-8041-b532-fcb00ec24361",
        289,
        "KG-03 — Question wording and annotation-guideline synthesis from facet evidence",
        "Convert induced facet/frame outputs into better closed questions and annotation guidelines using evidence examples. Owns wording quality, label boundaries, yes/no vs categorical choice, negation handl",
    ),
    (
        "2f728174-6a47-80cd-ab3e-e62b5d5794aa",
        327,
        "Send Bart Geerts pain-point message (outbound artifact)",
        "Scope cap: One message. Max 15 minutes drafting.",
    ),
]


def _rows() -> list[TaskCandidate]:
    """The board rows as the prompt sees them, in the order the board gave.

    Four fields reach the model — the external id, the number, the label and
    the summary — and they are the same four facts this file has always
    carried, under the names `TaskSource` gives them. The rest is filler that
    `TaskCandidate` requires and `build_prompt` never renders; `state` and
    `overdue` are what the scope and the day would have produced for these
    rows on 2026-09-08, so nothing here is a claim the board did not make.
    """
    return [
        TaskCandidate(
            source="notion",
            external_id=page_id,
            number=number,
            label=name,
            summary=summary,
            # Never rendered into the prompt; present because the model
            # requires every field and defaults nothing.
            state="next",
            due=None,
            overdue=False,
            blocked_by=[],
            url=f"https://www.notion.so/{page_id}",
        )
        for page_id, number, name, summary in BOARD
    ]


class TransportFailure(Exception):
    """OpenRouter did not answer. Not a judgement, and not a quality signal.

    Folded into the rate, an outage reads as a prompt regression and the
    verdict word is wrong for the cause. These are counted separately and the
    case is skipped, saying so.
    """


async def _ask(prompt: str) -> str:
    """One OpenRouter call, in the request shape the memory judge uses.

    The whole prompt goes in the user turn: `resolve_work`'s `ask` takes one
    string, and splitting it across roles would put the option list somewhere
    the measurement did not cover. `reasoning: minimal` because this is term
    typing rather than deliberation, and it sits in a planning turn's latency.
    No temperature pin — resampling measures the distribution.
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0)
        ) as client:
            response = await client.post(
                f"{os.environ.get('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')}"
                "/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "reasoning": {"effort": "minimal"},
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise TransportFailure(f"{type(exc).__name__}: {exc}") from exc
    if "choices" not in payload:
        # OpenRouter's way of surfacing an upstream hiccup: HTTP 200 with an
        # error body instead of a completion.
        raise TransportFailure(f"no choices in response: {json.dumps(payload)}")
    return payload["choices"][0]["message"]["content"]


async def _draws(message: str) -> tuple[Counter, list[str]]:
    """SAMPLES answers to one message, counted by the set of task numbers.

    Returns the counter and, separately, the transport failures. A judgement
    that raised — an unknown id, an unparseable answer — stays in the counter,
    because that is the prompt behaving badly and belongs in the rate.
    """
    rows = _rows()
    results = await asyncio.gather(
        *(resolve_work(message, rows, ask=_ask) for _ in range(SAMPLES)),
        return_exceptions=True,
    )
    counted: Counter = Counter()
    transport: list[str] = []
    for result in results:
        if isinstance(result, TransportFailure):
            transport.append(str(result))
        elif isinstance(result, BaseException):
            counted[f"raised: {result!r}"] += 1
        else:
            counted[frozenset(row.number for row in result)] += 1
    return counted, transport


async def _assert_rate(message: str, expected: set[int]) -> None:
    """Assert the rate, and say "transport" when the transport is what failed."""
    counted, transport = await _draws(message)
    if len(transport) > SAMPLES - THRESHOLD:
        # Too few draws came back for the threshold to be reachable, so there
        # is no measurement to pass or fail. A hiccup or two below this line
        # still leaves a verdict available and is counted against the rate.
        pytest.skip(
            f"OpenRouter failed on {len(transport)}/{SAMPLES} draws for "
            f"{message!r}, so the prompt was not measured: {transport}"
        )
    hits = counted[frozenset(expected)]
    assert hits >= THRESHOLD, (
        f"{hits}/{SAMPLES} answered {sorted(expected) or 'none'} for "
        f"{message!r}; draws: {counted}; "
        f"transport failures: {len(transport)}"
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # The case no pattern could reach: #427's title is a Dutch corporate
        # tax filing and carries no finance vocabulary at all.
        pytest.param(
            "I want to finish the next finance ticket in the first shallow work block",
            {427},
            id="quoted-the-next-finance-ticket",
        ),
        # Two items, each singled out by a description fitting exactly one row.
        pytest.param(
            "get the DNS and the holding page done today",
            {457, 458},
            id="quoted-dns-and-holding-page",
        ),
        # Held out: nothing in this sentence appears in the prompt. It points
        # at #500 through the row's own words instead of the prompt's.
        pytest.param(
            "finish the Auth0 M2M registration",
            {500},
            id="held-out-auth0-m2m",
        ),
    ],
)
async def test_a_message_naming_particular_work_resolves_to_those_rows(
    message: str, expected: set[int]
) -> None:
    await _assert_rate(message, expected)


@pytest.mark.parametrize(
    "message",
    [
        # Names a topic and a mood of work, not an item. The board holds five
        # C2F-ish tickets; attaching one of them to a broad session is wrong.
        pytest.param(
            "serious c2f work in the morning, gym in the evening",
            id="quoted-c2f-session",
        ),
        # Names work that is not on the board at all.
        pytest.param(
            "book a dentist appointment tomorrow",
            id="quoted-dentist",
        ),
        # Held out, and the hard direction: the snapshot holds three PORTAL
        # tickets (#457, #458, #464), so the empty answer has to survive an
        # area with obvious candidates sitting in it.
        pytest.param(
            "block out the afternoon for portal stuff",
            id="held-out-portal-topic",
        ),
    ],
)
async def test_a_message_naming_a_topic_or_nothing_on_the_board_resolves_to_none(
    message: str,
) -> None:
    await _assert_rate(message, set())
