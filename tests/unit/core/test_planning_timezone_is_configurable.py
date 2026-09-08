"""The timezone a planning day is locked in must be a setting, not a literal.

`planning_timezone` was read in four places and defined in none. Every read was
`getattr(settings, "planning_timezone", "")`, which on a `Settings` carrying
`extra="ignore"` returns the empty string forever -- so every read fell through
to a literal, and the two literals in one file disagreed ("Europe/Amsterdam" at
agent.py:617, "UTC" at agent.py:5647).

The failure mode is the quiet one: setting PLANNING_TIMEZONE in .env changes
nothing at all, and nothing says so.
"""

from fateforger.core.config import Settings


def test_the_field_exists() -> None:
    """A read of it must not fall through a getattr default."""

    assert "planning_timezone" in Settings.model_fields


def test_the_environment_can_change_it(monkeypatch) -> None:
    """The point of the field: an override actually overrides."""

    monkeypatch.setenv("PLANNING_TIMEZONE", "America/New_York")
    assert Settings().planning_timezone == "America/New_York"


def test_the_default_is_what_the_literals_already_said() -> None:
    """Adding the field must not silently move anybody's day."""

    monkeypatch_free = Settings()
    assert monkeypatch_free.planning_timezone == "Europe/Amsterdam"
