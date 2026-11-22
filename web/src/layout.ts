import type { Edge, Node } from "@xyflow/react";
import type { EdgeKind, ImpactNode } from "./types";
import { basename, CONTRACT, MINOR, type ChangedUnit, type Model } from "./model";

export type Detail = "auto" | "full" | "files" | "groups";
export type Tier = "full" | "files" | "groups";

export interface ViewOpts {
  focusIds: string[] | null;
  hidden: Set<string>;
  riskyOnly: boolean;
  hideTests: boolean;
  hideMinor: boolean;
  maxDepth: number; // 0 = all
  detail: Detail;
  expandedGroups: Set<string>;
  expandedCards: Set<string>;
  positions: Record<string, { x: number; y: number }>;
}

export const SIZE = {
  CW: 320, IW: 290, GP: 10, GROUP_HEAD: 30, CARD_HEAD: 38, ROW_H: 28, ERR_H: 18, CARD_PAD: 8, CARD_GAP: 8,
  GROUP_GAP: 22, COL_GAP: 120, GROUPROW_H: 72, COMPACT_H: 44, MAX_ROWS_CHANGED: 8, MAX_ROWS_MEDIUM: 3, MAX_ROWS_IMPACT: 6,
};

export interface RowData {
  id: string;
  name: string;
  change?: string | null;
  tone?: string;
  count?: number;
  errorsBelow?: number;
  via?: EdgeKind | null;
  depth?: number;
  error?: string;
  uncertain?: boolean;
  confidence?: string;
}

export interface HandleSpec { id: string; y: number }

export interface CardData extends Record<string, unknown> {
  kind: "changed" | "impact";
  key: string; // hide key
  title: string;
  path: string;
  group: string;
  lang: string | null;
  parts: string[];
  rows: RowData[] | null; // null = compact card
  more: number;
  ids: string[];
  count: number;
  errors: number;
  test: boolean;
  col: number;
  sources: HandleSpec[];
  targets: HandleSpec[];
}

export interface GroupData extends Record<string, unknown> {
  side: "changed" | "impact";
  gid: string;
  key: string;
  module: string;
  pkg: string;
  count: string;
  ids: string[];
  collapsed: boolean;
  collapsible?: boolean;
  stats?: { files: number; entities: number; contract: number; callers: number; errors: number; max: number };
  col: number;
  sources: HandleSpec[];
  targets: HandleSpec[];
}

export interface ColData extends Record<string, unknown> {
  tone: "changed" | "direct" | "indirect";
  title: string;
  count: string;
}

export interface WireData extends Record<string, unknown> {
  pairs: [string, string][];
  error: boolean;
  soft: boolean;
}

export interface Layout {
  nodes: Node[];
  edges: Edge<WireData>[];
  tier: Tier;
  visibleSeeds: Set<string>;
  visibleImpacted: Set<string>;
  counts: { units: number; groups: number; impacted: number };
}

type Anchor = { node: string; handle: string; y: number };

export function hideKey(kind: "unit" | "file" | "group", id: string): string {
  return `${kind}:${id}`;
}

/** Header + source of the same symbol (foo.h / foo.c) share one row. */
function collapseSymbols(entities: ImpactNode[]): ImpactNode[][] {
  const groups: ImpactNode[][] = [];
  const index = new Map<string, number>();
  for (const e of entities) {
    const key = `${e.qualname}\0${e.change ?? ""}`;
    const at = index.get(key);
    if (at == null) {
      index.set(key, groups.length);
      groups.push([e]);
    } else groups[at].push(e);
  }
  return groups.map((g) =>
    [...g].sort((a, b) => {
      const score = (n: ImpactNode) => (/\.(h|hh|hpp|hxx)$/i.test(n.path) ? 2 : 0) + (n.impacted ?? 0);
      return score(b) - score(a);
    }),
  );
}

function isRisky(m: Model, n: ImpactNode): boolean {
  return (!!n.change && CONTRACT.has(n.change)) || m.errorsBelow(n.id) > 0 || n.confidence !== "precise";
}

