import type { Focus } from "./types";
import type { Model } from "./model";

export type Heat = "changed" | "error" | "direct" | "indirect" | "none";

const RANK: Record<Heat, number> = { error: 4, changed: 3, direct: 2, indirect: 1, none: 0 };

export function worse(a: Heat, b: Heat): Heat {
  return RANK[a] >= RANK[b] ? a : b;
}

export function heatOfPath(m: Model, path: string): Heat {
  let h: Heat = "none";
  for (const u of m.units.values()) {
    if (u.files.some((f) => f.path === path)) h = worse(h, "changed");
  }
  for (const n of m.rep.nodes) {
    if (n.path !== path) continue;
    if (n.breaking?.length) h = worse(h, "error");
    else if (n.change) h = worse(h, "changed");
    else if (n.depth <= 1) h = worse(h, "direct");
    else h = worse(h, "indirect");
  }
  return h;
}

export interface TreeEntry {
  path: string;
  kind: "file" | "dir";
}

export interface TreeNode {
  name: string;
  path: string;
  kind: "file" | "dir";
  heat: Heat;
  children: TreeNode[];
}

/** Build a sorted IDEA-style tree: directories first, then alphabetical. */
export function buildTree(entries: TreeEntry[], heat: Map<string, Heat>): TreeNode {
  const root: TreeNode = { name: "", path: "", kind: "dir", heat: "none", children: [] };
  const byPath = new Map<string, TreeNode>([["", root]]);

  const ensureDir = (dir: string): TreeNode => {
    if (byPath.has(dir)) return byPath.get(dir)!;
    const parentPath = dir.includes("/") ? dir.slice(0, dir.lastIndexOf("/")) : "";
    const parent = ensureDir(parentPath);
    const name = dir.includes("/") ? dir.slice(dir.lastIndexOf("/") + 1) : dir;
    const node: TreeNode = { name, path: dir, kind: "dir", heat: "none", children: [] };
    parent.children.push(node);
    byPath.set(dir, node);
    return node;
  };

  for (const e of entries) {
    if (e.kind === "dir") {
      ensureDir(e.path);
      continue;
    }
    const parentPath = e.path.includes("/") ? e.path.slice(0, e.path.lastIndexOf("/")) : "";
    const parent = ensureDir(parentPath);
    const name = e.path.includes("/") ? e.path.slice(e.path.lastIndexOf("/") + 1) : e.path;
    if (parent.children.some((c) => c.path === e.path)) continue;
    parent.children.push({
      name,
      path: e.path,
      kind: "file",
      heat: heat.get(e.path) ?? "none",
      children: [],
    });
  }

  const sortRec = (n: TreeNode) => {
    n.children.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "dir" ? -1 : 1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    });
    for (const c of n.children) sortRec(c);
  };
  sortRec(root);

  const rollup = (n: TreeNode): Heat => {
    if (n.kind === "file") return n.heat;
    let h: Heat = "none";
    for (const c of n.children) h = worse(h, rollup(c));
    n.heat = h;
    return h;
  };
  rollup(root);
  return root;
}

export function heatMapForModel(m: Model, files: string[]): Map<string, Heat> {
  const map = new Map<string, Heat>();
  for (const p of files) map.set(p, heatOfPath(m, p));
  return map;
}

/** Paths that should start expanded: ancestors of any non-none heat. */
export function defaultExpanded(root: TreeNode): Set<string> {
  const out = new Set<string>();
  const walk = (n: TreeNode, ancestors: string[]) => {
    if (n.kind === "file" && n.heat !== "none") {
      for (const a of ancestors) out.add(a);
    }
    for (const c of n.children) walk(c, n.path ? [...ancestors, n.path] : ancestors);
  };
  walk(root, []);
  return out;
}

export function entityIdsForPath(m: Model, path: string): string[] {
  return m.rep.nodes.filter((n) => n.path === path).map((n) => n.id);
}

export function focusForPath(m: Model, path: string): Focus | null {
  for (const u of m.units.values()) {
    if (u.files.some((f) => f.path === path)) return { type: "unit", id: u.id };
  }
  if (m.rep.nodes.some((n) => n.path === path && !n.change)) {
    return { type: "impactFile", group: m.groupOfPath(path), path };
  }
  const changed = m.rep.nodes.find((x) => x.path === path && x.change);
  if (changed) return { type: "entity", id: changed.id };
  return null;
}
