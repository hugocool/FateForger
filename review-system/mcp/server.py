"""
Review System MCP Server
Context Triad: Tools (muscles) + Resources (brain) + Prompts (templates)

Usage:
  stdio:  python -m mcp.server
  SSE:    python -m mcp.server --transport sse --port 8000
  Docker: see docker-compose.yml
"""

import os
import json
import asyncio
from pathlib import Path
from datetime import date, datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# ─── Init ─────────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="review-system",
    version="1.0.0",
    description="Weekly review system — read/write Notion DBs, serve guidelines, provide reasoning templates.",
)

# Lazy-import the tool layer so the MCP server starts fast
# and tools are only loaded when first called
def get_read_tools():
    from tools.read import get_last_review, get_reviews, get_outcomes
    return get_last_review, get_reviews, get_outcomes

def get_write_tools():
    from tools.write import (
        create_review, patch_review_field,
        append_phase_content, create_outcome, update_outcome_status
    )
    return create_review, patch_review_field, append_phase_content, create_outcome, update_outcome_status


# ─── RESOURCE: The Brain ──────────────────────────────────────────────────────
# Agents read this before acting. Serves SKILL.md content at runtime.
# Tool descriptions hint at this URI so agents discover it automatically.

@mcp.resource("mcp://review/guidelines")
def get_guidelines() -> str:
    """
    The complete review system manual.
    Read this resource before conducting a review session or analysing patterns.
    Contains: phase gate conditions, extraction rules, tool contracts,
    session protocol, DB schemas, and directive formats.
    """
    skill_path = Path(__file__).parent.parent / "skills" / "review_system" / "SKILL.md"
    if skill_path.exists():
        return skill_path.read_text()
    # Inline fallback if file not found
    return _INLINE_GUIDELINES


@mcp.resource("mcp://review/schema")
def get_schema() -> str:
    """
    JSON schema for Weekly Reviews and Outcomes databases.
    Use this to understand what fields exist and what types they are.
    """
    return json.dumps({
        "weekly_reviews": {
            "week": "date — ISO format, Monday of the review week",
            "intention": "text — one-sentence operational intention",
            "wip_count": "number — items in progress at review time",
            "themes": "text — 3-5 word signal, e.g. 'sales avoidance, scope creep'",
            "failure_looks_like": "text — concrete pre-mortem, observable state",
            "thursday_signal": "text — leading indicator visible by midweek",
            "clarity_gaps": "text — where extraction required most pushback",
            "timebox_directives": "text — compact rules for timebox agent",
            "scrum_directives": "text — ticket patterns for scrum agent",
        },
        "outcomes": {
            "title": "text — verb + artifact, e.g. 'Send Bart pain-point message'",
            "dod": "text — single binary sentence",
            "priority": "select — Must | Support",
            "status": "select — Hit | Partial | Miss",
            "review": "relation → Weekly Reviews",
            "ticket": "url — optional link to Notion/TickTick ticket",
        }
    }, indent=2)


# ─── PROMPT: The Template ─────────────────────────────────────────────────────
# McpWorkbench calls prompts/get to bootstrap agent reasoning.
# The agent receives a pre-formatted reasoning chain instead of needing
# the full SKILL.md in its system message.

