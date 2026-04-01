"""Timebox patcher REPL CLI.

Usage:
    python -m fateforger.cli.patch [--date YYYY-MM-DD] [--calendar-id ID]

Session commands:
    load [date]     Fetch TBPlan from GCal for date (default: today)
    show            Print current plan as JSON
    patch <text>    Apply a patch instruction (prompts for confirm)
    validate        Validate current plan (resolve times + overlap check)
    submit          Push current plan to GCal (prompts for confirm)
    reset           Clear conversation history
    quit / exit     Exit the session
"""

from __future__ import annotations

import asyncio
import cmd
import os
from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from fateforger.agents.timeboxing.patcher_context import PatchConversation
from fateforger.agents.timeboxing.patching import TimeboxPatcher
from fateforger.agents.timeboxing.planning_policy import STAGE4_REFINEMENT_PROMPT
from fateforger.agents.timeboxing.tb_models import TBPlan

if TYPE_CHECKING:
    from fateforger.agents.timeboxing.tb_ops import TBPatch


class PatchSession:
    """Holds in-session state: current TBPlan, remote identity data, PatchConversation."""

    def __init__(self) -> None:
        self.plan: TBPlan | None = None
        self._remote_plan: TBPlan | None = None
        self._event_id_map: dict[str, str] = {}
        self._event_ids_by_index: list[str] = []
        self.conversation = PatchConversation()
        self._patcher: TimeboxPatcher | None = None

    def _set_plan(self, plan: TBPlan) -> None:
        self.plan = plan

    def _require_plan(self) -> TBPlan:
        if self.plan is None:
            raise RuntimeError("No plan loaded. Run 'load' first.")
        return self.plan

    def _validate(self) -> list[dict]:
        """Resolve times and check overlaps. Returns resolved event list."""
        return self._require_plan().resolve_times(validate_non_overlap=True)

    def _show(self) -> str:
        """Return current plan as pretty JSON."""
        return self._require_plan().model_dump_json(indent=2)

    def _get_patcher(self) -> TimeboxPatcher:
        if self._patcher is None:
            self._patcher = TimeboxPatcher()
        return self._patcher

    async def _load_from_gcal(self, *, calendar_id: str, day: date, tz: ZoneInfo) -> TBPlan:
        """Fetch events from GCal MCP, convert to TBPlan, store remote identity data."""
        from fateforger.agents.timeboxing.mcp_clients import McpCalendarClient
        from fateforger.agents.timeboxing.sync_engine import gcal_response_to_tb_plan_with_identity
        from fateforger.core.config import settings

        client = McpCalendarClient(server_url=settings.mcp_calendar_server_url)
        try:
            snapshot = await client.list_day_snapshot(
                calendar_id=calendar_id, day=day, tz=tz
            )
        finally:
            await client.close()
        plan, event_id_map, event_ids_by_index = gcal_response_to_tb_plan_with_identity(
            snapshot.response,
            plan_date=day,
            tz_name=tz.key,
        )
        self._remote_plan = plan
        self._event_id_map = event_id_map
        self._event_ids_by_index = event_ids_by_index
        return plan

    async def _apply_patch(self, instruction: str) -> tuple[TBPlan, TBPatch]:
        """Apply a patch instruction. Returns (new_plan, patch)."""
        plan = self._require_plan()
        return await self._get_patcher().apply_patch(
            stage="Refine",
            current=plan,
            user_message=instruction,
            stage_rules=STAGE4_REFINEMENT_PROMPT,
            conversation=self.conversation,
        )

    async def _submit_to_gcal(self, *, calendar_id: str) -> None:
        """Push current plan to GCal via CalendarSubmitter."""
        from fateforger.agents.timeboxing.submitter import CalendarSubmitter

        plan = self._require_plan()
        if self._remote_plan is None:
            raise RuntimeError("No remote plan available. Run 'load' first.")
        submitter = CalendarSubmitter()
        await submitter.submit_plan(
            plan,
            remote=self._remote_plan,
            event_id_map=self._event_id_map,
            remote_event_ids_by_index=self._event_ids_by_index,
            calendar_id=calendar_id,
        )


