"""GitHub-flavoured Markdown → Slack ``mrkdwn``.

The harness answers in Markdown. Slack speaks a different dialect that happens
to share some characters, so an unconverted answer arrives with its own syntax
showing: ``### Heading``, ``**bold**``, ``---`` and ``* `` bullets printed
literally (#179).

**Why a parser and not a substitution pass.** ``re`` is banned project-wide, and
the ban is right here for a reason beyond the letter of it: a character-level
translator has no idea what it is inside. It converts the ``**`` in a code fence,
breaks a URL containing underscores, and mangles a bold span that crosses a line
break -- and it does all of that silently, on the one input nobody wrote a test
for. Parsing to an AST makes every construct an explicit case, and a construct
this file does not name renders as its own literal text rather than as a wrong
guess.

**Why mistune.** Already installed via ``ultimate-notion``, and its
``renderer=None`` mode hands back a plain nested ``dict`` AST -- no renderer
subclass, no token-stream state machine. markdown-it-py is equally capable and
was rejected only because its flat token stream costs more code to walk for the
same result.

``mrkdwn`` is poorer than Markdown, so three constructs have no target and need
a decision rather than a default; each is argued at its own branch below.
"""

from __future__ import annotations

import textwrap
from html import escape
from typing import Any

import mistune

#: AST mode. Strikethrough is the one GFM extension worth enabling: it has an
#: exact mrkdwn equivalent (``~x~``). Tables deliberately stay off -- Slack has
#: no table, and an unparsed table survives as its own pipe-separated lines,
#: which reads better than anything this file could flatten it into.
_parse = mistune.create_markdown(renderer=None, plugins=["strikethrough"])


def _escape(text: str) -> str:
    """Escape Slack's three reserved characters, and only those.

    Applied to every emitted run *including code*, because Slack un-escapes
    these inside code blocks too -- skipping them there is how a fence
    containing ``<http://x|y>`` turns into a live link. ``quote=False`` because
    Slack does not reserve quotes, and escaping them would show the entity.
    """
    return escape(text, quote=False)


def _inline(nodes: list[dict[str, Any]]) -> str:
    return "".join(_inline_node(node) for node in nodes)


def _inline_node(node: dict[str, Any]) -> str:
    kind = node["type"]
    if kind == "text":
        return _escape(node["raw"])
    if kind == "strong":
        # The whole point of the issue: Slack's bold is one asterisk.
        return f"*{_inline(node['children'])}*"
    if kind == "emphasis":
        # Both Markdown spellings of italic (``*x*`` and ``_x_``) parse to this
        # one node, which is why the conversion is unambiguous in the direction
        # that matters and would not be as a text substitution.
        return f"_{_inline(node['children'])}_"
    if kind == "strikethrough":
        return f"~{_inline(node['children'])}~"
    if kind == "codespan":
        return f"`{_escape(node['raw'])}`"
    if kind in ("link", "image"):
        url = _escape(node["attrs"]["url"])
        label = _inline(node.get("children", []))
        # ``<url>`` when the label is just the URL again: Slack renders the bare
        # form identically and ``<url|url>`` only looks like a mistake.
        return f"<{url}>" if label == url else f"<{url}|{label}>"
    if kind in ("softbreak", "linebreak"):
        # Kept as a real newline. Slack honours newlines inside a message, so
        # the author's line layout survives; re-flowing it would silently
        # rewrite a deliberately broken list of times into a paragraph.
        return "\n"
    if kind in ("inline_html", "block_html"):
        return _escape(node["raw"])
    # An unrecognised inline node renders as its own text rather than vanishing.
    return _escape(node.get("raw", ""))


def _list(node: dict[str, Any]) -> str:
    ordered = bool(node["attrs"]["ordered"])
    start = int(node["attrs"].get("start", 1))
    lines: list[str] = []
    for index, item in enumerate(node["children"], start):
        lead = f"{index}. " if ordered else "• "
        pad = " " * len(lead)
        body = "\n".join(
            rendered for rendered in (_block(child) for child in item["children"]) if rendered
        )
        # Indent the item whole, then replace the first line's padding with the
        # marker. A nested list is just another block of this item, so it gets
        # indented by the same step without needing to know its own depth.
        block = textwrap.indent(body, pad)
        lines.append(lead + block[len(pad) :])
    return "\n".join(lines)


def _block(node: dict[str, Any]) -> str:
    kind = node["type"]
    if kind in ("paragraph", "block_text"):
        return _inline(node["children"])
    if kind == "heading":
        # mrkdwn has no headings at any level, so this is a choice, not a
        # mapping. A bold line on its own is the closest thing Slack renders,
        # and blocks are already separated by a blank line below, which is what
        # keeps it reading as a heading rather than as an emphatic sentence.
        # The level is dropped: Slack has exactly one bold weight, so h1 and h3
        # would render identically anyway, and prefixing anything to mark the
        # difference reintroduces the literal syntax this file exists to remove.
        return f"*{_inline(node['children'])}*"
    if kind == "block_code":
        # Untouched: no escaping of Markdown, no conversion of anything inside.
        # The language hint is dropped because Slack renders it as the code's
        # first line, which is worse than losing it.
        return "```\n" + _escape(node["raw"]).rstrip("\n") + "\n```"
    if kind == "list":
        return _list(node)
    if kind == "block_quote":
        inner = "\n".join(
            rendered for rendered in (_block(child) for child in node["children"]) if rendered
        )
        return textwrap.indent(inner, "> ")
    if kind == "thematic_break":
        # Slack's real divider exists only in Block Kit, and this seam carries
        # text. A drawn rule is the substitute; dropping the break outright was
        # rejected because the harness uses it to separate stages, and merging
        # two stages into one wall of text is the failure the issue reports.
        return "─" * 24
    if kind == "blank_line":
        # Block separation is handled by the join below; emitting anything here
        # would double every gap.
        return ""
    if kind in ("block_html", "block_error"):
        return _escape(node.get("raw", ""))
    return _inline(node.get("children", []))


def to_mrkdwn(markdown: str) -> str:
    """Render Markdown as Slack ``mrkdwn``.

    Total: text carrying no Markdown at all comes back as itself, modulo the
    three characters Slack reserves.
    """
    blocks = (_block(node) for node in _parse(markdown))
    return "\n\n".join(block for block in blocks if block)
