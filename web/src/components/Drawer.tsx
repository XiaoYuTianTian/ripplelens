import { useEffect, useState, type ReactNode } from "react";
import type { Focus, ImpactEdge, ImpactNode } from "../types";
import { basename, CONTRACT, toneOf, type Model } from "../model";
import { fmt, useT } from "../i18n";
import { Icon } from "../view";

interface Props {
  model: Model;
  focus: Focus;
  tab: "details" | "ai";
  onTab: (t: "details" | "ai") => void;
  onFocus: (f: Focus) => void;
  onPeek: (ids: string[] | null) => void;
  onClose: () => void;
  reviewed: Set<string>;
  onToggleReviewed: (unit: string) => void;
  ai: ReactNode;
  aiBadge?: ReactNode;
}

export default function Drawer(p: Props) {
  const t = useT();
  return (
    <aside className="drawer" onMouseLeave={() => p.onPeek(null)}>
      <div className="drawer-tabs">
        <button className={p.tab === "details" ? "on" : ""} onClick={() => p.onTab("details")}>
          {t.details}
        </button>
        <button className={p.tab === "ai" ? "on" : ""} onClick={() => p.onTab("ai")}>
          <Icon name="spark" size={12} /> {t.aiReview} {p.aiBadge}
        </button>
        <span className="spacer" />
        <button className="x" title={`${t.close} (Esc)`} onClick={p.onClose}>
          <Icon name="close" size={14} />
        </button>
      </div>
      <div className="drawer-body">{p.tab === "details" ? <Details {...p} /> : p.ai}</div>
    </aside>
  );
}

function Details(p: Props) {
  const { model: m, focus: f } = p;
  if (!f || f.type === "overview") return <Overview {...p} />;
  if (f.type === "entity") {
    const n = m.byId.get(f.id);
    return n ? <FileDetail {...p} paths={[n.path]} focusId={n.id} /> : <Overview {...p} />;
  }
  if (f.type === "unit") {
    const u = m.units.get(f.id);
    return u ? <FileDetail {...p} paths={u.files.map((file) => file.path)} /> : <Overview {...p} />;
  }
  if (f.type === "impactFile") return <FileDetail {...p} paths={[f.path]} />;
  return <GroupView {...p} gid={f.id} side={f.side} />;
}