@mcp.prompt("review_session")
def review_session_prompt() -> str:
    """
    Bootstraps a review session. Returns a reasoning chain that primes
    the agent with phase structure, gate conditions, and extraction rules.
    Use this as the system message for a review runner agent.
    """
    return """You are conducting a weekly review session using the Review System.

CRITICAL: Read mcp://review/guidelines before your first response. It contains the complete phase structure, gate conditions, and extraction rules you must follow.

SESSION STARTUP (do this before Phase 1):
1. Call get_last_review() — load last week's row
2. Call get_outcomes(review_id) — load last week's outcomes  
3. Present each outcome, ask for Hit/Partial/Miss score
4. Call update_outcome_status() for each scored outcome
5. Call create_review(week_date) — create this week's row, store the review_id
6. Begin Phase 1

CORE RULES (memorise these):
- Never suggest options — extract from the user
- Never advance a gate until fully met
- Write to Notion immediately when each gate is met — never batch
- One question at a time
- Synthesise what you heard before asking the next question

PHASE WRITE TRIGGERS:
- Phase 1 complete → patch_review_field(themes) + append_phase_content('reflect', ...)
- Phase 2 complete → patch_review_field(wip_count) + append_phase_content('board_scan', ...)
- Phase 3 each outcome → create_outcome(review_id, title, dod, priority)
- Phase 4 complete → patch_review_field(failure_looks_like) + patch_review_field(thursday_signal) + append_phase_content('risks_systems', ...)
- Phase 5 complete → patch_review_field(intention) + patch_review_field(timebox_directives) + patch_review_field(scrum_directives) + patch_review_field(clarity_gaps) + append_phase_content('close', ...)

Start by loading last week's data and scoring last week's outcomes."""


@mcp.prompt("pattern_analysis")
def pattern_analysis_prompt() -> str:
    """
    Bootstraps a standalone pattern analysis session.
    Use when running analysis outside of a review session.
    """
    return """You are running a pattern analysis on the user's review history.

Call get_reviews(n=8) and get_outcomes() for each review row.

Produce a structured analysis covering:
1. Must-outcome hit rate (counts: Hit / Partial / Miss across last N weeks)
2. Recurring themes (which theme words keep appearing)
3. Recurring clarity gaps (where extraction was consistently hard)
4. Current timebox_directives from the last review
5. Current scrum_directives from the last review
6. Trend: is hit rate improving, stable, or declining?

Be specific. Reference actual data from the DB — week dates, outcome titles, exact theme text.
Do not generalise from no data."""


# ─── TOOLS: The Muscles ───────────────────────────────────────────────────────
# CRITICAL: Before calling any tool, read mcp://review/guidelines
# to understand the session protocol and when to call each tool.

@mcp.tool()
async def get_last_review() -> dict:
    """
    Returns the most recent Weekly Review row with all properties.
    
    WHEN TO CALL: At the very start of every session, before Phase 1.
    Use the returned review_id to call get_outcomes().
    
    CRITICAL: Read mcp://review/guidelines before your first response.
    
    Returns: dict with keys: id, week, intention, wip_count, themes,
             failure_looks_like, thursday_signal, clarity_gaps,
             timebox_directives, scrum_directives
             OR {"exists": false} if no reviews yet.
    """
    fn, _, _ = get_read_tools()
    return await fn()


@mcp.tool()
async def get_reviews(n: int = 8) -> list:
    """
    Returns the last N Weekly Review rows, ordered by date descending.
    
    WHEN TO CALL: For pattern analysis across multiple weeks.
    
    Args:
        n: Number of reviews to return (default 8, max 52)
    
    Returns: list of dicts, same schema as get_last_review()
    """
    _, fn, _ = get_read_tools()
    return await fn(min(n, 52))


@mcp.tool()
async def get_outcomes(review_id: str) -> list:
    """
    Returns all Outcome rows linked to the given review.
    
    WHEN TO CALL: At session open, after get_last_review().
    Present these to the user for scoring (Hit/Partial/Miss)
    before creating the new week's review row.
    
    Args:
        review_id: The id field from a Weekly Review row
    
    Returns: list of dicts with keys: id, title, dod, priority, status, ticket
    """
    _, _, fn = get_read_tools()
    return await fn(review_id)


@mcp.tool()
async def create_review(week_date: str) -> str:
    """
    Creates a new Weekly Review row and returns its ID.
    
    WHEN TO CALL: At the very start of each session, after scoring
    last week's outcomes. Use Monday's date for the week.
    Store the returned review_id — all subsequent patch calls need it.
    
    Args:
        week_date: ISO date string for Monday of this week, e.g. "2026-03-10"
    
    Returns: review_id (str) — store this for the session
    """
    fn, _, _, _, _ = get_write_tools()
    return await fn(week_date)


