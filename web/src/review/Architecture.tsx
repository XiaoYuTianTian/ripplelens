import type { ArchDep, Architecture } from "../types";
import { basename } from "../model";
import { fmt, useT } from "../i18n";

function DepLine({ d, sign, onOpen }: { d: ArchDep; sign: "+" | "−"; onOpen?: (path: string) => void }) {
  const t = useT();
  return (
    <div className={`kv arch-dep ${onOpen ? "clickable" : ""}`} onClick={() => onOpen?.(d.file)} title={d.code}>
      <span className={`arch-sign ${sign === "+" ? "add" : "del"}`}>{sign}</span>
      <span className="mono ellipsis">
        {d.from_module} → {d.external ? `${t.arch.external}:${d.to_module.replace(/^external:/, "")}` : d.to_module}
      </span>
      <span className="muted ellipsis kv-extra">
        {basename(d.file)}:{d.line}
      </span>
    </div>
  );
}

const crossing = (d: ArchDep) => d.cross_module || d.external;

/** Module-level evidence for the review guide; nothing is rendered when the change does not touch the structure. */
export function ArchOverview({ arch, onOpen }: { arch: Architecture | null | undefined; onOpen: (path: string) => void }) {
  const t = useT();
  if (!arch) return null;
  const added = arch.deps_added.filter(crossing);
  const removed = arch.deps_removed.filter(crossing);
  const contracts = arch.contracts.filter((c) => c.external_modules.length);
  if (!arch.violations.length && !arch.cycles.length && !added.length && !removed.length && !contracts.length) return null;
  return (
    <section className={`d-section arch ${arch.violations.length || arch.cycles.length ? "d-danger" : ""}`}>
      <div className="d-sub">{t.arch.title}</div>
      <p className="d-hint">{arch.rules.source ? fmt(t.arch.rules, { src: arch.rules.source }) : t.arch.noRules}</p>
      {arch.rules.error && <div className="callout callout-warn">{arch.rules.error}</div>}
      {arch.violations.length > 0 && (
        <>
          <div className="arch-head">
            {t.arch.violations} <span>{arch.violations.length}</span>
          </div>
          {arch.violations.map((v, i) => (
            <div key={i} className="kv clickable arch-violation" onClick={() => onOpen(v.file)} title={v.code}>
              <span className="chip chip-danger">{v.rule}</span>
              <span className="mono ellipsis">
                {v.from} → {v.to}
              </span>
              <span className="muted ellipsis kv-extra">{v.reason || v.detail}</span>
            </div>
          ))}
        </>
      )}
      {arch.cycles.length > 0 && (
        <>
          <div className="arch-head">
            {t.arch.cycles} <span>{arch.cycles.length}</span>
          </div>
          {arch.cycles.map((c, i) => (
            <div key={i} className="kv mono arch-cycle">
              {c.join(" → ")}
            </div>
          ))}
        </>
      )}
      {added.length > 0 && (
        <>
          <div className="arch-head">
            {t.arch.depsAdded} <span>{added.length}</span>
          </div>
          {added.slice(0, 20).map((d, i) => (
            <DepLine key={i} d={d} sign="+" onOpen={onOpen} />
          ))}
        </>
      )}
      {removed.length > 0 && (
        <>
          <div className="arch-head">
            {t.arch.depsRemoved} <span>{removed.length}</span>
          </div>
          {removed.slice(0, 20).map((d, i) => (
            <DepLine key={i} d={d} sign="−" />
          ))}
        </>
      )}
      {contracts.length > 0 && (
        <>
          <div className="arch-head">
            {t.arch.contracts} <span>{contracts.length}</span>
          </div>
          {contracts.slice(0, 20).map((c) => (
            <div key={c.id} className="kv clickable" onClick={() => onOpen(c.file)}>
              <span className="mono ellipsis">{c.qualname}</span>
              <span className="muted ellipsis kv-extra">{fmt(t.arch.reaches, { m: c.external_modules.join(", ") })}</span>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

export function ArchInFile({ arch, path }: { arch: Architecture | null | undefined; path: string }) {
  const t = useT();
  if (!arch) return null;
  const added = arch.deps_added.filter((d) => d.file === path && crossing(d));
  const removed = arch.deps_removed.filter((d) => d.file === path && crossing(d));
  const violations = arch.violations.filter((v) => v.file === path);
  if (!added.length && !removed.length && !violations.length) return null;
  return (
    <section className={`d-section arch ${violations.length ? "d-danger" : ""}`}>
      <div className="d-sub">{t.arch.inFile}</div>
      {violations.map((v, i) => (
        <div key={`v${i}`} className="kv arch-violation" title={v.code}>
          <span className="chip chip-danger">{v.rule}</span>
          <span className="mono ellipsis">{v.import}</span>
          <span className="muted ellipsis kv-extra">{v.reason || v.detail}</span>
        </div>
      ))}
      {added.map((d, i) => (
        <DepLine key={`a${i}`} d={d} sign="+" />
      ))}
      {removed.map((d, i) => (
        <DepLine key={`r${i}`} d={d} sign="−" />
      ))}
    </section>
  );
}
