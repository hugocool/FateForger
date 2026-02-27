import { applyHighlight } from "./highlight.js";
import { bindKeyboard, goStep, resetOverview, toggleFocus } from "./navigation.js";
import { setupEdgeTooltips, tagEdges, tagNodes } from "./tagging.js";
import type { PanZoomFactory, PanZoomInstance, ViewerOptions, ViewerSelectors } from "./types.js";

const DEFAULT_SELECTORS: ViewerSelectors = {
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
};

export class D2StoryViewer {
  doc: Document;
  steps;
  nodeIds;
  detailPanels;
  edgeTooltips;
  selectors: ViewerSelectors;

  curStep = 0;
  focusMode = false;
  zoomRaf: number | null = null;
  pz: PanZoomInstance | null = null;
  contextNodeOpacity: string;
  contextEdgeOpacity: string;
  zoomFill: number;
  zoomFrames: number;
  panZoomMin: number;
  panZoomMax: number;
  panZoomOptions: Record<string, unknown>;
  exposeGlobals: boolean;
  autoBindControls: boolean;
  svgPanZoomImpl?: PanZoomFactory;

  canvasWrap: HTMLElement | null = null;
  svgHost: HTMLElement | null = null;
  stepTagEl: HTMLElement | null = null;
  stepTitleEl: HTMLElement | null = null;
  stepBodyEl: HTMLElement | null = null;
  prevBtn: HTMLButtonElement | null = null;
  nextBtn: HTMLButtonElement | null = null;
  focusBtn: HTMLButtonElement | null = null;
  fitBtn: HTMLButtonElement | null = null;
  zoomInBtn: HTMLButtonElement | null = null;
  zoomOutBtn: HTMLButtonElement | null = null;
  detailDrawerEl: HTMLElement | null = null;
  drawerNodeIdEl: HTMLElement | null = null;
  drawerBodyEl: HTMLElement | null = null;
  edgeTooltipEl: HTMLElement | null = null;

  onResize: (() => void) | null = null;
  onMouseMove: ((e: MouseEvent) => void) | null = null;
  onCanvasClick: ((e: MouseEvent) => void) | null = null;
  onKeyDown: ((e: KeyboardEvent) => void) | null = null;

  constructor(options: ViewerOptions) {
    this.doc = options.document || document;
    this.steps = options.steps || [];
    this.nodeIds = options.nodeIds || [];
    this.detailPanels = options.detailPanels || {};
    this.edgeTooltips = options.edgeTooltips || {};
    this.selectors = { ...DEFAULT_SELECTORS, ...(options.selectors || {}) };

    this.contextNodeOpacity = options.contextNodeOpacity || "0.22";
    this.contextEdgeOpacity = options.contextEdgeOpacity || "0.18";
    this.zoomFill = options.zoomFill || 0.65;
    this.zoomFrames = options.zoomFrames || 22;
    this.panZoomMin = options.panZoomMin || 0.05;
    this.panZoomMax = options.panZoomMax || 15;
    this.panZoomOptions = options.panZoomOptions || {};

    this.exposeGlobals = options.exposeGlobals ?? true;
    this.autoBindControls = options.autoBindControls ?? false;
    this.svgPanZoomImpl = options.svgPanZoom || (window as unknown as { svgPanZoom?: PanZoomFactory }).svgPanZoom;
  }

  getDiagramSvg(): SVGSVGElement | null {
    return this.doc.querySelector<SVGSVGElement>(this.selectors.targetSvg);
  }

  showDetail(nodeId: string): void {
    const content = this.detailPanels[nodeId];
    if (!content || !this.detailDrawerEl) return;
    if (this.drawerNodeIdEl) this.drawerNodeIdEl.textContent = nodeId;
    if (this.drawerBodyEl) this.drawerBodyEl.innerHTML = content;
    this.detailDrawerEl.classList.add("open");
  }

  hideDetail(): void {
    this.detailDrawerEl?.classList.remove("open");
  }

  hideEdgeTooltip(): void {
    this.edgeTooltipEl?.classList.remove("visible");
  }

  hideTransientUI(): void {
    this.hideDetail();
    this.hideEdgeTooltip();
  }

  goStep(idx: number, btn: HTMLElement | null = null): void {
    goStep(this, idx, btn);
  }

  toggleFocus(): void {
    toggleFocus(this);
  }

  resetOverview(): void {
    resetOverview(this);
  }

  zoomIn(): void {
    this.pz?.zoomIn();
  }

  zoomOut(): void {
    this.pz?.zoomOut();
  }