function Caller({
  edge,
  other,
  sitesIn,
  onFocus,
  onPeek,
}: {
  edge: ImpactEdge;
  other: ImpactNode;
  sitesIn?: ImpactNode;
  onFocus: (f: Focus) => void;
  onPeek: (ids: string[] | null) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const host = sitesIn ?? other;
  const err = other.breaking[0];
  const firstSite = edge.sites[0];
  return (
    <div className={`caller ${err && !sitesIn ? "has-error" : ""}`} onMouseEnter={() => onPeek([other.id])} onMouseLeave={() => onPeek(null)}>
      <div className="caller-head">
        <span className={`via-tag via-${edge.kind}`}>{t.via[edge.kind]}</span>
        <button className="link mono" onClick={() => onFocus({ type: "entity", id: other.id })}>
          {other.qualname}
        </button>
        <span className="caller-file">
          {basename(other.path)}:{other.lines[0]}
        </span>
      </div>
      {err && !sitesIn && <div className="err-msg">{err.message}</div>}
      {firstSite != null && <Snippet path={host.path} line={firstSite} context={1} />}
      {edge.sites.length > 1 && (
        <button className="btn-link" onClick={() => setOpen(!open)}>
          {open ? t.hideSites : fmt(t.sites, { n: edge.sites.length })}
        </button>
      )}
      {open && edge.sites.slice(1, 6).map((ln) => <Snippet key={ln} path={host.path} line={ln} context={1} />)}
    </div>
  );
}

// ---------------------------------------------------------------------------- unit / file / group

function FileDetail({ model: m, paths, focusId, onFocus, onPeek }: Props & { paths: string[]; focusId?: string }) {
  const t = useT();
  const files = [...new Set(paths)];
  useEffect(() => {
    if (focusId) document.querySelector(`.drawer [data-entity="${CSS.escape(focusId)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [focusId]);
  return (
    <>
      {files.map((path) => {
        const nodes = m.rep.nodes.filter((n) => n.path === path).sort((a, b) => Number(!!b.change) - Number(!!a.change) || a.lines[0] - b.lines[0]);
        const changed = nodes.filter((n) => n.change);
        const impacted = nodes.filter((n) => !n.change);
        return (
          <section key={path} className="file-detail">
            <h2 className="d-title">
              <span className="mono">{basename(path)}</span>
            </h2>
            <div className="file-list">
              <code>{path}</code>
            </div>
            {changed.map((n) => (
              <SymbolBlock key={n.id} m={m} n={n} focused={n.id === focusId} onFocus={onFocus} onPeek={onPeek} />
            ))}
            {impacted.length > 0 && (
              <Section title={t.impactedFiles} count={impacted.length}>
                {impacted.map((n) => (
                  <div key={n.id} className={`symbol-block ${n.id === focusId ? "is-focus" : ""}`} data-entity={n.id}>
                    <EntityLine m={m} n={n} onFocus={onFocus} onPeek={onPeek} extra={n.breaking[0]?.message} />
                    {n.breaking[0] && <Snippet path={n.path} line={n.breaking[0].line} />}
                  </div>
                ))}
              </Section>
            )}
          </section>
        );
      })}
    </>
  );
}

function SymbolBlock({ m, n, focused, onFocus, onPeek }: { m: Model; n: ImpactNode; focused?: boolean; onFocus: (f: Focus) => void; onPeek: (ids: string[] | null) => void }) {
  const t = useT();
  const sig = n.signature;
  const downstream = (m.out.get(n.id) ?? []).filter((e) => m.byId.get(e.target) && !m.byId.get(e.target)!.change);
  return (
    <div className={`symbol-block ${focused ? "is-focus" : ""}`} data-entity={n.id} onMouseEnter={() => onPeek([n.id])} onMouseLeave={() => onPeek(null)}>
      <h3 className="d-sub">
        {n.change && <span className={`tag tag-${toneOf(n.change)}`}>{t.change[n.change]}</span>}
        <span className="mono">{n.qualname}</span>
        <span className="muted">:{n.lines[0]}</span>
      </h3>
      {sig && (sig.before || sig.after) && sig.before !== sig.after && (
        <div className="sigdiff">
          {sig.before && <div className="del">− {sig.before}</div>}
          {sig.after && <div className="add">+ {sig.after}</div>}
        </div>
      )}
      {n.breaking.map((b, i) => (
        <div key={i} className="err-msg">
          {basename(n.path)}:{b.line} {b.message}
        </div>
      ))}
      {downstream.length > 0 && (
        <Section title={t.directCallers} count={downstream.length}>
          {downstream.slice(0, 12).map((e) => {
            const other = m.byId.get(e.target)!;
            return <Caller key={e.target + e.kind} edge={e} other={other} onFocus={onFocus} onPeek={onPeek} />;
          })}
        </Section>
      )}
    </div>
  );
}


function GroupView({ model: m, gid, side, onFocus, onPeek }: Props & { gid: string; side: "changed" | "impact" }) {
  const t = useT();
  const g = m.groups.get(gid);
  const units = [...m.units.values()].filter((u) => u.group === gid).sort((a, b) => m.riskOfUnit(b) - m.riskOfUnit(a));
  const impacted = m.rep.nodes.filter((n) => !n.change && m.groupOfPath(n.path) === gid);
  return (
    <>
      <div className="d-kicker">{g?.module}</div>
      <h2 className="d-title">
        <span className="mono">{g?.pkg}</span>
      </h2>
      {side === "changed" ? (
        <Section title={t.inThisGroup} count={units.length}>
          {units.map((u) => (
            <div key={u.id} className="kv clickable" onClick={() => onFocus({ type: "unit", id: u.id })} onMouseEnter={() => onPeek(u.entities.map((e) => e.id))}>
              <span className="mono ellipsis">{u.name}</span>
              <span className="muted">{fmt(t.functions, { n: u.entities.length })}</span>
            </div>
          ))}
        </Section>
      ) : (
        <Section title={t.impactAll} count={impacted.length}>
          {impacted.slice(0, 80).map((n) => (
            <EntityLine key={n.id} m={m} n={n} onFocus={onFocus} onPeek={onPeek} />
          ))}
        </Section>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------- overview

function Overview({ model: m, onFocus, onPeek }: Props) {
  const t = useT();
  const { rep } = m;
  const errors = rep.nodes.filter((n) => n.breaking.length);
  const contract = rep.nodes.filter((n) => n.change && CONTRACT.has(n.change));
  const top = rep.nodes.filter((n) => !n.change).slice(0, 10);
  return (
    <>
      <h2 className="d-title">{t.overview}</h2>
      <p className="d-hint">{t.overviewHint}</p>
      {errors.length > 0 && (
        <Section title={t.errorsHere} count={errors.length} tone="danger">
          {errors.map((n) => (
            <EntityLine key={n.id} m={m} n={n} onFocus={onFocus} onPeek={onPeek} extra={n.breaking[0]?.message} />
          ))}
        </Section>
      )}
      {contract.length > 0 && (
        <Section title={t.contractChanges} count={contract.length}>
          {contract.map((n) => (
            <EntityLine key={n.id} m={m} n={n} onFocus={onFocus} onPeek={onPeek} />
          ))}
        </Section>
      )}
      {top.length > 0 ? (
        <Section title={t.impactAll} count={rep.summary.impacted}>
          {top.map((n) => (
            <EntityLine key={n.id} m={m} n={n} onFocus={onFocus} onPeek={onPeek} />
          ))}
        </Section>
      ) : (
        <p className="muted">{t.noImpact}</p>
      )}
      {rep.truncation.length > 0 && (
        <Section title={t.truncation} tone="warn">
          {rep.truncation.slice(0, 10).map((x, i) => (
            <div key={i} className="kv">
              <span className="chip chip-warn">{x.reason}</span>
              <code className="ellipsis">{x.at.split("#")[1] ?? x.at}</code>
              {x.total ? <span className="muted">{x.shown}/{x.total}</span> : null}
            </div>
          ))}
        </Section>
      )}
      {Object.keys(rep.blind_spots).length > 0 && (
        <Section title={t.blindSpots}>
          {Object.entries(rep.blind_spots).map(([lang, items]) => (
            <div key={lang} className="blind">
              <strong>{lang}</strong> {items.join("; ")}
            </div>
          ))}
        </Section>
      )}
      {rep.warnings.map((w) => (
        <div key={w} className="callout callout-warn">
          {w}
        </div>
      ))}
      <div className="foot muted">{fmt(t.timing, { tool: rep.tool.name, version: rep.tool.version, s: rep.timing.total_seconds, r: rep.timing.requests })}</div>
    </>
  );
}

// ---------------------------------------------------------------------------- bits

function EntityLine({ n, onFocus, onPeek, extra }: { m: Model; n: ImpactNode; onFocus: (f: Focus) => void; onPeek: (ids: string[] | null) => void; extra?: string }) {
  const t = useT();
  return (
    <div className="kv clickable" onClick={() => onFocus({ type: "entity", id: n.id })} onMouseEnter={() => onPeek([n.id])} onMouseLeave={() => onPeek(null)}>
      {n.change ? (
        <span className={`tag tag-${toneOf(n.change)}`}>{t.change[n.change]}</span>
      ) : (
        <span className={`dot ${n.depth > 1 ? "hollow" : ""} ${n.breaking.length ? "dot-error" : ""}`} />
      )}
      <span className="mono ellipsis">{n.qualname}</span>
      <span className="muted ellipsis kv-extra">{extra ?? (n.change ? (n.impacted ? `→ ${n.impacted}` : "") : `${basename(n.path)}:${n.lines[0]}`)}</span>
    </div>
  );
}

function Section({ title, count, tone, children }: { title: string; count?: number; tone?: "danger" | "warn"; children: ReactNode }) {
  return (
    <section className={`d-section ${tone ? `d-${tone}` : ""}`}>
      <div className="d-sub">
        {title} {count != null && <span>{count}</span>}
      </div>
      {children}
    </section>
  );
}

const snippetCache = new Map<string, Promise<{ start: number; lines: string[] }>>();

export function Snippet({ path, line, context = 3 }: { path: string; line: number; context?: number }) {
  const t = useT();
  const [data, setData] = useState<{ start: number; lines: string[] } | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    const start = Math.max(1, line - context);
    const key = `${path}:${start}:${line + context}`;
    if (!snippetCache.has(key)) {
      snippetCache.set(
        key,
        fetch(`/api/source?path=${encodeURIComponent(path)}&start=${start}&end=${line + context}`).then((r) => (r.ok ? r.json() : Promise.reject())),
      );
    }
    let live = true;
    snippetCache.get(key)!.then(
      (d) => live && setData(d),
      () => live && setErr(true),
    );
    return () => {
      live = false;
    };
  }, [path, line, context]);
  if (err) return <div className="snippet muted">{t.sourceUnavailable}</div>;
  if (!data) return <div className="snippet muted">…</div>;
  const indent = Math.min(...data.lines.filter((l) => l.trim()).map((l) => l.match(/^\s*/)![0].length));
  return (
    <pre className="snippet">
      {data.lines.map((l, i) => {
        const n = data.start + i;
        return (
          <div key={n} className={n === line ? "hl-line" : ""}>
            <span className="ln">{n}</span>
            {l.slice(Number.isFinite(indent) ? indent : 0) || " "}
          </div>
        );
      })}
    </pre>
  );
}
