from __future__ import annotations

from fateforger.llm.toon import toon_encode


def test_toon_encode_emits_header_and_rows() -> None:
    out = toon_encode(
        name="users",
        rows=[{"id": 1, "name": "Alice", "role": "admin"}, {"id": 2, "name": "Bob", "role": "user"}],
        fields=["id", "name", "role"],
    )
    assert out.startswith("users[2]{id,name,role}:")
    assert "1,Alice,admin" in out
    assert "2,Bob,user" in out


def test_toon_encode_quotes_commas() -> None:
    out = toon_encode(
        name="items",
        rows=[{"name": "Hello, world", "note": "x"}],
        fields=["name", "note"],
    )
    assert 'items[1]{name,note}:' in out
    assert '"Hello, world",x' in out

