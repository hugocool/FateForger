"""Timeboxing package.

Keep package initialization side-effect free to avoid circular imports during
Slack/runtime bootstrap. Import concrete symbols from submodules directly.
"""

__all__: list[str] = []