  bindControls(): void {
    this.doc.querySelectorAll<HTMLElement>(this.selectors.stepButtons).forEach((btn) => {
      if (btn.hasAttribute("onclick")) return;
      const step = Number.parseInt(btn.dataset.step || "", 10);
      if (Number.isNaN(step)) return;
      btn.addEventListener("click", () => this.goStep(step, btn));
    });
    if (this.prevBtn && !this.prevBtn.hasAttribute("onclick")) {
      this.prevBtn.addEventListener("click", () => this.goStep(this.curStep - 1));
    }
    if (this.nextBtn && !this.nextBtn.hasAttribute("onclick")) {
      this.nextBtn.addEventListener("click", () => this.goStep(this.curStep + 1));
    }
    if (this.focusBtn && !this.focusBtn.hasAttribute("onclick")) {
      this.focusBtn.addEventListener("click", () => this.toggleFocus());
    }
    if (this.fitBtn && !this.fitBtn.hasAttribute("onclick")) {
      this.fitBtn.addEventListener("click", () => this.resetOverview());
    }
    if (this.zoomInBtn && !this.zoomInBtn.hasAttribute("onclick")) {
      this.zoomInBtn.addEventListener("click", () => this.zoomIn());
    }
    if (this.zoomOutBtn && !this.zoomOutBtn.hasAttribute("onclick")) {
      this.zoomOutBtn.addEventListener("click", () => this.zoomOut());
    }
  }

  exposeInlineApi(): void {
    if (!this.exposeGlobals) return;
    (window as unknown as Record<string, unknown>).viewer = this;
    (window as unknown as Record<string, unknown>).goStep = (idx: number, btn: HTMLElement | null = null) =>
      this.goStep(idx, btn);
    (window as unknown as Record<string, unknown>).toggleFocus = () => this.toggleFocus();
    (window as unknown as Record<string, unknown>).resetOverview = () => this.resetOverview();
    (window as unknown as Record<string, unknown>).hideDetail = () => this.hideDetail();
    (window as unknown as Record<string, unknown>).pz = this.pz;
  }

  init(): void {
    this.canvasWrap = this.doc.querySelector<HTMLElement>(this.selectors.canvasWrap);
    this.svgHost = this.doc.querySelector<HTMLElement>(this.selectors.svgHost);
    this.stepTagEl = this.doc.querySelector<HTMLElement>(this.selectors.stepTag);
    this.stepTitleEl = this.doc.querySelector<HTMLElement>(this.selectors.stepTitle);
    this.stepBodyEl = this.doc.querySelector<HTMLElement>(this.selectors.stepBody);
    this.prevBtn = this.doc.querySelector<HTMLButtonElement>(this.selectors.prevBtn);
    this.nextBtn = this.doc.querySelector<HTMLButtonElement>(this.selectors.nextBtn);
    this.focusBtn = this.doc.querySelector<HTMLButtonElement>(this.selectors.focusBtn);
    this.fitBtn = this.doc.querySelector<HTMLButtonElement>(this.selectors.fitBtn);
    this.zoomInBtn = this.doc.querySelector<HTMLButtonElement>(this.selectors.zoomInBtn);
    this.zoomOutBtn = this.doc.querySelector<HTMLButtonElement>(this.selectors.zoomOutBtn);
    this.detailDrawerEl = this.doc.querySelector<HTMLElement>(this.selectors.detailDrawer);
    this.drawerNodeIdEl = this.doc.querySelector<HTMLElement>(this.selectors.drawerNodeId);
    this.drawerBodyEl = this.doc.querySelector<HTMLElement>(this.selectors.drawerBody);
    this.edgeTooltipEl = this.doc.querySelector<HTMLElement>(this.selectors.edgeTooltip);

    if (!this.canvasWrap || !this.svgHost || !this.svgPanZoomImpl) return;

    const targetSvg = this.getDiagramSvg();
    if (!targetSvg) return;

    const resize = (): void => {
      if (!this.canvasWrap) return;
      targetSvg.setAttribute("width", String(this.canvasWrap.clientWidth));
      targetSvg.setAttribute("height", String(this.canvasWrap.clientHeight));
      this.pz?.resize();
    };
    resize();
    this.onResize = () => {
      resize();
      this.pz?.fit();
      this.pz?.center();
    };
    window.addEventListener("resize", this.onResize);

    this.pz = this.svgPanZoomImpl(targetSvg, {
      zoomEnabled: true,
      controlIconsEnabled: false,
      fit: true,
      center: true,
      minZoom: this.panZoomMin,
      maxZoom: this.panZoomMax,
      zoomScaleSensitivity: 0.3,
      ...this.panZoomOptions,
    });

    tagNodes(this);
    tagEdges(this);
    setupEdgeTooltips(this);

    this.onCanvasClick = (e: MouseEvent) => {
      if (!e.target || !(e.target instanceof Element)) return;
      if (!e.target.closest(this.selectors.detailDrawer) && !e.target.closest(".d2-node")) {
        this.hideTransientUI();
      }
    };
    this.canvasWrap.addEventListener("click", this.onCanvasClick);

    bindKeyboard(this);
    if (this.autoBindControls) this.bindControls();

    this.goStep(0, this.doc.querySelector<HTMLElement>(`${this.selectors.stepButtons}[data-step="0"]`));
    applyHighlight(this, this.steps[0]?.nodes || []);
    this.exposeInlineApi();
  }

  destroy(): void {
    if (this.onResize) window.removeEventListener("resize", this.onResize);
    if (this.onMouseMove) this.doc.removeEventListener("mousemove", this.onMouseMove);
    if (this.onCanvasClick && this.canvasWrap) this.canvasWrap.removeEventListener("click", this.onCanvasClick);
    if (this.onKeyDown) this.doc.removeEventListener("keydown", this.onKeyDown);
    if (this.zoomRaf) cancelAnimationFrame(this.zoomRaf);
    this.pz?.destroy?.();
  }
}
