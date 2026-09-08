# tests/integration/test_eval_work_lookup.py
"""Quality of the work lookup against the live model (Task 4, #-work-links).

`tests/unit/test_work_lookup.py` stubs the model and proves the plumbing. This
is the other half CLAUDE.md requires: the same question put to a real model,
resampled, asserted on the rate. One draw tests the model's luck.

The candidate rows below are the live board's 12 current-sprint Ready tickets
as `TaskBoard.list_tasks("current_sprint_ready")` returned them on 2026-09-08,
in that order — the order is part of the question (see `build_prompt`), so
they are stored in it rather than sorted here. Only the four fields the prompt
renders are kept; the rest of `TaskRow` is filler that never reaches a model.

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
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter

import httpx
import pytest

from fateforger.agents.tasks.board import TaskRow
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


def _rows() -> list[TaskRow]:
    """The board rows as the prompt sees them, in the order the board gave."""
    return [
        TaskRow(
            page_id=page_id,
            number=number,
            name=name,
            summary=summary,
            # Never rendered into the prompt; present because TaskRow requires it.
            status="Not started",
            ticket_status="Ready",
            priority="High",
            dod="",
            url=f"https://www.notion.so/{page_id}",
            last_edited="2026-09-08T00:00:00.000Z",
        )
        for page_id, number, name, summary in BOARD
    ]


async def _ask(prompt: str) -> str:
    """One OpenRouter call, in the request shape the memory judge uses.

    The whole prompt goes in the user turn: `resolve_work`'s `ask` takes one
    string, and splitting it across roles would put the option list somewhere
    the measurement did not cover. `reasoning: minimal` because this is term
    typing rather than deliberation, and it sits in a planning turn's latency.
    No temperature pin — resampling measures the distribution.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
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
    if "choices" not in payload:
        raise AssertionError(f"OpenRouter returned no choices: {json.dumps(payload)}")
    return payload["choices"][0]["message"]["content"]


async def _draws(message: str) -> Counter:
    """SAMPLES answers to one message, counted by the set of task numbers."""
    rows = _rows()
    results = await asyncio.gather(
        *(resolve_work(message, rows, ask=_ask) for _ in range(SAMPLES)),
        return_exceptions=True,
    )
    counted: Counter = Counter()
    for result in results:
        if isinstance(result, BaseException):
            counted[f"raised: {result!r}"] += 1
        else:
            counted[frozenset(row.number for row in result)] += 1
    return counted


def _rate(counted: Counter, expected: frozenset) -> int:
    return counted[expected]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # The case no pattern could reach: #427's title is a Dutch corporate
        # tax filing and carries no finance vocabulary at all.
        ("I want to finish the next finance ticket in the first shallow work block", {427}),
        # Two items, each singled out by a description fitting exactly one row.
        ("get the DNS and the holding page done today", {457, 458}),
    ],
)
async def test_a_message_naming_particular_work_resolves_to_those_rows(
    message: str, expected: set[int]
) -> None:
    counted = await _draws(message)
    hits = _rate(counted, frozenset(expected))
    assert hits >= THRESHOLD, f"{hits}/{SAMPLES} for {message!r}; draws: {counted}"


@pytest.mark.parametrize(
    "message",
    [
        # Names a topic and a mood of work, not an item. The board holds five
        # C2F-ish tickets; attaching one of them to a broad session is wrong.
        "serious c2f work in the morning, gym in the evening",
        # Names work that is not on the board at all.
        "book a dentist appointment tomorrow",
    ],
)
async def test_a_message_naming_a_topic_or_nothing_on_the_board_resolves_to_none(
    message: str,
) -> None:
    counted = await _draws(message)
    hits = _rate(counted, frozenset())
    assert hits >= THRESHOLD, f"{hits}/{SAMPLES} none for {message!r}; draws: {counted}"
