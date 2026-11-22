import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AiHook } from "../App";
import type { Focus } from "../types";
import { idsOfFocus, type Model } from "../model";
import { fmt, useT } from "../i18n";
import { Icon, type Finding } from "../view";

interface AiStatus {
  provider: "openai" | "anthropic";
  base_url: string;
  model: string;
  has_key: boolean;
  enabled: boolean;
  auto: boolean;
  acknowledged: boolean;
  configured: boolean;
  from_env: string[];
}

interface AiResult {
  state: "running" | "done" | "error";
  text: string;
  findings: Finding[];
  error?: string;
  model?: string;
  tokens?: number;
  cached?: boolean;
  label: string;
}

const token = () => document.querySelector<HTMLMetaElement>('meta[name="rl-token"]')?.content ?? "";
const uiLang = () => (document.documentElement.lang.startsWith("zh") ? "zh" : "en");
const keyOf = (f: Focus) => (f && f.type !== "overview" ? JSON.stringify(f) : "");
const SEV_RANK = { high: 0, medium: 1, low: 2 } as const;

async function post(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-RippleLens-Token": token() },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error || `HTTP ${r.status}`);
  }
  return r;
}

function labelOf(m: Model, f: Focus): string {
  if (!f) return "";
  switch (f.type) {
    case "entity":
      return m.byId.get(f.id)?.qualname ?? f.id;
    case "unit":
      return m.units.get(f.id)?.name ?? f.id;
    case "impactFile":
      return f.path;
    case "group": {
      const g = m.groups.get(f.id);
      return g ? `${g.module ? `${g.module} / ` : ""}${g.pkg}` : f.id;
    }
    default:
      return "";
  }
}

