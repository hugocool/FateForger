"""A diagnostic written and never shown is worse than one never written.

17 call sites passed `extra={...}` and not one field reached any output. In the
default configuration the stdlib formatter renders only the format string; in
JSON mode `StructuredJsonFormatter` reads extras but keeps only an allowlist,
and none of the three keys the codebase actually passes was on it.

The cost was real: chasing #217, `error_type` was the field naming which
exception had been caught by a broad handler, and it was invisible in both
modes. The cause was eventually found in a line logged by `autogen_core`.
"""

import logging

from fateforger.core.logging_config import ExtraAwareFormatter


def _render(**extra) -> str:
    record = logging.LogRecord(
        name="probe", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="a message", args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return ExtraAwareFormatter("%(levelname)s:%(name)s:%(message)s").format(record)


def test_an_extra_field_is_rendered() -> None:
    assert "error_type=CandidateNotApplied" in _render(
        error_type="CandidateNotApplied"
    )


def test_the_message_still_comes_first() -> None:
    """Appended, so existing lines keep their shape and greps keep working."""

    rendered = _render(error_type="X")
    assert rendered.index("a message") < rendered.index("error_type=X")


def test_a_record_with_no_extras_is_untouched() -> None:
    """The change must be additive or it rewrites every line in the system."""

    assert _render() == "ERROR:probe:a message"


def test_standard_record_attributes_are_not_treated_as_extras() -> None:
    """Otherwise every line grows lineno=, pathname=, msecs= and so on."""

    rendered = _render(error_type="X")
    for noise in ("pathname=", "lineno=", "msecs=", "levelno="):
        assert noise not in rendered


def test_the_json_allowlist_keeps_the_keys_actually_used() -> None:
    """JSON mode had its own copy of the same hole."""

    from fateforger.core.logging_config import _STRUCTURED_EXTRACT_FIELDS

    for field in ("error_type", "quality_snapshot", "reason_code"):
        assert field in _STRUCTURED_EXTRACT_FIELDS
