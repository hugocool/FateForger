import type { D2StoryViewer } from "./viewer.js";
import { applyHighlight, autoZoom } from "./highlight.js";

export function goStep(viewer: D2StoryViewer, idx: number, btn: HTMLElement | null = null): void {
  if (!viewer.steps.length) return;
  const bounded = Math.max(0, Math.min(viewer.steps.length - 1, idx));
  viewer.curStep = bounded;
  const win = viewer.doc.defaultView as (Window & { curStep?: number }) | null;
  if (win) win.curStep = bounded;
  const step = viewer.steps[bounded];

  viewer.doc.querySelectorAll(viewer.selectors.stepButtons).forEach((b) => b.classList.remove("active"));
  const target =
    btn || viewer.doc.querySelector<HTMLElement>(`${viewer.selectors.stepButtons}[data-step="${bounded}"]`);
  if (target) target.classList.add("active");

  if (viewer.stepTagEl) viewer.stepTagEl.textContent = step.tag || "";
  if (viewer.stepTitleEl) viewer.stepTitleEl.textContent = step.title || "";
  if (viewer.stepBodyEl) viewer.stepBodyEl.innerHTML = step.body || "";

  viewer.hideTransientUI();
  applyHighlight(viewer, step.nodes || []);
  autoZoom(viewer, step.nodes || []);

  if (viewer.prevBtn) viewer.prevBtn.disabled = bounded === 0;
  if (viewer.nextBtn) viewer.nextBtn.disabled = bounded === viewer.steps.length - 1;
}

export function toggleFocus(viewer: D2StoryViewer): void {
  viewer.focusMode = !viewer.focusMode;
  if (viewer.focusBtn) {
    viewer.focusBtn.textContent = viewer.focusMode ? "○ Context" : "● Focus";
    viewer.focusBtn.title = viewer.focusMode
      ? "Context mode: full diagram, active nodes highlighted"
      : "Focus mode: only active nodes and edges shown";
  }
  const stepNodes = viewer.steps[viewer.curStep]?.nodes || [];
  applyHighlight(viewer, stepNodes);
}

export function resetOverview(viewer: D2StoryViewer): void {
  viewer.focusMode = false;
  if (viewer.focusBtn) {
    viewer.focusBtn.textContent = "● Focus";
    viewer.focusBtn.title =
      "Toggle between Focus (only active nodes) and Context (everything, with highlights)";
  }
  viewer.hideTransientUI();
  goStep(
    viewer,
    0,
    viewer.doc.querySelector<HTMLElement>(`${viewer.selectors.stepButtons}[data-step="0"]`)
  );
  if (viewer.pz) {
    viewer.pz.fit();
    viewer.pz.center();
  }
}

export function bindKeyboard(viewer: D2StoryViewer): void {
  viewer.onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") goStep(viewer, viewer.curStep + 1);
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") goStep(viewer, viewer.curStep - 1);
  };
  viewer.doc.addEventListener("keydown", viewer.onKeyDown);
}
