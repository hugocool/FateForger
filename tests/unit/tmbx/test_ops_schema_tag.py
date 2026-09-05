"""The wire schema names ``op`` as required on every op (#171).

``op`` is a ``Literal`` with a Python default so code can build an
``AddBlock`` without restating it. Pydantic turned that default into
``"default": "add"`` and left ``op`` out of ``required`` in the JSON schema
-- the schema that ``tmbx://schema/ops`` inlines into the planner's system
prompt. A schema-following model read that as permission to omit the tag:
resampled on 2026-09-05, gemini-3.6-flash left ``op`` off every op of its
first patch in 6 of 10 runs, and the server refused each with
``Unable to extract tag using discriminator 'op'``. Marking it required in
the schema alone cut that to 2 of 10.

These tests pin the wire shape. The Python default is kept on purpose.
"""

from __future__ import annotations

import pytest

from tmbx.core.ops import AddBlock, MoveBlock, Patch, RemoveBlock, UpdateBlock

OPS = {
    "AddBlock": ("add", AddBlock),
    "RemoveBlock": ("remove", RemoveBlock),
    "UpdateBlock": ("update", UpdateBlock),
    "MoveBlock": ("move", MoveBlock),
}


@pytest.mark.parametrize("name", sorted(OPS))
def test_the_schema_the_planner_reads_requires_op_on_every_op(name: str) -> None:
    defn = Patch.model_json_schema()["$defs"][name]
    assert "op" in defn["required"], f"{name}: op is not required on the wire"
    assert defn["required"][0] == "op", f"{name}: op should lead, it is the tag"


@pytest.mark.parametrize("name", sorted(OPS))
def test_the_schema_offers_no_default_for_op(name: str) -> None:
    """A default is an invitation to omit. The tag has exactly one value and no fallback."""
    prop = Patch.model_json_schema()["$defs"][name]["properties"]["op"]
    assert "default" not in prop
    assert prop["const"] == OPS[name][0]


def test_python_construction_still_defaults_the_tag() -> None:
    """The wire requires it; code that builds ops directly does not restate it."""
    assert RemoveBlock(h="DW1").op == "remove"
    assert MoveBlock(h="DW1").op == "move"
    assert UpdateBlock(h="DW1", n="x").op == "update"


def test_the_preamble_names_op_as_the_tag_before_the_schema() -> None:
    """The schema alone got 8 of 10; naming the key in prose took it to 19 of 20.

    Asserts the resource text -- a string this system minted, not user content.
    """
    from tmbx.server import _OPS_SCHEMA_PREAMBLE

    preamble = _OPS_SCHEMA_PREAMBLE
    assert 'EVERY OP CARRIES `"op"`' in preamble
    assert "not `type`" in preamble
    assert preamble.index('`"op"`') < preamble.index("ADDS ARE APPLIED"), (
        "the tag rule must come before the ordering rules, which it governs"
    )


def test_an_untagged_op_is_still_refused_with_the_discriminator_message() -> None:
    """The refusal a model sees when it omits the tag; the schema change must not soften it."""
    with pytest.raises(ValueError, match="discriminator 'op'"):
        Patch.model_validate({"ops": [{"h": "DW1"}]})
