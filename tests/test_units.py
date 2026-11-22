from ripplelens.analysis.changes import hover_signature, match_entities, normalize_code
from ripplelens.analysis.symbols import CLASS, FUNCTION, METHOD, VARIABLE, SymbolIndex, flatten
from ripplelens.git.diff import parse_unified

DIFF = """diff --git a/lib/lib.go b/lib/lib.go
index 1111111..2222222 100644
--- a/lib/lib.go
+++ b/lib/lib.go
@@ -17 +17 @@ func Helper(x int) int {
-func Helper(x int) int {
+func Helper(x, y int) int {
@@ -30,2 +30,0 @@ func Total(shapes []Shape) float64 {
-	// gone
-	// gone
diff --git a/new.py b/new.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new.py
@@ -0,0 +1,3 @@
+def f():
+    pass
+
diff --git a/old.ts b/renamed.ts
similarity index 90%
rename from old.ts
rename to renamed.ts
index 4444444..5555555 100644
--- a/old.ts
+++ b/renamed.ts
@@ -1 +1 @@
-export const A = 1;
+export const A = 2;
diff --git a/gone.c b/gone.c
deleted file mode 100644
index 6666666..0000000
--- a/gone.c
+++ /dev/null
@@ -1,2 +0,0 @@
-int g(void) {
-}
"""


def test_parse_unified():
    files = parse_unified(DIFF)
    assert [f.status for f in files] == ["M", "A", "R", "D"]
    go = files[0]
    assert go.path == "lib/lib.go"
    assert go.changed_new_lines() == {16}
    assert go.changed_old_lines() == {16, 29, 30}
    assert files[1].new_path == "new.py" and files[1].old_path is None
    assert files[1].changed_new_lines() == {0, 1, 2}
    assert files[2].old_path == "old.ts" and files[2].new_path == "renamed.ts" and files[2].similarity == 90
    assert files[3].new_path is None and files[3].changed_old_lines() == {0, 1}


def test_normalize_code():
    assert normalize_code("foo( a,b )\n", False) == normalize_code("foo(a, b)", False)
    assert normalize_code("x = 1\n\n  # c\n", False) != normalize_code("x = 2", False)
    # python indentation is semantic
    a = "def f():\n    if x:\n        y()\n    z()"
    b = "def f():\n    if x:\n        y()\n        z()"
    assert normalize_code(a, True) != normalize_code(b, True)
    assert normalize_code("  def f():\n      y()", True) == normalize_code("def f():\n    y()", True)


def test_hover_signature_variants():
    md = {"contents": {"kind": "markdown", "value": "```go\nfunc Helper(x int) int\n```\n\nDocs here"}}
    assert hover_signature(md) == "func Helper(x int) int"
    marked = {"contents": [{"language": "java", "value": "int fx.lib.Lib.helper(int x)"}, "Javadoc"]}
    assert hover_signature(marked) == "int fx.lib.Lib.helper(int x)"
    clangd = {"contents": {"kind": "markdown", "value": "### function `helper`\n\n---\n→ `int`\n\n---\n```cpp\n// In namespace fx\nint helper(int x)\n```"}}
    assert hover_signature(clangd) == "// In namespace fx int helper(int x)"
    assert hover_signature(None) == ""
    wrapped = {"contents": {"value": "```python\n(function) def helper(\n    x: int,\n    y: int\n) -> int\n```"}}
    flat = {"contents": {"value": "```python\n(function) def helper(x: int, y: int) -> int\n```"}}
    assert hover_signature(wrapped) == hover_signature(flat) == "(function) def helper(x: int, y: int) -> int"


def _sym(name, kind, start, end, children=None, sel=None):
    return {
        "name": name,
        "kind": kind,
        "range": {"start": {"line": start, "character": 0}, "end": {"line": end, "character": 1}},
        "selectionRange": {"start": {"line": sel if sel is not None else start, "character": 4},
                           "end": {"line": sel if sel is not None else start, "character": 8}},
        "children": children or [],
    }


def test_flatten_hierarchical_and_index():
    syms = [
        _sym("Circle", CLASS, 0, 10, [_sym("area", METHOD, 2, 4, [_sym("tmp", VARIABLE, 3, 3)]), _sym("r", 8, 6, 6)]),
        _sym("helper", FUNCTION, 12, 14),
        _sym("(*Server).Handle", METHOD, 16, 20),
    ]
    ents = flatten(syms, "x")
    idx = SymbolIndex(ents)
    names = {e.qualname for e in idx.entities}
    assert names == {"Circle", "Circle.area", "Circle.r", "helper", "Server.Handle"}  # local tmp excluded
    assert idx.innermost(3).qualname == "Circle.area"
    assert idx.innermost(5).qualname == "Circle"
    assert idx.innermost(11) is None
    assert idx.enclosing_for_impact(6).qualname == "Circle.r"  # field initializer: no callable, falls back
    assert idx.enclosing_for_impact(8).qualname == "Circle"


def test_flatten_symbol_information():
    flat = [
        {"name": "A", "kind": CLASS, "location": {"uri": "file:///x.py", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 9, "character": 0}}}},
        {"name": "m", "kind": METHOD, "containerName": "A", "location": {"uri": "file:///x.py", "range": {"start": {"line": 2, "character": 4}, "end": {"line": 4, "character": 0}}}},
    ]
    idx = SymbolIndex(flatten(flat, "x.py"))
    assert idx.innermost(3).qualname == "A.m"


def test_match_entities_overloads():
    base = flatten([_sym("f", FUNCTION, 0, 2), _sym("f", FUNCTION, 4, 6), _sym("g", FUNCTION, 8, 9)], "x")
    head = flatten([_sym("f", FUNCTION, 0, 2), _sym("g", FUNCTION, 4, 5), _sym("h", FUNCTION, 7, 8)], "x")
    pairs = match_entities(base, head)
    as_names = [((b.qualname if b else None), (h.qualname if h else None)) for b, h in pairs]
    assert ("f", "f") in as_names
    assert ("f", None) in as_names
    assert ("g", "g") in as_names
    assert (None, "h") in as_names


def test_true_case_restores_disk_spelling(tmp_path):
    import os
    import sys

    import pytest

    from ripplelens.lsp.client import true_case

    if sys.platform not in ("win32", "darwin"):
        pytest.skip("case-sensitive file system")
    f = tmp_path / "MixedCase" / "orderService.ts"
    f.parent.mkdir()
    f.write_text("", encoding="utf-8")
    assert true_case(str(f).lower()).endswith(os.path.join("MixedCase", "orderService.ts"))


def test_clean_name_strips_jdtls_params():
    from ripplelens.analysis.symbols import _clean_name

    assert _clean_name("area()") == "area"
    assert _clean_name("area(double)") == "area"
    assert _clean_name("total(List<Shape>)") == "total"
    assert _clean_name("(*Foo).Bar") == "(*Foo).Bar"


def test_repo_tree_lists_git_files(tmp_path):
    import subprocess

    from ripplelens.repo_tree import tree_payload

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.ts").write_text("x", encoding="utf-8")
    (tmp_path / "pkg" / "b.ts").write_text("y", encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "b"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    payload = tree_payload(tmp_path)
    paths = {e["path"] for e in payload["entries"]}
    assert "pkg" in paths and "pkg/a.ts" in paths
    assert any(e["path"] == "pkg" and e["kind"] == "dir" for e in payload["entries"])
