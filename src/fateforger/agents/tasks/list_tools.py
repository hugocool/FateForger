"""TickTick list-management tooling for the Tasks agent."""

from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from yarl import URL

from fateforger.tools.ticktick_mcp import (
    get_ticktick_mcp_url,
    normalize_ticktick_mcp_url,
    probe_ticktick_mcp_endpoint,
    ticktick_localhost_fallback_urls,
)


logger = logging.getLogger(__name__)


class TickTickListOperation(str, Enum):
    """Supported list management operations."""

    CREATE_LIST = "create_list"
    ADD_ITEMS = "add_items"
    REMOVE_ITEMS = "remove_items"
    DELETE_ITEMS = "delete_items"
    DELETE_LIST = "delete_list"
    SHOW_LISTS = "show_lists"
    SHOW_LIST_ITEMS = "show_list_items"
    UPDATE_ITEMS = "update_items"


class TickTickListModel(str, Enum):
    """TickTick modeling strategies for user-facing lists."""

    PROJECT = "project"
    SUBTASK = "subtask"


class TickTickListActionInput(BaseModel):
    """Structured input for list-management operations."""

    operation: TickTickListOperation
    model: TickTickListModel = TickTickListModel.PROJECT
    list_name: str | None = None
    list_id: str | None = None
    items: list[str] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    item_matches: list[str] = Field(default_factory=list)
    parent_task_id: str | None = None
    create_if_missing: bool = True


class TickTickListActionResult(BaseModel):
    """Structured output returned to the Tasks agent."""

    ok: bool
    operation: TickTickListOperation
    model: TickTickListModel
    resolved_list_id: str | None = None
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)


class TickTickProject(BaseModel):
    """Minimal project representation extracted from TickTick MCP responses."""

    id: str
    name: str


class TickTickTask(BaseModel):
    """Minimal task representation extracted from TickTick MCP responses."""

    id: str
    title: str
    project_id: str | None = None


class TickTickPendingTask(BaseModel):
    """Pending-task row enriched with project metadata."""

    id: str
    title: str
    project_id: str | None = None
    project_name: str | None = None


class TickTickMentionCandidate(BaseModel):
    """Scored task candidate for a specific mention."""

    task_id: str
    title: str
    project_id: str | None = None
    project_name: str | None = None
    score: float
    matched_query: str
    match_type: str


class TickTickMentionResolution(BaseModel):
    """Resolution outcome for a single mention."""

    mention: str
    status: Literal["resolved", "ambiguous", "unresolved"]
    expanded_queries: list[str]
    resolved_task_id: str | None = None
    resolved_title: str | None = None
    resolved_project_id: str | None = None
    resolved_project_name: str | None = None
    candidates: list[TickTickMentionCandidate] = Field(default_factory=list)


