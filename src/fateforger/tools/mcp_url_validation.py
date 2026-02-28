"""Typed MCP URL validation and normalization helpers."""

from __future__ import annotations

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, field_validator
from yarl import URL


class McpUrlSpec(BaseModel):
    """Validated MCP URL spec with scheme coercion and canonical path."""

    model_config = ConfigDict(frozen=True)

    url: AnyHttpUrl
    default_path: str = "/mcp"

    @field_validator("url", mode="before")
    @classmethod
    def _coerce_scheme(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("MCP URL is not configured.")
        return text if "://" in text else f"http://{text}"

    @field_validator("default_path")
    @classmethod
    def _coerce_default_path(cls, value: str) -> str:
        text = (value or "").strip() or "/mcp"
        return text if text.startswith("/") else f"/{text}"

    def as_url(self) -> URL:
        parsed = URL(str(self.url))
        canonical_path = parsed.path if parsed.path not in {"", "/"} else self.default_path
        return parsed.with_path(canonical_path)


def canonical_mcp_url(raw_url: str, *, default_path: str = "/mcp") -> str:
    """Return a canonical MCP URL."""
    return str(McpUrlSpec(url=raw_url, default_path=default_path).as_url())


def rewrite_mcp_host(raw_url: str, host: str, *, default_path: str = "/mcp") -> str:
    """Rewrite hostname while keeping scheme/port/path."""
    return str(
        McpUrlSpec(url=raw_url, default_path=default_path).as_url().with_host(host)
    )


__all__ = ["McpUrlSpec", "canonical_mcp_url", "rewrite_mcp_host"]
