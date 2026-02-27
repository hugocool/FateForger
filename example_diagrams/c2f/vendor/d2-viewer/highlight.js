import { clamp } from "./utils.js";
export function applyHighlight(viewer, nodeIds) {
    const allNodes = Array.from(viewer.doc.querySelectorAll(".d2-node"));
    const allEdges = Array.from(viewer.doc.querySelectorAll(".d2-edge"));
    if (!nodeIds || nodeIds.length === 0) {
        viewer.svgHost?.classList.remove("focus-mode");
        allNodes.forEach((n) => {
            n.classList.remove("lit", "dimmed", "ancestor");
            n.style.opacity = "";
        });
        allEdges.forEach((e) => {
            e.classList.remove("lit", "dimmed", "ancestor");
            e.style.opacity = "";
        });
        return;
    }
    const active = new Set(nodeIds);
    if (viewer.focusMode)
        viewer.svgHost?.classList.add("focus-mode");
    else
        viewer.svgHost?.classList.remove("focus-mode");
    allNodes.forEach((n) => {
        const id = n.dataset.nodeId || "";
        if (active.has(id)) {
            n.classList.add("lit");
            n.classList.remove("dimmed", "ancestor");
            n.style.opacity = "1";
            return;
        }
        n.classList.remove("lit", "ancestor");
        n.classList.add("dimmed");
        n.style.opacity = viewer.focusMode ? "" : viewer.contextNodeOpacity;
    });
    allNodes.forEach((n) => {
        if (!n.classList.contains("lit"))
            return;
        let el = n.parentElement;
        while (el && el !== viewer.doc.body) {
            if (el.classList?.contains("d2-node") && el.classList.contains("dimmed")) {
                el.classList.remove("dimmed");
                el.classList.add("ancestor");
                el.style.opacity = "1";
            }
            el = el.parentElement;
        }
    });
    allEdges.forEach((e) => {
        const ancestorLit = Boolean(e.closest(".d2-node.lit"));
        const src = e.dataset.edgeSrc;
        const dst = e.dataset.edgeDst;
        const connectsLit = !ancestorLit && Boolean(src && dst && active.has(src) && active.has(dst));
        if (ancestorLit || connectsLit) {
            e.classList.add("lit");
            e.classList.remove("dimmed");
            e.style.opacity = "1";
            return;
        }
        e.classList.remove("lit");
        e.classList.add("dimmed");
        e.style.opacity = viewer.focusMode ? "" : viewer.contextEdgeOpacity;
    });
}
export function autoZoom(viewer, nodeIds) {
    if (!viewer.pz)
        return;
    if (!nodeIds || nodeIds.length === 0) {
        viewer.pz.fit();
        viewer.pz.center();
        return;
    }
    if (!viewer.canvasWrap)
        return;
    const active = new Set(nodeIds);
    const litEls = Array.from(viewer.doc.querySelectorAll(".d2-node")).filter((n) => active.has(n.dataset.nodeId || ""));
    if (!litEls.length)
        return;
    const wrapRect = viewer.canvasWrap.getBoundingClientRect();
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    litEls.forEach((el) => {
        const r = el.getBoundingClientRect();
        const lx = r.left - wrapRect.left;
        const ly = r.top - wrapRect.top;
        minX = Math.min(minX, lx);
        minY = Math.min(minY, ly);
        maxX = Math.max(maxX, lx + r.width);
        maxY = Math.max(maxY, ly + r.height);
    });
    const bbW = maxX - minX;
    const bbH = maxY - minY;
    if (bbW <= 0 || bbH <= 0)
        return;
    const bbCx = (minX + maxX) / 2;
    const bbCy = (minY + maxY) / 2;
    const vW = viewer.canvasWrap.clientWidth;
    const vH = viewer.canvasWrap.clientHeight;
    const currentZoom = viewer.pz.getZoom();
    const currentPan = viewer.pz.getPan();
    const centerSvgX = (bbCx - currentPan.x) / currentZoom;
    const centerSvgY = (bbCy - currentPan.y) / currentZoom;
    const scaleNeeded = Math.min((vW * viewer.zoomFill) / bbW, (vH * viewer.zoomFill) / bbH);
    const targetZoom = clamp(currentZoom * scaleNeeded, viewer.panZoomMin, viewer.panZoomMax);
    const targetPanX = vW / 2 - centerSvgX * targetZoom;
    const targetPanY = vH / 2 - centerSvgY * targetZoom;
    if (viewer.zoomRaf)
        cancelAnimationFrame(viewer.zoomRaf);
    const startZoom = currentZoom;
    const startPanX = currentPan.x;
    const startPanY = currentPan.y;
    let frame = 0;
    const totalFrames = viewer.zoomFrames;
    const ease = (t) => (t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t);
    const tick = () => {
        frame += 1;
        const t = ease(Math.min(frame / totalFrames, 1));
        viewer.pz?.zoom(startZoom + (targetZoom - startZoom) * t);
        viewer.pz?.pan({
            x: startPanX + (targetPanX - startPanX) * t,
            y: startPanY + (targetPanY - startPanY) * t,
        });
        if (frame < totalFrames)
            viewer.zoomRaf = requestAnimationFrame(tick);
    };
    viewer.zoomRaf = requestAnimationFrame(tick);
}
