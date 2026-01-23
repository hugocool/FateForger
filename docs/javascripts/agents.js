(() => {
  const GRID_SELECTOR = ".ff-agents-grid";
  const TITLE_SELECTOR = ".ff-agent-name";

  const MAX_PX = 22;
  const MIN_PX = 10;
  const COMFORT_FACTOR = 0.95;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  const computeInnerWidth = (element) => {
    const style = window.getComputedStyle(element);
    const paddingLeft = Number.parseFloat(style.paddingLeft || "0") || 0;
    const paddingRight = Number.parseFloat(style.paddingRight || "0") || 0;
    return element.clientWidth - paddingLeft - paddingRight;
  };

  const fitAgentTitles = () => {
    const grid = document.querySelector(GRID_SELECTOR);
    if (!grid) return;

    const titles = Array.from(grid.querySelectorAll(TITLE_SELECTOR));
    if (titles.length === 0) return;

    const cards = titles.map((title) => title.closest(".ff-agent-card")).filter(Boolean);
    if (cards.length === 0) return;

    grid.style.setProperty("--ff-agent-title-size", `${MAX_PX}px`);

    const availableWidths = cards.map((card) => computeInnerWidth(card));
    const minAvailableWidth = Math.min(...availableWidths.filter((w) => Number.isFinite(w) && w > 0));
    if (!Number.isFinite(minAvailableWidth) || minAvailableWidth <= 0) return;

    const titleWidths = titles.map((title) => title.scrollWidth);
    const maxTitleWidth = Math.max(...titleWidths.filter((w) => Number.isFinite(w) && w > 0));
    if (!Number.isFinite(maxTitleWidth) || maxTitleWidth <= 0) return;

    const ratio = minAvailableWidth / maxTitleWidth;
    const fittedPx = clamp(MAX_PX * ratio * COMFORT_FACTOR, MIN_PX, MAX_PX);

    grid.style.setProperty("--ff-agent-title-size", `${fittedPx}px`);
  };

  let resizeTimer = null;
  const scheduleFit = () => {
    if (resizeTimer) window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(fitAgentTitles, 80);
  };

  window.addEventListener("resize", scheduleFit, { passive: true });
  window.addEventListener("load", fitAgentTitles, { passive: true });

  if (document.fonts?.ready) {
    document.fonts.ready.then(fitAgentTitles).catch(() => {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fitAgentTitles, { passive: true });
  } else {
    fitAgentTitles();
  }
})();
