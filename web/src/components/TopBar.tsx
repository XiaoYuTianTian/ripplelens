import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import type { Focus, ImpactNode } from "../types";
import { basename, toneOf, type Model } from "../model";
import type { Detail } from "../layout";
import { fmt, useT, type Lang } from "../i18n";
import { Icon, Logo } from "../view";

export interface Filters {
  riskyOnly: boolean;
  hideTests: boolean;
  hideMinor: boolean;
  maxDepth: number;
}

export type MainView = "chain" | "map";

export interface Prefs {
  theme: "system" | "light" | "dark";
  lang: "auto" | Lang;
  detail: Detail;
  view: MainView;
  /** Set when the user picks Chain or Impact, so a later default does not override them. */
  viewChosen?: boolean;
  treeCollapsed?: boolean;
}

/** Map legacy prefs.view `"ripple"` → `"map"`. */
export function normalizePrefs(p: Prefs): Prefs {
  const raw = String((p as { view?: string }).view ?? "map");
  const stored: MainView = raw === "chain" ? "chain" : "map";
  const view: MainView = p.viewChosen ? stored : "map";
  return { ...p, view };
}

interface Props {
  model: Model;
  focusLabel: string | null;
  onClearFocus: () => void;
  onFocus: (f: Focus) => void;
  reviewed: number;
  totalUnits: number;
  filters: Filters;
  onFilters: (f: Filters) => void;
  prefs: Prefs;
  onPrefs: (p: Prefs) => void;
  onResetLayout: () => void;
  onReload: () => void;
  searchRef: RefObject<HTMLInputElement | null>;
  menu: string | null;
  onMenu: (m: string | null) => void;
  aiButton: ReactNode;
  reviewChip: ReactNode;
}

