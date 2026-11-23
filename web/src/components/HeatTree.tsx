import { useEffect, useMemo, useRef, useState } from "react";
import type { Focus } from "../types";
import type { Model } from "../model";
import { buildTree, defaultExpanded, entityIdsForPath, focusForPath, heatMapForModel, type Heat, type TreeEntry, type TreeNode } from "../heat";
import { useT } from "../i18n";
import { Icon } from "../view";

interface Props {
  model: Model;
  focus: Focus;
  activePath: string | null;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onFocus: (f: Focus) => void;
  onHover: (ids: string[] | null, extent?: "file" | "chain") => void;
}

export default function HeatTree(p: Props) {
  const t = useT();
  const [entries, setEntries] = useState<TreeEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const seeded = useRef(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/tree")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: { entries: TreeEntry[] }) => {
        if (cancelled) return;
        setEntries(data.entries);
        setError(null);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [p.model.key]);

  const files = useMemo(() => (entries ?? []).filter((e) => e.kind === "file").map((e) => e.path), [entries]);
  const heat = useMemo(() => heatMapForModel(p.model, files), [p.model, files]);
  const root = useMemo(() => (entries ? buildTree(entries, heat) : null), [entries, heat]);

  useEffect(() => {
    if (!root || seeded.current) return;
    seeded.current = true;
    setExpanded(defaultExpanded(root));
  }, [root]);

  useEffect(() => {
    if (!p.activePath || !listRef.current) return;
    const el = [...listRef.current.querySelectorAll<HTMLElement>("[data-path]")].find((n) => n.dataset.path === p.activePath);
    el?.scrollIntoView({ block: "nearest" });
  }, [p.activePath]);

  const toggle = (path: string) =>
    setExpanded((prev) => {
      const n = new Set(prev);
      if (n.has(path)) n.delete(path);
      else n.add(path);
      return n;
    });

  const expandImpacted = () => root && setExpanded(defaultExpanded(root));
  const collapseAll = () => setExpanded(new Set());

  if (p.collapsed) {
    return (
      <aside className="heat-tree collapsed">
        <button className="heat-tree-expand" title={t.treeShow} onClick={p.onToggleCollapsed}>
          <Icon name="chevron" size={14} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="heat-tree">
      <div className="heat-tree-head">
        <span className="heat-tree-title">{t.heatTree}</span>
        <span className="spacer" />
        <button className="btn-link" onClick={expandImpacted} title={t.treeExpandImpact}>
          {t.treeExpandImpact}
        </button>
        <button className="btn-link" onClick={collapseAll} title={t.treeCollapse}>
          {t.treeCollapse}
        </button>
        <button className="icon-btn sm" title={t.treeHide} onClick={p.onToggleCollapsed}>
          <Icon name="chevron" size={14} />
        </button>
      </div>
      <div className="heat-legend">
        <i className="heat-dot heat-changed" /> {t.heatChanged}
        <i className="heat-dot heat-error" /> {t.heatError}
        <i className="heat-dot heat-direct" /> {t.heatDirect}
        <i className="heat-dot heat-indirect" /> {t.heatIndirect}
      </div>
      <div className="heat-tree-body" ref={listRef}>
        {!root && !error && (
          <div className="muted pad">
            <span className="spinner" /> {t.loading}
          </div>
        )}
        {error && <div className="muted pad">{error}</div>}
        {root &&
          root.children.map((c) => (
            <Row
              key={c.path}
              node={c}
              depth={0}
              expanded={expanded}
              activePath={p.activePath}
              onToggle={toggle}
              onFocus={p.onFocus}
              onHover={p.onHover}
              model={p.model}
            />
          ))}
      </div>
    </aside>
  );
}

function Row({
  node,
  depth,
  expanded,
  activePath,
  onToggle,
  onFocus,
  onHover,
  model,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  activePath: string | null;
  onToggle: (p: string) => void;
  onFocus: (f: Focus) => void;
  onHover: (ids: string[] | null, extent?: "file" | "chain") => void;
  model: Model;
}) {
  const open = node.kind === "dir" && expanded.has(node.path);
  const active = activePath === node.path;
  const onClick = () => {
    if (node.kind === "dir") {
      onToggle(node.path);
      return;
    }
    const f = focusForPath(model, node.path);
    if (f) onFocus(f);
  };
  return (
    <>
      <div
        className={`heat-row heat-${node.heat} ${active ? "active" : ""} ${node.kind}`}
        style={{ paddingLeft: 8 + depth * 14 }}
        data-path={node.path}
        onClick={onClick}
        onMouseEnter={() => node.kind === "file" && onHover(entityIdsForPath(model, node.path), "file")}
        onMouseLeave={() => onHover(null)}
        title={node.path}
      >
        <span className={`heat-bar heat-${node.heat}`} />
        {node.kind === "dir" ? (
          <span className={`heat-chev ${open ? "open" : ""}`}>
            <Icon name="chevron" size={11} />
          </span>
        ) : (
          <span className="heat-file-gap" />
        )}
        <span className="heat-name ellipsis">{node.name || "/"}</span>
        {node.heat !== "none" && <span className={`heat-dot heat-${node.heat}`} />}
      </div>
      {open &&
        node.children.map((c) => (
          <Row
            key={c.path}
            node={c}
            depth={depth + 1}
            expanded={expanded}
            activePath={activePath}
            onToggle={onToggle}
            onFocus={onFocus}
            onHover={onHover}
            model={model}
          />
        ))}
    </>
  );
}

export function pathOfFocus(m: Model, focus: Focus): string | null {
  if (!focus || focus.type === "overview") return null;
  if (focus.type === "impactFile") return focus.path;
  if (focus.type === "entity") return m.byId.get(focus.id)?.path ?? null;
  if (focus.type === "unit") return m.units.get(focus.id)?.files[0]?.path ?? null;
  if (focus.type === "group") {
    const u = [...m.units.values()].find((x) => x.group === focus.id);
    return u?.files[0]?.path ?? null;
  }
  return null;
}

export type { Heat };
