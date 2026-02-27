from __future__ import annotations

import pytest
from pydantic import ValidationError

from fateforger.tools.mcp_url_validation import canonical_mcp_url, rewrite_mcp_host


def test_canonical_mcp_url_adds_scheme_and_default_path() -> None:
    assert canonical_mcp_url("localhost:8000") == "http://localhost:8000/mcp"
    assert canonical_mcp_url("http://localhost:8000") == "http://localhost:8000/mcp"
    assert (
        canonical_mcp_url("https://localhost:8443/custom")
        == "https://localhost:8443/custom"
    )


def test_rewrite_mcp_host_preserves_port_and_path() -> None:
    assert (
        rewrite_mcp_host("http://ticktick-mcp:8000/mcp", "localhost")
        == "http://localhost:8000/mcp"
    )


def test_canonical_mcp_url_rejects_invalid_scheme() -> None:
    with pytest.raises(ValidationError):
        canonical_mcp_url("ftp://example.com/mcp")
