from typing import Literal, Optional

from pydantic import Field

from .base import ActionBase


class BootstrapAction(ActionBase):
    action: Literal["create_event", "postpone", "unknown"] = Field(...)
    commit_time_str: Optional[str] = None
    minutes: Optional[int] = Field(default=None, ge=1, le=240)


BOOTSTRAP_PROMPT = "Bootstrap prompt"
