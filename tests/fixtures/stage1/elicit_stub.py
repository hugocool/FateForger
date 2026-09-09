"""One recorder for `elicit`, shared by every test that drives the host.

`resolve(SKELETON)` runs the three Stage 1 judgements (#262), so any test
about something else -- the frame judgement, the Slack route -- would drive
real judges against its own model double. Those tests are not about Stage 1's
quality, only about the host calling it with the rows it fetched and the
snapshot it was given and carrying the result, so `elicit` is replaced
wholesale and the stub records what it saw.

It lives here rather than in one of the three test modules because it was
pasted into three; a stub with three copies is three places a premise can
drift.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fateforger.agents.timeboxing.elicitation import ALL_CELLS, CoverageMatrix
from fateforger.agents.timeboxing.elicitation_judges import ElicitationResult, Judges
from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningFact,
    PlanningSessionSnapshot,
    ProbeDraft,
    coverage_fact_id,
)


class StubElicit:
    """Stands in for `elicit`, recording the `(snapshot, rows, now)` of every call.

    Answers the same shape every time: a matrix at the day's stable id with
    every cell `not_applicable`, and one probe. A test that cares which cells
    are covered belongs in the elicitation suite, against the real judges.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[PlanningSessionSnapshot, list[dict[str, Any]], datetime]] = []

    async def __call__(
        self,
        snapshot: PlanningSessionSnapshot,
        rows: list[dict[str, Any]],
        judges: Judges,
        *,
        session_key: str,
        now: datetime,
        **_: Any,
    ) -> ElicitationResult:
        self.calls.append((snapshot, rows, now))
        matrix = CoverageMatrix(cells={cell.id: "not_applicable" for cell in ALL_CELLS})
        fact = PlanningFact(
            fact_id=coverage_fact_id(snapshot.planning_day.date),
            kind=FactKind.COVERAGE_MATRIX,
            value=matrix.model_dump(mode="json"),
            source="system",
        )
        return ElicitationResult(
            matrix_fact=fact,
            probes=[
                ProbeDraft(cell_id="elicit.body.unclear", question="q?", why_needed="w")
            ],
        )


def install_stub_elicit(monkeypatch: Any) -> StubElicit:
    """Patch `elicit` and `build_judges` where the host imported them.

    Both are patched on the host module by name, which is why the host imports
    them at module level: patching the definition site would leave the host's
    own reference bound to the real one.
    """
    import fateforger.slack_bot.timeboxing_host as host_module

    stub = StubElicit()
    monkeypatch.setattr(host_module, "elicit", stub)
    monkeypatch.setattr(
        host_module,
        "build_judges",
        lambda client: Judges(placement=None, coverage=None, probe=None),
    )
    return stub
