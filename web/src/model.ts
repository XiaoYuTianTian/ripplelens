import type { ChangeClass, EdgeKind, FileEntry, Focus, GroupEntry, Impact, ImpactEdge, ImpactNode } from "./types";

export const CONTRACT: ReadonlySet<ChangeClass> = new Set(["signature", "removed", "renamed", "value", "type"]);
export const MINOR: ReadonlySet<ChangeClass> = new Set(["cosmetic", "added"]);

export type ChangeTone = "contract" | "body" | "minor";
export function toneOf(c: ChangeClass | null): ChangeTone {
  if (!c) return "minor";
  return CONTRACT.has(c) ? "contract" : c === "body" ? "body" : "minor";
}

export interface Group {
  id: string;
  module: string;
  pkg: string;
  language: string | null;
}

export interface ChangedUnit {
  id: string;
  name: string;
  group: string;
  files: FileEntry[];
  parts: string[]; // file extensions when a unit spans several files (x.component.ts/html/scss)
  entities: ImpactNode[];
  test: boolean;
}

export interface Model {
  rep: Impact;
  byId: Map<string, ImpactNode>;
  out: Map<string, ImpactEdge[]>;
  inc: Map<string, ImpactEdge[]>;
  groups: Map<string, Group>;
  units: Map<string, ChangedUnit>;
  unitOfEntity: Map<string, string>;
  groupOfPath: (path: string) => string;
  isTest: (path: string) => boolean;
  down: (ids: Iterable<string>) => Set<string>;
  up: (ids: Iterable<string>) => Set<string>;
  scopeOf: (ids: Iterable<string>) => Set<string>;
  errorsBelow: (id: string) => number;
  riskOfUnit: (u: ChangedUnit) => number;
  key: string;
}

function dirname(p: string): string {
  const i = p.lastIndexOf("/");
  return i >= 0 ? p.slice(0, i) : "";
}

export function basename(p: string): string {
  const i = p.lastIndexOf("/");
  return i >= 0 ? p.slice(i + 1) : p;
}

function extOf(p: string): string {
  const b = basename(p);
  const i = b.lastIndexOf(".");
  return i > 0 ? b.slice(i + 1) : b;
}

/** Older reports have no groups: fall back to one group per directory. */
function fallbackGroups(rep: Impact): { groups: GroupEntry[]; locate: NonNullable<Impact["locate"]> } {
  const paths = new Set([...rep.files.map((f) => f.path), ...rep.nodes.map((n) => n.path)]);
  const groups = new Map<string, GroupEntry>();
  const locate: NonNullable<Impact["locate"]> = {};
  for (const p of paths) {
    const d = dirname(p);
    const id = `|${d}`;
    if (!groups.has(id)) groups.set(id, { id, module: "", module_path: "", package: d || ".", language: null, units: [] });
    groups.get(id)!.units.push({ id: p, name: basename(p), files: [p], test: false });
    locate[p] = { group: id, unit: p, test: false };
  }
  return { groups: [...groups.values()], locate };
}

