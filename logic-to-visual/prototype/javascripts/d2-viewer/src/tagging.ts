import type { D2StoryViewer } from "./viewer.js";
import { b64, decodeEdgeEndpointsFromClassToken } from "./utils.js";

export function tagNodes(viewer: D2StoryViewer): void {
  const diagramSvg = viewer.getDiagramSvg();
  if (!diagramSvg) return;

  viewer.nodeIds.forEach((id) => {
    const cls = b64(id);
    Array.from(diagramSvg.getElementsByClassName(cls)).forEach((el) => {
      const node = el as HTMLElement;
      el.classList.add("d2-node");
      node.dataset.nodeId = id;
      if (viewer.detailPanels[id]) {
        node.style.cursor = "pointer";
        node.addEventListener("click", (e) => {
          e.stopPropagation();
          viewer.showDetail(id);
        });
      }
    });
  });
}

export function tagEdges(viewer: D2StoryViewer): void {
  const diagramSvg = viewer.getDiagramSvg();
  if (!diagramSvg) return;

  diagramSvg.querySelectorAll("g").forEach((g) => {
    if (g.classList.contains("svg-pan-zoom_viewport")) return;
    if (g.classList.contains("svg-pan-zoom-control")) return;
    if ((g.id || "").startsWith("viewport-")) return;
    if (g.closest("#svg-pan-zoom-controls")) return;
    if (g.classList.contains("d2-node")) return;

    const hasArrow = g.querySelector(
      ":scope > path[marker-end], :scope > path[marker-start], :scope > g > path[marker-end], :scope > g > path[marker-start]"
    );
    const hasShape = g.querySelector(
      ":scope > g.shape, :scope > rect, :scope > ellipse, :scope > polygon"
    );
    if (!hasArrow || hasShape) return;

    g.classList.add("d2-edge");
    const edgeClassToken = Array.from(g.classList).find(
      (token) => token !== "d2-edge" && !token.startsWith("svg-pan-zoom")
    );
    const endpoints = decodeEdgeEndpointsFromClassToken(edgeClassToken);
    if (endpoints) {
      g.dataset.edgeSrc = endpoints[0];
      g.dataset.edgeDst = endpoints[1];
    }

    const label = Array.from(g.querySelectorAll("text"))
      .map((t) => t.textContent?.trim() || "")
      .filter(Boolean)
      .join(" ");
    if (label && viewer.edgeTooltips[label]) g.dataset.edgeLabel = label;
  });
}

export function setupEdgeTooltips(viewer: D2StoryViewer): void {
  if (!viewer.edgeTooltipEl) return;

  viewer.doc.querySelectorAll<SVGGElement>(".d2-edge[data-edge-label]").forEach((g) => {
    const label = g.dataset.edgeLabel || "";
    g.addEventListener("mouseenter", () => {
      if (!viewer.edgeTooltipEl) return;
      viewer.edgeTooltipEl.innerHTML = viewer.edgeTooltips[label] || label;
      viewer.edgeTooltipEl.classList.add("visible");
    });
    g.addEventListener("mouseleave", () => viewer.hideEdgeTooltip());
    g.style.cursor = "help";
  });

  viewer.onMouseMove = (e: MouseEvent) => {
    if (!viewer.edgeTooltipEl?.classList.contains("visible")) return;
    const x = e.clientX + 14;
    const y = e.clientY - 10;
    viewer.edgeTooltipEl.style.left = `${Math.min(x, window.innerWidth - 290)}px`;
    viewer.edgeTooltipEl.style.top = `${Math.min(y, window.innerHeight - 130)}px`;
  };
  viewer.doc.addEventListener("mousemove", viewer.onMouseMove);
}
