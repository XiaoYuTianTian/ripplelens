import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ReactFlowProvider, type Node } from "@xyflow/react";
import type { Focus, Impact } from "./types";
import { basename, buildModel, idsOfFocus, type Model } from "./model";
import { computeLayout, type CardData } from "./layout";
import { DICTS, fmt, I18n, resolveLang, useT } from "./i18n";
import { Icon, Logo, View, type ViewState } from "./view";
import Board, { type BoardApi } from "./components/Board";
import Drawer from "./components/Drawer";
import HeatTree, { pathOfFocus } from "./components/HeatTree";
import MapView from "./components/MapView";
import TopBar, { normalizePrefs, useOutsideClose, type Filters, type Prefs } from "./components/TopBar";

function stored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    const v = JSON.parse(raw);
    return fallback && typeof fallback === "object" && !Array.isArray(fallback) ? { ...fallback, ...v } : v;
  } catch {
    return fallback;
  }
}
const save = (key: string, v: unknown) => {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* storage full or disabled */
  }
};

const DEFAULT_PREFS: Prefs = { theme: "system", lang: "auto", detail: "auto", view: "map", treeCollapsed: false };
const TREE_W = 272;
const DEFAULT_FILTERS: Filters = { riskyOnly: false, hideTests: false, hideMinor: false, maxDepth: 0 };

export interface AiSlot {
  button: ReactNode;
  panel: ReactNode;
  badge?: ReactNode;
  marks: Map<string, "high" | "medium" | "low">;
}
export type AiHook = (model: Model, focus: Focus, setFocus: (f: Focus) => void, setTab: (t: "details" | "ai") => void) => AiSlot;

