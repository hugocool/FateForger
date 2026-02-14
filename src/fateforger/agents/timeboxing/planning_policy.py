"""Shared planning policy text for Stage 3 + Stage 4 prompts."""

from __future__ import annotations

PLANNING_POLICY_VERSION = "stage3-stage4-policy-v1-2026-02-14"

QUALITY_LEVEL_LABELS: dict[int, str] = {
    0: "Insufficient",
    1: "Minimal",
    2: "Okay",
    3: "Detailed",
    4: "Ultra",
}

SHARED_PLANNING_POLICY_PROMPT = """
Shared planning policy (must be applied in all planning stages):
- Keep at least one fixed chain anchor (`fs` or `fw`) for non-background events.
- Non-background events must not overlap.
- Background (`BG`) events are exempt from overlap checks, but must use fixed timing (`fs` or `fw`).
- Use clear, neutral event names and short practical descriptions.
- Choose timing mode intentionally:
  - `fw` for immovable fixed windows (meetings, hard commitments).
  - `fs` for fixed starts with flexible end.
  - `ap` for flow-chained blocks after the previous block.
  - `bn` for reverse-fit blocks that end at the next anchor.
- Plan in block terms first (DW/SW/PR/R/BU/H), not per-task minute precision.
""".strip()

STAGE3_OUTLINE_PROMPT = """
Stage 3 outline mode:
- Produce a short, editable ordering outline for the day.
- Keep output glanceable and compact.
- Keep durations coarse for flexible blocks.
- Show exact times only for anchored blocks (`fs`/`fw`) or if user explicitly requested exact times.
- Do not fully optimize micro-buffers and tiny details yet.
""".strip()

STAGE4_REFINEMENT_PROMPT = """
Stage 4 refine mode:
- Apply macro pass first:
  1) lock anchors and immovables,
  2) place deep-work/shallow-work blocks coherently,
  3) keep schedule non-overlapping and practical.
- Then apply micro pass:
  1) improve task-to-block mapping,
  2) add/rebalance buffers and recovery blocks where needed,
  3) improve sequencing quality without breaking anchors.
- Prefer minimal patch operations that preserve existing intent and ordering unless change is requested.
""".strip()

QUALITY_RUBRIC_PROMPT = """
Quality rubric (self-check before returning TBPatch):
- 0 Insufficient: missing anchors or invalid/overlapping sequence.
- 1 Minimal: valid skeleton but weak task/block quality.
- 2 Okay: valid schedule with coherent block allocation and core intent coverage.
- 3 Detailed: includes sensible buffers/recovery and tighter execution detail.
- 4 Ultra: high-quality sequence with robust buffers/review polish.
Target: raise quality when possible while preserving user intent.
""".strip()

__all__ = [
    "PLANNING_POLICY_VERSION",
    "QUALITY_LEVEL_LABELS",
    "SHARED_PLANNING_POLICY_PROMPT",
    "STAGE3_OUTLINE_PROMPT",
    "STAGE4_REFINEMENT_PROMPT",
    "QUALITY_RUBRIC_PROMPT",
]
