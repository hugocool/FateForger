"""
Read tools — stateless, no review logic.
All session calls are synchronous (ultimate-notion) run in a thread via asyncio.to_thread.
"""
import asyncio
import os
from typing import Optional
from datetime import date

import ultimate_notion as uno


# ── Session helper ────────────────────────────────────────────────────────────
# Each tool call opens and closes its own session. Thread-safe.

def _session():
    return uno.Session()


def _review_db(notion: uno.Session):
    return notion.get_or_create_db(
        parent=None,  # won't create — DB must already exist
        schema=_import_weekly_review(),
    )


def _import_weekly_review():
    from models.weekly_review import WeeklyReview
    return WeeklyReview


def _import_outcome():
    from models.outcome import Outcome
    return Outcome


# ── Serialisers ───────────────────────────────────────────────────────────────

def _review_to_dict(page) -> dict:
    p = page.props
    return {
        "id":                  str(page.id),
        "week":                str(p.week.start) if p.week else None,
        "intention":           str(p.intention)          if p.intention          else "",
        "wip_count":           p.wip_count,
        "themes":              str(p.themes)             if p.themes             else "",
        "failure_looks_like":  str(p.failure_looks_like) if p.failure_looks_like else "",
        "thursday_signal":     str(p.thursday_signal)    if p.thursday_signal    else "",
        "clarity_gaps":        str(p.clarity_gaps)       if p.clarity_gaps       else "",
        "timebox_directives":  str(p.timebox_directives) if p.timebox_directives else "",
        "scrum_directives":    str(p.scrum_directives)   if p.scrum_directives   else "",
    }


def _outcome_to_dict(page) -> dict:
    p = page.props
    # Relation returns a View of linked pages; grab first ID if present
    review_pages = list(p.review) if p.review else []
    review_id = str(review_pages[0].id) if review_pages else None
    return {
        "id":        str(page.id),
        "title":     str(page.title),
        "dod":       str(p.dod)      if p.dod      else "",
        "priority":  p.priority.name if p.priority else "",
        "status":    p.status.name   if p.status   else "",
        "ticket":    str(p.ticket)   if p.ticket   else "",
        "review_id": review_id,
    }


# ── Sync implementations (run in thread) ─────────────────────────────────────

def _sync_get_last_review() -> dict:
    WeeklyReview = _import_weekly_review()
    with _session() as notion:
        db = notion.search_db('Weekly Reviews').item()
        WeeklyReview.bind_db(db)
        view = db.query.sort(uno.prop('week').desc()).execute()
        if not view:
            return {"exists": False}
        return _review_to_dict(view[0])


def _sync_get_reviews(n: int) -> list:
    WeeklyReview = _import_weekly_review()
    with _session() as notion:
        db = notion.search_db('Weekly Reviews').item()
        WeeklyReview.bind_db(db)
        view = db.query.sort(uno.prop('week').desc()).execute()
        return [_review_to_dict(p) for p in list(view)[:n]]


def _sync_get_outcomes(review_id: str) -> list:
    Outcome = _import_outcome()
    with _session() as notion:
        db = notion.search_db('Outcomes').item()
        Outcome.bind_db(db)
        view = db.query.filter(
            uno.prop('review').contains(review_id)
        ).execute()
        return [_outcome_to_dict(p) for p in view]


# ── Async public API ──────────────────────────────────────────────────────────

async def get_last_review() -> dict:
    """Returns the most recent Weekly Review row, or {"exists": False} if none."""
    return await asyncio.to_thread(_sync_get_last_review)


async def get_reviews(n: int = 8) -> list:
    """Returns last N Weekly Review rows ordered by date descending."""
    return await asyncio.to_thread(_sync_get_reviews, min(n, 52))


async def get_outcomes(review_id: str) -> list:
    """Returns all Outcome rows linked to the given review_id."""
    return await asyncio.to_thread(_sync_get_outcomes, review_id)
