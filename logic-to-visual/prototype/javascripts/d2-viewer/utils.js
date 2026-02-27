export function b64(value) {
    return btoa(value);
}
export function decodeEdgeEndpointsFromClassToken(token) {
    if (!token)
        return null;
    let decoded = "";
    try {
        decoded = atob(token);
    }
    catch (_err) {
        return null;
    }
    decoded = decoded.replace(/&gt;/g, ">").trim().replace(/\[\d+\]$/, "");
    let prefix = "";
    const scoped = decoded.match(/^([a-zA-Z0-9_.]+)\.\((.+)\)$/);
    if (scoped) {
        prefix = scoped[1];
        decoded = scoped[2];
    }
    else if (decoded.startsWith("(") && decoded.endsWith(")")) {
        decoded = decoded.slice(1, -1);
    }
    const parts = decoded.split("->").map((s) => s.trim());
    if (parts.length !== 2 || !parts[0] || !parts[1])
        return null;
    const qualify = (part) => (part.includes(".") || !prefix ? part : `${prefix}.${part}`);
    return [qualify(parts[0]), qualify(parts[1])];
}
export function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}
