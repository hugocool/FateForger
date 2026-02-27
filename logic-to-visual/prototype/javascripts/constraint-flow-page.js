import { D2StoryViewer } from "./d2-viewer/index.js";
import { DETAIL_PANELS, EDGE_TOOLTIPS, NODE_IDS, STEPS } from "./constraint-flow-data.js";

const viewer = new D2StoryViewer({
  steps: STEPS,
  nodeIds: NODE_IDS,
  detailPanels: DETAIL_PANELS,
  edgeTooltips: EDGE_TOOLTIPS,
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