export const useAiReview: AiHook = (m, focus, setFocus, setTab) => {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [results, setResults] = useState<Record<string, AiResult>>({});
  const [settings, setSettings] = useState(false);
  const [batch, setBatch] = useState<{ keys: string[]; running: boolean } | null>(null);
  const aborts = useRef(new Map<string, AbortController>());
  const stopBatch = useRef(false);

  const refresh = useCallback(() => {
    fetch("/api/ai/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((s: AiStatus | null) => s && setStatus(s))
      .catch(() => undefined);
  }, []);
  useEffect(refresh, [refresh]);

  const patch = (key: string, fn: (r: AiResult) => AiResult) => setResults((prev) => (prev[key] ? { ...prev, [key]: fn(prev[key]) } : prev));

  const run = useCallback(
    async (key: string, ids: string[], label: string, force = false) => {
      aborts.current.get(key)?.abort();
      const ctl = new AbortController();
      aborts.current.set(key, ctl);
      setResults((prev) => ({ ...prev, [key]: { state: "running", text: "", findings: [], label } }));
      try {
        const resp = await post("/api/ai/review", { ids, lang: uiLang(), force }, ctl.signal);
        const reader = resp.body!.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, i);
            buf = buf.slice(i + 2);
            for (const line of chunk.split("\n")) {
              if (!line.startsWith("data:")) continue;
              const ev = JSON.parse(line.slice(5));
              if (ev.type === "start") patch(key, (r) => ({ ...r, model: ev.model, tokens: ev.tokens, cached: ev.cached }));
              else if (ev.type === "delta") patch(key, (r) => ({ ...r, text: r.text + ev.text }));
              else if (ev.type === "finding") patch(key, (r) => ({ ...r, findings: [...r.findings, ev.finding] }));
              else if (ev.type === "done") patch(key, (r) => ({ ...r, state: "done", findings: ev.findings }));
              else if (ev.type === "error") patch(key, (r) => ({ ...r, state: "error", error: ev.message }));
            }
          }
        }
        patch(key, (r) => (r.state === "running" ? { ...r, state: "done" } : r));
      } catch (e) {
        if ((e as Error).name === "AbortError") {
          setResults((prev) => {
            const n = { ...prev };
            if (n[key]?.state === "running") delete n[key];
            return n;
          });
        } else patch(key, (r) => ({ ...r, state: "error", error: (e as Error).message }));
      } finally {
        if (aborts.current.get(key) === ctl) aborts.current.delete(key);
      }
    },
    [],
  );

  const active = !!status?.configured && !!status.enabled;
  const fkey = keyOf(focus);

  useEffect(() => {
    if (!active || !status?.auto || !fkey || results[fkey]) return;
    const ids = idsOfFocus(m, focus);
    if (ids.length) void run(fkey, ids, labelOf(m, focus));
    // results is read only to skip scopes that already ran
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, status?.auto, fkey]);

  const reviewAll = useCallback(async () => {
    const groups = [...new Set([...m.units.values()].map((u) => u.group))];
    const scopes = groups.map((gid) => ({ type: "group", id: gid, side: "changed" }) as Focus);
    const keys = scopes.map(keyOf);
    stopBatch.current = false;
    setBatch({ keys, running: true });
    for (const f of scopes) {
      if (stopBatch.current) break;
      const k = keyOf(f);
      await run(k, idsOfFocus(m, f), labelOf(m, f));
    }
    setBatch((b) => (b ? { ...b, running: false } : b));
  }, [m, run]);

  const marks = useMemo(() => {
    const out = new Map<string, "high" | "medium" | "low">();
    for (const r of Object.values(results))
      for (const f of r.findings) {
        if (!f.entity) continue;
        const prev = out.get(f.entity);
        if (!prev || SEV_RANK[f.severity] < SEV_RANK[prev]) out.set(f.entity, f.severity);
      }
    return out;
  }, [results]);

  const current = fkey ? results[fkey] : undefined;
  const badge = current?.findings.length ? <span className="beta">{current.findings.length}</span> : current?.state === "running" ? <span className="ai-live"><i /></span> : null;

  const button = (
    <AiButton
      on={active}
      onClick={() => {
        setTab("ai");
        if (!focus) setFocus({ type: "overview" });
      }}
    />
  );

  const panel = (
    <AiPanel
      model={m}
      status={status}
      focus={focus}
      result={current}
      results={results}
      batch={batch}
      settings={settings || !status?.configured}
      onSettings={setSettings}
      onStatus={(s) => {
        setStatus(s);
        setSettings(false);
      }}
      onRun={(force) => fkey && run(fkey, idsOfFocus(m, focus), labelOf(m, focus), force)}
      onStop={() => {
        stopBatch.current = true;
        for (const c of aborts.current.values()) c.abort();
      }}
      onReviewAll={reviewAll}
      onFocus={setFocus}
    />
  );

  return { button, panel, badge, marks };
};

function AiButton({ on, onClick }: { on: boolean; onClick: () => void }) {
  const t = useT();
  return (
    <button className={`icon-btn ${on ? "ai-on" : ""}`} title={on ? t.aiReview : t.ai.off} onClick={onClick}>
      <Icon name="spark" size={15} />
    </button>
  );
}

// ---------------------------------------------------------------------------- panel

interface PanelProps {
  model: Model;
  status: AiStatus | null;
  focus: Focus;
  result?: AiResult;
  results: Record<string, AiResult>;
  batch: { keys: string[]; running: boolean } | null;
  settings: boolean;
  onSettings: (open: boolean) => void;
  onStatus: (s: AiStatus) => void;
  onRun: (force: boolean) => void;
  onStop: () => void;
  onReviewAll: () => void;
  onFocus: (f: Focus) => void;
}