@mcp.tool()
async def patch_review_field(review_id: str, field: str, value: str) -> None:
    """
    Patches a single field on an existing Weekly Review row.
    
    WHEN TO CALL: Immediately when each phase gate is met — not at session end.
    One call per field. Never call this to replace the full row.
    
    VALID FIELDS:
    - intention          (Phase 5)
    - wip_count          (Phase 2) — pass as string, will be cast to int
    - themes             (Phase 1) — 3-5 word signal
    - failure_looks_like (Phase 4) — concrete pre-mortem
    - thursday_signal    (Phase 4) — leading indicator
    - clarity_gaps       (Phase 5) — where extraction was hardest
    - timebox_directives (Phase 5) — compact rules for timebox agent
    - scrum_directives   (Phase 5) — ticket patterns for scrum agent
    
    Args:
        review_id: From create_review() return value
        field: One of the valid field names above
        value: The value to write
    """
    _, fn, _, _, _ = get_write_tools()
    await fn(review_id, field, value)


@mcp.tool()
async def append_phase_content(review_id: str, phase: str, markdown: str) -> None:
    """
    Appends the narrative for a completed phase to the review page body.
    Always appends — never replaces existing content.
    
    WHEN TO CALL: After each phase gate is met, write the narrative.
    This preserves the full conversation context in Notion.
    
    VALID PHASES: "reflect" | "board_scan" | "risks_systems" | "close"
    
    Args:
        review_id: From create_review() return value
        phase: One of the valid phase names above
        markdown: The narrative content for this phase (free-form markdown)
    """
    _, _, fn, _, _ = get_write_tools()
    await fn(review_id, phase, markdown)


@mcp.tool()
async def create_outcome(
    review_id: str,
    title: str,
    dod: str,
    priority: str,
    ticket: Optional[str] = None
) -> str:
    """
    Creates an Outcome row linked to the current review.
    
    WHEN TO CALL: During Phase 3, one call per outcome as each is compressed
    to a binary DoD. Do NOT wait until session end.
    
    Args:
        review_id: From create_review() return value
        title: Verb + artifact, e.g. "Send Bart pain-point message (artifact)"
        dod: Single binary sentence, e.g. "Message sent and calendar invite accepted"
        priority: "Must" | "Support"
        ticket: Optional URL to the linked Notion/TickTick ticket
    
    Returns: outcome_id (str) — not needed during the session but useful for reference
    """
    _, _, _, fn, _ = get_write_tools()
    return await fn(review_id, title, dod, priority, ticket)


@mcp.tool()
async def update_outcome_status(outcome_id: str, status: str) -> None:
    """
    Updates the status of an outcome from a previous session.
    
    WHEN TO CALL: At the start of each new session, after get_outcomes(),
    before create_review(). Score each outcome from last week.
    
    Args:
        outcome_id: The id field from an Outcome row
        status: "Hit" | "Partial" | "Miss"
    """
    _, _, _, _, fn = get_write_tools()
    await fn(outcome_id, status)


# ─── Inline guidelines fallback ───────────────────────────────────────────────
_INLINE_GUIDELINES = """
# Review System Guidelines
See skills/review_system/SKILL.md for the full version.
Key rules:
- Never suggest — only extract
- Never advance a gate until fully met  
- Write to Notion immediately on each gate — never batch
- Process language in DoDs → push back
- One question at a time
""".strip()


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    transport = "sse" if "--transport" in sys.argv and "sse" in sys.argv else "stdio"
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000

    if transport == "sse":
        mcp.run(transport="sse", host="0.0.0.0", port=port)
    else:
        mcp.run(transport="stdio")
