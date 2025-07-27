import json
import pytest
from pydantic import ValidationError

from src.actions import BootstrapAction


class TestActions:
    def test_bootstrap_action_valid(self):
        data = {"action": "create_event", "commit_time_str": "tomorrow 8am"}
        obj = BootstrapAction.parse_raw(json.dumps(data))
        assert obj.action == "create_event"

    def test_bootstrap_action_invalid(self):
        with pytest.raises(ValidationError):
            BootstrapAction.parse_raw("{}")
