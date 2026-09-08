"""Markdown → Slack ``mrkdwn`` (#179).

One construct per test, asserting the transformation rather than a sample of
prose: the harness's wording changes every turn and none of these tests should
care. Each was mutation-checked -- the production branch it covers was broken on
purpose and this file was confirmed to fail, and to fail here rather than
somewhere incidental.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fateforger.slack_bot import harness_bridge, mrkdwn
from fateforger.slack_bot.mrkdwn import to_mrkdwn

# -- inline ---------------------------------------------------------------


def test_bold_loses_one_asterisk():
    assert to_mrkdwn("a **loud** word") == "a *loud* word"


def test_both_spellings_of_italic_become_underscores():
    """``*x*`` and ``_x_`` are one node to the parser, so both must land on
    Slack's single spelling. A substitution pass cannot do this without first
    deciding whether an asterisk opened bold or italic."""
    assert to_mrkdwn("*soft* and _soft_") == "_soft_ and _soft_"


def test_bold_inside_italic_keeps_both():
    assert to_mrkdwn("_soft **and loud**_") == "_soft *and loud*_"


def test_strikethrough_loses_one_tilde():
    assert to_mrkdwn("~~dropped~~") == "~dropped~"


def test_link_becomes_slack_angle_form():
    assert to_mrkdwn("[the plan](https://x.test/a_b)") == "<https://x.test/a_b|the plan>"


def test_a_url_that_is_its_own_label_is_not_repeated():
    assert to_mrkdwn("<https://x.test/a>") == "<https://x.test/a>"


def test_slacks_reserved_characters_are_escaped():
    """Unescaped, ``<`` opens something Slack tries to interpret."""
    assert to_mrkdwn("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_text_carrying_no_markdown_survives_unchanged():
    assert to_mrkdwn("Stage 1 done. Nothing committed.") == "Stage 1 done. Nothing committed."


def test_a_line_break_inside_a_paragraph_stays_a_line_break():
    """Slack honours newlines, and the harness breaks lines deliberately."""
    assert to_mrkdwn("09:00 gym\n11:00 deep work") == "09:00 gym\n11:00 deep work"


# -- headings -------------------------------------------------------------


def test_a_heading_becomes_a_bold_line_and_keeps_no_hash_markers():
    assert to_mrkdwn("### 1. Calendar Events") == "*1. Calendar Events*"


def test_every_heading_level_renders_the_same_way():
    """Slack has one bold weight; pretending otherwise means re-emitting the
    literal syntax the conversion exists to remove."""
    assert to_mrkdwn("# Top") == "*Top*"
    assert to_mrkdwn("###### Top") == "*Top*"


def test_a_heading_is_separated_from_the_text_under_it():
    assert to_mrkdwn("## Plan\n\nTwo blocks moved.") == "*Plan*\n\nTwo blocks moved."


# -- lists ----------------------------------------------------------------


def test_bullets_become_slacks_bullet_character():
    assert to_mrkdwn("* one\n* two") == "• one\n• two"


def test_a_dash_bullet_is_the_same_bullet():
    assert to_mrkdwn("- one\n- two") == "• one\n• two"


def test_a_nested_bullet_is_indented_under_its_parent():
    assert to_mrkdwn("- one\n  - deeper") == "• one\n  • deeper"


def test_a_numbered_list_keeps_its_numbers():
    assert to_mrkdwn("1. first\n2. second") == "1. first\n2. second"


def test_a_numbered_list_that_does_not_start_at_one_is_not_renumbered():
    assert to_mrkdwn("3. third\n4. fourth") == "3. third\n4. fourth"


def test_inline_markup_inside_a_bullet_is_still_converted():
    assert to_mrkdwn("- **22 rules** suspended") == "• *22 rules* suspended"


# -- code: the case most likely to be broken ------------------------------


def test_a_fence_containing_markdown_is_not_converted():
    body = 'x = "**still bold**" and _still italic_'
    assert to_mrkdwn(f"```python\n{body}\n```") == f"```\n{body}\n```"


def test_a_fence_still_escapes_slacks_reserved_characters():
    """Slack un-escapes entities inside a fence, so escaping there is what
    keeps ``<http://x|y>`` from becoming a live link in a code sample."""
    assert to_mrkdwn("```\n<http://x|y> & z\n```") == "```\n&lt;http://x|y&gt; &amp; z\n```"


def test_an_indented_code_block_is_a_fence_too():
    assert to_mrkdwn("    plan_read()\n") == "```\nplan_read()\n```"


def test_inline_code_containing_markdown_is_not_converted():
    assert to_mrkdwn("call `a **b** c` now") == "call `a **b** c` now"


# -- block furniture ------------------------------------------------------


def test_a_horizontal_rule_becomes_a_drawn_rule_not_three_hyphens():
    rendered = to_mrkdwn("above\n\n---\n\nbelow")
    above, rule, below = rendered.split("\n\n")
    assert (above, below) == ("above", "below")
    assert set(rule) == {"─"}


def test_a_blockquote_keeps_slacks_quote_marker():
    assert to_mrkdwn("> he said\n> twice") == "> he said\n> twice"


def test_blocks_are_separated_by_one_blank_line():
    assert to_mrkdwn("one\n\n\n\ntwo") == "one\n\ntwo"


# -- the seam -------------------------------------------------------------


class _Done:
    returncode = 0
    stderr = ""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_ask_converts_before_anyone_can_post_it(monkeypatch):
    """The conversion lives in the bridge, not at a posting site.

    Both Slack entry points read ``HarnessReply.text`` and both had the same
    bug; a third would have inherited it. This is what makes that impossible.
    """
    monkeypatch.setattr(
        harness_bridge.subprocess,
        "run",
        lambda args, **kwargs: _Done("### Stage 1\n\n**22 rules** suspended"),
    )
    assert harness_bridge.ask("plan tuesday").text == "*Stage 1*\n\n*22 rules* suspended"


def test_a_reply_that_renders_to_nothing_is_not_reported_as_no_output(monkeypatch):
    """Emptiness is judged on what the harness said, not on what survives the
    conversion -- otherwise a rendering quirk gets blamed on the harness, and
    "harness produced no output" sends the reader to the wrong system."""
    monkeypatch.setattr(
        harness_bridge.subprocess, "run", lambda args, **kwargs: _Done("something")
    )
    monkeypatch.setattr(harness_bridge, "to_mrkdwn", lambda text: "")
    assert harness_bridge.ask("plan tuesday").text == ""


def test_an_empty_harness_answer_still_raises(monkeypatch):
    monkeypatch.setattr(harness_bridge.subprocess, "run", lambda args, **kwargs: _Done("   "))
    with pytest.raises(harness_bridge.HarnessError):
        harness_bridge.ask("plan tuesday")


def test_the_renderer_imports_no_regex_engine():
    """CLAUDE.md bans ``re`` outright, and a dialect translator is where the
    temptation to reach for it is strongest. Asserted over the parsed module
    rather than its text, the same way the read-path guard elsewhere is."""
    tree = ast.parse(Path(mrkdwn.__file__).read_text())
    imported = {
        alias.name.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "re" not in imported
    assert "regex" not in imported