export function buildModel(rep: Impact): Model {
  const byId = new Map(rep.nodes.map((n) => [n.id, n]));
  const out = new Map<string, ImpactEdge[]>();
  const inc = new Map<string, ImpactEdge[]>();
  for (const e of rep.edges) {
    if (!byId.has(e.source) || !byId.has(e.target)) continue;
    (out.get(e.source) ?? out.set(e.source, []).get(e.source)!).push(e);
    (inc.get(e.target) ?? inc.set(e.target, []).get(e.target)!).push(e);
  }
  const { groups: gEntries, locate } = rep.groups && rep.locate ? { groups: rep.groups, locate: rep.locate } : fallbackGroups(rep);
  const groups = new Map<string, Group>();
  for (const g of gEntries) groups.set(g.id, { id: g.id, module: g.module, pkg: g.package, language: g.language });
  const groupOfPath = (p: string) => locate[p]?.group ?? `|${dirname(p)}`;
  if (!groups.has("|")) groups.set("|", { id: "|", module: "", pkg: ".", language: null });
  const isTest = (p: string) => !!locate[p]?.test;

  const unitName = new Map<string, string>();
  for (const g of gEntries) for (const u of g.units) unitName.set(u.id, u.name);
  const units = new Map<string, ChangedUnit>();
  const unitOfEntity = new Map<string, string>();
  for (const f of rep.files) {
    const loc = locate[f.path];
    const uid = loc?.unit ?? f.path;
    const gid = loc?.group ?? groupOfPath(f.path);
    if (!groups.has(gid)) groups.set(gid, { id: gid, module: "", pkg: dirname(f.path) || ".", language: f.language });
    let u = units.get(uid);
    if (!u) {
      u = { id: uid, name: unitName.get(uid) ?? basename(f.path), group: gid, files: [], parts: [], entities: [], test: true };
      units.set(uid, u);
    }
    u.files.push(f);
    u.test = u.test && !!loc?.test;
    for (const id of f.entities) {
      const n = byId.get(id);
      if (n && !unitOfEntity.has(id)) {
        u.entities.push(n);
        unitOfEntity.set(id, uid);
      }
    }
  }
  for (const u of units.values()) {
    if (u.files.length > 1) {
      const name = u.name;
      u.parts = u.files.map((f) => {
        const b = basename(f.path);
        return b.startsWith(`${name}.`) ? b.slice(name.length + 1) : extOf(f.path);
      });
    }
    else if (u.name !== basename(u.files[0].path)) u.name = basename(u.files[0].path);
  }

  const walk = (start: Iterable<string>, adj: Map<string, ImpactEdge[]>, key: "source" | "target") => {
    const seen = new Set<string>();
    const q = [...start];
    while (q.length) {
      const id = q.pop()!;
      for (const e of adj.get(id) ?? []) {
        const nxt = e[key];
        if (!seen.has(nxt)) {
          seen.add(nxt);
          q.push(nxt);
        }
      }
    }
    return seen;
  };
  const down = (ids: Iterable<string>) => walk(ids, out, "target");
  const up = (ids: Iterable<string>) => walk(ids, inc, "source");
  const scopeOf = (ids: Iterable<string>) => {
    const list = [...ids];
    return new Set([...list, ...down(list), ...up(list)]);
  };
  const errCache = new Map<string, number>();
  const errorsBelow = (id: string) => {
    if (!errCache.has(id)) {
      let n = 0;
      for (const d of down([id])) n += byId.get(d)?.breaking.length ? 1 : 0;
      errCache.set(id, n);
    }
    return errCache.get(id)!;
  };
  const riskOfUnit = (u: ChangedUnit) => {
    let r = 0;
    for (const e of u.entities) {
      const t = toneOf(e.change);
      r += t === "contract" ? 3 : t === "body" ? 1 : 0.2;
      r += errorsBelow(e.id) * 10 + (e.impacted ?? 0) * 0.25 + (e.confidence === "degraded" ? 2 : 0);
    }
    return r;
  };
  const key = `${rep.repo.root}@${rep.repo.head.sha ?? "worktree"}..${rep.repo.base.sha}`;
  return { rep, byId, out, inc, groups, units, unitOfEntity, groupOfPath, isTest, down, up, scopeOf, errorsBelow, riskOfUnit, key };
}

export function groupLabel(g: Group | undefined): { module: string; pkg: string } {
  if (!g) return { module: "", pkg: "" };
  return { module: g.module, pkg: g.pkg || "." };
}

/** Entity ids the focus stands for. */
export function idsOfFocus(m: Model, f: Focus): string[] {
  if (!f) return [];
  switch (f.type) {
    case "entity":
      return [f.id];
    case "unit":
      return (m.units.get(f.id)?.entities ?? []).map((e) => e.id);
    case "impactFile":
      return m.rep.nodes.filter((n) => !n.change && n.path === f.path).map((n) => n.id);
    case "group":
      return f.side === "changed"
        ? [...m.units.values()].filter((u) => u.group === f.id).flatMap((u) => u.entities.map((e) => e.id))
        : m.rep.nodes.filter((n) => !n.change && m.groupOfPath(n.path) === f.id).map((n) => n.id);
    default:
      return [];
  }
}

export function whyPath(m: Model, id: string): ImpactNode[] {
  return (m.rep.paths[id] ?? [id]).map((x) => m.byId.get(x)).filter((x): x is ImpactNode => !!x);
}

export const EDGE_KINDS: EdgeKind[] = ["call", "ref", "dispatch", "implements"];
