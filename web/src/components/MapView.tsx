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
  crossGroupLinks,
  labelFits,
  layoutTreemap,
  pruneHeated,
  toMapTree,
  type MapLink,
  type MapRect,
} from "../treemapLayout";
import { useT } from "../i18n";
import { Icon } from "../view";

interface Props {
  model: Model;
  focus: Focus;
  leftInset: number;
  onFocus: (f: Focus) => void;
  onOpenChain: (f: Focus) => void;
  onHover: (ids: string[] | null) => void;
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

function center(r: MapRect): { x: number; y: number } {
  return { x: r.x + r.w / 2, y: r.y + r.h / 2 };
}

export default function MapView(p: Props) {
  const t = useT();
  const wrap = useRef<HTMLDivElement>(null);
  const [entries, setEntries] = useState<TreeEntry[] | null>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [hoverPath, setHoverPath] = useState<string | null>(null);
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

  const files = useMemo(() => (entries ?? []).filter((e) => e.kind === "file").map((e) => e.path), [entries]);
  const heat = useMemo(() => heatMapForModel(p.model, files), [p.model, files]);
  const mapRoot = useMemo(() => {
    if (!entries) return null;
    const tree = pruneHeated(buildTree(entries, heat));
    return toMapTree(tree, (path) => p.model.groupOfPath(path));
  }, [entries, heat, p.model]);

  const rects = useMemo(() => {
    if (!mapRoot) return [] as MapRect[];
    return layoutTreemap(mapRoot, size.w, size.h);
  }, [mapRoot, size]);

  const links = useMemo(() => (rects.length ? crossGroupLinks(p.model, rects) : []), [p.model, rects]);
  const byPath = useMemo(() => new Map(rects.map((r) => [r.path, r])), [rects]);
  const active = focusPath(p.model, p.focus);

  const visibleLinks: MapLink[] = useMemo(() => {
    const key = hoverPath ?? (active && byPath.has(active) ? active : null);
    if (!key) return [];
    return links.filter((l) => l.sourcePath === key || l.targetPath === key).slice(0, 12);
  }, [links, hoverPath, active, byPath]);

  const clickRect = (r: MapRect) => {
    setMenu(null);
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
      <div className="map-canvas" style={{ width: size.w, height: size.h }}>
        {ordered.map((r) => {
          const on = rectActive(r, active, p.focus);
          const dim = !!(hoverPath || active) && !on && hoverPath !== r.path && !(active && (active === r.path || active.startsWith(r.path + "/")));
          const showLabel = labelFits(r);
          return (
            <button
              key={`${r.kind}:${r.path || "."}`}
              type="button"
              className={`map-cell heat-${r.heat} ${r.kind} depth-${Math.min(r.depth, 4)} ${on ? "active" : ""} ${dim ? "dim" : ""}`}
              style={{ left: r.x, top: r.y, width: r.w, height: r.h }}
              title={`${r.path || "."} · ${r.weight} file${r.weight === 1 ? "" : "s"}`}
              onMouseEnter={() => {
                setHoverPath(r.path);
                const ids = p.model.rep.nodes.filter((n) => n.path === r.path || (r.path && n.path.startsWith(r.path + "/"))).map((n) => n.id);
                p.onHover(ids.length ? ids : null);
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
                  {r.w > 72 && r.kind === "dir" && <span className="map-count">{r.weight}</span>}
                </span>
              )}
            </button>
          );
        })}
        <svg className="map-wires" width={size.w} height={size.h}>
          {visibleLinks.map((l) => {
            const a = byPath.get(l.sourcePath);
            const b = byPath.get(l.targetPath);
            if (!a || !b) return null;
            const ca = center(a);
            const cb = center(b);
            const midX = (ca.x + cb.x) / 2;
            const midY = (ca.y + cb.y) / 2 - 24;
            return (
              <path
                key={l.id}
                className={`map-wire ${l.soft ? "soft" : ""}`}
                d={`M ${ca.x} ${ca.y} Q ${midX} ${midY} ${cb.x} ${cb.y}`}
              />
            );
          })}
        </svg>
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
