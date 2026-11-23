import { useEffect, useMemo, useRef, useState } from "react";
import type { Focus } from "../types";
import type { Model } from "../model";
import {
  buildTree,
  focusForPath,
  heatMapForModel,
  type TreeEntry,
} from "../heat";
import {
  contextView,
  labelFits,
  layoutTreemap,
  peerLinks,
  toMapTree,
  type MapLink,
  type MapRect,
} from "../treemapLayout";
import { fmt, useT } from "../i18n";
import { Icon } from "../view";

interface Props {
  model: Model;
  focus: Focus;
  leftInset: number;
  onFocus: (f: Focus) => void;
  onOpenChain: (f: Focus) => void;
  onHover: (ids: string[] | null, extent?: "file" | "chain") => void;
}

function focusPath(m: Model, focus: Focus): string | null {
  if (!focus) return null;
  if (focus.type === "entity") return m.byId.get(focus.id)?.path ?? null;
  if (focus.type === "unit") return m.units.get(focus.id)?.files[0]?.path ?? null;
  if (focus.type === "impactFile") return focus.path;
  if (focus.type === "group") {
    const g = m.groups.get(focus.id);
    if (!g) return null;
    const hit = m.rep.nodes.find((n) => m.groupOfPath(n.path) === focus.id);
    return hit?.path ?? null;
  }
  return null;
}

function rectActive(r: MapRect, active: string | null, focus: Focus): boolean {
  if (!focus || focus.type === "overview") return false;
  if (focus.type === "group") return r.groupId === focus.id;
  if (!active) return false;
  return active === r.path || active.startsWith(r.path ? r.path + "/" : "");
}