class PatchRepl(cmd.Cmd):
    """Interactive REPL for the timebox patcher."""

    intro = "Timebox Patcher — type 'help' for commands, 'quit' to exit.\nStart with: load [YYYY-MM-DD]\n"
    prompt = "patch> "

    def __init__(self, *, calendar_id: str, tz: str = "Europe/Amsterdam") -> None:
        super().__init__()
        self._session = PatchSession()
        self._calendar_id = calendar_id
        self._tz = ZoneInfo(tz)

    def do_load(self, arg: str) -> None:
        """load [YYYY-MM-DD]  — Fetch TBPlan from GCal (default: today)."""
        raw = arg.strip()
        try:
            day = datetime.strptime(raw, "%Y-%m-%d").date() if raw else date.today()
        except ValueError:
            print(f"Invalid date: {raw!r}. Use YYYY-MM-DD.")
            return
        print(f"Fetching {day} from {self._calendar_id!r} …")
        try:
            plan = asyncio.run(
                self._session._load_from_gcal(calendar_id=self._calendar_id, day=day, tz=self._tz)
            )
            self._session._set_plan(plan)
            self._session.conversation.reset()
            print(f"Loaded {len(plan.events)} events.")
            self._print_summary(plan)
        except Exception as exc:
            print(f"Error: {exc}")

    def do_show(self, _arg: str) -> None:
        """show  — Print current plan as JSON."""
        try:
            print(self._session._show())
        except RuntimeError as exc:
            print(f"Error: {exc}")

    def do_patch(self, arg: str) -> None:
        """patch <instruction>  — Apply a patch instruction."""
        instruction = arg.strip()
        if not instruction:
            print("Usage: patch <instruction>")
            return
        try:
            print(f"Patching: {instruction!r} …")
            new_plan, patch = asyncio.run(self._session._apply_patch(instruction))
            print(f"Applied {len(patch.ops)} ops. Preview:")
            self._print_summary(new_plan)
            if input("Apply? [y/N] ").strip().lower() == "y":
                self._session._set_plan(new_plan)
                print("Plan updated.")
            else:
                # NOTE: the conversation history inside apply_patch was already appended
                # before control returned here. A rejected plan means self.plan is unchanged
                # but the conversation now contains a user/assistant exchange for the
                # discarded patch. Future patch calls will see that context.
                print("Discarded.")
        except RuntimeError as exc:
            print(f"Error: {exc}")
        except Exception as exc:
            print(f"Patch failed: {exc}")

    def do_validate(self, _arg: str) -> None:
        """validate  — Validate current plan (resolve times, check overlaps)."""
        try:
            resolved = self._session._validate()
            print(f"Valid — {len(resolved)} events:")
            for r in resolved:
                print(f"  {r.get('start_time', '?')}–{r.get('end_time', '?')}  [{r['t']}] {r['n']}")
        except Exception as exc:
            print(f"Error: {exc}")

    def do_submit(self, _arg: str) -> None:
        """submit  — Push current plan to GCal (prompts for confirm)."""
        try:
            self._session._require_plan()
        except RuntimeError as exc:
            print(f"Error: {exc}")
            return
        if input(f"Submit to {self._calendar_id!r}? [y/N] ").strip().lower() != "y":
            print("Cancelled.")
            return
        try:
            asyncio.run(self._session._submit_to_gcal(calendar_id=self._calendar_id))
            print("Submitted.")
        except Exception as exc:
            print(f"Submit failed: {exc}")

    def do_reset(self, _arg: str) -> None:
        """reset  — Clear conversation history."""
        self._session.conversation.reset()
        print("Conversation history cleared.")

    def do_quit(self, _arg: str) -> bool:
        """quit  — Exit."""
        print("Bye.")
        return True

    def do_exit(self, _arg: str) -> bool:
        """exit  — Exit."""
        return self.do_quit("")

    def do_EOF(self, _arg: str) -> bool:
        """Exit on Ctrl-D / EOF."""
        print()
        return self.do_quit("")

    def emptyline(self) -> None:
        """Do nothing on empty input (override default re-run behavior)."""
        pass

    def _print_summary(self, plan: TBPlan) -> None:
        try:
            for r in plan.resolve_times(validate_non_overlap=False):
                print(f"  {r.get('start_time', '?')}–{r.get('end_time', '?')}  [{r['t']}] {r['n']}")
        except Exception:
            print(f"  ({len(plan.events)} events, unable to resolve times)")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Timebox Patcher REPL")
    parser.add_argument("--calendar-id", default=os.getenv("TIMEBOX_CALENDAR_ID", "primary"))
    parser.add_argument("--tz", default=os.getenv("TIMEBOX_TZ", "Europe/Amsterdam"))
    parser.add_argument("--date", dest="preload_date", help="Preload plan for YYYY-MM-DD")
    args = parser.parse_args()

    repl = PatchRepl(calendar_id=args.calendar_id, tz=args.tz)
    if args.preload_date:
        repl.do_load(args.preload_date)
    repl.cmdloop()


if __name__ == "__main__":
    main()
