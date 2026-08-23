"""The admonisher persona, carried as a DSH skill rather than an agent.

Separate agents with handoffs is an AutoGen shape. Under the harness there is
one loop, so a handoff is really a decision about how much context to bring
into scope — progressive disclosure — and the persona is a skill that loads
when the conversation earns it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2] / ".dsh" / "skills" / "admonisher" / "SKILL.md"


@pytest.fixture(scope="module")
def parts():
    text = SKILL.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert match, "SKILL.md needs YAML frontmatter or the harness will not index it"
    return match.group(1), match.group(2)


def test_the_skill_is_where_the_harness_looks(parts):
    """Rank 100 is <projectRoot>/.dsh/skills, project root being the git root.

    In the repo rather than ~/.dsh, so it is versioned — the profile and its
    symlinks already live unversioned outside any repo and nothing announces
    their absence on a rebuild.
    """
    assert SKILL.exists()


def test_it_declares_a_name_and_a_description(parts):
    """Discovery parses frontmatter; a missing field means it never appears."""
    front, _ = parts
    assert re.search(r"^name:\s*admonisher\s*$", front, re.M)
    assert re.search(r"^description:\s*\S", front, re.M)


def test_the_description_says_when_to_load_it_not_what_it_is(parts):
    """The catalog is how the model chooses. A noun phrase gives it nothing."""
    front, _ = parts
    description = re.search(r"^description:\s*(.+)$", front, re.M).group(1)
    assert "Use when" in description
    assert len(description) > 120, "too thin to discriminate against other skills"


def test_it_says_what_it_is_not_for(parts):
    """Two neighbours it would otherwise steal turns from."""
    _, body = parts
    assert "timeboxing" in body
    assert "planner" in body


def test_the_persona_survived(parts):
    """The voice is the point; a bare rules list is not the admonisher."""
    _, body = parts
    assert "Stoica" in body
    assert "hero in training" in body


def test_it_still_refuses_a_vague_deferral(parts):
    """The one behaviour the old prompt was most specific about."""
    _, body = parts
    assert "vague deferral" in body.lower()
    assert "concrete time" in body


def test_handoffs_became_disclosure_rather_than_transfer(parts):
    """One loop. Announcing a transfer would describe machinery that is gone."""
    _, body = parts
    assert "handoff" in body.lower()
    assert "one loop" in body.lower()


def test_it_does_not_claim_to_schedule_nudges(parts):
    """Admonishing runs on its own schedule and storage.

    A skill that says it set a reminder, when it cannot, is a promise the user
    would rely on.
    """
    _, body = parts
    assert "do not schedule" in body.lower()


def test_it_must_not_invent_commitments(parts):
    """The whole value rests on the line between stated and inferred.

    An admonisher that blurs it manufactures obligations and then holds him to
    them — the same failure as a plan that does not mark its assumptions, and
    as a journal recording ACCEPTED for a commit nobody was asked about.
    """
    _, body = parts
    assert "did not say" in body
    assert "ask" in body.lower()
