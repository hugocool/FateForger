import { D2StoryViewer } from "./vendor/d2-viewer/index.js";
import { DETAIL_PANELS, EDGE_TOOLTIPS, NODE_IDS, STEPS } from "./narrative-data.js";

async function loadSvg() {
  const host = document.querySelector("#svg-host");
  if (!host) throw new Error("#svg-host not found");

  const response = await fetch("./c2f_indexer_deployment.svg");
  if (!response.ok) {
    throw new Error(`Failed to load SVG: ${response.status} ${response.statusText}`);
  }
  const svgText = await response.text();
  host.innerHTML = svgText;
}

async function init() {
  await loadSvg();

  const viewer = new D2StoryViewer({
    steps: STEPS,
    nodeIds: NODE_IDS,
    detailPanels: DETAIL_PANELS,
    edgeTooltips: EDGE_TOOLTIPS,
    contextNodeOpacity: "0.44",
    contextEdgeOpacity: "0.34",
    selectors: {
      canvasWrap: "#canvas-wrap",
      svgHost: "#svg-host",
      targetSvg: "#svg-host > svg",
      stepButtons: ".step-btn",
      stepTag: "#step-tag",
      stepTitle: "#step-title",
      stepBody: "#step-body",
      prevBtn: "#btn-prev",
      nextBtn: "#btn-next",
      focusBtn: "#btn-focus",
      fitBtn: "#btn-fit",
      zoomInBtn: "#btn-zoom-in",
      zoomOutBtn: "#btn-zoom-out",
      detailDrawer: "#detail-drawer",
      drawerNodeId: "#drawer-node-id",
      drawerBody: "#drawer-body",
      edgeTooltip: "#edge-tooltip",
    },
    svgPanZoom: window.svgPanZoom,
    exposeGlobals: true,
    autoBindControls: false,
  });

  viewer.init();
}

init().catch((error) => {
  const el = document.querySelector("#step-body");
  if (el) {
    el.innerHTML = `<div class=\"callout\"><b>Initialization failed</b><br>${String(error)}</div>`;
  }
  console.error(error);
});