export default function TopBar(p: Props) {
  const t = useT();
  const { rep } = p.model;
  const s = rep.summary;
  const issues = rep.servers.filter((x) => x.status !== "ok");
  const filtersOn = p.filters.riskyOnly || p.filters.hideTests || p.filters.hideMinor || p.filters.maxDepth > 0;
  const toggle = (name: string) => p.onMenu(p.menu === name ? null : name);
  return (
    <header className="topbar">
      <div className="brand">
        <Logo />
        <span className="brand-name">RippleLens</span>
      </div>
      <div className="range" title={`${rep.repo.base.sha} → ${rep.repo.head.sha ?? "worktree"}`}>
        <span className="ref">{rep.repo.base.ref}</span>
        <span className="range-arrow">→</span>
        <span className="ref ref-head">{rep.repo.head.ref}</span>
      </div>
      <div className="metrics">
        <span className="metric metric-changed">
          <b>{s.files_changed}</b>
          <i>{fmt(t.filesChanged, { n: s.files_changed })}</i>
        </span>
        <span className="metric metric-impact">
          <b>{s.impact_analyzed === false ? "—" : (s.impacted ?? 0)}</b>
          <i>{s.impact_analyzed === false ? t.impactNotAnalyzedShort : fmt(t.impactedFns, { n: s.impacted ?? 0 })}</i>
        </span>
        {s.breaking > 0 && (
          <button className="metric metric-error" onClick={() => p.onFocus({ type: "overview" })}>
            <b>{s.breaking}</b>
            <i>{fmt(t.compileErrors, { n: s.breaking })}</i>
          </button>
        )}
      </div>
      <Seg
        value={p.prefs.view}
        options={[
          ["chain", t.viewChain],
          ["map", t.viewMap],
        ]}
        onChange={(v) => p.onPrefs({ ...p.prefs, view: v as MainView, viewChosen: true })}
      />
      {p.focusLabel && (
        <div className="focus-chip">
          <span className="focus-label">{t.focus}</span>
          <span className="focus-name">{p.focusLabel}</span>
          <button className="x" onClick={p.onClearFocus} title={`${t.kBack} (Esc)`}>
            <Icon name="close" size={12} />
          </button>
        </div>
      )}
      <span className="spacer" />
      {issues.length > 0 && (
        <span className="server-issue" title={issues.map((x) => `${x.name}: ${x.reason ?? x.status}`).join("\n")}>
          <Icon name="warn" size={13} />
          {issues.map((x) => `${x.name} ${x.status}`).join(" · ")}
        </span>
      )}
      {p.totalUnits > 0 && (
        <div className="progress" title={fmt(t.reviewed, { a: p.reviewed, b: p.totalUnits })}>
          <span className="progress-bar">
            <i style={{ width: `${(100 * p.reviewed) / p.totalUnits}%` }} />
          </span>
          {fmt(t.reviewed, { a: p.reviewed, b: p.totalUnits })}
        </div>
      )}
      <Search model={p.model} inputRef={p.searchRef} onFocus={p.onFocus} open={p.menu === "search"} onOpen={(o) => p.onMenu(o ? "search" : null)} />
      <div className="pop-anchor">
        <button className={`icon-btn ${filtersOn ? "is-on" : ""}`} title={t.filters} onClick={() => toggle("filters")}>
          <Icon name="filter" size={15} />
        </button>
        {p.menu === "filters" && <FiltersPop filters={p.filters} onChange={p.onFilters} />}
      </div>
      {p.reviewChip}
      {p.aiButton}
      <div className="pop-anchor">
        <button className="icon-btn" title={`${t.help} (?)`} onClick={() => toggle("help")}>
          <Icon name="help" size={16} />
        </button>
        {p.menu === "help" && <HelpPop />}
      </div>
      <div className="pop-anchor">
        <button className="icon-btn" title={t.more} onClick={() => toggle("more")}>
          <Icon name="more" size={16} />
        </button>
        {p.menu === "more" && <MorePop {...p} />}
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------- search

function Search({ model: m, inputRef, onFocus, open, onOpen }: { model: Model; inputRef: RefObject<HTMLInputElement | null>; onFocus: (f: Focus) => void; open: boolean; onOpen: (o: boolean) => void }) {
  const t = useT();
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const results = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return [];
    const out: { key: string; label: string; sub: string; node?: ImpactNode; focus: Focus }[] = [];
    for (const u of m.units.values()) {
      if (u.name.toLowerCase().includes(s) || u.files.some((f) => f.path.toLowerCase().includes(s)))
        out.push({ key: `u:${u.id}`, label: u.name, sub: u.files[0].path, focus: { type: "unit", id: u.id } });
    }
    const nodes = m.rep.nodes.filter((n) => n.qualname.toLowerCase().includes(s) || n.name.toLowerCase().includes(s));
    nodes.sort((a, b) => Number(!!b.change) - Number(!!a.change) || a.depth - b.depth);
    for (const n of nodes) out.push({ key: n.id, label: n.qualname, sub: `${basename(n.path)}:${n.lines[0]}`, node: n, focus: { type: "entity", id: n.id } });
    return out.slice(0, 14);
  }, [m, q]);
  useEffect(() => setCursor(0), [q]);
  const pick = (i: number) => {
    const r = results[i];
    if (!r) return;
    onFocus(r.focus);
    setQ("");
    onOpen(false);
    inputRef.current?.blur();
  };
  return (
    <div className="pop-anchor">
      <label className="search">
        <Icon name="search" size={14} />
        <input
          ref={inputRef}
          value={q}
          placeholder={t.search}
          onChange={(e) => {
            setQ(e.target.value);
            onOpen(true);
          }}
          onFocus={() => q && onOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") (e.preventDefault(), setCursor((c) => Math.min(results.length - 1, c + 1)));
            else if (e.key === "ArrowUp") (e.preventDefault(), setCursor((c) => Math.max(0, c - 1)));
            else if (e.key === "Enter") pick(cursor);
            else if (e.key === "Escape") {
              setQ("");
              onOpen(false);
              inputRef.current?.blur();
            }
            e.stopPropagation();
          }}
        />
        <kbd>/</kbd>
      </label>
      {open && results.length > 0 && (
        <div className="pop search-pop">
          {results.map((r, i) => (
            <div key={r.key} className={`kv clickable ${i === cursor ? "on" : ""}`} onMouseEnter={() => setCursor(i)} onMouseDown={(e) => (e.preventDefault(), pick(i))}>
              {r.node ? (
                r.node.change ? (
                  <span className={`tag tag-${toneOf(r.node.change)}`}>{t.change[r.node.change]}</span>
                ) : (
                  <span className={`dot ${r.node.depth > 1 ? "hollow" : ""}`} />
                )
              ) : (
                <span className="lang file-ico" />
              )}
              <span className="mono ellipsis">{r.label}</span>
              <span className="muted ellipsis kv-extra">{r.sub}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------- popovers

function FiltersPop({ filters: f, onChange }: { filters: Filters; onChange: (f: Filters) => void }) {
  const t = useT();
  return (
    <div className="pop pop-right filters-pop" onClick={(e) => e.stopPropagation()}>
      <label className="check">
        <input type="checkbox" checked={f.riskyOnly} onChange={(e) => onChange({ ...f, riskyOnly: e.target.checked })} />
        <span>
          {t.riskyOnly}
          <small>{t.riskyHint}</small>
        </span>
      </label>
      <label className="check">
        <input type="checkbox" checked={f.hideTests} onChange={(e) => onChange({ ...f, hideTests: e.target.checked })} />
        <span>{t.hideTests}</span>
      </label>
      <label className="check">
        <input type="checkbox" checked={f.hideMinor} onChange={(e) => onChange({ ...f, hideMinor: e.target.checked })} />
        <span>{t.hideMinor}</span>
      </label>
      <div className="seg-row">
        <span>{t.depth}</span>
        <Seg value={String(f.maxDepth)} options={[["1", "1"], ["2", "2"], ["3", "3"], ["0", t.depthAll]]} onChange={(v) => onChange({ ...f, maxDepth: Number(v) })} />
      </div>
    </div>
  );
}

function HelpPop() {
  const t = useT();
  const keys: [string, string][] = [
    ["/", t.kSearch],
    ["j  k", t.kMove],
    ["Enter", t.kFocus],
    ["Esc", t.kBack],
    ["h", t.kHide],
    ["r", t.kReviewed],
    ["f", t.kFit],
    ["?", t.kHelp],
  ];
  return (
    <div className="pop pop-right help-pop" onClick={(e) => e.stopPropagation()}>
      <div className="pop-title">{t.legend}</div>
      <div className="legend">
        <span className="tag tag-contract">{t.change.signature}</span>
        <span>{t.lgContract}</span>
        <span className="tag tag-body">{t.change.body}</span>
        <span>{t.lgBody}</span>
        <span className="lg-dots">
          <span className="dot" />
          <span className="dot hollow" />
        </span>
        <span>{t.lgImpact}</span>
        <span className="lg-dots">
          <span className="dot dot-error" />
        </span>
        <span>{t.lgError}</span>
        <span className="lg-dots">
          <span className="dot dashed" />
        </span>
        <span>{t.lgUncertain}</span>
        <span className="heat-dot heat-changed" />
        <span>{t.lgHeat}</span>
        <span className="lg-span">{t.lgChain}</span>
        <span className="lg-span">{t.lgMap}</span>
      </div>
      <div className="pop-title">{t.shortcuts}</div>
      <div className="keys">
        {keys.map(([k, v]) => (
          <div key={k}>
            <kbd>{k}</kbd>
            <span>{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MorePop(p: Props) {
  const t = useT();
  const [copied, setCopied] = useState<string | null>(null);
  const copy = async (what: "md" | "ai") => {
    const r = await fetch(what === "md" ? "/api/markdown" : "/api/context?budget=12000");
    const text = await r.text();
    let body = text;
    if (what === "ai") {
      try {
        body = JSON.parse(text).text ?? text;
      } catch {
        /* plain text */
      }
    }
    await navigator.clipboard.writeText(body);
    setCopied(what);
    setTimeout(() => setCopied(null), 1400);
  };
  const pr = p.prefs;
  return (
    <div className="pop pop-right more-pop" onClick={(e) => e.stopPropagation()}>
      <div className="seg-row">
        <span>{t.theme}</span>
        <Seg value={pr.theme} options={[["system", t.themeSystem], ["light", t.themeLight], ["dark", t.themeDark]]} onChange={(v) => p.onPrefs({ ...pr, theme: v as Prefs["theme"] })} />
      </div>
      <div className="seg-row">
        <span>{t.language}</span>
        <Seg value={pr.lang} options={[["auto", t.langAuto], ["zh", "中文"], ["en", "English"]]} onChange={(v) => p.onPrefs({ ...pr, lang: v as Prefs["lang"] })} />
      </div>
      <div className="seg-row">
        <span>{t.detail}</span>
        <Seg
          value={pr.detail}
          options={[["auto", t.detailAuto], ["full", t.detailFull], ["files", t.detailFiles], ["groups", t.detailGroups]]}
          onChange={(v) => p.onPrefs({ ...pr, detail: v as Detail })}
        />
      </div>
      <div className="pop-sep" />
      <button className="menu-item" onClick={() => copy("md")}>
        {copied === "md" ? t.copied : t.copyMarkdown}
      </button>
      <button className="menu-item" onClick={() => copy("ai")}>
        {copied === "ai" ? t.copied : t.copyForAi}
      </button>
      <button className="menu-item" onClick={() => (p.onResetLayout(), p.onMenu(null))}>
        {t.resetLayout}
      </button>
      <button className="menu-item" onClick={() => (p.onReload(), p.onMenu(null))}>
        {t.reload}
      </button>
    </div>
  );
}

export function Seg({ value, options, onChange }: { value: string; options: [string, string][]; onChange: (v: string) => void }) {
  return (
    <span className="seg">
      {options.map(([v, label]) => (
        <button key={v} className={v === value ? "on" : ""} onClick={() => onChange(v)}>
          {label}
        </button>
      ))}
    </span>
  );
}

export function useOutsideClose(open: boolean, close: () => void) {
  const ref = useRef(close);
  ref.current = close;
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest(".pop-anchor")) ref.current();
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);
}