export default function App({ useAi }: { useAi: AiHook }) {
  const [rep, setRep] = useState<Impact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<Prefs>(() => normalizePrefs(stored("rl:prefs", DEFAULT_PREFS)));
  const lang = resolveLang(prefs.lang);
  const t = DICTS[lang];

  const load = useCallback(() => {
    fetch("/api/impact")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: Impact) => {
        setRep(data);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  useEffect(() => {
    save("rl:prefs", prefs);
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => (document.documentElement.dataset.theme = prefs.theme === "system" ? (mq.matches ? "dark" : "light") : prefs.theme);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [prefs, lang]);

  const model = useMemo(() => (rep ? buildModel(rep) : null), [rep]);

  return (
    <I18n.Provider value={t}>
      {error ? (
        <div className="empty">
          <Logo size={40} />
          <p>{fmt(t.loadFailed, { e: error })}</p>
          <button className="btn" onClick={load}>
            {t.retry}
          </button>
        </div>
      ) : !model ? (
        <div className="empty">
          <div className="spinner" /> {t.loading}
        </div>
      ) : (
        <ReactFlowProvider>
          <Review key={model.key} model={model} prefs={prefs} onPrefs={setPrefs} onReload={load} useAi={useAi} />
        </ReactFlowProvider>
      )}
    </I18n.Provider>
  );
}

function Review({ model: m, prefs, onPrefs, onReload, useAi }: { model: Model; prefs: Prefs; onPrefs: (p: Prefs) => void; onReload: () => void; useAi: AiHook }) {
  const t = useT();
  const K = m.key;
  const [focus, setFocusRaw] = useState<Focus>(null);
  const [tab, setTab] = useState<"details" | "ai">("details");
  const [hoverIds, setHoverIds] = useState<string[] | null>(null);
  const [hoverExtent, setHoverExtent] = useState<"file" | "chain">("chain");
  const [peekIds, setPeekIds] = useState<string[] | null>(null);
  const [filters, setFilters] = useState<Filters>(() => stored("rl:filters", DEFAULT_FILTERS));
  const [hidden, setHidden] = useState<Record<string, string>>(() => stored(`rl:hidden:${K}`, {}));
  const [reviewed, setReviewed] = useState<Set<string>>(() => new Set(stored<string[]>(`rl:rev:${K}`, [])));
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>(() => stored(`rl:pos:${K}`, {}));
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [expandedCards, setExpandedCards] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const [fitNonce, setFitNonce] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const board = useRef<BoardApi>(null);
  const dragging = useRef(false);

  useEffect(() => save("rl:filters", filters), [filters]);
  useEffect(() => save(`rl:hidden:${K}`, hidden), [K, hidden]);
  useEffect(() => save(`rl:rev:${K}`, [...reviewed]), [K, reviewed]);
  useEffect(() => save(`rl:pos:${K}`, positions), [K, positions]);
  useOutsideClose(menu !== null && menu !== "search", () => setMenu(null));

  const setFocus = useCallback((f: Focus) => {
    setFocusRaw(f);
    setHoverIds(null);
    setPeekIds(null);
    setMenu(null);
  }, []);
  const ai = useAi(m, focus, setFocus, setTab);

  const focusIds = useMemo(() => {
    if (!focus || focus.type === "overview") return null;
    const ids = idsOfFocus(m, focus);
    return ids.length ? ids : null;
  }, [m, focus]);

  const hiddenSet = useMemo(() => new Set(Object.keys(hidden)), [hidden]);
  const layout = useMemo(
    () =>
      computeLayout(
        m,
        { focusIds, hidden: hiddenSet, ...filters, detail: prefs.detail, expandedGroups, expandedCards, positions },
        {
          changed: t.changed,
          direct: t.direct,
          indirect: t.indirect,
          files: (n) => fmt(t.files, { n }),
          functions: (n) => fmt(t.functions, { n }),
          groups: (n) => fmt(t.groups, { n }),
        },
      ),
    [m, focusIds, hiddenSet, filters, prefs.detail, expandedGroups, expandedCards, positions, t],
  );

  const activeHover = peekIds ?? hoverIds;
  const lit = useMemo(() => {
    if (!activeHover) return null;
    if (!peekIds && hoverExtent === "file") return new Set(activeHover);
    return m.scopeOf(activeHover);
  }, [m, activeHover, peekIds, hoverExtent]);
  const edges = useMemo(() => {
    if (lit) return layout.edges.filter((e) => e.data!.pairs.some(([s, d]) => lit.has(s) && lit.has(d)));
    return focusIds ? layout.edges : [];
  }, [layout.edges, lit, focusIds]);

  const changedCards = useMemo(() => layout.nodes.filter((n) => n.type === "card" && (n.data as CardData).kind === "changed"), [layout.nodes]);

  const hide = useCallback((key: string, label: string) => setHidden((h) => ({ ...h, [key]: label })), []);
  const toggleReviewed = useCallback(
    (unit: string) =>
      setReviewed((r) => {
        const n = new Set(r);
        if (n.has(unit)) n.delete(unit);
        else n.add(unit);
        return n;
      }),
    [],
  );
  const toggleIn = (setter: typeof setExpandedGroups) => (key: string) =>
    setter((s) => {
      const n = new Set(s);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });

  const view: ViewState = {
    m,
    lit,
    focused: !!focusIds,
    selected,
    reviewed,
    marks: ai.marks,
    act: {
      hover: (ids, extent = "chain") => {
        setHoverExtent(extent);
        setHoverIds(ids);
      },
      focus: setFocus,
      hide,
      toggleCard: toggleIn(setExpandedCards),
      toggleGroup: toggleIn(setExpandedGroups),
      toggleReviewed,
      dragging: () => dragging.current,
    },
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.ctrlKey || e.metaKey || e.altKey) return;
      const idx = selected ? changedCards.findIndex((n) => n.id === selected) : -1;
      const sel = idx >= 0 ? changedCards[idx] : null;
      switch (e.key) {
        case "/":
          e.preventDefault();
          searchRef.current?.focus();
          break;
        case "Escape":
          if (menu) setMenu(null);
          else if (focus) setFocus(null);
          else setSelected(null);
          break;
        case "j":
        case "k": {
          if (!changedCards.length) break;
          const next = changedCards[Math.max(0, Math.min(changedCards.length - 1, idx < 0 ? 0 : idx + (e.key === "j" ? 1 : -1)))];
          setSelected(next.id);
          if (prefs.view === "chain") board.current?.reveal(next.id);
          break;
        }
        case "Enter":
          if (sel) setFocus({ type: "unit", id: sel.id.slice(2) });
          break;
        case "h":
          if (sel) {
            const d = sel.data as CardData;
            hide(d.key, d.title);
            const after = changedCards[idx + 1] ?? changedCards[idx - 1];
            setSelected(after && after.id !== sel.id ? after.id : null);
          }
          break;
        case "r":
          if (sel) toggleReviewed(sel.id.slice(2));
          break;
        case "f":
          board.current?.fit();
          break;
        case "?":
          setMenu((x) => (x === "help" ? null : "help"));
          break;
        default:
          return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [changedCards, selected, menu, focus, setFocus, hide, toggleReviewed]);

  const focusLabel = useMemo(() => {
    if (!focus) return null;
    switch (focus.type) {
      case "entity":
        return m.byId.get(focus.id)?.qualname ?? null;
      case "unit":
        return m.units.get(focus.id)?.name ?? null;
      case "impactFile":
        return basename(focus.path);
      case "group": {
        const g = m.groups.get(focus.id);
        return g ? `${g.module ? `${g.module} / ` : ""}${g.pkg}` : null;
      }
      default:
        return null;
    }
  }, [m, focus]);

  const drawerOpen = focus !== null && prefs.view === "chain";
  const treeCollapsed = !!prefs.treeCollapsed;
  const leftInset = treeCollapsed ? 28 : TREE_W;
  const activePath = pathOfFocus(m, focus);
  const fitKey = `${JSON.stringify(focus)}|${JSON.stringify(filters)}|${prefs.detail}|${prefs.view}|${layout.tier}|${Object.keys(hidden).length}|${fitNonce}|${drawerOpen}|${leftInset}`;
  const hiddenEntries = Object.entries(hidden);
  const reviewedCount = [...reviewed].filter((u) => m.units.has(u)).length;

  const onDragStop = (nodes: Node[]) => {
    setTimeout(() => (dragging.current = false), 60);
    if (focusIds) return;
    setPositions((p) => {
      const n = { ...p };
      for (const x of nodes) n[x.id] = { x: Math.round(x.position.x), y: Math.round(x.position.y) };
      return n;
    });
  };

  const paneClick = () => {
    setMenu(null);
    setSelected(null);
  };
  const hasChanges = m.rep.nodes.some((n) => n.change);

  return (
    <View.Provider value={view}>
      <div
        className={`app with-tree ${treeCollapsed ? "tree-collapsed" : ""} ${drawerOpen ? "with-drawer" : ""} ${lit ? "peeking" : ""} ${focusIds ? "focused" : ""}`}
      >
        <TopBar
          model={m}
          focusLabel={focusLabel}
          onClearFocus={() => setFocus(null)}
          onFocus={setFocus}
          reviewed={reviewedCount}
          totalUnits={m.units.size}
          filters={filters}
          onFilters={setFilters}
          prefs={prefs}
          onPrefs={onPrefs}
          onResetLayout={() => {
            setPositions({});
            setExpandedGroups(new Set());
            setExpandedCards(new Set());
            setFitNonce((x) => x + 1);
          }}
          onReload={onReload}
          searchRef={searchRef}
          menu={menu}
          onMenu={setMenu}
          aiButton={ai.button}
        />
        <HeatTree
          model={m}
          focus={focus}
          activePath={activePath}
          collapsed={treeCollapsed}
          onToggleCollapsed={() => onPrefs({ ...prefs, treeCollapsed: !treeCollapsed })}
          onFocus={setFocus}
          onHover={(ids, extent) => {
            setHoverExtent(extent ?? "file");
            setHoverIds(ids);
          }}
        />
        {hasChanges ? (
          prefs.view === "map" ? (
            <MapView
              model={m}
              focus={focus}
              leftInset={leftInset}
              onFocus={setFocus}
              onHover={(ids, extent) => {
            setHoverExtent(extent ?? "file");
            setHoverIds(ids);
          }}
              onOpenChain={(f) => {
                setFocus(f);
                onPrefs({ ...prefs, view: "chain" });
              }}
            />
          ) : (
            <Board
              nodes={layout.nodes}
              edges={edges}
              fitKey={fitKey}
              leftInset={leftInset}
              rightInset={drawerOpen ? 476 : 0}
              onDragStart={() => (dragging.current = true)}
              onDragStop={onDragStop}
              onPaneClick={paneClick}
              api={board}
            />
          )
        ) : (
          <div className="empty" style={{ marginLeft: leftInset }}>
            <Logo size={40} />
            <p>{t.noChanges}</p>
          </div>
        )}
        {layout.counts.units === 0 && hasChanges && prefs.view === "chain" && (
          <div className="board-note" style={{ left: `calc(50% + ${leftInset / 2}px)` }}>
            {t.noMatch}{" "}
            <button className="btn-link" onClick={() => (setFilters(DEFAULT_FILTERS), setHidden({}))}>
              {t.clearFilters}
            </button>
          </div>
        )}
        {layout.tier === "groups" && !focusIds && prefs.view === "chain" && (
          <div className="toast" style={{ left: `calc(50% + ${leftInset / 2}px)` }}>
            {fmt(t.largeBanner, { files: m.rep.summary.files_changed, groups: layout.counts.groups })}
          </div>
        )}
        <div className="dock dock-left" style={{ left: leftInset + 16 }}>
          {hiddenEntries.length > 0 && (
            <div className="pop-anchor">
              <button className="pill" onClick={() => setMenu(menu === "hidden" ? null : "hidden")}>
                <Icon name="eye" size={13} /> {fmt(t.hidden, { n: hiddenEntries.length })}
              </button>
              {menu === "hidden" && (
                <div className="pop pop-up hidden-pop">
                  {hiddenEntries.map(([k, label]) => (
                    <div key={k} className="kv">
                      <span className="mono ellipsis">{label}</span>
                      <button
                        className="btn-link"
                        onClick={() =>
                          setHidden((h) => {
                            const n = { ...h };
                            delete n[k];
                            return n;
                          })
                        }
                      >
                        {t.restore}
                      </button>
                    </div>
                  ))}
                  <div className="pop-sep" />
                  <button className="menu-item" onClick={() => (setHidden({}), setMenu(null))}>
                    {t.restoreAll}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
        {prefs.view === "chain" && (
        <div className="dock dock-right" style={{ right: drawerOpen ? 476 : 16 }}>
          <button className="pill icon" title={`${t.fit} (f)`} onClick={() => board.current?.fit()}>
            <Icon name="fit" size={14} />
          </button>
          <button className="pill icon" title={t.zoomOut} onClick={() => board.current?.zoom(-1)}>
            <Icon name="minus" size={14} />
          </button>
          <button className="pill icon" title={t.zoomIn} onClick={() => board.current?.zoom(1)}>
            <Icon name="plus" size={14} />
          </button>
        </div>
        )}
        {drawerOpen && (
          <Drawer
            model={m}
            focus={focus}
            tab={tab}
            onTab={setTab}
            onFocus={setFocus}
            onPeek={setPeekIds}
            onClose={() => setFocus(null)}
            reviewed={reviewed}
            onToggleReviewed={toggleReviewed}
            ai={ai.panel}
            aiBadge={ai.badge}
          />
        )}
      </div>
    </View.Provider>
  );
}
