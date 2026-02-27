export type Step = {
  tag: string;
  title: string;
  body: string;
  nodes: string[];
};

export type ViewerSelectors = {
  canvasWrap: string;
  svgHost: string;
  targetSvg: string;
  stepButtons: string;
  stepTag: string;
  stepTitle: string;
  stepBody: string;
  prevBtn: string;
  nextBtn: string;
  focusBtn: string;
  fitBtn: string;
  zoomInBtn: string;
  zoomOutBtn: string;
  detailDrawer: string;
  drawerNodeId: string;
  drawerBody: string;
  edgeTooltip: string;
};

export type PanZoomInstance = {
  fit(): void;
  center(): void;
  zoom(v: number): void;
  pan(v: { x: number; y: number }): void;
  zoomIn(): void;
  zoomOut(): void;
  getZoom(): number;
  getPan(): { x: number; y: number };
  resize(): void;
  destroy?(): void;
};

export type PanZoomFactory = (
  svg: SVGSVGElement,
  options: Record<string, unknown>
) => PanZoomInstance;

export type ViewerOptions = {
  document?: Document;
  steps: Step[];
  nodeIds: string[];
  detailPanels?: Record<string, string>;
  edgeTooltips?: Record<string, string>;
  selectors?: Partial<ViewerSelectors>;
  contextNodeOpacity?: string;
  contextEdgeOpacity?: string;
  zoomFill?: number;
  zoomFrames?: number;
  panZoomMin?: number;
  panZoomMax?: number;
  panZoomOptions?: Record<string, unknown>;
  exposeGlobals?: boolean;
  autoBindControls?: boolean;
  svgPanZoom?: PanZoomFactory;
};
