"""
Small, runnable demo that uses Trustcall + OpenAI to patch a Timebox JSON.

Prereqs:
- OPENAI_API_KEY (and optional OPENAI_BASE_URL/OPENAI_ORG) set in env or .env
- Uses the lightweight Timebox/CalendarEvent models from the patching_json notebook.
"""

import json
import datetime as dt
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.callbacks.base import BaseCallbackHandler
from pydantic import BaseModel, Field, model_validator
from trustcall import create_extractor


class CalendarEvent(BaseModel):
    event_type: str = Field(description="DW|SW|M etc.")
    summary: str
    description: Optional[str] = None
    start_time: Optional[dt.time] = None
    end_time: Optional[dt.time] = None
    duration: Optional[dt.timedelta] = None
    anchor_prev: bool = Field(
        default=True,
        description="If both start/end omitted: True -> start at previous end; False -> end at next start",
    )


class Timebox(BaseModel):
    events: List[CalendarEvent] = Field(default_factory=list)
    date: dt.date = Field(
        default_factory=lambda: dt.date.today() + dt.timedelta(days=1),
        description="Date we are planning for, defaults to tomorrow",
    )
    timezone: str = Field(default="UTC", description="Timezone for the timebox")

    @model_validator(mode="after")
    def schedule_and_validate(self):
        planning_date = self.date
        events = self.events

        last_dt: Optional[dt.datetime] = None
        for ev in events:
            if ev.start_time and ev.duration and ev.end_time is None:
                ev.end_time = (dt.datetime.combine(planning_date, ev.start_time) + ev.duration).time()
            elif ev.end_time and ev.duration and ev.start_time is None:
                ev.start_time = (dt.datetime.combine(planning_date, ev.end_time) - ev.duration).time()
            elif ev.start_time and ev.end_time and ev.duration is None:
                ev.duration = dt.datetime.combine(planning_date, ev.end_time) - dt.datetime.combine(planning_date, ev.start_time)

            if ev.start_time is None and ev.end_time is None and not ev.anchor_prev:
                if last_dt is None:
                    raise ValueError(f"{ev.summary}: needs start or duration")
                ev.start_time = last_dt.time()
                ev.end_time = (last_dt + ev.duration).time()  # type: ignore[arg-type]

            if ev.end_time is None:
                raise ValueError(f"{ev.summary}: end_time could not be inferred")
            last_dt = dt.datetime.combine(planning_date, ev.end_time)

        next_dt: Optional[dt.datetime] = None
        for ev in reversed(events):
            if ev.anchor_prev and ev.start_time is None and ev.end_time is None:
                if next_dt is None:
                    raise ValueError(f"{ev.summary}: needs end or duration")
                if ev.duration is None:
                    raise ValueError(f"{ev.summary}: missing duration")
                ev.end_time = next_dt.time()
                ev.start_time = (next_dt - ev.duration).time()
            if ev.start_time is None:
                raise ValueError(f"{ev.summary}: start_time could not be inferred")
            next_dt = dt.datetime.combine(planning_date, ev.start_time)

        for a, b in zip(events, events[1:]):
            dt_a_end = dt.datetime.combine(planning_date, a.end_time)  # type: ignore[arg-type]
            dt_b_start = dt.datetime.combine(planning_date, b.start_time)  # type: ignore[arg-type]
            if dt_a_end > dt_b_start:
                raise ValueError(f"Overlap: {a.summary} → {b.summary}")

        last_event = events[-1]
        dt_last_start = dt.datetime.combine(planning_date, last_event.start_time)  # type: ignore[arg-type]
        if dt_last_start.date() != planning_date:
            raise ValueError(f"{last_event.summary}: start {dt_last_start} is not on {planning_date}")

        return self


def build_sample_timebox() -> Timebox:
    return Timebox(
        date=dt.date.today(),
        timezone="Europe/Amsterdam",
        events=[
            CalendarEvent(
                event_type="M",
                summary="Team sync",
                start_time=dt.time(10, 0),
                end_time=dt.time(10, 30),
                anchor_prev=False,
            ),
            CalendarEvent(
                event_type="DW",
                summary="Deep work",
                start_time=dt.time(10, 30),
                end_time=dt.time(12, 30),
                anchor_prev=False,
            ),
        ],
    )


class FileLoggingCallback(BaseCallbackHandler):
    """Logs every LLM prompt to a file so you can inspect what Trustcall sends."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def on_llm_start(self, serialized, prompts, **kwargs):
        # `prompts` is a list of rendered prompts that go to the provider.
        with self.path.open("a") as f:
            f.write("\n--- LLM START ---\n")
            for p in prompts:
                f.write(p)
                f.write("\n")

    def on_llm_end(self, response, **kwargs):
        with self.path.open("a") as f:
            f.write("\n--- LLM END ---\n")
            f.write(str(response))
            f.write("\n")


def main():
    load_dotenv()

    llm = ChatOpenAI(
        model="gpt-5",
        temperature=0.2,
        max_tokens=None,
        timeout=30,
    )

    extractor = create_extractor(
        llm,
        tools=[Timebox],
        tool_choice="Timebox",
        enable_updates=True,
        enable_inserts=True,
        enable_deletes=True,
    )

    current = build_sample_timebox().model_dump(mode="json")

    # System primer so the LLM knows about anchoring/overlaps/etc.
    system_instructions = (
        "You maintain a daily timebox. Keep fixed-time events pinned; avoid overlaps. "
        "If anchor_prev is true, start the event at the previous event's end; otherwise respect explicit start/end. "
        "If moving a fixed event, shift downstream events to preserve order and avoid collisions. "
        "Event types: M=meeting (fixed), DW=deep work, SW=shallow work, H=habit, R=recovery, BU=buffer, BG=background."
    )

    messages = [
        ("system", system_instructions),
        ("human", "Please move the team sync from 10 AM to 11 AM and shift all downstream events accordingly."),
    ]

    log_path = Path("logs/timebox_patch_demo_llm.log")
    callbacks = [FileLoggingCallback(log_path)]

    print("Existing timebox:")
    print(json.dumps(current, indent=2, default=str))

    result = extractor.invoke(
        {
            "messages": messages,
            "existing": {"Timebox": current},
        },
        config={"callbacks": callbacks},
    )

    patched: Timebox = result["responses"][0]
    patched_json = patched.model_dump(mode="json")

    print("\nPatched timebox:")
    print(json.dumps(patched_json, indent=2, default=str))

    updated_docs = result["messages"][0].additional_kwargs.get("updated_docs", {})
    if updated_docs:
        print("\nUpdated docs map:", updated_docs)
    print(f"\nLLM prompts/responses logged to: {log_path}")


if __name__ == "__main__":
    main()
