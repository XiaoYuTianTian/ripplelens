"""Run tests/scenarios.py end to end against real language servers.

  python eval/run_scenarios.py [name-prefix ...]
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from scenarios import SCENARIOS, check, materialize

from ripplelens.analysis.impact import Options
from ripplelens.analysis.pipeline import analyze
from ripplelens.lsp.pool import ServerPool
from ripplelens.lsp.registry import Registry


def main() -> int:
    wanted = sys.argv[1:]
    base = Path(tempfile.gettempdir()) / "ripplelens-scenarios"
    base.mkdir(exist_ok=True)
    out_dir = ROOT / "eval" / "scenario-results"
    out_dir.mkdir(exist_ok=True)
    results = []
    for sc in SCENARIOS:
        if wanted and not any(sc.name.startswith(w) for w in wanted):
            continue
        repo = materialize(sc, base / sc.name)
        t0 = time.monotonic()
        pool = ServerPool(Registry.load(repo))
        try:
            rep = analyze(repo, options=Options(), pool=pool, progress=lambda m: None)
        except Exception as e:
            print(f"[ERROR] {sc.name}: {type(e).__name__}: {e}")
            results.append((sc.name, "error"))
            continue
        finally:
            pool.shutdown()
        (out_dir / f"{sc.name}.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        unavailable = [s for s in rep["servers"] if s["status"] == "unavailable"]
        if unavailable:
            print(f"[SKIP] {sc.name}: {unavailable[0]['reason']}")
            results.append((sc.name, "skip"))
            continue
        fails = check(sc, rep)
        status = "PASS" if not fails else "FAIL"
        print(f"[{status}] {sc.name} ({time.monotonic() - t0:.1f}s)")
        for f in fails:
            print(f"        {f}")
        results.append((sc.name, status.lower()))
    bad = [r for r in results if r[1] in ("fail", "error")]
    print(f"{sum(1 for r in results if r[1] == 'pass')} passed, {len(bad)} failed, "
          f"{sum(1 for r in results if r[1] == 'skip')} skipped")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