export function computeLayout(m: Model, o: ViewOpts, t: { changed: string; direct: string; indirect: string; files: (n: number) => string; functions: (n: number) => string; groups: (n: number) => string }): Layout {
  const S = SIZE;
  const scope = o.focusIds ? m.scopeOf(o.focusIds) : null;

  // ---------------------------------------------------------------- visibility
  const unitsVisible: { u: ChangedUnit; entities: ImpactNode[] }[] = [];
  const visibleSeeds = new Set<string>();
  for (const u of m.units.values()) {
    if (o.hidden.has(hideKey("unit", u.id)) || o.hidden.has(hideKey("group", `changed:${u.group}`))) continue;
    if (o.hideTests && u.test) continue;
    const entities = u.entities.filter((e) => {
      if (scope && !scope.has(e.id)) return false;
      if (o.hideMinor && e.change && MINOR.has(e.change)) return false;
      if (o.riskyOnly && !isRisky(m, e)) return false;
      return true;
    });
    if (!entities.length) {
      if (u.entities.length || scope) continue;
      unitsVisible.push({ u, entities: [] });
      continue;
    }
    unitsVisible.push({ u, entities });
    for (const e of entities) visibleSeeds.add(e.id);
  }
  const visibleImpacted = new Set<string>();
  for (const n of m.rep.nodes) {
    if (n.change) continue;
    if (scope ? !scope.has(n.id) : !n.seeds.some((s) => visibleSeeds.has(s))) continue;
    if (o.maxDepth && n.depth > o.maxDepth) continue;
    if (o.hideTests && m.isTest(n.path)) continue;
    const gid = m.groupOfPath(n.path);
    if (o.hidden.has(hideKey("file", `${gid}|${n.path}`)) || o.hidden.has(hideKey("group", `impact:${gid}`))) continue;
    visibleImpacted.add(n.id);
  }

  let tier: Tier;
  if (o.detail !== "auto") tier = o.detail;
  else if (scope) tier = visibleImpacted.size > 300 ? "files" : "full";
  else if (unitsVisible.length <= 30 && visibleImpacted.size <= 90) tier = "full";
  else if (unitsVisible.length <= 80 && visibleImpacted.size <= 300) tier = "files";
  else tier = "groups";

  const nodes: Node[] = [];
  const anchorsSrc = new Map<string, Anchor>();
  const anchorsTgt = new Map<string, Anchor>();
  const pos = (id: string, x: number, y: number) => (!scope && o.positions[id]) || { x, y };

  // ---------------------------------------------------------------- changed column
  const X0 = 0;
  const gw0 = S.CW + 2 * S.GP;
  const byGroup = new Map<string, typeof unitsVisible>();
  for (const v of unitsVisible) (byGroup.get(v.u.group) ?? byGroup.set(v.u.group, []).get(v.u.group)!).push(v);
  const groupRisk = (list: typeof unitsVisible) => list.reduce((a, v) => a + m.riskOfUnit(v.u), 0);
  const changedGroups = [...byGroup.entries()].sort((a, b) => groupRisk(b[1]) - groupRisk(a[1]) || a[0].localeCompare(b[0]));
  const colCount = (n: number, unit: "files" | "functions" | "groups") => (unit === "files" ? t.files(n) : unit === "groups" ? t.groups(n) : t.functions(n));
  nodes.push(colHead("col:0", X0, t.changed, tier === "groups" ? colCount(changedGroups.length, "groups") : colCount(unitsVisible.reduce((a, v) => a + v.u.files.length, 0), "files"), "changed"));

  let y = 0;
  for (const [gid, list] of changedGroups) {
    list.sort((a, b) => m.riskOfUnit(b.u) - m.riskOfUnit(a.u) || a.u.name.localeCompare(b.u.name));
    const g = m.groups.get(gid);
    const ids = list.flatMap((v) => v.entities.map((e) => e.id));
    const gkey = `changed:${gid}`;
    const collapsed = tier === "groups" && !o.expandedGroups.has(gkey);
    if (collapsed) {
      const contract = list.reduce((a, v) => a + v.entities.filter((e) => e.change && CONTRACT.has(e.change)).length, 0);
      const entities = list.reduce((a, v) => a + v.entities.length, 0);
      const reached = new Set(ids.flatMap((id) => [...m.down([id])].filter((d) => !m.byId.get(d)?.change)));
      const errors = [...reached].filter((d) => m.byId.get(d)!.breaking.length).length;
      const callers = reached.size;
      const files = list.reduce((a, v) => a + v.u.files.length, 0);
      const nid = `gr:${gkey}`;
      const p = pos(nid, X0, y);
      nodes.push({
        id: nid, type: "groupRow", position: p, width: gw0, height: S.GROUPROW_H,
        data: {
          side: "changed", gid, key: hideKey("group", gkey), module: g?.module ?? "", pkg: g?.pkg ?? "", count: t.files(list.reduce((a, v) => a + v.u.files.length, 0)),
          ids, collapsed: true, stats: { files, entities, contract, callers, errors, max: 0 }, col: 0,
          sources: [{ id: "s:card", y: S.GROUPROW_H / 2 }], targets: [],
        } satisfies GroupData,
      });
      for (const id of ids) anchorsSrc.set(id, { node: nid, handle: "s:card", y: p.y + S.GROUPROW_H / 2 });
      y += S.GROUPROW_H + S.CARD_GAP;
      continue;
    }
    const gnid = `g:${gkey}`;
    const children: Node[] = [];
    let cy = S.GROUP_HEAD;
    const gp = pos(gnid, X0, y);
    for (const { u, entities } of list) {
      const cid = `u:${u.id}`;
      const cap = o.expandedCards.has(cid) ? Infinity : tier === "full" ? S.MAX_ROWS_CHANGED : S.MAX_ROWS_MEDIUM;
      const grouped = collapseSymbols(entities);
      const shownGroups = grouped.slice(0, cap);
      const more = grouped.length - shownGroups.length;
      const shown = shownGroups.map((g) => g[0]);
      const rows: RowData[] = shown.map((e) => ({
        id: e.id, name: e.qualname, change: e.change, tone: e.change && CONTRACT.has(e.change) ? "contract" : e.change === "body" ? "body" : "minor",
        count: e.impacted ?? 0, errorsBelow: m.errorsBelow(e.id), confidence: e.confidence,
      }));
      const h = S.CARD_HEAD + (rows.length + (more ? 1 : 0)) * S.ROW_H + S.CARD_PAD;
      const cp = pos(cid, S.GP, cy);
      const sources: HandleSpec[] = rows.map((r, i) => ({ id: `s:${r.id}`, y: S.CARD_HEAD + i * S.ROW_H + S.ROW_H / 2 }));
      sources.push({ id: "s:card", y: S.CARD_HEAD / 2 });
      const ownIds = entities.map((e) => e.id);
      const total = new Set(ownIds.flatMap((id) => [...m.down([id])].filter((d) => !m.byId.get(d)?.change))).size;
      children.push({
        id: cid, type: "card", parentId: gnid, position: cp, width: S.CW, height: h,
        data: {
          kind: "changed", key: hideKey("unit", u.id), title: u.name, path: u.files[0].path, group: gid, lang: u.files[0].language,
          parts: u.parts, rows, more, ids: ownIds, count: total, errors: ownIds.reduce((a, id) => a + m.errorsBelow(id), 0), test: u.test, col: 0,
          sources, targets: [],
        } satisfies CardData,
      });
      shownGroups.forEach((g, i) => {
        const y = gp.y + cp.y + S.CARD_HEAD + i * S.ROW_H + S.ROW_H / 2;
        for (const e of g) anchorsSrc.set(e.id, { node: cid, handle: `s:${g[0].id}`, y });
      });
      for (const g of grouped.slice(shownGroups.length)) {
        for (const e of g) anchorsSrc.set(e.id, { node: cid, handle: "s:card", y: gp.y + cp.y + S.CARD_HEAD / 2 });
      }
      cy += h + S.CARD_GAP;
    }
    const gh = cy - S.CARD_GAP + S.GP;
    nodes.push({
      id: gnid, type: "box", position: gp, width: gw0, height: gh, zIndex: 0,
      data: {
        side: "changed", gid, key: hideKey("group", gkey), module: g?.module ?? "", pkg: g?.pkg ?? "", count: t.files(list.reduce((a, v) => a + v.u.files.length, 0)),
        ids, collapsed: false, collapsible: tier === "groups", col: 0, sources: [], targets: [],
      } satisfies GroupData,
    });
    nodes.push(...children);
    y += gh + S.GROUP_GAP;
  }

  // ---------------------------------------------------------------- impact columns
  const columns: ImpactNode[][] = [[], []];
  for (const id of visibleImpacted) {
    const n = m.byId.get(id)!;
    columns[n.depth <= 1 ? 0 : 1].push(n);
  }
  const gwI = S.IW + 2 * S.GP;
  let x = X0 + gw0 + S.COL_GAP;
  columns.forEach((items, ci) => {
    if (!items.length) return;
    const col = ci + 1;
    nodes.push(colHead(`col:${col}`, x, col === 1 ? t.direct : t.indirect, t.functions(items.length), col === 1 ? "direct" : "indirect"));
    const bary = (n: ImpactNode) => {
      const ys = (m.inc.get(n.id) ?? []).map((e) => anchorsSrc.get(e.source)?.y).filter((v): v is number => v != null);
      return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : Number.MAX_SAFE_INTEGER;
    };
    const groups = new Map<string, Map<string, ImpactNode[]>>();
    for (const n of items) {
      const gid = m.groupOfPath(n.path);
      const files = groups.get(gid) ?? groups.set(gid, new Map()).get(gid)!;
      (files.get(n.path) ?? files.set(n.path, []).get(n.path)!).push(n);
    }
    const avg = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);
    const fileBary = new Map<string, number>();
    for (const [gid, files] of groups) for (const [p, list] of files) fileBary.set(`${gid}|${p}`, Math.min(...list.map(bary)));
    const orderedGroups = [...groups.entries()]
      .map(([gid, files]) => ({ gid, files, b: avg([...files.keys()].map((p) => fileBary.get(`${gid}|${p}`)!)) }))
      .sort((a, b) => a.b - b.b || a.gid.localeCompare(b.gid));
    let yy = 0;
    for (const { gid, files } of orderedGroups) {
      const g = m.groups.get(gid);
      const gkey = `impact${col}:${gid}`;
      const all = [...files.values()].flat();
      const ids = all.map((n) => n.id);
      const collapsed = tier === "groups" && !o.expandedGroups.has(gkey);
      if (collapsed) {
        const nid = `gr:${gkey}`;
        const p = pos(nid, x, yy);
        nodes.push({
          id: nid, type: "groupRow", position: p, width: gwI, height: S.GROUPROW_H,
          data: {
            side: "impact", gid, key: hideKey("group", `impact:${gid}`), module: g?.module ?? "", pkg: g?.pkg ?? "", count: t.functions(all.length), ids, collapsed: true,
            stats: { files: files.size, entities: all.length, contract: 0, callers: all.length, errors: all.filter((n) => n.breaking.length).length, max: items.length }, col,
            sources: [{ id: "s:card", y: S.GROUPROW_H / 2 }], targets: [{ id: "t:card", y: S.GROUPROW_H / 2 }],
          } satisfies GroupData,
        });
        for (const id of ids) {
          anchorsSrc.set(id, { node: nid, handle: "s:card", y: p.y + S.GROUPROW_H / 2 });
          anchorsTgt.set(id, { node: nid, handle: "t:card", y: p.y + S.GROUPROW_H / 2 });
        }
        yy += S.GROUPROW_H + S.CARD_GAP;
        continue;
      }
      const gnid = `g:${gkey}`;
      const gp = pos(gnid, x, yy);
      const children: Node[] = [];
      let cy = S.GROUP_HEAD;
      const orderedFiles = [...files.entries()].sort((a, b) => fileBary.get(`${gid}|${a[0]}`)! - fileBary.get(`${gid}|${b[0]}`)! || a[0].localeCompare(b[0]));
      for (const [path, list] of orderedFiles) {
        list.sort((a, b) => bary(a) - bary(b) || b.score - a.score);
        const cid = `f:${col}:${gid}|${path}`;
        const compact = tier !== "full";
        const cap = o.expandedCards.has(cid) ? Infinity : S.MAX_ROWS_IMPACT;
        const shown = compact ? [] : list.slice(0, cap);
        const more = compact ? 0 : list.length - shown.length;
        const rows: RowData[] = shown.map((n) => ({
          id: n.id, name: n.qualname, via: n.via, depth: n.depth, error: n.breaking[0]?.message,
          uncertain: n.confidence !== "precise", confidence: n.confidence,
        }));
        const sources: HandleSpec[] = [{ id: "s:card", y: S.CARD_HEAD / 2 }];
        const targets: HandleSpec[] = [{ id: "t:card", y: S.CARD_HEAD / 2 }];
        let ry = S.CARD_HEAD;
        const cp = pos(cid, S.GP, cy);
        rows.forEach((r) => {
          const mid = ry + S.ROW_H / 2;
          sources.push({ id: `s:${r.id}`, y: mid });
          targets.push({ id: `t:${r.id}`, y: mid });
          anchorsSrc.set(r.id, { node: cid, handle: `s:${r.id}`, y: gp.y + cp.y + mid });
          anchorsTgt.set(r.id, { node: cid, handle: `t:${r.id}`, y: gp.y + cp.y + mid });
          ry += S.ROW_H + (r.error ? S.ERR_H : 0);
        });
        for (const n of list.slice(shown.length)) {
          anchorsSrc.set(n.id, { node: cid, handle: "s:card", y: gp.y + cp.y + S.CARD_HEAD / 2 });
          anchorsTgt.set(n.id, { node: cid, handle: "t:card", y: gp.y + cp.y + S.CARD_HEAD / 2 });
        }
        const h = compact ? S.COMPACT_H : ry + (more ? S.ROW_H : 0) + S.CARD_PAD;
        children.push({
          id: cid, type: "card", parentId: gnid, position: cp, width: S.IW, height: h,
          data: {
            kind: "impact", key: hideKey("file", `${gid}|${path}`), title: basename(path), path, group: gid, lang: null, parts: [],
            rows: compact ? null : rows, more, ids: list.map((n) => n.id), count: list.length,
            errors: list.filter((n) => n.breaking.length).length, test: m.isTest(path), col, sources, targets,
          } satisfies CardData,
        });
        cy += h + S.CARD_GAP;
      }
      const gh = cy - S.CARD_GAP + S.GP;
      nodes.push({
        id: gnid, type: "box", position: gp, width: gwI, height: gh, zIndex: 0,
        data: {
          side: "impact", gid, key: hideKey("group", `impact:${gid}`), module: g?.module ?? "", pkg: g?.pkg ?? "", count: t.functions(all.length),
          ids, collapsed: false, collapsible: tier === "groups", col, sources: [], targets: [],
        } satisfies GroupData,
      });
      nodes.push(...children);
      yy += gh + S.GROUP_GAP;
    }
    x += gwI + S.COL_GAP;
  });

  // ---------------------------------------------------------------- edges (rendered only for the active scope)
  const colByNode = new Map<string, number>();
  for (const n of nodes) colByNode.set(n.id, (n.data as { col?: number }).col ?? -1);
  const colOf = (id: string) => colByNode.get(id) ?? -1;
  const edges = new Map<string, Edge<WireData>>();
  for (const e of m.rep.edges) {
    const target = m.byId.get(e.target);
    if (!target || target.change) continue;
    if (!visibleImpacted.has(e.target) || !(visibleSeeds.has(e.source) || visibleImpacted.has(e.source))) continue;
    const a = anchorsSrc.get(e.source);
    const b = anchorsTgt.get(e.target);
    if (!a || !b || a.node === b.node) continue;
    // depth >= 2 shares one column: loop out to the right instead of crossing the column
    const tHandle = colOf(a.node) === colOf(b.node) ? `r${b.handle}` : b.handle;
    const id = `${a.node}/${a.handle}>${b.node}/${tHandle}`;
    const soft = e.kind === "dispatch" || e.kind === "implements" || target.confidence !== "precise";
    const ex = edges.get(id);
    if (ex) {
      ex.data!.pairs.push([e.source, e.target]);
      ex.data!.error ||= target.breaking.length > 0;
      ex.data!.soft &&= soft;
      continue;
    }
    edges.set(id, {
      id, source: a.node, sourceHandle: a.handle, target: b.node, targetHandle: tHandle, type: "wire",
      data: { pairs: [[e.source, e.target]], error: target.breaking.length > 0, soft },
      selectable: false, focusable: false, zIndex: 1000,
    });
  }

  return {
    nodes, edges: [...edges.values()], tier, visibleSeeds, visibleImpacted,
    counts: { units: unitsVisible.length, groups: changedGroups.length, impacted: visibleImpacted.size },
  };
}

function colHead(id: string, x: number, title: string, count: string, tone: ColData["tone"]): Node {
  return {
    id, type: "colHead", position: { x, y: -52 }, draggable: false, selectable: false,
    width: 300, height: 30, data: { title, count, tone } satisfies ColData,
  };
}
