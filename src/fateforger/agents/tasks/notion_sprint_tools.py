"""Notion sprint domain tooling for the Tasks agent."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Sequence

from pydantic import BaseModel, Field

from fateforger.tools.notion_mcp import get_notion_mcp_headers, get_notion_mcp_url


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
        self, *, server_url: str | None = None, timeout: float = 10.0, workbench: Any = None
    ) -> None:
        self._server_url = (server_url or get_notion_mcp_url()).strip()
        self._timeout = timeout
        self._workbench = workbench

    async def find_sprint_items(
        self,
        query: str,
        data_source_url: str,
        filters: dict[str, Any] | None,
        limit: int | None,
    ) -> dict[str, Any]:
        """Search sprint records from a specific Notion data source."""
        normalized_limit = 25 if limit is None else limit
        arguments: dict[str, Any] = {
            "query": query,
            "query_type": "internal",
            "data_source_url": data_source_url,
        }
        if filters:
            arguments["filters"] = filters

        result = await self._call_tool_alias(SEARCH_TOOL_ALIASES, arguments)
        payload = self._decode_payload(result)

        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            results = payload["results"][: max(0, normalized_limit)]
        elif isinstance(payload, list):
            results = payload[: max(0, normalized_limit)]
        else:
            results = []

        return {
            "ok": True,
            "query": query,
            "results": results,
            "count": len(results),
            "raw": payload,
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
