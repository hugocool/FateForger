"""
Round-trip tests for the tool layer against live Notion DBs.
Requires NOTION_TOKEN set in .env and both DBs to exist.

Run: pytest tests/ -v
"""
import pytest
import os
from datetime import date
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.skipif(
    not os.getenv("NOTION_TOKEN"),
    reason="NOTION_TOKEN not set — configure .env first"
)


@pytest.mark.asyncio
async def test_create_and_read_review():
    from tools.write import create_review, patch_review_field
    from tools.read import get_last_review

    today = date.today().isoformat()
    review_id = await create_review(today)
    assert review_id

    await patch_review_field(review_id, "themes", "test: focus drift")
    last = await get_last_review()
    assert last["themes"] == "test: focus drift"
    assert last["week"] == today
    assert last["exists"] is not False


@pytest.mark.asyncio
async def test_patch_all_fields():
    from tools.write import create_review, patch_review_field
    from tools.read import get_last_review

    review_id = await create_review(date.today().isoformat())

    fields = {
        "intention":           "Ship the thing",
        "wip_count":           "5",
        "themes":              "delivery focus",
        "failure_looks_like":  "Friday: landing page not live",
        "thursday_signal":     "PR merged by Thursday noon",
        "clarity_gaps":        "Phase 3 outcomes took 3 rounds",
        "timebox_directives":  "Max 90 min on any single ticket",
        "scrum_directives":    "DoDs must be URL-verifiable",
    }

    for field, value in fields.items():
        await patch_review_field(review_id, field, value)

    last = await get_last_review()
    assert last["intention"] == "Ship the thing"
    assert last["wip_count"] == 5
    assert last["thursday_signal"] == "PR merged by Thursday noon"


@pytest.mark.asyncio
async def test_create_and_score_outcomes():
    from tools.write import create_review, create_outcome, update_outcome_status
    from tools.read import get_outcomes

    review_id = await create_review(date.today().isoformat())

    must_id = await create_outcome(
        review_id,
        title="Ship landing page",
        dod="Page is live at /landing and passes mobile check",
        priority="Must",
    )
    support_id = await create_outcome(
        review_id,
        title="Draft onboarding email",
        dod="Email draft exists in Notion at /emails/onboarding",
        priority="Support",
        ticket="https://notion.so/example",
    )

    outcomes = await get_outcomes(review_id)
    assert len(outcomes) == 2
    titles = [o["title"] for o in outcomes]
    assert "Ship landing page" in titles
    assert "Draft onboarding email" in titles

    await update_outcome_status(must_id, "Hit")
    await update_outcome_status(support_id, "Miss")

    outcomes = await get_outcomes(review_id)
    by_id = {o["id"]: o for o in outcomes}
    assert by_id[must_id]["status"] == "Hit"
    assert by_id[support_id]["status"] == "Miss"


@pytest.mark.asyncio
async def test_append_phase_content():
    from tools.write import create_review, append_phase_content

    review_id = await create_review(date.today().isoformat())
    # Should not raise
    await append_phase_content(
        review_id, "reflect",
        "Won: shipped the API integration.\n\nMissed: demo prep — ran out of time Thursday."
    )
    await append_phase_content(
        review_id, "board_scan",
        "WIP: 6 items. Stale: 2 items untouched since 15 days ago.\n\nBalance: 40% build, 30% sales, 30% systems."
    )


@pytest.mark.asyncio
async def test_get_reviews_pagination():
    from tools.read import get_reviews

    reviews = await get_reviews(n=3)
    assert isinstance(reviews, list)
    assert len(reviews) <= 3
    for r in reviews:
        assert "id" in r
        assert "week" in r


@pytest.mark.asyncio
async def test_invalid_field_raises():
    from tools.write import patch_review_field
    with pytest.raises(ValueError, match="Invalid field"):
        await patch_review_field("fake-id", "not_a_real_field", "value")


@pytest.mark.asyncio
async def test_invalid_status_raises():
    from tools.write import update_outcome_status
    with pytest.raises(ValueError, match="status must be"):
        await update_outcome_status("fake-id", "Maybe")


@pytest.mark.asyncio
async def test_invalid_priority_raises():
    from tools.write import create_outcome
    with pytest.raises(ValueError, match="priority must be"):
        await create_outcome("fake-id", "title", "dod", "Kinda Important")
