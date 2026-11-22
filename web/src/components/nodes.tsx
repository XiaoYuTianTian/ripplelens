import { memo } from "react";
import { BaseEdge, getBezierPath, Handle, Position, type Edge, type EdgeProps, type Node, type NodeProps } from "@xyflow/react";
import type { CardData, ColData, GroupData, HandleSpec, RowData, WireData } from "../layout";
import { fmt, useT } from "../i18n";
import { Icon, langColor, litState, useView } from "../view";
import type { ChangeClass } from "../types";

function Handles({ sources, targets }: { sources: HandleSpec[]; targets: HandleSpec[] }) {
  return (
    <>
      {targets.map((h) => (
        <Handle key={h.id} id={h.id} type="target" position={Position.Left} isConnectable={false} className="hd" style={{ top: h.y }} />
      ))}
      {targets.map((h) => (
        <Handle key={`r${h.id}`} id={`r${h.id}`} type="target" position={Position.Right} isConnectable={false} className="hd" style={{ top: h.y }} />
      ))}
      {sources.map((h) => (
        <Handle key={h.id} id={h.id} type="source" position={Position.Right} isConnectable={false} className="hd" style={{ top: h.y }} />
      ))}
    </>
  );
}

const stop = (e: React.SyntheticEvent) => e.stopPropagation();

// ---------------------------------------------------------------------------- cards

export const CardNode = memo(function CardNode({ id, data }: NodeProps<Node<CardData>>) {
  const v = useView();
  const t = useT();
  const d = data;
  const unitId = d.kind === "changed" ? id.slice(2) : null;
  const reviewed = unitId ? v.reviewed.has(unitId) : false;
  const mark = d.ids.reduce<"high" | "medium" | "low" | null>((acc, x) => {
    const s = v.marks.get(x);
    if (!s) return acc;
    if (s === "high" || acc === "high") return "high";
    return s === "medium" || acc === "medium" ? "medium" : "low";
  }, null);
  const focusCard = () => {
    if (v.act.dragging()) return;
    v.act.focus(d.kind === "changed" ? { type: "unit", id: unitId! } : { type: "impactFile", group: d.group, path: d.path });
  };
  const cls = [
    "card",
    `card-${d.kind}`,
    d.kind === "impact" ? `col-${d.col}` : "",
    litState(v.lit, d.ids),
    v.selected === id ? "selected" : "",
    reviewed ? "is-reviewed" : "",
    d.errors && d.kind === "impact" ? "has-errors" : "",
    d.rows ? "" : "compact",
  ].join(" ");
  return (
    <div className={cls} onMouseEnter={() => v.act.hover(d.ids)} onMouseLeave={() => v.act.hover(null)} title={d.path}>
      <Handles sources={d.sources} targets={d.targets} />
      <div className="card-head" onClick={focusCard}>
        {d.kind === "changed" && <span className="lang" style={{ background: langColor(d.lang) }} />}
        <span className="file">{d.title}</span>
        {d.parts.length > 0 && (
          <span className="parts">
            {d.parts.map((p) => (
              <i key={p}>{p}</i>
            ))}
          </span>
        )}
        {d.test && <span className="chip-test">{t.test}</span>}
        {mark && <span className={`ai-mark ai-${mark}`}>AI</span>}
        {reviewed && (
          <span className="reviewed-mark">
            <Icon name="check" size={12} />
          </span>
        )}
        <span className="card-meta">
          {d.kind === "changed" ? <>→ {d.count}</> : d.rows ? null : <>{d.count}</>}
          {d.errors > 0 && <span className="err-count">{d.errors}</span>}
        </span>
        <span className="card-actions" onClick={stop}>
          {unitId && (
            <button title={reviewed ? t.unmarkReviewed : t.markReviewed} onClick={() => v.act.toggleReviewed(unitId)}>
              <Icon name="check" size={13} />
            </button>
          )}
          <button title={t.focusOn} onClick={focusCard}>
            <Icon name="focus" size={13} />
          </button>
          <button title={t.hide} onClick={() => v.act.hide(d.key, d.kind === "changed" ? d.title : d.path)}>
            <Icon name="hide" size={13} />
          </button>
        </span>
      </div>
      {d.rows && (
        <div className="rows">
          {d.rows.map((r) => (d.kind === "changed" ? <ChangedRow key={r.id} r={r} cardIds={d.ids} /> : <ImpactRow key={r.id} r={r} cardIds={d.ids} />))}
          {d.more > 0 && (
            <div className="row more" onClick={(e) => (stop(e), v.act.toggleCard(id))}>
              + {fmt(t.moreRows, { n: d.more })}
            </div>
          )}
        </div>
      )}
    </div>
  );
});

