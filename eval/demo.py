"""Create a demo repo with several kinds of changes and open the review UI.

  python eval/demo.py [shop|python|ts] [--no-open] [--port N] [--dest DIR]

`shop` is a three-module TypeScript monorepo (core / api / web) used for the README
screenshots: a signature change with compile errors at two call sites, a constant change,
a body change in a shared helper, an Angular-style component spread over .ts/.html/.scss,
and a core -> api import that breaks the rules in .ripplelens/architecture.toml.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from scenarios import Scenario, git, materialize, rmtree

Edits = dict[str, list[tuple[str, str]]]

SHOP_EDITS: Edits = {
    "packages/core/src/pricing/discount.ts": [
        ("export const TAX_RATE = 0.08;", "export const TAX_RATE = 0.1;"),
        (
            "export function applyDiscount(price: number, pct: number): number {\n  return round(price * (1 - pct / 100));",
            "export function applyDiscount(price: number, discount: Discount): number {\n"
            "  return round(price * (1 - Math.min(discount.pct, 90) / 100));",
        ),
    ],
    "packages/core/src/pricing/money.ts": [
        ("return Math.round(value * 100) / 100;", "return Math.round((value + Number.EPSILON) * 100) / 100;"),
    ],
    "packages/core/src/cart/cart.ts": [
        ("applyDiscount(sub, this.discount.pct)", "applyDiscount(sub, this.discount)"),
        (
            'import { round } from "../pricing/money";',
            'import { round } from "../pricing/money";\nimport { flashSale } from "../../../api/src/billing/promo";',
        ),
        (
            "  setDiscount(d: Discount): void {",
            "  saleTotal(): number {\n"
            "    return round(flashSale(this.lines.map((l) => l.price * l.qty)).reduce((s, p) => s + p, 0));\n"
            "  }\n\n"
            "  setDiscount(d: Discount): void {",
        ),
    ],
    "packages/core/src/pricing/discount.spec.ts": [
        ("applyDiscount(100, 10) === 90", 'applyDiscount(100, { code: "T", pct: 10 }) === 90'),
    ],
    "apps/web/src/app/checkout/checkout.component.ts": [
        ("return formatMoney(this.cart.total());", "return `${this.cart.count()} items · ${formatMoney(this.cart.total())}`;"),
    ],
    "apps/web/src/app/checkout/checkout.component.html": [
        ('<p class="total">{{ totalLabel }}</p>', '<p class="total" data-test="total">{{ totalLabel }}</p>'),
    ],
    "apps/web/src/app/checkout/checkout.component.scss": [
        ("font-weight: 600;", "font-weight: 650;\n  font-variant-numeric: tabular-nums;"),
    ],
}

DEMOS: dict[str, Scenario | tuple[Path, Edits]] = {
    "shop": (Path(__file__).resolve().parent / "demo" / "shop", SHOP_EDITS),
    "python": Scenario(
        "demo-python",
        "python",
        {
            "fx/lib.py": [
                ("def helper(x: int) -> int:\n    return x + LIMIT", "def helper(x: int, y: int) -> int:\n    return x + y + LIMIT"),
                ("return 3.14 * self.r * self.r", "return 3.14159 * self.r * self.r"),
                ("LIMIT = 10", "LIMIT = 20"),
            ]
        },
        changed={},
        impacted=set(),
    ),
    "ts": Scenario(
        "demo-ts",
        "ts",
        {
            "src/lib.ts": [
                ("export function helper(x: number): number {\n  return x + LIMIT;", "export function helper(x: number, y = 0): number {\n  return x + y + LIMIT;"),
                ("return 3.14 * this.r * this.r;", "return Math.PI * this.r * this.r;"),
            ]
        },
        changed={},
        impacted=set(),
    ),
}


def build_repo(src: Path, edits: Edits, dest: Path) -> Path:
    """Copy `src` into a fresh git repo, commit it, then apply `edits` to the working tree."""
    rmtree(dest)
    shutil.copytree(src, dest)
    dest = dest.resolve()
    git(dest, "init", "-q", "-b", "main")
    git(dest, "-c", "user.email=demo@ripplelens.dev", "-c", "user.name=demo", "add", "-A")
    git(dest, "-c", "user.email=demo@ripplelens.dev", "-c", "user.name=demo", "-c", "core.autocrlf=false", "commit", "-q", "-m", "base")
    for rel, reps in edits.items():
        p = dest / rel
        text = p.read_text(encoding="utf-8")
        for old, new in reps:
            if old not in text:
                raise SystemExit(f"demo edit anchor not found in {rel}: {old!r}")
            text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8", newline="\n")
    return dest


def make(name: str, dest: Path | None = None) -> Path:
    demo = DEMOS[name]
    dest = dest or Path(tempfile.gettempdir()) / "ripplelens-demo" / name
    if isinstance(demo, Scenario):
        return materialize(demo, dest)
    return build_repo(demo[0], demo[1], dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("demo", nargs="?", default="shop", choices=sorted(DEMOS))
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--dest", type=Path, default=None, help="where to create the demo repo")
    args = ap.parse_args()
    repo = make(args.demo, args.dest)
    from ripplelens.cli import main as cli

    argv = ["review", "--repo", str(repo), "--port", str(args.port)]
    if args.no_open:
        argv.append("--no-open")
    return cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
