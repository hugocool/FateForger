"""Calendar submission via the deterministic sync engine.

Replaces the stub submitter with real MCP-based sync operations.
"""

from __future__ import annotations

import logging
from typing import Any

from fateforger.core.config import settings

from .sync_engine import (
    SyncTransaction,
    execute_sync,
    gcal_response_to_tb_plan,
    plan_sync,
    undo_sync,
)
from .tb_models import TBPlan

logger = logging.getLogger(__name__)


class CalendarSubmitter:
    """Submit ``TBPlan`` changes to Google Calendar via MCP sync engine.

    Manages the MCP workbench lifecycle and provides submit / undo.
    """

    def __init__(
        self,
        *,
        server_url: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        """Initialize the submitter.

        Args:
            server_url: MCP calendar server URL.  Falls back to config.
            timeout_s: HTTP timeout for MCP calls.
        """
        self._server_url = server_url or settings.mcp_calendar_server_url
        self._timeout_s = timeout_s
        self._last_tx: SyncTransaction | None = None

    def _get_workbench(self) -> Any:
        """Create an MCP workbench for the calendar server.

        Returns:
            An ``McpWorkbench`` instance.
        """
        from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams

        return McpWorkbench(
            StreamableHttpServerParams(
                url=self._server_url,
                timeout=self._timeout_s,
            )
        )

    async def submit_plan(
        self,
        desired: TBPlan,
        *,
        remote: TBPlan,
        event_id_map: dict[str, str],
        calendar_id: str = "primary",
    ) -> SyncTransaction:
        """Diff and submit a plan to Google Calendar.

        Args:
            desired: The target ``TBPlan`` to sync.
            remote: The current remote state (from ``gcal_response_to_tb_plan``).
            event_id_map: Maps ``(summary|start_iso)`` → ``gcal_event_id``.
            calendar_id: Target GCal calendar ID.

        Returns:
            A ``SyncTransaction`` with per-op results.
        """
        ops = plan_sync(remote, desired, event_id_map, calendar_id=calendar_id)

        if not ops:
            logger.info("No sync ops needed — plans are identical.")
            tx = SyncTransaction(status="committed")
            self._last_tx = tx
            return tx

        logger.info(
            "Submitting %d sync ops: %s",
            len(ops),
            ", ".join(f"{op.op_type.value}({op.gcal_event_id[:12]})" for op in ops),
        )

        wb = self._get_workbench()
        tx = await execute_sync(ops, wb)
        self._last_tx = tx

        logger.info("Sync transaction status: %s", tx.status)
        return tx

    async def undo_last(self) -> SyncTransaction | None:
        """Undo the last submitted transaction.

        Returns:
            The undo ``SyncTransaction``, or ``None`` if no transaction to undo.
        """
        if not self._last_tx or self._last_tx.status not in ("committed", "partial"):
            logger.warning("No transaction to undo.")
            return None

        wb = self._get_workbench()
        undo_tx = await undo_sync(self._last_tx, wb)
        self._last_tx = None  # Clear after undo
        return undo_tx

    @property
    def last_transaction(self) -> SyncTransaction | None:
        """Return the last sync transaction (for inspection / logging)."""
        return self._last_tx


__all__ = ["CalendarSubmitter"]