function ChangedRow({ r, cardIds }: { r: RowData; cardIds: string[] }) {
  const v = useView();
  const t = useT();
  const lit = v.lit ? (v.lit.has(r.id) ? "lit" : "") : "";
  return (
    <div
      className={`row ${lit} ${v.marks.has(r.id) ? `marked-${v.marks.get(r.id)}` : ""}`}
      data-entity={r.id}
      onMouseEnter={() => v.act.hover([r.id])}
      onMouseLeave={() => v.act.hover(cardIds)}
      onClick={(e) => {
        stop(e);
        if (!v.act.dragging()) v.act.focus({ type: "entity", id: r.id });
      }}
    >
      <span className={`tag tag-${r.tone}`}>{t.change[r.change as ChangeClass] ?? r.change}</span>
      <span className="sym">{r.name}</span>
      {r.errorsBelow ? <span className="err-dot" title={fmt(t.errorsN, { n: r.errorsBelow })} /> : null}
      <span className="count">{r.count || ""}</span>
    </div>
  );
}

function ImpactRow({ r, cardIds }: { r: RowData; cardIds: string[] }) {
  const v = useView();
  const t = useT();
  const lit = v.lit ? (v.lit.has(r.id) ? "lit" : "") : "";
  return (
    <div
      className={`row row-impacted ${lit} ${r.depth && r.depth > 1 ? "depth-2" : ""} ${r.uncertain ? "uncertain" : ""} ${r.error ? "has-error" : ""}`}
      data-entity={r.id}
      onMouseEnter={() => v.act.hover([r.id])}
      onMouseLeave={() => v.act.hover(cardIds)}
      onClick={(e) => {
        stop(e);
        if (!v.act.dragging()) v.act.focus({ type: "entity", id: r.id });
      }}
    >
      <div className="row-line">
        <span className="dot" />
        <span className="sym">{r.name}</span>
        {r.error ? <span className="err-badge">{t.compileError}</span> : r.via ? <span className="via">{t.via[r.via]}</span> : null}
      </div>
      {r.error && (
        <div className="err-msg" title={r.error}>
          {r.error}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------- groups

export const GroupNode = memo(function GroupNode({ data }: NodeProps<Node<GroupData>>) {
  const v = useView();
  const t = useT();
  const d = data;
  const gkey = d.side === "changed" ? `changed:${d.gid}` : `impact${d.col}:${d.gid}`;
  return (
    <div className={`group group-${d.side} ${litState(v.lit, d.ids)}`}>
      <div
        className="group-head"
        onMouseEnter={() => v.act.hover(d.ids)}
        onMouseLeave={() => v.act.hover(null)}
        onClick={() => !v.act.dragging() && v.act.focus({ type: "group", id: d.gid, side: d.side })}
      >
        {d.module && <span className="group-module">{d.module}</span>}
        {d.module && <span className="group-sep">/</span>}
        <span className="group-pkg">{d.pkg}</span>
        <span className="group-count">{d.count}</span>
        <span className="group-actions" onClick={stop}>
          {d.collapsible && (
            <button title={t.detailGroups} onClick={() => v.act.toggleGroup(gkey)}>
              <Icon name="minus" size={12} />
            </button>
          )}
          <button title={t.hide} onClick={() => v.act.hide(d.key, `${d.module ? `${d.module} / ` : ""}${d.pkg}`)}>
            <Icon name="hide" size={12} />
          </button>
        </span>
      </div>
    </div>
  );
});

export const GroupRowNode = memo(function GroupRowNode({ data }: NodeProps<Node<GroupData>>) {
  const v = useView();
  const t = useT();
  const d = data;
  const s = d.stats!;
  const gkey = d.side === "changed" ? `changed:${d.gid}` : `impact${d.col}:${d.gid}`;
  return (
    <div
      className={`card grouprow grouprow-${d.side} ${d.side === "impact" ? `col-${d.col}` : "card-changed"} ${litState(v.lit, d.ids)} ${s.errors ? "has-errors" : ""}`}
      onMouseEnter={() => v.act.hover(d.ids)}
      onMouseLeave={() => v.act.hover(null)}
    >
      <Handles sources={d.sources} targets={d.targets} />
      <div className="card-head" onClick={() => !v.act.dragging() && v.act.toggleGroup(gkey)}>
        <span className="chev">
          <Icon name="chevron" size={12} />
        </span>
        <span className="file">
          {d.module && <span className="group-module">{d.module} / </span>}
          {d.pkg}
        </span>
        <span className="card-actions" onClick={stop}>
          <button title={t.focusOn} onClick={() => v.act.focus({ type: "group", id: d.gid, side: d.side })}>
            <Icon name="focus" size={13} />
          </button>
          <button title={t.hide} onClick={() => v.act.hide(d.key, `${d.module ? `${d.module} / ` : ""}${d.pkg}`)}>
            <Icon name="hide" size={13} />
          </button>
        </span>
      </div>
      <div className="grouprow-stats">
        {d.side === "changed" ? (
          <>
            <span>{fmt(t.files, { n: s.files })}</span>
            <span>·</span>
            <span>{fmt(t.callers, { n: s.callers })}</span>
            {s.contract > 0 && (
              <>
                <span>·</span>
                <span className="txt-changed">{fmt(t.contractN, { n: s.contract })}</span>
              </>
            )}
          </>
        ) : (
          <>
            <span>{fmt(t.functions, { n: s.entities })}</span>
            <span>·</span>
            <span>{fmt(t.files, { n: s.files })}</span>
          </>
        )}
        {s.errors > 0 && (
          <>
            <span>·</span>
            <span className="txt-danger">{fmt(t.errorsN, { n: s.errors })}</span>
          </>
        )}
      </div>
      {d.side === "impact" && s.max > 0 && (
        <div className="heat">
          <i style={{ width: `${Math.max(4, (100 * s.entities) / s.max)}%` }} />
        </div>
      )}
    </div>
  );
});

export const ColHeadNode = memo(function ColHeadNode({ data }: NodeProps<Node<ColData>>) {
  return (
    <div className={`col-head col-${data.tone}`}>
      <span className="col-dot" />
      <span className="col-title">{data.title}</span>
      <span className="col-count">{data.count}</span>
    </div>
  );
});

// ---------------------------------------------------------------------------- wires

export function WireEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data }: EdgeProps<Edge<WireData>>) {
  const [path] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, curvature: 0.35 });
  const cls = `wire ${data?.error ? "wire-error" : ""} ${data?.soft ? "wire-soft" : ""}`;
  return (
    <>
      <BaseEdge id={id} path={path} className={cls} interactionWidth={0} />
      <circle cx={targetX} cy={targetY} r={2.8} className={`wire-end ${data?.error ? "wire-error" : ""}`} />
    </>
  );
}

export const nodeTypes = {
  card: CardNode,
  box: GroupNode,
  groupRow: GroupRowNode,
  colHead: ColHeadNode,
};
export const edgeTypes = { wire: WireEdge };
