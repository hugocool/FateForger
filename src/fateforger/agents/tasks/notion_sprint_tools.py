"""Notion sprint domain tooling for the Tasks agent."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Sequence

from pydantic import BaseModel, Field
from yarl import URL

from fateforger.tools.mcp_url_validation import rewrite_mcp_host
from fateforger.tools.notion_mcp import (
    get_notion_mcp_headers,
    get_notion_mcp_url,
    normalize_notion_mcp_url,
    probe_notion_mcp_endpoint,
)


logger = logging.getLogger(__name__)


SEARCH_TOOL_ALIASES: tuple[str, ...] = ("notion-search", "notion_search", "search")
FETCH_TOOL_ALIASES: tuple[str, ...] = ("notion-fetch", "notion_fetch", "fetch")
UPDATE_PAGE_TOOL_ALIASES: tuple[str, ...] = (
    "notion-update-page",
    "notion_update_page",
    "update_page",
)


class SprintPatchResult(BaseModel):
    ok: bool
    mode: str
    summary: str
    selection_with_ellipsis: str | None = None
    patch_text: str | None = None
    verified: bool | None = None


@dataclass
class _Candidate:
    score: float
    block_text: str


class NotionSprintManager:
    """Executes sprint-focused Notion operations via MCP tools."""

    def __init__(
        self,
        *,
        server_url: str | None = None,
        timeout: float = 10.0,
        workbench: Any = None,
        default_data_source_url: str | None = None,
        default_database_id: str | None = None,
        default_data_source_urls: Sequence[str] | None = None,
        default_database_ids: Sequence[str] | None = None,
        default_source_resolution_parallelism: int = 4,
        dry_run_patch_parallelism: int = 4,
    ) -> None:
        self._server_url = normalize_notion_mcp_url(
            server_url or get_notion_mcp_url()
        ).strip()
        self._timeout = timeout
        self._workbench = workbench
        self._default_source_resolution_parallelism = max(
            1, int(default_source_resolution_parallelism)
        )
        self._dry_run_patch_parallelism = max(1, int(dry_run_patch_parallelism))
        self._default_data_source_url = (default_data_source_url or "").strip()
        self._default_database_id = (default_database_id or "").strip()
        initial_data_source_urls = list(default_data_source_urls or [])
        if self._default_data_source_url:
            initial_data_source_urls.append(self._default_data_source_url)
        self._default_data_source_urls = self._dedupe_keep_order(
            [value.strip() for value in initial_data_source_urls if str(value).strip()]
        )

        initial_database_ids = list(default_database_ids or [])
        if self._default_database_id:
            initial_database_ids.append(self._default_database_id)
        self._default_database_ids = self._dedupe_keep_order(
            [value.strip() for value in initial_database_ids if str(value).strip()]
        )

    async def find_sprint_items(
        self,
        query: str,
        data_source_url: str | None,
        filters: dict[str, Any] | None,
        limit: int | None,
    ) -> dict[str, Any]:
        """Search sprint records from a specific Notion data source."""
        normalized_limit = 25 if limit is None else limit
        resolved_data_source_urls = await self._resolve_data_source_urls(data_source_url)
        if not resolved_data_source_urls:
            return {
                "ok": False,
                "query": query,
                "results": [],
                "count": 0,
                "raw": {},
                "error": (
                    "No Notion sprint data source is configured. "
                    "Set NOTION_SPRINT_DATA_SOURCE_URL(S) or NOTION_SPRINT_DB_ID(S), "
                    "or pass data_source_url explicitly."
                ),
            }
        aggregated_results: list[Any] = []
        raw_payloads: dict[str, Any] = {}
        source_errors: list[str] = []
        for resolved_data_source_url in resolved_data_source_urls:
            arguments: dict[str, Any] = {
                "query": query,
                "query_type": "internal",
                "data_source_url": resolved_data_source_url,
            }
            if filters:
                arguments["filters"] = filters
            try:
                result = await self._call_tool_alias(SEARCH_TOOL_ALIASES, arguments)
                payload = self._decode_payload(result)
                raw_payloads[resolved_data_source_url] = payload
            except Exception as exc:
                source_errors.append(
                    f"{resolved_data_source_url}: {type(exc).__name__}: {exc}"
                )
                continue

            if isinstance(payload, dict) and isinstance(payload.get("results"), list):
                source_results = payload["results"]
            elif isinstance(payload, list):
                source_results = payload
            else:
                source_results = []
            for source_result in source_results:
                if isinstance(source_result, dict):
                    enriched = dict(source_result)
                    enriched.setdefault("data_source_url", resolved_data_source_url)
                    aggregated_results.append(enriched)
                else:
                    aggregated_results.append(source_result)

        results = aggregated_results[: max(0, normalized_limit)]
        if not results and source_errors:
            return {
                "ok": False,
                "query": query,
                "results": [],
                "count": 0,
                "raw": raw_payloads,
                "errors": source_errors,
                "data_source_urls": resolved_data_source_urls,
                "error": "All configured Notion sprint sources failed.",
            }

        return {
            "ok": True,
            "query": query,
            "data_source_url": (
                resolved_data_source_urls[0] if len(resolved_data_source_urls) == 1 else None
            ),
            "data_source_urls": resolved_data_source_urls,
            "results": results,
            "count": len(results),
            "raw": raw_payloads,
            "errors": source_errors,
        }

    async def link_sprint_subtasks(
        self,
        *,
        parent_page_id: str,
        child_page_ids: list[str],
        relation_property: str,
        unlink: bool | None,
    ) -> dict[str, Any]:
        """Link or unlink child sprint pages through a relation property."""
        normalized_unlink = bool(unlink)
        updated = 0
        errors: list[str] = []

        value = [] if normalized_unlink else [parent_page_id]
        for child_page_id in child_page_ids:
            payload = {
                "page_id": child_page_id,
                "command": "update_properties",
                "properties": {relation_property: value},
            }
            try:
                await self._call_tool_alias(
                    UPDATE_PAGE_TOOL_ALIASES,
                    {"data": json.dumps(payload, ensure_ascii=True)},
                )
                updated += 1
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(
                    f"Failed to update relation for {child_page_id}: {type(exc).__name__}"
                )

        return {
            "ok": not errors,
            "updated": updated,
            "errors": errors,
            "summary": (
                f"{'Unlinked' if normalized_unlink else 'Linked'} {updated}/{len(child_page_ids)} child page(s)."
            ),
        }

    async def patch_sprint_page_content(
        self,
        *,
        page_id: str,
        search_text: str,
        replace_text: str,
        langdiff_plan_json: str | None,
        dry_run: bool | None,
        match_threshold: float | None,
        match_distance: int | None,
    ) -> dict[str, Any]:
        """Patch sprint page text with fuzzy matching and safe conflict handling."""
        normalized_dry_run = True if dry_run is None else dry_run
        normalized_match_threshold = 0.55 if match_threshold is None else match_threshold
        normalized_match_distance = 1000 if match_distance is None else match_distance
        if langdiff_plan_json:
            plan_search, plan_replace = self._parse_langdiff_plan(langdiff_plan_json)
            if plan_search:
                search_text = plan_search
            if plan_replace:
                replace_text = plan_replace

        dmp = self._new_dmp(
            match_threshold=normalized_match_threshold,
            match_distance=normalized_match_distance,
        )

        content = await self._fetch_page_text(page_id)
        blocks = self._extract_blocks(content)
        candidate = self._find_candidate_block(
            dmp=dmp,
            blocks=blocks,
            search_text=search_text,
            threshold=normalized_match_threshold,
        )
        if candidate is None:
            return SprintPatchResult(
                ok=False,
                mode="conflict",
                summary="Could not locate a unique matching block for the requested patch.",
            ).model_dump(mode="json")

        patch_text = dmp.patch_toText(dmp.patch_make(search_text, replace_text))
        patches = dmp.patch_fromText(patch_text)
        patched_block, flags = dmp.patch_apply(patches, candidate.block_text)
        success_rate = sum(1 for flag in flags if flag) / max(1, len(flags))
        if success_rate < 0.9:
            return SprintPatchResult(
                ok=False,
                mode="conflict",
                summary="Patch confidence too low; refusing to overwrite content.",
                patch_text=patch_text,
            ).model_dump(mode="json")

        selection = self._selection_with_ellipsis(candidate.block_text)
        if normalized_dry_run:
            return {
                **SprintPatchResult(
                    ok=True,
                    mode="preview",
                    summary="Patch preview generated.",
                    selection_with_ellipsis=selection,
                    patch_text=patch_text,
                ).model_dump(mode="json"),
                "before_sha256": self._sha(candidate.block_text),
                "after_sha256": self._sha(patched_block),
            }

        update_payload = {
            "page_id": page_id,
            "command": "replace_content_range",
            "selection_with_ellipsis": selection,
            "new_str": patched_block,
        }
        await self._call_tool_alias(
            UPDATE_PAGE_TOOL_ALIASES,
            {"data": json.dumps(update_payload, ensure_ascii=True)},
        )
        verified_text = await self._fetch_page_text(page_id)
        verified = replace_text in verified_text

        return SprintPatchResult(
            ok=verified,
            mode="applied",
            summary=(
                "Patch applied and verified."
                if verified
                else "Patch applied but verification failed to find replacement text."
            ),
            selection_with_ellipsis=selection,
            patch_text=patch_text,
            verified=verified,
        ).model_dump(mode="json")

    async def patch_sprint_event(
        self,
        *,
        page_id: str,
        search_text: str,
        replace_text: str,
        langdiff_plan_json: str | None,
        dry_run: bool | None,
        match_threshold: float | None,
        match_distance: int | None,
    ) -> dict[str, Any]:
        """Opinionated single-event patch command for sprint records."""
        result = await self.patch_sprint_page_content(
            page_id=page_id,
            search_text=search_text,
            replace_text=replace_text,
            langdiff_plan_json=langdiff_plan_json,
            dry_run=dry_run,
            match_threshold=match_threshold,
            match_distance=match_distance,
        )
        return {
            "ok": bool(result.get("ok")),
            "mode": result.get("mode"),
            "page_id": page_id,
            "summary": result.get("summary", ""),
            "result": result,
        }

    async def patch_sprint_events(
        self,
        *,
        page_ids: list[str] | None,
        query: str | None,
        data_source_url: str | None,
        filters: dict[str, Any] | None,
        limit: int | None,
        search_text: str,
        replace_text: str,
        langdiff_plan_json: str | None,
        dry_run: bool | None,
        match_threshold: float | None,
        match_distance: int | None,
        stop_on_error: bool | None,
    ) -> dict[str, Any]:
        """Opinionated bulk patch command for sprint records."""
        started_at = time.perf_counter()
        selected_page_ids: list[str] = []
        for raw_page_id in (page_ids or []):
            normalized = str(raw_page_id).strip()
            if normalized:
                selected_page_ids.append(normalized)

        selection_mode = "explicit"
        if not selected_page_ids:
            normalized_query = (query or "").strip()
            if not normalized_query:
                return {
                    "ok": False,
                    "summary": (
                        "No target events provided. Pass page_ids, or provide query "
                        "with optional filters/limit to select sprint events first."
                    ),
                    "selection_mode": "none",
                    "attempted": 0,
                    "patched": 0,
                    "failed": 0,
                    "results": [],
                }
            search = await self.find_sprint_items(
                query=normalized_query,
                data_source_url=data_source_url,
                filters=filters,
                limit=limit,
            )
            if not search.get("ok"):
                return {
                    "ok": False,
                    "summary": "Could not search sprint events before patching.",
                    "selection_mode": "search",
                    "search": search,
                    "attempted": 0,
                    "patched": 0,
                    "failed": 0,
                    "results": [],
                }
            selected_page_ids = self._extract_page_ids(search.get("results"))
            selection_mode = "search"
            if not selected_page_ids:
                return {
                    "ok": False,
                    "summary": "Search returned no patchable sprint event IDs.",
                    "selection_mode": selection_mode,
                    "search_count": int(search.get("count", 0)),
                    "attempted": 0,
                    "patched": 0,
                    "failed": 0,
                    "results": [],
                }

        normalized_stop_on_error = True if stop_on_error is None else stop_on_error
        per_event_results: list[dict[str, Any]] = []
        patched = 0
        failed = 0

        use_parallel_dry_run = (
            bool(dry_run)
            and not normalized_stop_on_error
            and self._dry_run_patch_parallelism > 1
            and len(selected_page_ids) > 1
        )
        if use_parallel_dry_run:
            per_event_results = await self._run_parallel_dry_run_previews(
                selected_page_ids=selected_page_ids,
                search_text=search_text,
                replace_text=replace_text,
                langdiff_plan_json=langdiff_plan_json,
                dry_run=dry_run,
                match_threshold=match_threshold,
                match_distance=match_distance,
            )
            patched = sum(1 for row in per_event_results if bool(row.get("ok")))
            failed = len(per_event_results) - patched
        else:
            for page_id in selected_page_ids:
                patch_result = await self.patch_sprint_page_content(
                    page_id=page_id,
                    search_text=search_text,
                    replace_text=replace_text,
                    langdiff_plan_json=langdiff_plan_json,
                    dry_run=dry_run,
                    match_threshold=match_threshold,
                    match_distance=match_distance,
                )
                event_ok = bool(patch_result.get("ok"))
                if event_ok:
                    patched += 1
                else:
                    failed += 1
                per_event_results.append(
                    {
                        "page_id": page_id,
                        "ok": event_ok,
                        "mode": patch_result.get("mode"),
                        "summary": patch_result.get("summary"),
                        "result": patch_result,
                    }
                )
                if failed > 0 and normalized_stop_on_error:
                    break

        attempted = len(per_event_results)
        logger.info(
            "Notion sprint bulk patch completed",
            extra={
                "event": "notion_sprint_bulk_patch_latency",
                "attempted": attempted,
                "patched": patched,
                "failed": failed,
                "selection_mode": selection_mode,
                "parallel_dry_run": use_parallel_dry_run,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
            },
        )
        return {
            "ok": failed == 0,
            "summary": (
                f"Patched {patched}/{attempted} sprint event(s)"
                + (" (stopped on first error)." if failed and normalized_stop_on_error else ".")
            ),
            "selection_mode": selection_mode,
            "attempted": attempted,
            "patched": patched,
            "failed": failed,
            "results": per_event_results,
        }

    async def _fetch_page_text(self, page_id: str) -> str:
        result = await self._call_tool_alias(FETCH_TOOL_ALIASES, {"id": page_id})
        payload = self._decode_payload(result)
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            for key in ("content", "markdown", "text"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
        return str(payload)

    async def _resolve_data_source_urls(
        self, provided_data_source_url: str | None
    ) -> list[str]:
        started_at = time.perf_counter()
        explicit = (provided_data_source_url or "").strip()
        if explicit:
            return [explicit]
        if self._default_data_source_urls:
            return list(self._default_data_source_urls)
        if not self._default_database_ids:
            return []

        extracted_urls: list[str] = []
        semaphore = asyncio.Semaphore(self._default_source_resolution_parallelism)
        batches: list[list[str] | None] = [None] * len(self._default_database_ids)

        async def _resolve(index: int, database_id: str) -> None:
            async with semaphore:
                try:
                    result = await self._call_tool_alias(FETCH_TOOL_ALIASES, {"id": database_id})
                    payload = self._decode_payload(result)
                    batches[index] = self._extract_data_source_urls(payload)
                except Exception as exc:
                    logger.warning(
                        "Notion sprint data-source resolution failed for database id",
                        extra={
                            "event": "notion_sprint_default_db_resolution_failed",
                            "database_id": database_id,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    batches[index] = []

        await asyncio.gather(
            *(
                _resolve(index, database_id)
                for index, database_id in enumerate(self._default_database_ids)
            )
        )
        for batch in batches:
            extracted_urls.extend(batch or [])

        self._default_data_source_urls = self._dedupe_keep_order(
            [value for value in extracted_urls if value]
        )
        logger.info(
            "Notion sprint source resolution completed",
            extra={
                "event": "notion_sprint_source_resolution_latency",
                "database_count": len(self._default_database_ids),
                "resolved_count": len(self._default_data_source_urls),
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
            },
        )
        return list(self._default_data_source_urls)

    async def _run_parallel_dry_run_previews(
        self,
        *,
        selected_page_ids: list[str],
        search_text: str,
        replace_text: str,
        langdiff_plan_json: str | None,
        dry_run: bool | None,
        match_threshold: float | None,
        match_distance: int | None,
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(self._dry_run_patch_parallelism)
        results: list[dict[str, Any] | None] = [None] * len(selected_page_ids)

        async def _preview(index: int, page_id: str) -> None:
            async with semaphore:
                patch_result = await self.patch_sprint_page_content(
                    page_id=page_id,
                    search_text=search_text,
                    replace_text=replace_text,
                    langdiff_plan_json=langdiff_plan_json,
                    dry_run=dry_run,
                    match_threshold=match_threshold,
                    match_distance=match_distance,
                )
                results[index] = {
                    "page_id": page_id,
                    "ok": bool(patch_result.get("ok")),
                    "mode": patch_result.get("mode"),
                    "summary": patch_result.get("summary"),
                    "result": patch_result,
                }

        await asyncio.gather(
            *(
                _preview(index, page_id)
                for index, page_id in enumerate(selected_page_ids)
            )
        )
        return [row if row is not None else {} for row in results]

    async def _call_tool_alias(
        self, aliases: Sequence[str], arguments: dict[str, Any]
    ) -> str | dict[str, Any] | list[Any]:
        errors: list[str] = []
        for name in aliases:
            try:
                result = await self._call_tool(name, arguments)
                return result
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
        raise RuntimeError("All tool aliases failed: " + " | ".join(errors))

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> str | dict | list:
        workbench = self._ensure_workbench()
        result = await workbench.call_tool(name, arguments=arguments)
        if isinstance(result, (dict, list)):
            return result
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
                "autogen_ext tools are required for Notion MCP access"
            ) from exc
        if not self._server_url:
            raise RuntimeError("Notion MCP URL is not configured.")
        ok, reason = probe_notion_mcp_endpoint(
            self._server_url, connect_timeout_s=min(self._timeout, 1.5)
        )
        if not ok:
            parsed = URL(self._server_url)
            if parsed.host == "notion-mcp":
                fallback = rewrite_mcp_host(
                    self._server_url, "localhost", default_path="/mcp"
                )
                fallback_ok, fallback_reason = probe_notion_mcp_endpoint(
                    fallback, connect_timeout_s=min(self._timeout, 1.5)
                )
                if fallback_ok:
                    logger.warning(
                        "Notion MCP endpoint '%s' is unavailable (%s); using '%s'.",
                        self._server_url,
                        reason,
                        fallback,
                    )
                    self._server_url = fallback
                    ok = True
                else:
                    reason = fallback_reason or reason
            if not ok:
                raise RuntimeError(reason or "Notion MCP endpoint is unavailable.")
        params = StreamableHttpServerParams(
            url=self._server_url,
            headers=get_notion_mcp_headers(),
            timeout=self._timeout,
        )
        self._workbench = McpWorkbench(params)
        return self._workbench

    @staticmethod
    def _decode_payload(payload: str | dict | list) -> str | dict | list:
        if isinstance(payload, (dict, list)):
            return payload
        text = payload.strip()
        if not text:
            return ""
        try:
            return json.loads(text)
        except Exception:
            return text

    @staticmethod
    def _extract_page_ids(results: Any) -> list[str]:
        if not isinstance(results, list):
            return []
        page_ids: list[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            for key in ("id", "page_id"):
                raw = item.get(key)
                if isinstance(raw, str) and raw.strip():
                    page_ids.append(raw.strip())
                    break
        return page_ids

    @staticmethod
    def _extract_data_source_urls(payload: str | dict | list) -> list[str]:
        if isinstance(payload, dict):
            text = json.dumps(payload, ensure_ascii=True)
        elif isinstance(payload, list):
            text = json.dumps(payload, ensure_ascii=True)
        else:
            text = payload
        matches = re.findall(r"collection://[0-9a-fA-F-]{8,}", text)
        return NotionSprintManager._dedupe_keep_order(matches)

    @staticmethod
    def _dedupe_keep_order(values: Sequence[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    @staticmethod
    def _extract_blocks(content: str) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []
        for line in content.splitlines():
            if line.strip():
                current.append(line)
                continue
            if current:
                blocks.append("\n".join(current))
                current = []
        if current:
            blocks.append("\n".join(current))
        return blocks

    @staticmethod
    def _selection_with_ellipsis(text: str) -> str:
        compact = " ".join(text.split())
        if len(compact) <= 24:
            return compact
        return f"{compact[:12]}...{compact[-12:]}"

    @staticmethod
    def _sha(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    def _new_dmp(self, *, match_threshold: float, match_distance: int):
        try:
            import diff_match_patch as dmp_module
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "diff-match-patch is required for sprint page patching."
            ) from exc
        dmp = dmp_module.diff_match_patch()
        dmp.Match_Threshold = max(0.0, min(1.0, match_threshold))
        dmp.Match_Distance = max(0, int(match_distance))
        return dmp

    @staticmethod
    def _parse_langdiff_plan(plan_json: str) -> tuple[str, str]:
        try:
            from langdiff import Object, Parser, String
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("langdiff is required to parse langdiff_plan_json.") from exc

        class _EditPlan(Object):
            pass

        _EditPlan.__annotations__ = {
            "search_text": String,
            "replace_text": String,
        }

        root = _EditPlan()
        parser = Parser(root)
        parser.push(plan_json)
        parser.complete()
        value = root.value if isinstance(root.value, dict) else {}
        return str(value.get("search_text", "")).strip(), str(
            value.get("replace_text", "")
        ).strip()

    def _find_candidate_block(
        self,
        *,
        dmp: Any,
        blocks: list[str],
        search_text: str,
        threshold: float,
    ) -> _Candidate | None:
        scored: list[_Candidate] = []
        target = search_text.strip()
        if not target:
            return None

        for block in blocks:
            loc = dmp.match_main(block, target, 0)
            if loc < 0:
                continue
            end = min(len(block), loc + len(target))
            window = block[loc:end]
            diffs = dmp.diff_main(target, window)
            score = dmp.diff_levenshtein(diffs) / max(1, len(target))
            scored.append(_Candidate(score=score, block_text=block))

        if not scored:
            return None
        scored.sort(key=lambda c: c.score)
        best = scored[0]
        if best.score > max(0.0, min(1.0, threshold)):
            return None
        if len(scored) > 1 and abs(scored[1].score - best.score) < 0.05:
            return None
        return best


__all__ = ["NotionSprintManager", "SprintPatchResult"]