export default function MapView(p: Props) {
  const t = useT();
  const wrap = useRef<HTMLDivElement>(null);
  const [entries, setEntries] = useState<TreeEntry[] | null>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [hoverPath, setHoverPath] = useState<string | null>(null);
  const [scope, setScope] = useState("");
  const [menu, setMenu] = useState<{ path: string; x: number; y: number; focus: Focus } | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/tree")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: { entries: TreeEntry[] }) => {
        if (!cancelled) setEntries(data.entries);
      })
      .catch(() => !cancelled && setEntries([]));
    return () => {
      cancelled = true;
    };
  }, [p.model.key]);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (cr) setSize({ w: Math.max(120, cr.width - 24), h: Math.max(120, cr.height - 24) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const files = useMemo(() => {
    const paths = new Set((entries ?? []).filter((e) => e.kind === "file").map((e) => e.path));
    for (const f of p.model.rep.files) paths.add(f.path);
    for (const n of p.model.rep.nodes) paths.add(n.path);
    return [...paths];
  }, [entries, p.model]);
  const heat = useMemo(() => heatMapForModel(p.model, files), [p.model, files]);
  const mapRoot = useMemo(() => {
    if (!entries) return null;
    const known = new Set(entries.map((e) => e.path));
    const extra: TreeEntry[] = files.filter((path) => !known.has(path)).map((path) => ({ path, kind: "file" }));
    return toMapTree(buildTree([...entries, ...extra], heat), (path) => p.model.groupOfPath(path));
  }, [entries, files, heat, p.model]);

  const level = useMemo(() => (mapRoot ? contextView(mapRoot, scope) : null), [mapRoot, scope]);

  const rects = useMemo(() => {
    if (!level) return [] as MapRect[];
    return layoutTreemap(level, size.w, Math.max(80, size.h - 36));
  }, [level, size]);

  const byPath = useMemo(() => new Map(rects.map((r) => [r.path, r])), [rects]);
  const active = focusPath(p.model, p.focus);
  const focusKey = active && byPath.has(active) ? active : null;

  useEffect(() => {
    if (!active) return;
    if (!p.focus || p.focus.type === "group" || p.focus.type === "overview") return;
    const parent = active.includes("/") ? active.slice(0, active.lastIndexOf("/")) : "";
    setScope(parent);
  }, [p.focus, active]);

  const visibleLinks: MapLink[] = useMemo(() => {
    const key = hoverPath ?? focusKey;
    if (!key) return [];
    return peerLinks(p.model, rects, key, 6);
  }, [p.model, rects, hoverPath, focusKey]);
  const linked = useMemo(() => new Set(visibleLinks.flatMap((l) => [l.sourcePath, l.targetPath])), [visibleLinks]);

  const clickRect = (r: MapRect) => {
    setMenu(null);
    if (r.drill) {
      setScope(r.path);
      setHoverPath(null);
    }
    const f =
      focusForPath(p.model, r.path) ??
      (r.kind === "dir" ? ({ type: "group", id: r.groupId, side: "impact" } as Focus) : null);
    if (f) p.onFocus(f);
  };

  const openChain = (r: MapRect, clientX: number, clientY: number) => {
    const f =
      focusForPath(p.model, r.path) ??
      ({ type: "group", id: r.groupId, side: "impact" } as Focus);
    if (!f) return;
    const box = wrap.current?.getBoundingClientRect();
    setMenu({
      path: r.path,
      x: clientX - (box?.left ?? 0),
      y: clientY - (box?.top ?? 0),
      focus: f,
    });
  };

  if (!entries) {
    return (
      <div className="map-board" style={{ marginLeft: p.leftInset }} ref={wrap}>
        <div className="map-empty">
          <div className="spinner" /> {t.loading}
        </div>
      </div>
    );
  }

  if (!rects.length) {
    return (
      <div className="map-board" style={{ marginLeft: p.leftInset }} ref={wrap}>
        <div className="map-empty">{t.noChanges}</div>
      </div>
    );
  }

  // draw parents under children: sort by depth ascending so leaves paint on top
  const ordered = [...rects].sort((a, b) => a.depth - b.depth);

  return (
    <div
      className="map-board"
      style={{ marginLeft: p.leftInset }}
      ref={wrap}
      onClick={() => setMenu(null)}
    >
      <div className="map-crumbs">
        <button type="button" className={!scope ? "on" : ""} onClick={() => setScope("")}>
          {t.mapAll}
        </button>
        {scope.split("/").filter(Boolean).map((name, i, arr) => {
          const path = arr.slice(0, i + 1).join("/");
          return (
            <button key={path} type="button" className={path === scope ? "on" : ""} onClick={() => setScope(path)}>
              {name}
            </button>
          );
        })}
      </div>
      <div className="map-canvas" style={{ width: size.w, height: Math.max(80, size.h - 36) }}>
        {ordered.map((r) => {
          const on = rectActive(r, active, p.focus);
          const hoverRoot = hoverPath ?? focusKey;
          const inHover =
            !!hoverRoot && (r.path === hoverRoot || r.path.startsWith(`${hoverRoot}/`) || hoverRoot.startsWith(`${r.path}/`));
          const dim = !!hoverRoot && !on && !inHover && !linked.has(r.path);
          const showLabel = labelFits(r);
          return (
            <button
              key={`${r.kind}:${r.path || "."}`}
              type="button"
              className={`map-cell heat-${r.heat} ${r.kind} depth-${Math.min(r.depth, 4)} ${r.open ? "open" : ""} ${on ? "active" : ""} ${linked.has(r.path) ? "linked" : ""} ${dim ? "dim" : ""}`}
              style={{ left: r.x, top: r.y, width: r.w, height: r.h }}
              title={`${r.path || "."} · ${fmt(t.files, { n: r.weight })}`}
              onMouseEnter={() => {
                setHoverPath(r.path);
                const ids = p.model.rep.nodes.filter((n) => n.path === r.path || (r.path && n.path.startsWith(r.path + "/"))).map((n) => n.id);
                p.onHover(ids.length ? ids : null, "file");
              }}
              onMouseLeave={() => {
                setHoverPath(null);
                p.onHover(null);
              }}
              onClick={(e) => {
                e.stopPropagation();
                clickRect(r);
              }}
              onDoubleClick={(e) => {
                e.stopPropagation();
                e.preventDefault();
                const f =
                  focusForPath(p.model, r.path) ??
                  ({ type: "group", id: r.groupId, side: "impact" } as Focus);
                if (f) p.onOpenChain(f);
              }}
              onContextMenu={(e) => {
                e.preventDefault();
                e.stopPropagation();
                openChain(r, e.clientX, e.clientY);
              }}
            >
              {showLabel && (
                <span className="map-label">
                  <span className="map-name ellipsis">{r.name}</span>
                  {r.w > 72 && r.kind === "dir" && (
                    <span className="map-count">
                      {r.weight}
                      {r.drill ? ` · ${t.mapEnter}` : ""}
                    </span>
                  )}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {menu && (
        <div className="map-menu" style={{ left: menu.x, top: menu.y }} onClick={(e) => e.stopPropagation()}>
          <button
            type="button"
            className="map-menu-item"
            onClick={() => {
              p.onOpenChain(menu.focus);
              setMenu(null);
            }}
          >
            <Icon name="chevron" size={12} /> {t.openChain}
          </button>
        </div>
      )}
    </div>
  );
}
