from __future__ import annotations

import json
from pathlib import Path

from ripplelens.analysis.architecture import Resolver, analyze_architecture, check_rules, extract_imports, load_rules


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_extract_imports_per_language():
    ts = 'import { a } from "./a";\nimport type { B } from "@shop/core";\nimport {\n  x,\n} from "../x";\nconst y = require("lodash");\n'
    assert [i.spec for i in extract_imports("ts", ts)] == ["./a", "@shop/core", "../x", "lodash"]
    py = "from . import util, helpers\nfrom ..core.money import round\nimport os, shop.api as api\n"
    assert [i.spec for i in extract_imports("python", py)] == [".util", ".helpers", "..core.money", "os", "shop.api"]
    go = 'package x\n\nimport (\n\t"fmt"\n\tm "example.com/shop/money"\n)\nimport "example.com/shop/cart"\n'
    assert [i.spec for i in extract_imports("go", go)] == ["fmt", "example.com/shop/money", "example.com/shop/cart"]
    java = "package a;\nimport java.util.List;\nimport static shop.core.Money.round;\nimport shop.api.*;\n"
    assert [i.spec for i in extract_imports("java", java)] == ["java.util.List", "shop.core.Money.round", "shop.api.*"]
    c = '#include <stdio.h>\n#include "pricing/discount.h"\n'
    assert [(i.spec, i.line) for i in extract_imports("c", c)] == [("pricing/discount.h", 2)]


def test_resolver_workspace_packages_and_go_modules(tmp_path: Path):
    files = {
        "packages/core/package.json": json.dumps({"name": "@shop/core"}),
        "packages/core/src/index.ts": "",
        "packages/api/src/orders.ts": "",
        "svc/go.mod": "module example.com/shop\n\ngo 1.22\n",
        "svc/money/money.go": "package money\n",
        "svc/cart/cart.go": "package cart\n",
        "src/main/java/shop/core/Money.java": "",
        "lib/x.h": "",
    }
    _write(tmp_path, files)
    r = Resolver(tmp_path, files)
    assert r.resolve("ts", "packages/api/src/orders.ts", "@shop/core") == ("packages/core/src/index.ts", None)
    assert r.resolve("ts", "packages/api/src/orders.ts", "lodash") == (None, "lodash")
    assert r.resolve("go", "svc/cart/cart.go", "example.com/shop/money") == ("svc/money/money.go", None)
    assert r.resolve("go", "svc/cart/cart.go", "fmt") == (None, None)
    assert r.resolve("java", "src/main/java/shop/api/A.java", "shop.core.Money.round") == ("src/main/java/shop/core/Money.java", None)
    assert r.resolve("c", "src/a.c", "x.h") == ("lib/x.h", None)


def test_rules_deny_and_layers(tmp_path: Path):
    _write(
        tmp_path,
        {
            ".ripplelens/architecture.json": json.dumps(
                {
                    "layers": ["apps/*", "packages/api", "packages/core"],
                    "deny": [{"from": "packages/core", "to": ["packages/api"], "reason": "core stays business-free"}],
                }
            )
        },
    )
    rules = load_rules(tmp_path)
    assert rules["source"] == ".ripplelens/architecture.json"
    up = {"from_dir": "packages/core/src", "to_dir": "packages/api/src", "external": False}
    hits = {v["rule"] for v in check_rules(rules, up)}
    assert hits == {"deny", "layer"}
    down = {"from_dir": "apps/web/src", "to_dir": "packages/core/src", "external": False}
    assert check_rules(rules, down) == []


def test_no_rules_reports_facts_without_violations(tmp_path: Path):
    _write(tmp_path, {"packages/core/package.json": "{}", "packages/api/package.json": "{}"})
    assert load_rules(tmp_path)["source"] is None


def _shop(tmp_path: Path, rules: dict | None) -> dict:
    base = {
        "packages/core/package.json": json.dumps({"name": "@shop/core"}),
        "packages/api/package.json": json.dumps({"name": "@shop/api"}),
        "packages/core/src/money.ts": "export const round = (x: number) => x;\n",
        "packages/api/src/orders.ts": 'import { round } from "@shop/core";\nexport const quote = () => round(1);\n',
    }
    head = dict(base)
    head["packages/core/src/money.ts"] = 'import { quote } from "@shop/api";\nexport const round = (x: number) => x + quote();\n'
    _write(tmp_path, head)
    if rules is not None:
        _write(tmp_path, {".ripplelens/architecture.json": json.dumps(rules)})
    files = [{"path": "packages/core/src/money.ts", "status": "M", "old_path": None}]
    return analyze_architecture(
        repo_root=tmp_path,
        head_root=tmp_path,
        files=files,
        nodes=[],
        edges=[],
        read_base=lambda rel: base.get(rel),
        read_head=lambda rel: (tmp_path / rel).read_text(encoding="utf-8"),
        repo_files=list(head),
    )


def test_added_cross_module_dependency_cycle_and_violation(tmp_path: Path):
    arch = _shop(tmp_path, {"deny": [{"from": "packages/core", "to": "packages/api", "reason": "core stays business-free"}]})
    [dep] = arch["deps_added"]
    assert (dep["from_module"], dep["to_module"], dep["line"], dep["cross_module"]) == ("packages/core", "packages/api", 1, True)
    assert arch["cycles"] and set(arch["cycles"][0]) == {"packages/core", "packages/api"}
    assert {"packages/core", "packages/api"} <= set(arch["modules"])
    [v] = arch["violations"]
    assert v["rule"] == "deny" and v["file"] == "packages/core/src/money.ts" and v["reason"] == "core stays business-free"


def test_without_rules_no_violations_but_facts(tmp_path: Path):
    arch = _shop(tmp_path, None)
    assert arch["summary"]["has_rules"] is False
    assert arch["violations"] == []
    assert arch["summary"]["cross_module_added"] == 1


def test_contract_reaching_other_modules(tmp_path: Path):
    _write(tmp_path, {"core/go.mod": "module c\n", "api/go.mod": "module a\n", "core/m.go": "", "api/q.go": ""})
    nodes = [
        {"id": "s", "path": "core/m.go", "qualname": "Round", "change": "signature", "lines": [3, 5], "breaking": []},
        {"id": "t", "path": "api/q.go", "qualname": "Quote", "change": None, "lines": [7, 9], "breaking": [{"line": 8, "message": "x"}]},
    ]
    arch = analyze_architecture(
        repo_root=tmp_path, head_root=tmp_path, files=[], nodes=nodes,
        edges=[{"source": "s", "target": "t", "kind": "call", "sites": [8]}],
        read_base=lambda rel: None, read_head=lambda rel: None, repo_files=["core/m.go", "api/q.go"],
    )
    [c] = arch["contracts"]
    assert c["external_modules"] == ["api"] and c["compile_errors"] == 1
