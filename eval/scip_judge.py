"""Compare a RippleLens impact report against a SCIP index (compiler-grade ground truth).

  python eval/scip_judge.py --report path/to/impact.json --scip path/to/index.scip
  python eval/scip_judge.py --repos eval/repos.json --case go-cli/help-func [--workdir DIR]

For each changed entity in the report, the judge finds SCIP definition symbols that
overlap the entity's line range, then collects every non-definition occurrence of
those symbols in other files. Precision / recall are computed against the report's
impacted file set (function-level SCIP matching is best-effort via overlapping lines).

SCIP indexers are optional: when a command is missing the case is skipped. Product
code never depends on SCIP — this is evaluation only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from scip_reader import DEFINITION, Occurrence, read_index


@dataclass
class Scores:
    precision: float
    recall: float
    true_positive: int
    false_positive: int
    false_negative: int
    scip_files: int
    report_files: int


def _defs_by_path(docs) -> dict[str, list[Occurrence]]:
    out: dict[str, list[Occurrence]] = defaultdict(list)
    for d in docs:
        for o in d.occurrences:
            if o.roles & DEFINITION:
                out[d.path].append(o)
    return out


def _refs_by_symbol(docs) -> dict[str, set[str]]:
    """symbol -> set of relative paths that reference it (excluding pure definitions)."""
    out: dict[str, set[str]] = defaultdict(set)
    for d in docs:
        for o in d.occurrences:
            if o.roles & DEFINITION and not (o.roles & ~DEFINITION):
                continue
            out[o.symbol].add(d.path)
    return out


def symbols_for_entity(defs: list[Occurrence], start: int, end: int) -> set[str]:
    """0-based inclusive line range → SCIP definition symbols overlapping it."""
    return {o.symbol for o in defs if start <= o.line <= end}


def judge(report: dict, scip_path: Path) -> Scores:
    docs = read_index(scip_path)
    defs = _defs_by_path(docs)
    refs = _refs_by_symbol(docs)

    seed_symbols: set[str] = set()
    for n in report.get("nodes", []):
        if not n.get("change"):
            continue
        path = n["path"].replace("\\", "/")
        start, end = n["lines"][0] - 1, n["lines"][1] - 1
        seed_symbols |= symbols_for_entity(defs.get(path, []), start, end)

    scip_files: set[str] = set()
    for sym in seed_symbols:
        scip_files |= refs.get(sym, set())
    # a definition site is not "impacted"
    changed_paths = {n["path"].replace("\\", "/") for n in report.get("nodes", []) if n.get("change")}
    scip_files -= changed_paths

    report_files = {n["path"].replace("\\", "/") for n in report.get("nodes", []) if not n.get("change") and n.get("depth", 0) > 0}
    if not report_files:
        report_files = set(report.get("summary", {}).get("impacted_files_list") or [])
    # fall back to unique paths of non-seed nodes
    if not report_files:
        report_files = {
            n["path"].replace("\\", "/")
            for n in report.get("nodes", [])
            if not n.get("change")
        }

    tp = len(report_files & scip_files)
    fp = len(report_files - scip_files)
    fn = len(scip_files - report_files)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return Scores(precision, recall, tp, fp, fn, len(scip_files), len(report_files))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _clone(url: str, dest: Path, ref: str) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True)
    _git(dest, "fetch", "--tags", "--quiet")
    _git(dest, "checkout", "--force", "--quiet", ref)
    return dest


def run_case(repo_cfg: dict, case: dict, workdir: Path) -> dict:
    dest = workdir / repo_cfg["id"]
    _clone(repo_cfg["url"], dest, case["head"])
    scip_cmd = list(repo_cfg.get("scip", {}).get("command") or [])
    if not scip_cmd:
        return {"id": case["id"], "skipped": "no scip command"}
    exe = scip_cmd[0]
    if exe == "npx":
        pass
    elif shutil.which(exe) is None:
        return {"id": case["id"], "skipped": f"{exe} not on PATH"}

    index = dest / "index.scip"
    if index.exists():
        index.unlink()
    try:
        subprocess.run(scip_cmd, cwd=str(dest), check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        return {"id": case["id"], "skipped": f"scip failed: {e}"}

    sys.path.insert(0, str(ROOT / "src"))
    from ripplelens.analysis.impact import Options
    from ripplelens.analysis.pipeline import analyze

    rep = analyze(dest, case["base"], case["head"], Options(diagnostics=True), progress=lambda m: None)
    scores = judge(rep, index)
    return {
        "id": f"{repo_cfg['id']}/{case['id']}",
        "precision": round(scores.precision, 3),
        "recall": round(scores.recall, 3),
        "true_positive": scores.true_positive,
        "false_positive": scores.false_positive,
        "false_negative": scores.false_negative,
        "scip_files": scores.scip_files,
        "report_files": scores.report_files,
        "entities_changed": rep["summary"]["entities_changed"],
        "impacted": rep["summary"]["impacted"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--report", type=Path, help="impact.json from ripplelens analyze")
    ap.add_argument("--scip", type=Path, help="SCIP index to compare against")
    ap.add_argument("--repos", type=Path, default=ROOT / "eval" / "repos.json")
    ap.add_argument("--case", help="repoId/caseId; default: all cases that have their indexer")
    ap.add_argument("--workdir", type=Path, default=Path.home() / ".cache" / "ripplelens-eval")
    ap.add_argument("--json", action="store_true", help="print machine-readable results")
    args = ap.parse_args()

    if args.report and args.scip:
        rep = json.loads(args.report.read_text(encoding="utf-8"))
        s = judge(rep, args.scip)
        out = {
            "precision": round(s.precision, 3),
            "recall": round(s.recall, 3),
            "true_positive": s.true_positive,
            "false_positive": s.false_positive,
            "false_negative": s.false_negative,
            "scip_files": s.scip_files,
            "report_files": s.report_files,
        }
        print(json.dumps(out, indent=2) if args.json else out)
        return 0

    repos = json.loads(args.repos.read_text(encoding="utf-8"))
    results = []
    for repo in repos:
        for case in repo["cases"]:
            key = f"{repo['id']}/{case['id']}"
            if args.case and args.case != key:
                continue
            print(f"→ {key}", flush=True)
            results.append(run_case(repo, case, args.workdir))
            print(results[-1], flush=True)

    if args.json:
        print(json.dumps(results, indent=2))
    # non-zero only when a run completed and recall is catastrophically low
    bad = [r for r in results if "recall" in r and r["recall"] < 0.3 and r.get("scip_files", 0) > 0]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
