import type { Model } from "./model";
import type { Heat, TreeNode } from "./heat";
import { worse } from "./heat";

export interface MapTreeNode {
  name: string;
  path: string;
  kind: "file" | "dir";
  heat: Heat;
  weight: number;
  groupId: string;
  /** Directory tile that can be opened; its contents are not drawn yet. */
  drill?: boolean;
  /** This directory is the one opened inside its parent. */
  open?: boolean;
  children: MapTreeNode[];
}

export interface MapRect {
  path: string;
  name: string;
  kind: "file" | "dir";
  heat: Heat;
  groupId: string;
  weight: number;
  depth: number;
  drill?: boolean;
  open?: boolean;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface MapLink {
  id: string;
  sourcePath: string;
  targetPath: string;
  count: number;
  soft: boolean;
}

const PAD = 3;
const HEADER = 26;
const MIN_LABEL = 36;

/** Keep heated leaves and ancestors. Unaffected files inside a heated directory stay, so area matches the package. */
export function pruneHeated(root: TreeNode): TreeNode {
  const walk = (n: TreeNode): TreeNode | null => {
    if (n.kind === "file") return n.heat === "none" ? null : { ...n, children: [] };
    const heatedKids = n.children.map(walk).filter((c): c is TreeNode => !!c);
    if (!heatedKids.length && n.heat === "none") return null;
    const have = new Set(heatedKids.map((c) => c.path));
    const files = n.children.filter((c) => c.kind === "file" && !have.has(c.path)).map((c) => ({ ...c, children: [] as TreeNode[] }));
    const kids = [...heatedKids, ...files];
    if (!kids.length) return null;
    let h: Heat = "none";
    for (const c of kids) h = worse(h, c.heat);
    return { ...n, heat: h, children: kids };
  };
  const kids = root.children.map(walk).filter((c): c is TreeNode => !!c);
  let h: Heat = "none";
  for (const c of kids) h = worse(h, c.heat);
  return { ...root, heat: h, children: kids };
}

function fileCount(n: TreeNode): number {
  if (n.kind === "file") return 1;
  return n.children.reduce((s, c) => s + fileCount(c), 0);
}

export function toMapTree(root: TreeNode, groupOfPath: (p: string) => string): MapTreeNode {
  const convert = (n: TreeNode): MapTreeNode => {
    const children = n.children.map(convert);
    const weight = n.kind === "file" ? 1 : Math.max(1, children.reduce((s, c) => s + c.weight, 0));
    const sample = n.kind === "file" ? n.path : deepestFile(n) ?? n.path;
    return {
      name: n.name || ".",
      path: n.path,
      kind: n.kind,
      heat: n.heat,
      weight,
      groupId: groupOfPath(sample),
      children,
    };
  };
  return convert(root);
}

function deepestFile(n: TreeNode): string | null {
  if (n.kind === "file") return n.path;
  for (const c of n.children) {
    const f = deepestFile(c);
    if (f) return f;
  }
  return null;
}

/** Squarified treemap (Bruls et al.): layout children into rect. */
function squarify(
  items: { node: MapTreeNode; value: number }[],
  x: number,
  y: number,
  w: number,
  h: number,
  depth: number,
  out: MapRect[],
) {
  if (!items.length || w <= 0 || h <= 0) return;
  const total = items.reduce((s, i) => s + i.value, 0);
  if (total <= 0) return;

  let row: typeof items = [];
  let rest = [...items];

  const worst = (rowItems: typeof items, len: number) => {
    if (!rowItems.length) return Infinity;
    const s = rowItems.reduce((a, b) => a + b.value, 0);
    const rowArea = (s / total) * (w * h);
    const side = len;
    let maxR = 0;
    let minR = Infinity;
    for (const it of rowItems) {
      const area = (it.value / s) * rowArea;
      const other = area / side;
      maxR = Math.max(maxR, side / other, other / side);
      minR = Math.min(minR, side / other, other / side);
    }
    return maxR;
  };

  const layoutRow = (rowItems: typeof items, horizontal: boolean) => {
    const s = rowItems.reduce((a, b) => a + b.value, 0);
    if (horizontal) {
      const rowH = (s / total) * h;
      let cx = x;
      for (const it of rowItems) {
        const cw = (it.value / s) * w;
        placeNode(it.node, cx, y, cw, rowH, depth, out);
        cx += cw;
      }
      y += rowH;
      h -= rowH;
    } else {
      const rowW = (s / total) * w;
      let cy = y;
      for (const it of rowItems) {
        const ch = (it.value / s) * h;
        placeNode(it.node, x, cy, rowW, ch, depth, out);
        cy += ch;
      }
      x += rowW;
      w -= rowW;
    }
  };

  while (rest.length) {
    const next = rest[0];
    const len = Math.min(w, h);
    const horizontal = h <= w;
    if (!row.length || worst([...row, next], len) <= worst(row, len)) {
      row.push(next);
      rest = rest.slice(1);
    } else {
      layoutRow(row, horizontal);
      row = [];
      // restart with remaining in shrunk rect — recurse
      squarify(rest, x, y, w, h, depth, out);
      return;
    }
  }
  if (row.length) layoutRow(row, h <= w);
}

function placeNode(node: MapTreeNode, x: number, y: number, w: number, h: number, depth: number, out: MapRect[]) {
  const rw = Math.max(0, w);
  const rh = Math.max(0, h);
  out.push({
    path: node.path,
    name: node.name,
    kind: node.kind,
    heat: node.heat,
    groupId: node.groupId,
    weight: node.weight,
    depth,
    drill: node.drill,
    open: node.open,
    x,
    y,
    w: rw,
    h: rh,
  });
  if (node.kind === "file" || !node.children.length) return;
  const innerX = x + PAD;
  const innerY = y + PAD + (rh > HEADER + PAD * 2 ? HEADER : 0);
  const innerW = rw - PAD * 2;
  const innerH = rh - PAD * 2 - (rh > HEADER + PAD * 2 ? HEADER : 0);
  if (innerW < 4 || innerH < 4) return;
  const kids = node.children
    .map((c) => ({ node: c, value: Math.max(1, c.weight) }))
    .sort((a, b) => b.value - a.value);
  squarify(kids, innerX, innerY, innerW, innerH, depth + 1, out);
}

export function layoutTreemap(root: MapTreeNode, width: number, height: number): MapRect[] {
  const out: MapRect[] = [];
  if (!root.children.length) {
    if (root.heat !== "none" && root.path) {
      out.push({
        path: root.path || ".",
        name: root.name,
        kind: root.kind,
        heat: root.heat,
        groupId: root.groupId,
        weight: root.weight,
        depth: 0,
        x: 0,
        y: 0,
        w: width,
        h: height,
      });
    }
    return out;
  }
  const kids = root.children
    .map((c) => ({ node: c, value: Math.max(1, c.weight) }))
    .sort((a, b) => b.value - a.value);
  squarify(kids, 0, 0, width, height, 0, out);
  return out;
}

/** Collapse function edges to package/dir links using group ids, then resolve to map paths. */
export function crossGroupLinks(m: Model, rects: MapRect[]): MapLink[] {
  const byGroup = new Map<string, MapRect>();
  const byPath = new Map<string, MapRect>();
  for (const r of rects) {
    byPath.set(r.path, r);
    if (!byGroup.has(r.groupId) || r.kind === "dir") byGroup.set(r.groupId, r);
  }

  const resolve = (path: string): string | null => {
    let p = path;
    while (p) {
      if (byPath.has(p)) return p;
      p = p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
    }
    const gid = m.groupOfPath(path);
    return byGroup.get(gid)?.path ?? (byPath.has("") ? "" : null);
  };

  const counts = new Map<string, { sourcePath: string; targetPath: string; count: number; soft: boolean }>();
  for (const e of m.rep.edges) {
    const a = m.byId.get(e.source);
    const b = m.byId.get(e.target);
    if (!a || !b) continue;
    const sp = resolve(a.path);
    const tp = resolve(b.path);
    if (!sp || !tp || sp === tp) continue;
    const key = `${sp}\0${tp}`;
    const soft = e.kind === "dispatch" || (e.sites?.length ?? 0) === 0;
    const cur = counts.get(key);
    if (cur) {
      cur.count += 1;
      cur.soft = cur.soft && soft;
    } else counts.set(key, { sourcePath: sp, targetPath: tp, count: 1, soft });
  }

  return [...counts.entries()].map(([id, v]) => ({ id, ...v }));
}

/** One edge per other package, at most `limit`, from the hovered block outward. */
export function findMapNode(root: MapTreeNode, path: string): MapTreeNode | null {
  if ((root.path || "") === path) return root;
  for (const c of root.children) {
    const hit = findMapNode(c, path);
    if (hit) return hit;
  }
  return null;
}

function collapse(node: MapTreeNode, open: boolean): MapTreeNode {
  return {
    ...node,
    open,
    drill: !open && node.kind === "dir" && node.children.length > 0,
    children: open
      ? node.children.map((c) => ({
          ...c,
          drill: c.kind === "dir" && c.children.length > 0,
          open: false,
          children: [],
        }))
      : [],
  };
}

/** Parent's direct children stay on screen. Only `opened` is expanded one level. */
export function contextView(root: MapTreeNode, opened: string): MapTreeNode {
  if (!opened) return collapse(root, true);
  const node = findMapNode(root, opened);
  if (!node) return collapse(root, true);
  const parentPath = opened.includes("/") ? opened.slice(0, opened.lastIndexOf("/")) : "";
  const parent = parentPath ? findMapNode(root, parentPath) : root;
  if (!parent) return collapse(root, true);
  return {
    ...parent,
    children: parent.children.map((c) => collapse(c, c.path === opened)),
  };
}

export function peerLinks(m: Model, rects: MapRect[], key: string, limit = 6): MapLink[] {
  const byPath = new Map(rects.map((r) => [r.path, r]));
  const here = byPath.get(key);
  if (!here) return [];
  const root = here.kind === "dir" ? key : key.includes("/") ? key.slice(0, key.lastIndexOf("/")) : "";
  const inside = (path: string) => path === root || (root !== "" && path.startsWith(`${root}/`)) || path === key;

  const lift = (path: string): string | null => {
    if (inside(path)) return null;
    let best = "";
    let found = false;
    for (const r of rects) {
      if (r.kind !== "dir") continue;
      if (!r.path) continue;
      const holds = path === r.path || path.startsWith(`${r.path}/`);
      if (!holds || inside(r.path)) continue;
      if (root && (root === r.path || root.startsWith(`${r.path}/`))) continue;
      if (!found || r.path.length > best.length) {
        best = r.path;
        found = true;
      }
    }
    return found ? best : null;
  };

  const counts = new Map<string, { targetPath: string; count: number; soft: boolean }>();
  for (const e of m.rep.edges) {
    const a = m.byId.get(e.source);
    const b = m.byId.get(e.target);
    if (!a || !b) continue;
    const aIn = inside(a.path);
    const bIn = inside(b.path);
    if (aIn === bIn) continue;
    const other = lift(aIn ? b.path : a.path);
    if (!other || other === root) continue;
    const soft = e.kind === "dispatch" || (e.sites?.length ?? 0) === 0;
    const cur = counts.get(other);
    if (cur) {
      cur.count += 1;
      cur.soft = cur.soft && soft;
    } else counts.set(other, { targetPath: other, count: 1, soft });
  }
  return [...counts.values()]
    .sort((a, b) => b.count - a.count)
    .slice(0, limit)
    .map((v) => ({ id: `${root}\0${v.targetPath}`, sourcePath: root || key, targetPath: v.targetPath, count: v.count, soft: v.soft }));
}

export function edgeAnchor(from: MapRect, to: MapRect): { x: number; y: number } {
  const fx = from.x + from.w / 2;
  const fy = from.y + from.h / 2;
  const tx = to.x + to.w / 2;
  const ty = to.y + to.h / 2;
  const dx = tx - fx;
  const dy = ty - fy;
  if (Math.abs(dx) >= Math.abs(dy)) return { x: dx >= 0 ? from.x + from.w : from.x, y: fy };
  return { x: fx, y: dy >= 0 ? from.y + from.h : from.y };
}

export function labelFits(r: MapRect): boolean {
  return r.w >= MIN_LABEL && r.h >= 14;
}

export { fileCount };
