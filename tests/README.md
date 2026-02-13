# Tests

pytest test suite for FateForger. Organized by scope: unit, integration, end-to-end.

## How to Run

```bash
# All tests
poetry run pytest

# Unit tests only
poetry run pytest tests/unit/ -v

# Specific test suite
poetry run pytest tests/unit/test_sync_engine.py -v

# By keyword
poetry run pytest tests/unit/ -k timeboxing -v

# Integration (requires running services)
poetry run pytest tests/integration/ -v

# E2E (requires Slack mock + running services)
poetry run pytest tests/e2e/ -v
```

## Structure

```
tests/
  conftest.py              # Shared fixtures (DB, mocks, factories)
  unit/                    # Fast, isolated, no external services
  integration/             # Requires DB / MCP containers
  e2e/                     # Full Slack flow simulation
```

## Test Index

### Sync Engine Suite (115 tests)

| File | Tests | Covers |
|------|-------|--------|
| `test_tb_models.py` | 32 | TBEvent, TBPlan, Timing union, ET enum, color map, event ID generation |
| `test_tb_ops.py` | 30 | TBPatch, TBOp union, apply_tb_ops(), all op types (add, remove, update, move, replace_all) |
| `test_sync_engine.py` | 29 | plan_sync(), execute_sync(), undo_sync(), gcal_response_to_tb_plan(), SyncOp, SyncTransaction |
| `test_phase4_rewiring.py` | 10 | Stage 4 refine node wiring, session.tb_plan update, base_snapshot preservation |
| `test_patching.py` | 14 | TimeboxPatcher, schema-in-prompt, _extract_patch(), markdown fence stripping, error handling |

### Timeboxing Tests

| File | Covers |
|------|--------|
| `test_timeboxing_graphflow_state_machine.py` | GraphFlow stage transitions, edge conditions |
| `test_timeboxing_flow.py` | Legacy flow logic |
| `test_timeboxing_activity.py` | Session activity tracking |
| `test_timeboxing_capture_inputs_prompt_block_based.py` | Stage 2 prompt uses block-based planning |
| `test_timeboxing_commit_modal.py` | Stage 0 commit UI |
| `test_timeboxing_commit_skips_initial_extraction.py` | Commit does not trigger constraint extraction |
| `test_timeboxing_constraint_extraction_background.py` | Background constraint extraction |
| `test_timeboxing_constraint_extractor_tool_nonblocking.py` | Non-blocking extractor tool |
| `test_timeboxing_constraint_extractor_tool_strict.py` | Strict mode extractor tool |
| `test_timeboxing_constraint_memory_client_tool_name.py` | MCP tool name sanitization |
| `test_timeboxing_durable_constraints.py` | Durable Notion constraint persistence |
| `test_timeboxing_prompt_rendering.py` | Jinja prompt rendering |
| `test_timeboxing_review_submit_prompt.py` | Stage 2 pre-gen trigger and Stage 5 pending-submit state |
| `test_timeboxing_skeleton_context_injection.py` | Skeleton context assembly |
| `test_timeboxing_skeleton_fallback.py` | Skeleton timeout fallback |
| `test_timeboxing_skeleton_pre_generation.py` | Stage 3 uses pre-generated skeleton when available |
| `test_timeboxing_stage_gate_json_context.py` | Stage gate JSON context building |
| `test_timeboxing_stage_prompts_block_based.py` | Stage prompts use block-based terms |
| `test_timeboxing_stage_prompts_no_tools.py` | Stage LLMs do not have tools registered |
| `test_timeboxing_submit_flow.py` | Confirm/cancel/undo session transitions + deterministic undo state |
| `test_timebox_schedule_and_validate.py` | Legacy Timebox validation |
| `test_contracts.py` | Typed stage context contracts |

### Slack Bot Tests

| File | Covers |
|------|--------|
| `test_slack_app_home_view.py` | App Home tab rendering |
| `test_slack_channel_default_routing.py` | Default channel routing |
| `test_slack_constraint_review.py` | Constraint review modal |
| `test_slack_revisor_channel_redirect.py` | Revisor channel redirect |
| `test_slack_setup_invite.py` | Setup invite flow |
| `test_slack_setup_response.py` | Setup response handling |
| `test_slack_timeboxing_channel_redirect.py` | Timeboxing channel redirect |
| `test_slack_timeboxing_dm_no_redirect.py` | DM does not redirect |
| `test_slack_timeboxing_focus_recovery.py` | Thread focus recovery |
| `test_slack_timeboxing_routing.py` | Timeboxing message routing |
| `test_slack_workspace_bootstrap.py` | Workspace bootstrap provisioning |

### Agent Tests

| File | Covers |
|------|--------|
| `test_calendar_haunter.py` | Calendar haunter nudge logic |
| `test_haunt_slack_delivery.py` | Haunt delivery to Slack |
| `test_diffing_agent.py` | Calendar plan diffing |
| `test_planner_agent_return_type.py` | Planner agent return type |
| `test_receptionist_handoff_message.py` | Receptionist handoff routing |
| `test_reconcile.py` | Planning reconciler |

### Planning Tests

| File | Covers |
|------|--------|
| `test_planning_add_to_calendar_flow.py` | Add-to-calendar flow |
| `test_planning_card.py` | Planning card rendering |
| `test_planning_reminder_blocks_include_dismiss.py` | Reminder blocks with dismiss |
| `test_planning_reminder_suppression.py` | Reminder suppression during timeboxing |
| `test_planning_time_picker_modal.py` | Time picker modal |

### Infrastructure Tests

| File | Covers |
|------|--------|
| `test_backoff.py` | Exponential backoff helper |
| `test_constraint_mcp_server_tools_openai_safe.py` | MCP tool name safety |
| `test_constraint_mcp_tool_names.py` | MCP tool name mapping |
| `test_constraint_retriever.py` | Constraint retriever logic |
| `test_models.py` | General data models |
| `test_openrouter_reasoning_effort_request.py` | OpenRouter reasoning effort |
| `test_settings_mcp_endpoints.py` | Settings MCP endpoints |
| `test_toon_encode.py` | TOON tabular encoding |
| `test_trustcall_timebox_patch.py` | Legacy trustcall patch (superseded) |

### Integration Tests

| File | Covers |
|------|--------|
| `test_calendar_haunter_integration.py` | Calendar haunter with real MCP |
| `test_haunting_service.py` | Haunting service lifecycle |
| `test_notion_constraint_store.py` | Notion constraint store |
| `test_slack_timebox_buttons.py` | Stage 5 Slack confirm/cancel/undo button wiring |
| `test_timeboxing_durable_constraint_retriever_wiring.py` | Durable constraint retriever wiring |

### E2E Tests

| File | Covers |
|------|--------|
| `test_slack_handoff_flow.py` | Full Slack handoff flow |
| `test_slack_timebox_command.py` | /timebox slash command flow |
| `test_slack_timeboxing_background_status.py` | Background status during timeboxing |
