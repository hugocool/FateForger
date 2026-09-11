"""Every file where a test still reaches past a public interface, and why.

Generated when the legacy agent was retired (2026-09); shrinks in the
composability work that follows. An entry is ``"<repo-relative path>":
(count, "<reason>")`` -- ``count`` is the number of offending sites the file
carries today, and it is a ceiling: the guard's ratchet test fails if a file
ever needs less than its listed count (fix a site, lower the number) or
needs none at all (remove the entry). It never fails for needing more --
that's the file-level check, and it's what keeps the list from growing back.
"""

ALLOWED: dict[str, tuple[int, str]] = {
    "tests/memory/test_anchor_graph.py": (
        1,
        "swaps MemoryService's judge mid-test to change judge behaviour between two calls "
        "(e.g. a read-path judge that must not be asked) while keeping the same DB-backed "
        "observation/constraint state; the constructor takes a judge but the tests need to "
        "change it after writes have already happened.",
    ),
    "tests/memory/test_concurrent_ingest.py": (
        1,
        "swaps MemoryService's judge mid-test to change judge behaviour between two calls "
        "(e.g. a read-path judge that must not be asked) while keeping the same DB-backed "
        "observation/constraint state; the constructor takes a judge but the tests need to "
        "change it after writes have already happened.",
    ),
    "tests/memory/test_idempotent_write.py": (
        5,
        "swaps MemoryService's judge mid-test to change judge behaviour between two calls "
        "(e.g. a read-path judge that must not be asked) while keeping the same DB-backed "
        "observation/constraint state; the constructor takes a judge but the tests need to "
        "change it after writes have already happened.",
    ),
    "tests/memory/test_necessity.py": (
        1,
        "swaps MemoryService's judge mid-test to change judge behaviour between two calls "
        "(e.g. a read-path judge that must not be asked) while keeping the same DB-backed "
        "observation/constraint state; the constructor takes a judge but the tests need to "
        "change it after writes have already happened.",
    ),
    "tests/memory/test_reprojection.py": (
        1,
        "swaps MemoryService's judge mid-test to change judge behaviour between two calls "
        "(e.g. a read-path judge that must not be asked) while keeping the same DB-backed "
        "observation/constraint state; the constructor takes a judge but the tests need to "
        "change it after writes have already happened.",
    ),
    "tests/memory/test_sampling.py": (
        1,
        "swaps MemoryService's judge mid-test to change judge behaviour between two calls "
        "(e.g. a read-path judge that must not be asked) while keeping the same DB-backed "
        "observation/constraint state; the constructor takes a judge but the tests need to "
        "change it after writes have already happened.",
    ),
    "tests/unit/constraints/test_constraint_memory_client_strict_failures.py": (
        6,
        "ConstraintMemoryClient.__init__ builds a real stdio McpWorkbench subprocess with no "
        "injectable workbench parameter; __new__ skips that to wire a fake workbench directly "
        "for MCP tool-error-text unit tests.",
    ),
    "tests/unit/constraints/test_notion_constraint_store_schema_compat.py": (
        1,
        "NotionConstraintStore.__init__ builds a real Notion database client with no "
        "injectable schema parameter; __new__ skips that to attach a bare SimpleNamespace "
        "schema for property-alias resolution tests.",
    ),
    "tests/unit/constraints/test_timeboxing_constraint_memory_client_tool_name.py": (
        21,
        "ConstraintMemoryClient.__init__ builds a real stdio McpWorkbench subprocess with no "
        "injectable workbench parameter; __new__ skips that to wire a fake workbench directly, "
        "and the local _DummyWorkbench double's canned _result is reconfigured per test "
        "instead of being passed to its constructor.",
    ),
    "tests/unit/core/test_llm_audit_pipeline.py": (
        5,
        "resets logging_config's private module-level LLM-audit singletons "
        "(_LLM_AUDIT_THREAD/_QUEUE/_SINK) between tests for isolation; the module holds this "
        "as private global state with no public reset function.",
    ),
    "tests/unit/core/test_mcp_url_validation.py": (
        4,
        "NotionMcpClient.__init__ probes a real MCP endpoint over the network with no "
        "injectable params; __new__ skips that to set _params/_server_url/_timeout directly "
        "for a probe-failure unit test.",
    ),
    "tests/unit/core/test_observability_metrics.py": (
        2,
        "swaps logging_config's private _METRIC_ADMONISHMENTS Prometheus counter to None and "
        "back, to test that recording is a no-op before metrics are initialized; the module "
        "exposes no public accessor for this state.",
    ),
    "tests/unit/haunt/test_receptionist_handoff_message.py": (
        1,
        "ReceptionistAgent.__init__ builds a real AutoGen AssistantAgent with no injectable "
        "one; the test constructs through the real constructor then swaps _assistant to an "
        "in-process fake, since there is no constructor seam for it.",
    ),
    "tests/unit/haunt/test_reconcile.py": (
        7,
        "reconfigures DummyCalendarClient's canned _events between two reconcile calls "
        "instead of rebuilding the double, and constructs McpCalendarClient via "
        "__new__/object.__new__ to inject a fake _workbench, since its __init__ builds a real "
        "MCP workbench subprocess with no injectable parameter.",
    ),
    "tests/unit/haunt/test_reconcile_two_rules.py": (
        5,
        "reconfigures _RequiredRule's canned _windows/_undecided between two reconcile calls "
        "instead of rebuilding the double, and swaps PlanningReconciler's "
        "_required_block_rule mid-test even though the constructor accepts one, to preserve "
        "already-scheduled jobs across the swap.",
    ),
    "tests/unit/schedular/test_planner_agent_return_type.py": (
        1,
        "PlannerAgent.__init__ builds real AutoGen assistant/MCP tooling with no injectable "
        "delegate; the test constructs through the real constructor then swaps _delegate to "
        "an in-process fake assistant to skip that initialization.",
    ),
    "tests/unit/schedular/test_planner_upsert_verification.py": (
        5,
        "PlannerAgent.__init__ builds a real MCP workbench with no injectable parameter; "
        "tests construct through the real constructor then swap _workbench to a fake to "
        "control tool responses.",
    ),
    "tests/unit/schedular/test_revisor_handoff_wiring.py": (
        7,
        "RevisorAgent.__init__ builds real AutoGen assistants (intent/general/guided) with no "
        "injectable parameter; tests construct through the real constructor then swap "
        "_intent_assistant/_assistant/_guided_assistant to in-process fakes.",
    ),
    "tests/unit/slack/test_planning_add_to_calendar_flow.py": (
        17,
        "PlanningCoordinator.__init__ takes runtime/focus/client but not draft_store/"
        "guardian/planning_session_store/anchor_store/intent_interpreter; tests construct "
        "through the real constructor then wire those five dependencies directly, since there "
        "is no factory parameter for them yet.",
    ),
    "tests/unit/slack/test_planning_session_dispatch.py": (
        2,
        "resets WorkspaceRegistry's private module-level _global singleton directly to "
        "restore or clear workspace state between tests; the registry exposes set_global() to "
        "write it but no public way to clear or read back the raw value for teardown.",
    ),
    "tests/unit/slack/test_slack_thread_memory.py": (
        4,
        "resets thread_memory's private module-level _SESSION/_SESSION_FAILED singletons "
        "directly between tests for isolation; the module holds this as private global state "
        "with no public reset function.",
    ),
    "tests/unit/slack/test_thread_reply_add_reports_back_to_the_thread.py": (
        3,
        "PlanningCoordinator.__init__ takes runtime/focus/client but not draft_store/"
        "guardian/planning_session_store; the test constructs through the real constructor "
        "then wires those three dependencies directly, since there is no factory parameter "
        "for them yet.",
    ),
    "tests/unit/slack/test_tmbx_client_commit.py": (
        5,
        "TmbxClient.__init__ builds a real network MCP client with no injectable parameter; "
        "the test constructs through the real constructor then swaps _client to a fake "
        "transport double to control tool responses and simulate a transient timeout retry.",
    ),
    "tests/unit/tasks/test_task_defaults_memory.py": (
        1,
        "TaskDefaultsMemoryStore.__init__ builds its real backing store with no injectable "
        "parameter; the test constructs through the real constructor then swaps _store to a "
        "fake that always raises, to test the failure/caching path.",
    ),
    "tests/unit/tasks/test_tasks_guided_refinement_session.py": (
        4,
        "TasksAgent.__init__ builds a real AutoGen guided-refinement assistant with no "
        "injectable parameter; tests swap _guided_assistant to an in-process fake, and one "
        "test seeds _guided_session directly to jump straight into the CLOSE phase, since "
        "there is no public setter for session state.",
    ),
    "tests/unit/tasks/test_tasks_notion_sprint_tools.py": (
        2,
        "NotionSprintManager exposes no public hook to intercept individual MCP tool calls or "
        "override its parallelism knob; tests swap the private _call_tool_alias method and "
        "_dry_run_patch_parallelism attribute directly to measure concurrent in-flight calls.",
    ),
    "tests/unit/tasks/test_tasks_ticktick_list_tools.py": (
        6,
        "TickTickListManager exposes no public hook to override its MCP-backed project/task "
        "lookups or its snapshot parallelism; tests swap the private _list_projects/"
        "_list_project_tasks methods and _pending_snapshot_parallelism attribute directly to "
        "inject failures and measure concurrency.",
    ),
    "tests/unit/tasks/test_ticktick_mcp_client.py": (
        8,
        "TickTickMcpClient.__init__ probes a real MCP endpoint over the network with no "
        "injectable params; __new__ skips that to set _params/_server_url/_timeout directly "
        "for loader-failure unit tests.",
    ),
    "tests/unit/timeboxing/test_candidate_must_be_applied.py": (
        6,
        "DeepSeekTimeboxPlanner.__init__ builds its real tmbx client, constraint reader, "
        "harness runner and clock with no injectable parameters; __new__ skips that to wire "
        "five in-process fakes directly for a candidate-not-applied contract test.",
    ),
    "tests/unit/timeboxing/test_deepseek_timebox_planner.py": (
        4,
        "TmbxClient.__init__ builds a real MCP client with no injectable parameter; tests "
        "build via __new__/object.__new__ and set _client directly to a recorded/sequenced "
        "fake transport to pin exact tool-call shapes and retry behaviour.",
    ),
    "tests/unit/timeboxing/test_mcp_workbench_shutdown.py": (
        2,
        "McpCalendarClient.__init__ builds a real MCP workbench subprocess with no injectable "
        "parameter; the test builds via object.__new__ and sets _workbench directly to a fake "
        "that records stop()/close() calls.",
    ),
    "tests/unit/timeboxing/test_runtime_shutdown.py": (
        6,
        "swaps the timeboxing runtime module's private module-level _runtime singleton to a "
        "fake and restores it, since shutdown_runtime() reads that private global directly "
        "and the module exposes no public setter for tests.",
    ),
    "tests/unit/timeboxing/test_stage_card_registry.py": (
        1,
        "sets the local _PostingClient double's _fail flag after construction to trigger its "
        "failure branch on the next call, instead of building a second double or passing the "
        "mode to its constructor.",
    ),
    "tests/unit/timeboxing/test_timeboxing_notion_query_read_only_topics.py": (
        3,
        "NotionConstraintStore.__init__ builds real Notion database clients with no "
        "injectable parameter; __new__ skips that to attach fake topics_db/constraints_db "
        "doubles for read-only topic-resolution tests.",
    ),
}
