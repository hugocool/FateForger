from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from fateforger.referents import Referent, StandingThing

NOW = datetime(2026, 9, 5, 11, 51, tzinfo=UTC)


def _thing(**over) -> StandingThing:
    base = dict(
        key="C1:123.456",
        agent_type="timeboxing_agent",
        kind="a plan for one day",
        day=date(2026, 9, 5),
        status="committed",
        never_used=False,
        last_activity=datetime(2026, 9, 5, 1, 41, tzinfo=UTC),
        accepts=("revise the committed plan",),
    )
    base.update(over)
    return StandingThing(**base)


def test_describe_names_the_weekday_because_a_bare_date_is_not_a_day_to_a_reader():
    ref = Referent(**_thing().model_dump(), ref_id="r1")
    described = ref.describe(NOW)
    assert described["day"] == "2026-09-05 (Saturday)"
    assert described["ref_id"] == "r1"


def test_describe_reports_age_relative_to_the_asked_moment_not_the_wall_clock():
    ref = Referent(**_thing().model_dump(), ref_id="r1")
    assert ref.describe(NOW)["last_activity"] == "10.2h ago"


def test_a_day_less_row_says_so_rather_than_omitting_the_field():
    ref = Referent(**_thing(day=None).model_dump(), ref_id="r1")
    assert ref.describe(NOW)["day"] == "no day locked yet"


def test_never_used_is_a_field_the_model_sees_not_a_phrase_inside_status():
    # Measured: how it is carried is inside the noise floor, but a consumer
    # must be able to filter and test on it. #352's door sees this as its
    # common case because autostart pre-warms a session per planning event.
    ref = Referent(**_thing(status="open", never_used=True).model_dump(), ref_id="r1")
    described = ref.describe(NOW)
    assert described["status"] == "open"
    assert described["opened_automatically_never_used"] is True


def test_the_gist_is_capped_so_a_long_plan_cannot_dominate_the_prompt():
    ref = Referent(
        **_thing(gist=tuple(f"B{i} block {i} 0{i}:00-0{i}:30" for i in range(20))).model_dump(),
        ref_id="r1",
    )
    assert len(ref.describe(NOW)["plan_contains"]) == 12


def test_an_empty_gist_omits_the_key_rather_than_showing_an_empty_list():
    ref = Referent(**_thing().model_dump(), ref_id="r1")
    assert "plan_contains" not in ref.describe(NOW)


def test_the_descriptor_is_frozen_and_refuses_unknown_fields():
    with pytest.raises(ValidationError):
        StandingThing(**_thing().model_dump(), surprise="no")