function AiPanel(p: PanelProps) {
  const t = useT();
  const s = p.status;
  if (!s) return <div className="muted">…</div>;
  if (p.settings) return <Settings status={s} onSaved={p.onStatus} onCancel={s.configured ? () => p.onSettings(false) : undefined} />;
  if (!s.enabled) return <Enable status={s} onStatus={p.onStatus} onSettings={() => p.onSettings(true)} />;

  const scoped = p.focus && p.focus.type !== "overview";
  const r = p.result;
  return (
    <>
      <div className="ai-meta">
        <span className="ai-model">{r?.model ? fmt(t.ai.tokens, { model: r.model, n: r.tokens ?? 0 }) : s.model}</span>
        {r?.state === "running" ? (
          <span className="ai-live">
            <i /> {t.ai.reviewing}
          </span>
        ) : (
          <button className="btn-link" onClick={() => p.onSettings(true)}>
            {t.ai.settings}
          </button>
        )}
      </div>
      {scoped ? (
        <ScopeResult r={r} onFocus={p.onFocus} />
      ) : (
        <AllChanges {...p} />
      )}
      <div className="d-actions">
        {scoped &&
          (r?.state === "running" ? (
            <button className="btn" onClick={p.onStop}>
              {t.ai.stop}
            </button>
          ) : (
            <button className="btn btn-ai" onClick={() => p.onRun(!!r)}>
              <Icon name="spark" size={12} /> {r ? t.ai.rerun : t.ai.run}
            </button>
          ))}
        {!scoped &&
          (p.batch?.running ? (
            <button className="btn" onClick={p.onStop}>
              {t.ai.stop}
            </button>
          ) : (
            <button className="btn btn-ai" onClick={p.onReviewAll}>
              <Icon name="spark" size={12} /> {t.ai.reviewAll}
            </button>
          ))}
      </div>
    </>
  );
}

function ScopeResult({ r, onFocus }: { r?: AiResult; onFocus: (f: Focus) => void }) {
  const t = useT();
  if (!r) return <p className="d-hint">{t.ai.run}</p>;
  return (
    <>
      {r.findings.map((f, i) => (
        <FindingCard key={i} f={f} onFocus={onFocus} />
      ))}
      {r.state === "running" && (
        <div className="ai-stream">
          {r.text.split("\n").slice(-4).join("\n")}
          <span className="caret" />
        </div>
      )}
      {r.state === "done" && r.findings.length === 0 && (
        <div className="clean">
          <span className="ok">
            <Icon name="check" size={14} />
          </span>
          {t.ai.noIssues}
        </div>
      )}
      {r.state === "error" && <div className="ai-error">{fmt(t.ai.error, { e: r.error ?? "" })}</div>}
    </>
  );
}

function AllChanges(p: PanelProps) {
  const t = useT();
  const keys = p.batch?.keys ?? Object.keys(p.results);
  const done = keys.map((k) => [k, p.results[k]] as const).filter(([, r]) => r);
  const findings = done.flatMap(([, r]) => r!.findings).sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity]);
  return (
    <>
      <h2 className="d-title">{t.aiReview}</h2>
      {done.length === 0 && <p className="d-hint">{t.ai.allHint}</p>}
      {done.length > 0 && (
        <div className="ai-progress">
          {done.map(([k, r]) => (
            <div key={k} className="kv clickable" onClick={() => p.onFocus(JSON.parse(k))}>
              {r!.state === "running" ? (
                <span className="ai-live">
                  <i />
                </span>
              ) : r!.state === "error" ? (
                <span className="dot dot-error" />
              ) : (
                <span className="ok">
                  <Icon name="check" size={12} />
                </span>
              )}
              <span className="mono ellipsis">{r!.label}</span>
              <span className="muted kv-extra">{r!.state === "done" ? (r!.findings.length ? fmt(t.ai.findings, { n: r!.findings.length }) : t.ai.noIssues) : ""}</span>
            </div>
          ))}
        </div>
      )}
      {findings.map((f, i) => (
        <FindingCard key={i} f={f} onFocus={p.onFocus} />
      ))}
      {done.length > 0 && !p.batch?.running && done.every(([, r]) => r!.state === "done") && findings.length === 0 && (
        <div className="clean">
          <span className="ok">
            <Icon name="check" size={14} />
          </span>
          {t.ai.noIssues}
        </div>
      )}
    </>
  );
}

