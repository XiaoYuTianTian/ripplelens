import { useCallback, useEffect, useState } from "react";
import type { AgentFinding, AgentReview, Focus, Severity } from "../types";
import type { Model } from "../model";
import { focusForPath } from "../heat";
import { fmt, useT } from "../i18n";
import { Icon } from "../view";

export interface ReviewChanges {
  findings?: { id: string; status?: AgentFinding["status"]; comment?: string }[];
  verdict?: AgentReview["human"]["verdict"];
  note?: string;
}

export interface AgentReviewState {
  review: AgentReview | null;
  update: (changes: ReviewChanges) => Promise<void>;
  error: string | null;
}

const POLL_MS = 5000;
const token = () => document.querySelector<HTMLMetaElement>('meta[name="rl-token"]')?.content ?? "";
export const SEV_RANK: Record<Severity, number> = { high: 0, medium: 1, low: 2 };

/** The agent pre-review stored next to the repo; re-read while the page is visible so a fresh submit shows up. */
export function useAgentReview(key: string): AgentReviewState {
  const [review, setReview] = useState<AgentReview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetch("/api/review")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { review: AgentReview | null }) => {
        setReview((prev) => (JSON.stringify(prev) === JSON.stringify(d.review) ? prev : d.review));
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(() => document.visibilityState === "visible" && load(), POLL_MS);
    return () => window.clearInterval(id);
  }, [load, key]);

  const update = useCallback(async (changes: ReviewChanges) => {
    const r = await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-RippleLens-Token": token() },
      body: JSON.stringify(changes),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      setError(j.error || `HTTP ${r.status}`);
      return;
    }
    setReview(j.review);
    setError(null);
  }, []);

  return { review, update, error };
}

/** Findings grouped by the map path they point at (scope, falling back to the file), worst severity first. */
export function findingScopes(review: AgentReview | null): Map<string, { n: number; sev: Severity }> {
  const out = new Map<string, { n: number; sev: Severity }>();
  for (const f of review?.findings ?? []) {
    if (f.status === "rejected") continue;
    const key = f.scope || f.path;
    if (!key) continue;
    const prev = out.get(key);
    out.set(key, { n: (prev?.n ?? 0) + 1, sev: prev && SEV_RANK[prev.sev] <= SEV_RANK[f.severity] ? prev.sev : f.severity });
  }
  return out;
}

export function focusOfFinding(m: Model, f: AgentFinding): Focus {
  if (f.entity && m.byId.has(f.entity)) return { type: "entity", id: f.entity };
  return (f.path && focusForPath(m, f.path)) || null;
}

export function ReviewChip({ review, onOpen }: { review: AgentReview | null; onOpen: () => void }) {
  const t = useT();
  if (!review) return null;
  const c = review.counts;
  const done = c.accepted + c.rejected;
  return (
    <button className={`review-chip ${review.stale ? "stale" : ""} ${c.open ? "" : "done"}`} onClick={onOpen} title={review.stale ? t.rv.stale : t.rv.chip}>
      {review.stale ? <Icon name="warn" size={12} /> : <Icon name="spark" size={12} />}
      <span className="review-chip-label">{t.rv.chip}</span>
      <b>{fmt(t.rv.chipCount, { n: c.total })}</b>
      <span className="review-chip-done">{fmt(t.rv.chipDone, { a: done, b: c.total })}</span>
    </button>
  );
}