class TickTickListManager:
    """Coordinator that executes list-management operations via TickTick MCP."""

    def __init__(
        self, *, server_url: str | None = None, timeout: float = 10.0, workbench: Any = None
    ) -> None:
        self._server_url = normalize_ticktick_mcp_url(
            server_url or get_ticktick_mcp_url()
        ).strip()
        self._timeout = timeout
        self._workbench = workbench

    async def manage_ticktick_lists(
        self,
        operation: str,
        model: str | None,
        list_name: str | None,
        list_id: str | None,
        items: list[str] | None,
        item_ids: list[str] | None,
        item_matches: list[str] | None,
        parent_task_id: str | None,
        create_if_missing: bool | None,
    ) -> dict[str, Any]:
        """Strict-compatible tool entrypoint used by the Tasks assistant."""
        normalized_model = model or TickTickListModel.PROJECT.value
        normalized_create_if_missing = (
            True if create_if_missing is None else create_if_missing
        )
        try:
            action = TickTickListActionInput(
                operation=operation,
                model=normalized_model,
                list_name=list_name,
                list_id=list_id,
                items=items or [],
                item_ids=item_ids or [],
                item_matches=item_matches or [],
                parent_task_id=parent_task_id,
                create_if_missing=normalized_create_if_missing,
            )
        except ValidationError as exc:
            return TickTickListActionResult(
                ok=False,
                operation=TickTickListOperation.SHOW_LISTS,
                model=TickTickListModel.PROJECT,
                errors=[f"invalid_input: {exc.errors()}"],
                summary="Invalid list-management input.",
            ).model_dump(mode="json")

        result = await self.execute(action)
        return result.model_dump(mode="json")

    async def resolve_ticktick_task_mentions(
        self,
        mentions: list[str] | None,
        expansion_queries: list[str] | None,
        max_candidates_per_mention: int | None,
        min_score: float | None,
        ambiguity_gap: float | None,
        include_all_projects: bool | None,
    ) -> dict[str, Any]:
        """Resolve task mentions against exhaustive cross-project candidates."""
        clean_mentions = self._clean_items(mentions or [])
        if not clean_mentions:
            return {
                "ok": False,
                "summary": "No task mentions were provided.",
                "results": [],
                "status_counts": {"resolved": 0, "ambiguous": 0, "unresolved": 0},
            }

        normalized_max_candidates = 5 if max_candidates_per_mention is None else max(
            1, max_candidates_per_mention
        )
        normalized_min_score = 0.45 if min_score is None else max(0.0, min(1.0, min_score))
        normalized_ambiguity_gap = (
            0.08 if ambiguity_gap is None else max(0.0, min(1.0, ambiguity_gap))
        )
        use_all_projects = True if include_all_projects is None else include_all_projects
        rows = (
            await self.list_all_pending_tasks()
            if use_all_projects
            else await self.list_pending_tasks(limit=200, per_project_limit=20)
        )

        resolutions: list[TickTickMentionResolution] = []
        counts = {"resolved": 0, "ambiguous": 0, "unresolved": 0}
        normalized_expansions = self._clean_items(expansion_queries or [])
        for mention in clean_mentions:
            expanded_queries = self._build_query_expansions(mention, normalized_expansions)
            candidates = self._score_mention_candidates(
                mention=mention,
                expanded_queries=expanded_queries,
                rows=rows,
                min_score=normalized_min_score,
            )[:normalized_max_candidates]
            resolution = self._classify_mention_resolution(
                mention=mention,
                expanded_queries=expanded_queries,
                candidates=candidates,
                ambiguity_gap=normalized_ambiguity_gap,
            )
            counts[resolution.status] += 1
            resolutions.append(resolution)

        return {
            "ok": True,
            "summary": (
                f"Resolved {counts['resolved']}, ambiguous {counts['ambiguous']}, "
                f"unresolved {counts['unresolved']} mention(s)."
            ),
            "exhausted": len(resolutions) == len(clean_mentions),
            "mention_count": len(clean_mentions),
            "status_counts": counts,
            "results": [resolution.model_dump(mode="json") for resolution in resolutions],
        }

    async def execute(self, action: TickTickListActionInput) -> TickTickListActionResult:
        """Dispatch a validated list-management action."""
        try:
            if action.model == TickTickListModel.PROJECT:
                return await self._execute_project(action)
            return await self._execute_subtask(action)
        except Exception as exc:
            logger.exception("TickTick list operation failed", exc_info=True)
            return self._error(
                action,
                f"TickTick MCP failure: {type(exc).__name__}. Please try again.",
            )

    async def _execute_project(
        self, action: TickTickListActionInput
    ) -> TickTickListActionResult:
        if action.operation == TickTickListOperation.SHOW_LISTS:
            projects = await self._list_projects()
            return TickTickListActionResult(
                ok=True,
                operation=action.operation,
                model=action.model,
                created=0,
                summary=f"Found {len(projects)} lists.",
                data={"lists": [p.model_dump(mode="json") for p in projects]},
            )

        if action.operation == TickTickListOperation.CREATE_LIST:
            if not action.list_name and not action.list_id:
                return self._error(action, "`create_list` requires list_name or list_id.")
            project, created, errors, ambiguous = await self._resolve_project(
                action, allow_create=True
            )
            if errors:
                return self._error(
                    action,
                    "Could not resolve target list.",
                    errors=errors,
                    data={"ambiguous_lists": [p.model_dump(mode="json") for p in ambiguous]},
                )
            assert project is not None
            created_items = 0
            if action.items:
                created_items, item_errors = await self._create_project_items(
                    project_id=project.id, items=action.items
                )
                errors.extend(item_errors)
            return TickTickListActionResult(
                ok=not errors,
                operation=action.operation,
                model=action.model,
                resolved_list_id=project.id,
                created=created_items,
                errors=errors,
                summary=(
                    f"{'Created' if created else 'Resolved'} list '{project.name}'. "
                    f"Added {created_items} item(s)."
                    if action.items
                    else f"{'Created' if created else 'Resolved'} list '{project.name}'."
                ),
                data={
                    "list_name": project.name,
                    "list_created": created,
                },
            )

        if action.operation == TickTickListOperation.ADD_ITEMS:
            if not action.items:
                return self._error(action, "`add_items` requires items.")
            project, created, errors, ambiguous = await self._resolve_project(
                action, allow_create=True
            )
            if errors:
                return self._error(
                    action,
                    "Could not resolve target list.",
                    errors=errors,
                    data={"ambiguous_lists": [p.model_dump(mode="json") for p in ambiguous]},
                )
            assert project is not None
            created_items, item_errors = await self._create_project_items(
                project_id=project.id, items=action.items
            )
            errors.extend(item_errors)
            return TickTickListActionResult(
                ok=not errors,
                operation=action.operation,
                model=action.model,
                resolved_list_id=project.id,
                created=created_items,
                errors=errors,
                summary=f"Added {created_items} item(s) to '{project.name}'.",
                data={
                    "list_name": project.name,
                    "list_created": created,
                },
            )

        if action.operation == TickTickListOperation.SHOW_LIST_ITEMS:
            if not action.list_id and not action.list_name:
                rows = await self.list_all_pending_tasks()
                items = [
                    {
                        "id": row.id,
                        "title": row.title,
                        "project_id": row.project_id,
                        "project_name": row.project_name,
                    }
                    for row in rows
                ]
                return TickTickListActionResult(
                    ok=True,
                    operation=action.operation,
                    model=action.model,
                    summary=f"Found {len(items)} pending task(s) across all projects.",
                    data={"list_name": "__all__", "items": items},
                )
            project, _, errors, ambiguous = await self._resolve_project(
                action, allow_create=False
            )
            if errors:
                return self._error(
                    action,
                    "Could not resolve target list.",
                    errors=errors,
                    data={"ambiguous_lists": [p.model_dump(mode="json") for p in ambiguous]},
                )
            assert project is not None
            tasks = await self._list_project_tasks(project.id)
            return TickTickListActionResult(
                ok=True,
                operation=action.operation,
                model=action.model,
                resolved_list_id=project.id,
                summary=f"Found {len(tasks)} item(s) in '{project.name}'.",
                data={
                    "list_name": project.name,
                    "items": [t.model_dump(mode="json") for t in tasks],
                },
            )

        if action.operation in (
            TickTickListOperation.REMOVE_ITEMS,
            TickTickListOperation.DELETE_ITEMS,
        ):
            project, _, errors, ambiguous = await self._resolve_project(
                action, allow_create=False
            )
            if errors:
                return self._error(
                    action,
                    "Could not resolve target list.",
                    errors=errors,
                    data={"ambiguous_lists": [p.model_dump(mode="json") for p in ambiguous]},
                )
            assert project is not None
            tasks = await self._list_project_tasks(project.id)
            task_ids, skipped, selection_errors = self._resolve_task_ids(action, tasks)
            if selection_errors:
                return self._error(
                    action,
                    "Item selection is ambiguous; no items were removed.",
                    resolved_list_id=project.id,
                    errors=selection_errors,
                    skipped=skipped,
                    data={"list_name": project.name},
                )
            if not task_ids:
                return TickTickListActionResult(
                    ok=True,
                    operation=action.operation,
                    model=action.model,
                    resolved_list_id=project.id,
                    skipped=skipped,
                    summary=f"No matching items found in '{project.name}'.",
                    data={"list_name": project.name},
                )
            deleted, delete_errors = await self._delete_items(
                project_id=project.id, task_ids=task_ids
            )
            return TickTickListActionResult(
                ok=not delete_errors,
                operation=action.operation,
                model=action.model,
                resolved_list_id=project.id,
                deleted=deleted,
                skipped=skipped,
                errors=delete_errors,
                summary=f"Removed {deleted} item(s) from '{project.name}'.",
                data={"list_name": project.name},
            )

        if action.operation == TickTickListOperation.DELETE_LIST:
            project, _, errors, ambiguous = await self._resolve_project(
                action, allow_create=False
            )
            if errors:
                return self._error(
                    action,
                    "Could not resolve target list.",
                    errors=errors,
                    data={"ambiguous_lists": [p.model_dump(mode="json") for p in ambiguous]},
                )
            assert project is not None
            await self._call_tool("delete_project", {"project_id": project.id})
            return TickTickListActionResult(
                ok=True,
                operation=action.operation,
                model=action.model,
                resolved_list_id=project.id,
                deleted=1,
                summary=f"Deleted list '{project.name}'.",
            )

        if action.operation == TickTickListOperation.UPDATE_ITEMS:
            project, _, errors, ambiguous = await self._resolve_project(
                action, allow_create=False
            )
            if errors:
                return self._error(
                    action,
                    "Could not resolve target list.",
                    errors=errors,
                    data={"ambiguous_lists": [p.model_dump(mode="json") for p in ambiguous]},
                )
            if not action.item_ids or not action.items:
                return self._error(
                    action,
                    "`update_items` requires item_ids and items of equal length."
                )
            if len(action.item_ids) != len(action.items):
                return self._error(
                    action,
                    "`update_items` requires item_ids and items with the same length.",
                )
            assert project is not None
            updated, skipped, update_errors = await self._update_items(
                project_id=project.id,
                task_ids=action.item_ids,
                titles=action.items,
            )
            return TickTickListActionResult(
                ok=not update_errors,
                operation=action.operation,
                model=action.model,
                resolved_list_id=project.id,
                updated=updated,
                skipped=skipped,
                errors=update_errors,
                summary=f"Updated {updated} item(s) in '{project.name}'.",
                data={"list_name": project.name},
            )

        return self._error(action, f"Unsupported operation: {action.operation.value}.")

    async def _execute_subtask(
        self, action: TickTickListActionInput
    ) -> TickTickListActionResult:
        project_id = (action.list_id or "").strip()

        if action.operation == TickTickListOperation.SHOW_LISTS:
            if not project_id:
                return self._error(
                    action,
                    "`show_lists` in subtask mode requires list_id as the parent project ID.",
                )
            tasks = await self._list_project_tasks(project_id)
            return TickTickListActionResult(
                ok=True,
                operation=action.operation,
                model=action.model,
                resolved_list_id=project_id,
                summary=f"Found {len(tasks)} parent task list candidate(s).",
                data={"lists": [t.model_dump(mode="json") for t in tasks]},
            )

        if action.operation == TickTickListOperation.CREATE_LIST:
            if not project_id or not action.list_name:
                return self._error(
                    action,
                    "`create_list` in subtask mode requires list_id (project_id) and list_name.",
                )
            create_text = await self._call_tool(
                "create_task", {"title": action.list_name, "project_id": project_id}
            )
            parent_id = self._extract_first_id(create_text)
            created = 1
            errors: list[str] = []
            if action.items:
                if not parent_id:
                    errors.append("Failed to parse parent task ID for subtask list.")
                else:
                    for item in self._clean_items(action.items):
                        await self._call_tool(
                            "create_subtask",
                            {
                                "subtask_title": item,
                                "parent_task_id": parent_id,
                                "project_id": project_id,
                            },
                        )
                        created += 1
            return TickTickListActionResult(
                ok=not errors,
                operation=action.operation,
                model=action.model,
                resolved_list_id=parent_id,
                created=created,
                errors=errors,
                summary=(
                    f"Created subtask list '{action.list_name}' with {created - 1} item(s)."
                ),
                data={"project_id": project_id, "parent_task_id": parent_id},
            )

        if action.operation == TickTickListOperation.ADD_ITEMS:
            if not project_id or not action.parent_task_id:
                return self._error(
                    action,
                    "`add_items` in subtask mode requires list_id (project_id) and parent_task_id.",
                )
            if not action.items:
                return self._error(action, "`add_items` requires items.")
            created = 0
            for item in self._clean_items(action.items):
                await self._call_tool(
                    "create_subtask",
                    {
                        "subtask_title": item,
                        "parent_task_id": action.parent_task_id,
                        "project_id": project_id,
                    },
                )
                created += 1
            return TickTickListActionResult(
                ok=True,
                operation=action.operation,
                model=action.model,
                resolved_list_id=action.parent_task_id,
                created=created,
                summary=f"Added {created} subtask item(s).",
                data={"project_id": project_id, "parent_task_id": action.parent_task_id},
            )

        if action.operation == TickTickListOperation.SHOW_LIST_ITEMS:
            if not project_id or not action.parent_task_id:
                return self._error(
                    action,
                    "`show_list_items` in subtask mode requires list_id (project_id) and parent_task_id.",
                )
            text = await self._call_tool(
                "get_task",
                {"project_id": project_id, "task_id": action.parent_task_id},
            )
            subtasks = self._parse_subtasks(text)
            return TickTickListActionResult(
                ok=True,
                operation=action.operation,
                model=action.model,
                resolved_list_id=action.parent_task_id,
                summary=f"Found {len(subtasks)} subtask item(s).",
                data={
                    "project_id": project_id,
                    "parent_task_id": action.parent_task_id,
                    "items": subtasks,
                },
            )

        if action.operation in (
            TickTickListOperation.REMOVE_ITEMS,
            TickTickListOperation.DELETE_ITEMS,
        ):
            if not project_id:
                return self._error(
                    action,
                    "`delete_items`/`remove_items` in subtask mode requires list_id (project_id).",
                )
            if not action.item_ids:
                return self._error(
                    action,
                    "Subtask deletion requires explicit item_ids for safety.",
                )
            deleted, delete_errors = await self._delete_items(
                project_id=project_id, task_ids=action.item_ids
            )
            return TickTickListActionResult(
                ok=not delete_errors,
                operation=action.operation,
                model=action.model,
                resolved_list_id=action.parent_task_id,
                deleted=deleted,
                errors=delete_errors,
                summary=f"Removed {deleted} subtask item(s).",
                data={"project_id": project_id, "parent_task_id": action.parent_task_id},
            )

        if action.operation == TickTickListOperation.UPDATE_ITEMS:
            if not project_id:
                return self._error(
                    action,
                    "`update_items` in subtask mode requires list_id (project_id).",
                )
            if not action.item_ids or not action.items:
                return self._error(
                    action,
                    "`update_items` requires item_ids and items of equal length.",
                )
            if len(action.item_ids) != len(action.items):
                return self._error(
                    action,
                    "`update_items` requires item_ids and items with the same length.",
                )
            updated, skipped, update_errors = await self._update_items(
                project_id=project_id,
                task_ids=action.item_ids,
                titles=action.items,
            )
            return TickTickListActionResult(
                ok=not update_errors,
                operation=action.operation,
                model=action.model,
                resolved_list_id=action.parent_task_id,
                updated=updated,
                skipped=skipped,
                errors=update_errors,
                summary=f"Updated {updated} subtask item(s).",
                data={"project_id": project_id, "parent_task_id": action.parent_task_id},
            )

        if action.operation == TickTickListOperation.DELETE_LIST:
            if not project_id or not action.parent_task_id:
                return self._error(
                    action,
                    "`delete_list` in subtask mode requires list_id (project_id) and parent_task_id.",
                )
            await self._call_tool(
                "delete_task",
                {"project_id": project_id, "task_id": action.parent_task_id},
            )
            return TickTickListActionResult(
                ok=True,
                operation=action.operation,
                model=action.model,
                resolved_list_id=action.parent_task_id,
                deleted=1,
                summary="Deleted subtask-mode list parent task.",
                data={"project_id": project_id, "parent_task_id": action.parent_task_id},
            )

        return self._error(action, f"Unsupported operation: {action.operation.value}.")

    async def _resolve_project(
        self, action: TickTickListActionInput, *, allow_create: bool
    ) -> tuple[TickTickProject | None, bool, list[str], list[TickTickProject]]:
        projects = await self._list_projects()
        errors: list[str] = []
        ambiguous: list[TickTickProject] = []

        if action.list_id:
            for project in projects:
                if project.id == action.list_id:
                    return project, False, errors, ambiguous
            errors.append(f"No list found with id '{action.list_id}'.")
            return None, False, errors, ambiguous

        if action.list_name:
            matched, ambiguous = self._match_projects_by_name(projects, action.list_name)
            if matched:
                return matched, False, errors, ambiguous
            if ambiguous:
                errors.append(f"List name '{action.list_name}' is ambiguous.")
                return None, False, errors, ambiguous

        if allow_create and action.create_if_missing:
            if not action.list_name:
                errors.append("list_name is required when creating a missing list.")
                return None, False, errors, ambiguous
            created = await self._create_project(action.list_name)
            if not created:
                errors.append("Failed to create list.")
                return None, False, errors, ambiguous
            return created, True, errors, ambiguous

        if action.list_name:
            errors.append(f"No list found with name '{action.list_name}'.")
        else:
            errors.append("Target list was not provided.")
        return None, False, errors, ambiguous

    async def _create_project(self, name: str) -> TickTickProject | None:
        text = await self._call_tool(
            "create_project", {"name": name, "view_mode": "list"}
        )
        projects = self._parse_projects(text)
        if projects:
            return projects[0]
        project_id = self._extract_first_id(text)
        if project_id:
            return TickTickProject(id=project_id, name=name)
        return None

    async def _create_project_items(
        self, *, project_id: str, items: list[str]
    ) -> tuple[int, list[str]]:
        clean = self._clean_items(items)
        if not clean:
            return 0, []
        payload = {"tasks": [{"title": item, "project_id": project_id} for item in clean]}
        text = await self._call_tool("batch_create_tasks", payload)
        created = self._parse_created_count(text) or len(clean)
        failures = self._parse_failed_count(text)
        errors: list[str] = []
        if failures and failures > 0:
            errors.append(f"TickTick reported {failures} failed task creation(s).")
        return created, errors

    async def _list_projects(self) -> list[TickTickProject]:
        text = await self._call_tool("get_projects", {})
        return self._parse_projects(text)

    async def _list_project_tasks(self, project_id: str) -> list[TickTickTask]:
        text = await self._call_tool("get_project_tasks", {"project_id": project_id})
        return self._parse_tasks(text)

    async def list_pending_tasks(
        self, *, limit: int = 12, per_project_limit: int = 4
    ) -> list[TickTickPendingTask]:
        """Return a bounded pending-task snapshot across projects."""
        try:
            projects = await self._list_projects()
        except Exception as exc:
            logger.warning(
                "TickTick pending snapshot unavailable; returning empty set (%s: %s)",
                type(exc).__name__,
                exc,
            )
            return []
        if not projects:
            return []
        rows: list[TickTickPendingTask] = []
        for project in projects:
            tasks = await self._list_project_tasks(project.id)
            for task in tasks[:per_project_limit]:
                rows.append(
                    TickTickPendingTask(
                        id=task.id,
                        title=task.title,
                        project_id=project.id,
                        project_name=project.name,
                    )
                )
                if len(rows) >= limit:
                    return rows
        return rows

    async def list_all_pending_tasks(self) -> list[TickTickPendingTask]:
        """Return all pending tasks across all projects."""
        # High ceiling keeps this bounded for safety while effectively "all"
        # for normal TickTick workspaces.
        return await self.list_pending_tasks(limit=10_000, per_project_limit=10_000)

    async def _delete_items(
        self, *, project_id: str, task_ids: list[str]
    ) -> tuple[int, list[str]]:
        deleted = 0
        errors: list[str] = []
        for task_id in task_ids:
            try:
                await self._call_tool(
                    "delete_task", {"project_id": project_id, "task_id": task_id}
                )
                deleted += 1
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"delete_task failed for {task_id}: {type(exc).__name__}")
        return deleted, errors

    async def _update_items(
        self, *, project_id: str, task_ids: list[str], titles: list[str]
    ) -> tuple[int, int, list[str]]:
        updated = 0
        skipped = 0
        errors: list[str] = []
        for task_id, title in zip(task_ids, titles):
            clean_title = (title or "").strip()
            if not clean_title:
                skipped += 1
                continue
            try:
                await self._call_tool(
                    "update_task",
                    {"project_id": project_id, "task_id": task_id, "title": clean_title},
                )
                updated += 1
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"update_task failed for {task_id}: {type(exc).__name__}")
        return updated, skipped, errors

    def _resolve_task_ids(
        self, action: TickTickListActionInput, tasks: list[TickTickTask]
    ) -> tuple[list[str], int, list[str]]:
        if action.item_ids:
            deduped = list(dict.fromkeys([task_id for task_id in action.item_ids if task_id]))
            return deduped, 0, []

        selectors = self._clean_items(action.item_matches or action.items)
        if not selectors:
            return [], 0, ["No item selectors provided."]

        resolved: list[str] = []
        skipped = 0
        errors: list[str] = []
        for selector in selectors:
            match = self._match_tasks_by_title(tasks, selector)
            if len(match) == 1:
                resolved.append(match[0].id)
                continue
            if len(match) == 0:
                skipped += 1
                continue
            errors.append(
                f"Item selector '{selector}' matched multiple items: "
                + ", ".join([task.title for task in match[:5]])
            )
        deduped = list(dict.fromkeys(resolved))
        return deduped, skipped, errors

    def _build_query_expansions(
        self, mention: str, expansion_queries: list[str]
    ) -> list[str]:
        queries: list[str] = []
        seen: set[str] = set()

        def _add(value: str) -> None:
            clean = " ".join((value or "").split()).strip()
            key = self._normalize(clean)
            if not clean or not key or key in seen:
                return
            seen.add(key)
            queries.append(clean)

        _add(mention)
        for query in expansion_queries:
            _add(query)

        tokens = re.findall(r"[A-Za-z0-9]+", mention)
        long_tokens = [token for token in tokens if len(token) >= 4]
        for token in long_tokens:
            _add(token)
        if len(tokens) >= 2:
            _add(" ".join(tokens))
            _add(" ".join(tokens[:2]))
            _add(" ".join(tokens[-2:]))
        return queries

    def _score_mention_candidates(
        self,
        *,
        mention: str,
        expanded_queries: list[str],
        rows: list[TickTickPendingTask],
        min_score: float,
    ) -> list[TickTickMentionCandidate]:
        best_by_task: dict[str, TickTickMentionCandidate] = {}
        for row in rows:
            if not row.id or not row.title:
                continue
            best_score = -1.0
            best_query = ""
            best_match_type = "none"
            for query in expanded_queries:
                score, match_type = self._score_title_match(row.title, query)
                if score > best_score:
                    best_score = score
                    best_query = query
                    best_match_type = match_type
            if best_score < min_score:
                continue
            candidate = TickTickMentionCandidate(
                task_id=row.id,
                title=row.title,
                project_id=row.project_id,
                project_name=row.project_name,
                score=round(best_score, 4),
                matched_query=best_query,
                match_type=best_match_type,
            )
            existing = best_by_task.get(row.id)
            if existing is None or candidate.score > existing.score:
                best_by_task[row.id] = candidate

        return sorted(
            best_by_task.values(),
            key=lambda candidate: (-candidate.score, candidate.title.lower()),
        )

    def _score_title_match(self, title: str, query: str) -> tuple[float, str]:
        normalized_title = self._normalize(title)
        normalized_query = self._normalize(query)
        if not normalized_title or not normalized_query:
            return 0.0, "none"
        if normalized_title == normalized_query:
            return 1.0, "exact"
        if normalized_query in normalized_title:
            return 0.9, "contains"
        if normalized_title in normalized_query and len(normalized_title) >= 4:
            return 0.86, "superset"

        title_tokens = set(normalized_title.split())
        query_tokens = set(normalized_query.split())
        overlap = title_tokens & query_tokens
        jaccard = (
            len(overlap) / len(title_tokens | query_tokens)
            if title_tokens and query_tokens
            else 0.0
        )
        seq = SequenceMatcher(None, normalized_title, normalized_query).ratio()
        score = max(seq, jaccard)
        if query_tokens and query_tokens.issubset(title_tokens):
            score = max(score, 0.82)
        if overlap and len(overlap) >= 2:
            score = max(score, 0.78)
        return score, "fuzzy"

    def _classify_mention_resolution(
        self,
        *,
        mention: str,
        expanded_queries: list[str],
        candidates: list[TickTickMentionCandidate],
        ambiguity_gap: float,
    ) -> TickTickMentionResolution:
        if not candidates:
            return TickTickMentionResolution(
                mention=mention,
                status="unresolved",
                expanded_queries=expanded_queries,
                candidates=[],
            )

        best = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        resolved = best.score >= 0.9 and (
            second is None or (best.score - second.score) >= ambiguity_gap
        )
        if resolved:
            return TickTickMentionResolution(
                mention=mention,
                status="resolved",
                expanded_queries=expanded_queries,
                resolved_task_id=best.task_id,
                resolved_title=best.title,
                resolved_project_id=best.project_id,
                resolved_project_name=best.project_name,
                candidates=candidates,
            )
        return TickTickMentionResolution(
            mention=mention,
            status="ambiguous",
            expanded_queries=expanded_queries,
            candidates=candidates,
        )

    def _match_tasks_by_title(
        self, tasks: list[TickTickTask], selector: str
    ) -> list[TickTickTask]:
        target = self._normalize(selector)
        exact = [task for task in tasks if self._normalize(task.title) == target]
        if exact:
            return exact
        return [task for task in tasks if target in self._normalize(task.title)]

    def _match_projects_by_name(
        self, projects: list[TickTickProject], name: str
    ) -> tuple[TickTickProject | None, list[TickTickProject]]:
        target = self._normalize(name)
        exact = [project for project in projects if self._normalize(project.name) == target]
        if len(exact) == 1:
            return exact[0], []
        if len(exact) > 1:
            return None, exact
        contains = [
            project for project in projects if target in self._normalize(project.name)
        ]
        if len(contains) == 1:
            return contains[0], []
        if len(contains) > 1:
            return None, contains
        return None, []

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        workbench = self._ensure_workbench()
        result = await workbench.call_tool(name, arguments=arguments)
        to_text = getattr(result, "to_text", None)
        if callable(to_text):
            text = to_text()
            return text if isinstance(text, str) else str(text)
        return str(result)

    def _ensure_workbench(self) -> Any:
        if self._workbench is not None:
            return self._workbench
        try:
            from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "autogen_ext tools are required for TickTick MCP access"
            ) from exc
        if not self._server_url:
            raise RuntimeError("TickTick MCP URL is not configured.")
        ok, reason = probe_ticktick_mcp_endpoint(
            self._server_url, connect_timeout_s=min(self._timeout, 1.5)
        )
        if not ok:
            parsed = URL(self._server_url)
            if parsed.host == "ticktick-mcp":
                for fallback in ticktick_localhost_fallback_urls(self._server_url):
                    fallback_ok, fallback_reason = probe_ticktick_mcp_endpoint(
                        fallback, connect_timeout_s=min(self._timeout, 1.5)
                    )
                    if not fallback_ok:
                        reason = fallback_reason or reason
                        continue
                    logger.warning(
                        "TickTick MCP endpoint '%s' is unavailable (%s); using '%s'.",
                        self._server_url,
                        reason,
                        fallback,
                    )
                    self._server_url = fallback
                    ok = True
                    break
            if not ok:
                raise RuntimeError(reason or "TickTick MCP endpoint is unavailable.")
        params = StreamableHttpServerParams(url=self._server_url, timeout=self._timeout)
        self._workbench = McpWorkbench(params)
        return self._workbench

    @staticmethod
    def _parse_projects(text: str) -> list[TickTickProject]:
        parsed = TickTickListManager._parse_json_list(
            text, id_keys=("id", "project_id"), name_keys=("name", "title")
        )
        if parsed:
            return [TickTickProject(id=item["id"], name=item["name"]) for item in parsed]

        projects: list[TickTickProject] = []
        current: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("Project ") and line.endswith(":"):
                if current.get("id") and current.get("name"):
                    projects.append(
                        TickTickProject(id=current["id"], name=current["name"])
                    )
                current = {}
                continue
            if line.startswith("Name:"):
                current["name"] = line.split(":", 1)[1].strip()
                continue
            if line.startswith("ID:"):
                current["id"] = line.split(":", 1)[1].strip()
        if current.get("id") and current.get("name"):
            projects.append(TickTickProject(id=current["id"], name=current["name"]))
        deduped: dict[str, TickTickProject] = {}
        for project in projects:
            deduped[project.id] = project
        return list(deduped.values())

    @staticmethod
    def _parse_tasks(text: str) -> list[TickTickTask]:
        parsed = TickTickListManager._parse_json_list(
            text,
            id_keys=("id", "task_id"),
            name_keys=("title", "name"),
            project_keys=("project_id", "projectId"),
        )
        if parsed:
            return [
                TickTickTask(
                    id=item["id"],
                    title=item["name"],
                    project_id=item.get("project_id"),
                )
                for item in parsed
            ]

        tasks: list[TickTickTask] = []
        current: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("Task ") and line.endswith(":"):
                if current.get("id") and current.get("title"):
                    tasks.append(
                        TickTickTask(
                            id=current["id"],
                            title=current["title"],
                            project_id=current.get("project_id"),
                        )
                    )
                current = {}
                continue
            if line.startswith("ID:"):
                current["id"] = line.split(":", 1)[1].strip()
                continue
            if line.startswith("Title:"):
                current["title"] = line.split(":", 1)[1].strip()
                continue
            if line.startswith("Project ID:"):
                current["project_id"] = line.split(":", 1)[1].strip()
        if current.get("id") and current.get("title"):
            tasks.append(
                TickTickTask(
                    id=current["id"],
                    title=current["title"],
                    project_id=current.get("project_id"),
                )
            )
        return tasks

    @staticmethod
    def _parse_subtasks(text: str) -> list[dict[str, str]]:
        subtasks: list[dict[str, str]] = []
        for raw in text.splitlines():
            line = raw.strip()
            if "subtask" not in line.lower():
                continue
            task_id = None
            id_match = re.search(r"ID:\s*([A-Za-z0-9]+)", line)
            if id_match:
                task_id = id_match.group(1)
            title = line.split("ID:", 1)[0]
            title = re.sub(r"^\d+[\).\s-]*", "", title).strip(" -:")
            if title:
                subtasks.append({"id": task_id or "", "title": title})
        return subtasks

    @staticmethod
    def _parse_json_list(
        text: str,
        *,
        id_keys: tuple[str, ...],
        name_keys: tuple[str, ...],
        project_keys: tuple[str, ...] = (),
    ) -> list[dict[str, str]]:
        try:
            payload = json.loads(text)
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        out: list[dict[str, str]] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            row_id = next(
                (str(row.get(key)).strip() for key in id_keys if row.get(key)),
                "",
            )
            row_name = next(
                (str(row.get(key)).strip() for key in name_keys if row.get(key)),
                "",
            )
            if not row_id or not row_name:
                continue
            item: dict[str, str] = {"id": row_id, "name": row_name}
            if project_keys:
                item["project_id"] = next(
                    (
                        str(row.get(key)).strip()
                        for key in project_keys
                        if row.get(key)
                    ),
                    "",
                )
            out.append(item)
        return out

    @staticmethod
    def _extract_first_id(text: str) -> str | None:
        match = re.search(r"ID:\s*([A-Za-z0-9]+)", text)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _parse_created_count(text: str) -> int | None:
        match = re.search(r"Successfully created:\s*(\d+)", text, flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_failed_count(text: str) -> int | None:
        match = re.search(r"Failed:\s*(\d+)", text, flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _clean_items(items: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for raw in items:
            clean = " ".join((raw or "").split()).strip()
            if not clean:
                continue
            seen[clean] = None
        return list(seen.keys())

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join((value or "").lower().split()).strip()

    @staticmethod
    def _error(
        action: TickTickListActionInput,
        summary: str,
        *,
        resolved_list_id: str | None = None,
        created: int = 0,
        updated: int = 0,
        deleted: int = 0,
        skipped: int = 0,
        errors: list[str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> TickTickListActionResult:
        return TickTickListActionResult(
            ok=False,
            operation=action.operation,
            model=action.model,
            resolved_list_id=resolved_list_id,
            created=created,
            updated=updated,
            deleted=deleted,
            skipped=skipped,
            errors=errors or [summary],
            summary=summary,
            data=data or {},
        )


__all__ = [
    "TickTickMentionCandidate",
    "TickTickMentionResolution",
    "TickTickListActionInput",
    "TickTickListActionResult",
    "TickTickListManager",
    "TickTickListModel",
    "TickTickListOperation",
]
