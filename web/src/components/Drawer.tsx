import { useEffect, useState, type ReactNode } from "react";
import type { Focus, ImpactEdge, ImpactNode } from "../types";
import { basename, CONTRACT, toneOf, whyPath, type Model } from "../model";
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
    return n ? <EntityView {...p} node={n} /> : <Overview {...p} />;
  }
  if (f.type === "unit") return <UnitView {...p} unitId={f.id} />;
  if (f.type === "impactFile") return <ImpactFileView {...p} path={f.path} />;
  return <GroupView {...p} gid={f.id} side={f.side} />;
}

// ---------------------------------------------------------------------------- entity

function EntityView({ model: m, node: n, onFocus, onPeek }: Props & { node: ImpactNode }) {
  const t = useT();
  const sig = n.signature;
  const why = whyPath(m, n.id);
  const upstream = m.inc.get(n.id) ?? [];
  const downstream = (m.out.get(n.id) ?? []).filter((e) => m.byId.get(e.target) && !m.byId.get(e.target)!.change);
  return (
    <>
      <div className="d-kicker">
        {n.path}:{n.lines[0]} · {n.kind}
        {!n.change && ` · ${fmt(t.depthN, { n: n.depth })}`}
      </div>
      <h2 className="d-title">
        {n.change ? <span className={`tag tag-${toneOf(n.change)}`}>{t.change[n.change]}</span> : <span className={`dot ${n.depth > 1 ? "hollow" : ""}`} />}
        <span className="mono">{n.qualname}</span>
      </h2>
      {n.change && <p className="d-hint">{t.sigHint[n.change]}</p>}
      {n.renamed_from && (
        <p className="d-hint">
          ← <code>{n.renamed_from}</code>
        </p>
      )}
      {sig && (sig.before || sig.after) && sig.before !== sig.after && (
        <div className="sigdiff">
          {sig.before && <div className="del">− {sig.before}</div>}
          {sig.after && <div className="add">+ {sig.after}</div>}
        </div>
      )}
      {n.breaking.length > 0 && (
        <Section title={t.errorsHere} tone="danger">
          {n.breaking.map((b, i) => (
            <div key={i} className="caller has-error">
              <div className="caller-head">
                <span className="err-badge">{t.compileError}</span>
                <span className="caller-file">
                  {basename(n.path)}:{b.line}
                </span>
              </div>
              <div className="err-msg">{b.message}</div>
              <Snippet path={n.path} line={b.line} />
            </div>
          ))}
        </Section>
      )}
      {!n.change && why.length > 1 && (
        <Section title={t.why}>
          <div className="whypath">
            {why.map((s, i) => (
              <span key={s.id} className="why-step">
                {i > 0 && <span className="why-arrow">{t.via[s.via ?? "call"]} →</span>}
                <button
                  className={`why-chip ${s.change ? "why-seed" : ""}`}
                  onMouseEnter={() => onPeek([s.id])}
                  onMouseLeave={() => onPeek(null)}
                  onClick={() => onFocus({ type: "entity", id: s.id })}
                >
                  {s.name}
                </button>
              </span>
            ))}
          </div>
        </Section>
      )}
      {downstream.length > 0 && (
        <Section title={n.change ? t.directCallers : t.downstream} count={downstream.length}>
          {downstream
            .sort((a, b) => m.byId.get(b.target)!.score - m.byId.get(a.target)!.score)
            .slice(0, 40)
            .map((e) => (
              <Caller key={e.target + e.kind} edge={e} other={m.byId.get(e.target)!} onFocus={onFocus} onPeek={onPeek} />
            ))}
        </Section>
      )}
      {!n.change && upstream.length > 0 && (
        <Section title={t.upstream} count={upstream.length}>
          {upstream.map((e) => {
            const src = m.byId.get(e.source);
            return src ? <Caller key={e.source + e.kind} edge={e} other={src} sitesIn={n} onFocus={onFocus} onPeek={onPeek} /> : null;
          })}
        </Section>
      )}
      <div className="confidence">
        <span className={`chip chip-${n.confidence}`}>
          {t.confidence}: {t[n.confidence]}
        </span>
        {n.reasons.map((r) => (
          <span key={r} className="reason">
            {r}
          </span>
        ))}
      </div>
      {n.truncated &&
        Object.entries(n.truncated).map(([k, v]) => (
          <div key={k} className="callout callout-warn">
            {t.truncation}: {k} {v.shown}/{v.total}
          </div>
        ))}
      {n.notes?.map((x) => (
        <div key={x} className="callout">
          {x}
        </div>
      ))}
    </>
  );
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
        {other.confidence !== "precise" && <span className={`chip chip-${other.confidence}`}>{t[other.confidence]}</span>}
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

function UnitView({ model: m, unitId, onFocus, onPeek, reviewed, onToggleReviewed }: Props & { unitId: string }) {
  const t = useT();
  const u = m.units.get(unitId);
  if (!u) return null;
  const g = m.groups.get(u.group);
  const total = new Set(u.entities.flatMap((e) => [...m.down([e.id])].filter((d) => !m.byId.get(d)?.change))).size;
  const done = reviewed.has(unitId);
  return (
    <>
      <div className="d-kicker">
        {g?.module ? `${g.module} / ` : ""}
        {g?.pkg}
      </div>
      <h2 className="d-title">
        <span className="mono">{u.name}</span>
      </h2>
      <div className="file-list">
        {u.files.map((f) => (
          <div key={f.path} className="file-line">
            <code>{f.path}</code>
            <span className="plus">+{f.added}</span>
            <span className="minus">−{f.removed}</span>
          </div>
        ))}
      </div>
      {u.files.flatMap((f) => f.degraded).map((d) => (
        <div key={d} className="callout callout-warn">
          {d}
        </div>
      ))}
      <Section title={t.inThisUnit} count={u.entities.length}>
        {u.entities.map((e) => (
          <EntityLine key={e.id} m={m} n={e} onFocus={onFocus} onPeek={onPeek} />
        ))}
      </Section>
      <p className="d-hint">{fmt(t.functions, { n: total })} · {t.impactAll}</p>
      <div className="d-actions">
        <button className={`btn ${done ? "" : "btn-primary"}`} onClick={() => onToggleReviewed(unitId)}>
          {done ? t.unmarkReviewed : t.markReviewed}
        </button>
      </div>
    </>
  );
}

function ImpactFileView({ model: m, path, onFocus, onPeek }: Props & { path: string }) {
  const t = useT();
  const nodes = m.rep.nodes.filter((n) => !n.change && n.path === path).sort((a, b) => a.depth - b.depth || b.score - a.score);
  const g = m.groups.get(m.groupOfPath(path));
  return (
    <>
      <div className="d-kicker">
        {g?.module ? `${g.module} / ` : ""}
        {g?.pkg}
      </div>
      <h2 className="d-title">
        <span className="mono">{basename(path)}</span>
      </h2>
      <div className="file-list">
        <code>{path}</code>
      </div>
      <Section title={t.impactedFiles} count={nodes.length}>
        {nodes.map((n) => (
          <EntityLine key={n.id} m={m} n={n} onFocus={onFocus} onPeek={onPeek} />
        ))}
      </Section>
    </>
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