export function AgentReviewPanel({
  model,
  state,
  onFocus,
}: {
  model: Model;
  state: AgentReviewState;
  onFocus: (f: Focus) => void;
}) {
  const t = useT();
  const r = state.review;
  const [note, setNote] = useState(r?.human.note ?? "");
  const [savedAt, setSavedAt] = useState<string | null>(null);
  useEffect(() => setNote(r?.human.note ?? ""), [r?.human.note]);
  if (!r) {
    return (
      <>
        <h2 className="d-title">{t.rv.tab}</h2>
        <p className="d-hint">{t.rv.none}</p>
        <p className="d-hint">{t.rv.noneHint}</p>
        {state.error && <div className="ai-error">{state.error}</div>}
      </>
    );
  }
  const order = [...r.findings].sort(
    (a, b) => Number(a.status !== "open") - Number(b.status !== "open") || SEV_RANK[a.severity] - SEV_RANK[b.severity],
  );
  const setVerdict = (verdict: AgentReview["human"]["verdict"]) => void state.update({ verdict: r.human.verdict === verdict ? null : verdict });
  return (
    <>
      <h2 className="d-title">
        {t.rv.tab}
        <span className={`tag rv-verdict rv-${r.agent.verdict}`}>{t.rv.verdict[r.agent.verdict] ?? r.agent.verdict}</span>
      </h2>
      {r.stale && (
        <div className="callout callout-warn">
          <Icon name="warn" size={12} /> {t.rv.stale}
        </div>
      )}
      {r.agent.summary && (
        <section className="d-section">
          <div className="d-sub">{t.rv.summary}</div>
          <p className="rv-summary">{r.agent.summary}</p>
        </section>
      )}
      <section className="d-section">
        <div className="d-sub">
          {fmt(t.rv.chipCount, { n: r.counts.total })} <span>{fmt(t.rv.chipDone, { a: r.counts.accepted + r.counts.rejected, b: r.counts.total })}</span>
        </div>
        {order.map((f) => (
          <FindingItem key={f.id} f={f} model={model} onFocus={onFocus} onUpdate={state.update} />
        ))}
      </section>
      <section className="d-section rv-verdict-box">
        <div className="d-sub">{t.rv.yourVerdict}</div>
        <div className="rv-verdict-row">
          <button className={`btn ${r.human.verdict === "approve" ? "btn-ok on" : ""}`} onClick={() => setVerdict("approve")}>
            <Icon name="check" size={12} /> {t.rv.approve}
          </button>
          <button className={`btn ${r.human.verdict === "request_changes" ? "btn-danger on" : ""}`} onClick={() => setVerdict("request_changes")}>
            {t.rv.requestChanges}
          </button>
        </div>
        <textarea className="rv-note" placeholder={t.rv.note} value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
        <div className="d-actions">
          <button
            className="btn btn-primary"
            disabled={note === r.human.note}
            onClick={() => void state.update({ note }).then(() => setSavedAt(new Date().toLocaleTimeString()))}
          >
            {t.rv.save}
          </button>
          {savedAt && note === r.human.note && <span className="muted">{t.rv.saved}</span>}
          <span className="spacer" />
          <a className="btn-link" href="/api/review/markdown" target="_blank" rel="noreferrer">
            {t.rv.export}
          </a>
        </div>
        {state.error && <div className="ai-error">{state.error}</div>}
      </section>
    </>
  );
}

export function FindingItem({
  f,
  model,
  onFocus,
  onUpdate,
  compact,
}: {
  f: AgentFinding;
  model: Model;
  onFocus: (x: Focus) => void;
  onUpdate: (c: ReviewChanges) => Promise<void>;
  compact?: boolean;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [comment, setComment] = useState(f.comment);
  useEffect(() => setComment(f.comment), [f.comment]);
  const target = focusOfFinding(model, f);
  const set = (status: AgentFinding["status"]) => void onUpdate({ findings: [{ id: f.id, status }] });
  return (
    <div className={`finding sev-${f.severity} rv-${f.status}`}>
      <div className="finding-head">
        <span className="sev">{t.ai.sev[f.severity]}</span>
        <span className={`rv-kind rv-kind-${f.kind}`}>{t.rv.kind[f.kind] ?? f.kind}</span>
        <span className="finding-title">{f.title}</span>
      </div>
      {f.path && !compact && (
        <button className="finding-at" disabled={!target} onClick={() => target && onFocus(target)}>
          {f.path}
          {f.line ? `:${f.line}` : ""}
          {f.scope && f.scope !== f.path.slice(0, f.path.lastIndexOf("/")) ? ` · ${t.rv.scope} ${f.scope}` : ""}
        </button>
      )}
      {f.evidence && (
        <p>
          <b>{t.ai.evidence}</b>
          {f.evidence}
        </p>
      )}
      {f.suggestion && (
        <p>
          <b>{t.ai.suggestion}</b>
          {f.suggestion}
        </p>
      )}
      {f.comment && !editing && <p className="rv-comment">{f.comment}</p>}
      {editing && (
        <div className="rv-comment-edit">
          <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder={t.rv.comment} autoFocus />
          <button
            className="btn btn-primary"
            onClick={() => void onUpdate({ findings: [{ id: f.id, comment }] }).then(() => setEditing(false))}
          >
            {t.rv.save}
          </button>
        </div>
      )}
      <div className="rv-actions">
        {f.status === "open" ? (
          <>
            <button className="btn btn-ok" onClick={() => set("accepted")}>
              <Icon name="check" size={12} /> {t.rv.accept}
            </button>
            <button className="btn" onClick={() => set("rejected")}>
              {t.rv.reject}
            </button>
          </>
        ) : (
          <>
            <span className={`rv-state rv-state-${f.status}`}>{f.status === "accepted" ? t.rv.accepted : t.rv.rejected}</span>
            <button className="btn-link" onClick={() => set("open")}>
              {t.rv.reopen}
            </button>
          </>
        )}
        <span className="spacer" />
        {!editing && (
          <button className="btn-link" onClick={() => setEditing(true)}>
            {t.rv.comment}
          </button>
        )}
      </div>
    </div>
  );
}