function FindingCard({ f, onFocus }: { f: Finding; onFocus: (x: Focus) => void }) {
  const t = useT();
  return (
    <div className={`finding sev-${f.severity}`}>
      <div className="finding-head">
        <span className="sev">{t.ai.sev[f.severity]}</span>
        <span className="finding-title">{f.title}</span>
      </div>
      {f.path && (
        <button className="finding-at" disabled={!f.entity} onClick={() => f.entity && onFocus({ type: "entity", id: f.entity })}>
          {f.path}
          {f.line ? `:${f.line}` : ""}
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
    </div>
  );
}

function Enable({ status, onStatus, onSettings }: { status: AiStatus; onStatus: (s: AiStatus) => void; onSettings: () => void }) {
  const t = useT();
  const [err, setErr] = useState<string | null>(null);
  return (
    <>
      <h2 className="d-title">{t.ai.off}</h2>
      <div className="ai-note">{t.ai.privacy}</div>
      <p className="d-hint">
        {status.provider} · <code>{status.base_url}</code> · <code>{status.model}</code>
      </p>
      {err && <div className="ai-error">{err}</div>}
      <div className="d-actions">
        <button
          className="btn btn-ai"
          onClick={() =>
            post("/api/ai/config", { enabled: true, acknowledged: true })
              .then((r) => r.json())
              .then(onStatus, (e: Error) => setErr(e.message))
          }
        >
          <Icon name="spark" size={12} /> {t.ai.enable}
        </button>
        <button className="btn" onClick={onSettings}>
          {t.ai.settings}
        </button>
      </div>
    </>
  );
}

function Settings({ status, onSaved, onCancel }: { status: AiStatus; onSaved: (s: AiStatus) => void; onCancel?: () => void }) {
  const t = useT();
  const [form, setForm] = useState({ provider: status.provider, base_url: status.base_url, model: status.model, api_key: "", auto: status.auto, enabled: status.enabled });
  const [err, setErr] = useState<string | null>(null);
  const env = new Set(status.from_env);
  const set = (k: keyof typeof form, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <>
      <h2 className="d-title">{t.ai.settings}</h2>
      {!status.configured && <p className="d-hint">{t.ai.notConfigured}</p>}
      <div className="ai-form">
        <label>
          {t.ai.provider}
          <select value={form.provider} disabled={env.has("provider")} onChange={(e) => set("provider", e.target.value)}>
            <option value="openai">OpenAI-compatible</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </label>
        <label>
          {t.ai.baseUrl}
          <input
            value={form.base_url}
            disabled={env.has("base_url")}
            placeholder={form.provider === "anthropic" ? "https://api.anthropic.com" : "https://api.openai.com/v1"}
            onChange={(e) => set("base_url", e.target.value)}
          />
        </label>
        <label>
          {t.ai.model}
          <input value={form.model} disabled={env.has("model")} placeholder={form.provider === "anthropic" ? "claude-sonnet-4-5" : "gpt-5"} onChange={(e) => set("model", e.target.value)} />
        </label>
        <label>
          {t.ai.apiKey}
          <input type="password" autoComplete="off" value={form.api_key} disabled={env.has("api_key")} placeholder={status.has_key ? t.ai.keySaved : "sk-…"} onChange={(e) => set("api_key", e.target.value)} />
        </label>
        <label className="row-check">
          <input type="checkbox" checked={form.auto} onChange={(e) => set("auto", e.target.checked)} />
          {t.ai.autoRun}
        </label>
        <label className="row-check">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          {t.ai.enable}
        </label>
        <div className="ai-note">{t.ai.privacy}</div>
        {err && <div className="ai-error">{err}</div>}
        <div className="d-actions">
          <button
            className="btn btn-primary"
            onClick={() =>
              post("/api/ai/config", { ...form, acknowledged: form.enabled || status.acknowledged })
                .then((r) => r.json())
                .then(onSaved, (e: Error) => setErr(e.message))
            }
          >
            {t.ai.save}
          </button>
          {onCancel && (
            <button className="btn" onClick={onCancel}>
              {t.close}
            </button>
          )}
        </div>
      </div>
    </>
  );
}
