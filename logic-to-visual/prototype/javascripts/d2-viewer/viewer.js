import { applyHighlight } from "./highlight.js";
import { bindKeyboard, goStep, resetOverview, toggleFocus } from "./navigation.js";
import { setupEdgeTooltips, tagEdges, tagNodes } from "./tagging.js";
const DEFAULT_SELECTORS = {
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
    constructor(options) {
        this.curStep = 0;
        this.focusMode = false;
        this.zoomRaf = null;
        this.pz = null;
        this.canvasWrap = null;
        this.svgHost = null;
        this.stepTagEl = null;
        this.stepTitleEl = null;
        this.stepBodyEl = null;
        this.prevBtn = null;
        this.nextBtn = null;
        this.focusBtn = null;
        this.fitBtn = null;
        this.zoomInBtn = null;
        this.zoomOutBtn = null;
        this.detailDrawerEl = null;
        this.drawerNodeIdEl = null;
        this.drawerBodyEl = null;
        this.edgeTooltipEl = null;
        this.onResize = null;
        this.onMouseMove = null;
        this.onCanvasClick = null;
        this.onKeyDown = null;
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
        this.svgPanZoomImpl = options.svgPanZoom || window.svgPanZoom;
    }
    getDiagramSvg() {
        return this.doc.querySelector(this.selectors.targetSvg);
    }
    showDetail(nodeId) {
        const content = this.detailPanels[nodeId];
        if (!content || !this.detailDrawerEl)
            return;
        if (this.drawerNodeIdEl)
            this.drawerNodeIdEl.textContent = nodeId;
        if (this.drawerBodyEl)
            this.drawerBodyEl.innerHTML = content;
        this.detailDrawerEl.classList.add("open");
    }
    hideDetail() {
        this.detailDrawerEl?.classList.remove("open");
    }
    hideEdgeTooltip() {
        this.edgeTooltipEl?.classList.remove("visible");
    }
    hideTransientUI() {
        this.hideDetail();
        this.hideEdgeTooltip();
    }
    goStep(idx, btn = null) {
        goStep(this, idx, btn);
    }
    toggleFocus() {
        toggleFocus(this);
    }
    resetOverview() {
        resetOverview(this);
    }
    zoomIn() {
        this.pz?.zoomIn();
    }
    zoomOut() {
        this.pz?.zoomOut();
    }
    bindControls() {
        this.doc.querySelectorAll(this.selectors.stepButtons).forEach((btn) => {
            if (btn.hasAttribute("onclick"))
                return;
            const step = Number.parseInt(btn.dataset.step || "", 10);
            if (Number.isNaN(step))
                return;
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
    exposeInlineApi() {
        if (!this.exposeGlobals)
            return;
        window.viewer = this;
        window.goStep = (idx, btn = null) => this.goStep(idx, btn);
        window.toggleFocus = () => this.toggleFocus();
        window.resetOverview = () => this.resetOverview();
        window.hideDetail = () => this.hideDetail();
        window.pz = this.pz;
    }
    init() {
        this.canvasWrap = this.doc.querySelector(this.selectors.canvasWrap);
        this.svgHost = this.doc.querySelector(this.selectors.svgHost);
        this.stepTagEl = this.doc.querySelector(this.selectors.stepTag);
        this.stepTitleEl = this.doc.querySelector(this.selectors.stepTitle);
        this.stepBodyEl = this.doc.querySelector(this.selectors.stepBody);
        this.prevBtn = this.doc.querySelector(this.selectors.prevBtn);
        this.nextBtn = this.doc.querySelector(this.selectors.nextBtn);
        this.focusBtn = this.doc.querySelector(this.selectors.focusBtn);
        this.fitBtn = this.doc.querySelector(this.selectors.fitBtn);
        this.zoomInBtn = this.doc.querySelector(this.selectors.zoomInBtn);
        this.zoomOutBtn = this.doc.querySelector(this.selectors.zoomOutBtn);
        this.detailDrawerEl = this.doc.querySelector(this.selectors.detailDrawer);
        this.drawerNodeIdEl = this.doc.querySelector(this.selectors.drawerNodeId);
        this.drawerBodyEl = this.doc.querySelector(this.selectors.drawerBody);
        this.edgeTooltipEl = this.doc.querySelector(this.selectors.edgeTooltip);
        if (!this.canvasWrap || !this.svgHost || !this.svgPanZoomImpl)
            return;
        const targetSvg = this.getDiagramSvg();
        if (!targetSvg)
            return;
        const resize = () => {
            if (!this.canvasWrap)
                return;
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
        this.onCanvasClick = (e) => {
            if (!e.target || !(e.target instanceof Element))
                return;
            if (!e.target.closest(this.selectors.detailDrawer) && !e.target.closest(".d2-node")) {
                this.hideTransientUI();
            }
        };
        this.canvasWrap.addEventListener("click", this.onCanvasClick);
        bindKeyboard(this);
        if (this.autoBindControls)
            this.bindControls();
        this.goStep(0, this.doc.querySelector(`${this.selectors.stepButtons}[data-step="0"]`));
        applyHighlight(this, this.steps[0]?.nodes || []);
        this.exposeInlineApi();
    }
    destroy() {
        if (this.onResize)
            window.removeEventListener("resize", this.onResize);
        if (this.onMouseMove)
            this.doc.removeEventListener("mousemove", this.onMouseMove);
        if (this.onCanvasClick && this.canvasWrap)
            this.canvasWrap.removeEventListener("click", this.onCanvasClick);
        if (this.onKeyDown)
            this.doc.removeEventListener("keydown", this.onKeyDown);
        if (this.zoomRaf)
            cancelAnimationFrame(this.zoomRaf);
        this.pz?.destroy?.();
    }
}
