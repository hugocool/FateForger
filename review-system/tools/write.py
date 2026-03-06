"""
Write tools — stateless, incremental, no review logic.
Each function does exactly one thing. Never batches.
All session calls are synchronous (ultimate-notion) run in a thread.
"""
import asyncio
from typing import Optional
from datetime import date

import ultimate_notion as uno


def _session():
    return uno.Session()


def _import_weekly_review():
    from models.weekly_review import WeeklyReview
    return WeeklyReview


def _import_outcome():
    from models.outcome import Outcome, PriorityOptions, StatusOptions
    return Outcome, PriorityOptions, StatusOptions


# ── Field map: tool arg name → schema attribute name ─────────────────────────
# Maps the string field names used by the MCP tool API to Python attribute names.
FIELD_ATTR = {
    "intention":           "intention",
    "wip_count":           "wip_count",
    "themes":              "themes",
    "failure_looks_like":  "failure_looks_like",
    "thursday_signal":     "thursday_signal",
    "clarity_gaps":        "clarity_gaps",
    "timebox_directives":  "timebox_directives",
    "scrum_directives":    "scrum_directives",
}

PHASE_HEADINGS = {
    "reflect":       "Phase 1 — Reflect",
    "board_scan":    "Phase 2 — Board Scan",
    "risks_systems": "Phase 4 — Risks & Systems",
    "close":         "Phase 5 — Close",
}


# ── Sync implementations ──────────────────────────────────────────────────────

def _sync_create_review(week_date: str) -> str:
    WeeklyReview = _import_weekly_review()
    with _session() as notion:
        db = notion.search_db('Weekly Reviews').item()
        WeeklyReview.bind_db(db)
        page = WeeklyReview.create(
            week=date.fromisoformat(week_date),
        )
        return str(page.id)


def _sync_patch_review_field(review_id: str, field: str, value: str) -> None:
    if field not in FIELD_ATTR:
        raise ValueError(f"Invalid field '{field}'. Valid fields: {sorted(FIELD_ATTR)}")

    WeeklyReview = _import_weekly_review()
    with _session() as notion:
        page = notion.get_page(review_id)
        attr = FIELD_ATTR[field]

        if field == "wip_count":
            setattr(page.props, attr, int(value))
        else:
            setattr(page.props, attr, value)
        # ultimate-notion triggers the API call on assignment — nothing else needed


def _sync_append_phase_content(review_id: str, phase: str, markdown: str) -> None:
    if phase not in PHASE_HEADINGS:
        raise ValueError(f"Invalid phase '{phase}'. Valid: {sorted(PHASE_HEADINGS)}")

    heading_text = PHASE_HEADINGS[phase]
    paragraphs = [p.strip() for p in markdown.split("\n\n") if p.strip()]

    blocks = [uno.Heading2(heading_text)]
    blocks += [uno.Paragraph(p) for p in paragraphs]

    with _session() as notion:
        page = notion.get_page(review_id)
        page.append(blocks)


def _sync_create_outcome(
    review_id: str, title: str, dod: str,
    priority: str, ticket: Optional[str]
) -> str:
    if priority not in ("Must", "Support"):
        raise ValueError("priority must be 'Must' or 'Support'")

    Outcome, PriorityOptions, _ = _import_outcome()

    with _session() as notion:
        db = notion.search_db('Outcomes').item()
        Outcome.bind_db(db)

        # Resolve the priority Option object from the schema
        priority_option = (
            PriorityOptions.MUST if priority == "Must" else PriorityOptions.SUPPORT
        )

        # Get the related review page
        review_page = notion.get_page(review_id)

        kwargs = dict(
            title=title,
            dod=dod,
            priority=priority_option,
            review=[review_page],
        )
        if ticket:
            kwargs["ticket"] = ticket

        page = Outcome.create(**kwargs)
        return str(page.id)


def _sync_update_outcome_status(outcome_id: str, status: str) -> None:
    if status not in ("Hit", "Partial", "Miss"):
        raise ValueError("status must be 'Hit', 'Partial', or 'Miss'")

    _, _, StatusOptions = _import_outcome()

    status_option = {
        "Hit":     StatusOptions.HIT,
        "Partial": StatusOptions.PARTIAL,
        "Miss":    StatusOptions.MISS,
    }[status]

    with _session() as notion:
        page = notion.get_page(outcome_id)
        page.props.status = status_option


# ── Async public API ──────────────────────────────────────────────────────────

async def create_review(week_date: str) -> str:
    """Creates a new Weekly Review row. Returns review_id."""
    return await asyncio.to_thread(_sync_create_review, week_date)


async def patch_review_field(review_id: str, field: str, value: str) -> None:
    """Patches a single field on an existing Weekly Review row."""
    await asyncio.to_thread(_sync_patch_review_field, review_id, field, value)


async def append_phase_content(review_id: str, phase: str, markdown: str) -> None:
    """Appends phase narrative to the review page body. Always appends, never replaces."""
    await asyncio.to_thread(_sync_append_phase_content, review_id, phase, markdown)


async def create_outcome(
    review_id: str,
    title: str,
    dod: str,
    priority: str,
    ticket: Optional[str] = None,
) -> str:
    """Creates an Outcome row linked to the review. Returns outcome_id."""
    return await asyncio.to_thread(
        _sync_create_outcome, review_id, title, dod, priority, ticket
    )


async def update_outcome_status(outcome_id: str, status: str) -> None:
    """Updates the status of an outcome from a previous session."""
    await asyncio.to_thread(_sync_update_outcome_status, outcome_id, status)
