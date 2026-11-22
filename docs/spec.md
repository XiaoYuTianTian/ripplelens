# RippleLens spec (v0)

**RippleLens** (package / CLI / plugin id: `ripplelens`) answers one question for
a reviewer, human or AI: **which functions does this diff actually affect, and
how sure are we?** Every fact comes from a compiler-backed language server;
there is no text matching and no tree-sitter graph. An optional AI review panel
can stream findings from a configured OpenAI-compatible or Anthropic endpoint;
it is off until the user enables it.

## 1. Languages and servers

| Language | Server | Notes |
|---|---|---|
| Go | gopls | needs the `go` toolchain; `go.work` for multi-module repos |
| TypeScript / JavaScript | TypeScript ≥ 7: `tsc --lsp --stdio` (native); otherwise `typescript-language-server` + project tsserver | the project's own TypeScript decides |
| Python | basedpyright (fallback: pyright) | `diagnosticMode=openFilesOnly` |
| Java | Eclipse JDT LS | JDK 21+ (JDK 25 verified); Maven/Gradle import |
| C / C++ | clangd | needs `compile_commands.json` (background index) |

Servers are configured by a registry that uses Claude Code's `.lsp.json` entry
shape (`command`, `args`, `extensionToLanguage`, `env`, `initializationOptions`,
`settings`, `startupTimeout`). A project `.lsp.json` or `.ripplelens/lsp.json`
overrides builtins by name. ripplelens-only keys live under `"ripplelens"`.

### LSP client

ripplelens has its own small JSON-RPC/stdio client (`ripplelens.lsp.client`)
instead of SolidLSP. The spike found SolidLSP is not on PyPI, imports GPL-3.0
code from Serena (`serena.util.*`), pulls in `sensai`/`ruamel.yaml`, and its core
class exposes neither call hierarchy nor progress-based readiness — the two
things ripplelens depends on most. The client needs ~500 lines and no
dependencies.

## 2. Spike results (Windows 10, fixtures in `tests/fixtures/<lang>`)

Each fixture has the same shape: `helper` called by `a`, `b` and passed as a
value in `c`; `Shape.area` implemented by `Circle.area`; `total` calls
`area` through the interface; `d` calls `Circle.area` directly; a constant used
by `helper`.

| Check | clangd 23 (C++) | clangd 23 (C) | tsc 7 native | basedpyright 1.40 | gopls | jdtls (JDK 25) |
|---|---|---|---|---|---|---|
| incomingCalls: direct callers | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (query base after signature change) |
| references: function used as value | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| incomingCalls also lists the value use | ✓ | ✓ | ✗ | ✗ | ✓ | ✓ |
| incomingCalls on override includes interface-dispatch caller | ✓ | n/a (fn ptr) | ✓ | ✗ | ✓ | ✓ |
| interface method: incomingCalls | ✓ | n/a | ✓ | ✓ | ✓ | ✓ on base revision |
| interface method: implementation | ✓ | n/a | ✓ | ✓ | ✓ | ✓ on base revision |
| constant: references | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ready after | 2.1 s | 2.1 s | 1.2 s | 1.9 s | ~1 s | ~6–12 s |

(`CI` = measured by `.github/workflows/ci.yml` on ubuntu-latest and windows-latest;
see `eval/spike-results/`.)

Findings that shaped the design:

1. **incomingCalls alone misses value uses** (tsc, pyright). Callers are therefore
   `incomingCalls ∪ (references − call sites)`; the extra ones are `ref` edges.
2. **Overrides do not see base-dispatch callers in pyright.** ripplelens walks *up*
   from a changed method to the methods it overrides (typeHierarchy when the
   server has it; otherwise base classes named in the class header resolved with
   `textDocument/definition`; for gopls, `implementation` on a concrete method
   returns the interface methods it satisfies) and adds their callers as
   `dispatch` edges with confidence `possible`.
3. **Function pointers in C are invisible to every static tool.** They are listed
   as a blind spot, never guessed.
4. **Readiness is server specific.** clangd only starts its background index after
   the first file loads the compilation database, so a quiet period before that is
   not readiness: ripplelens waits for the `indexing` progress to begin and end.
   jdtls is ready on `language/status: ServiceReady`. Others: no active
   `$/progress` and 1 s of silence, then a `documentSymbol` probe.
5. **clangd silently skips compile commands whose `directory` is a non-canonical
   path** (Windows 8.3 short names, symlinks). `doctor` checks for it.
6. **tsc 7 uses pull diagnostics** (`textDocument/diagnostic`); others push
   `publishDiagnostics`. The client supports both.
7. TypeScript 7 removed `tsserver.js`; `typescript-language-server` cannot run it.
   ripplelens picks the native server when the project's TypeScript is ≥ 7.
8. **jdtls method names include the parameter list** (`area()` vs `area(double)`).
   Names are stripped before pairing so a signature change is one entity, not
   removed+added. After a signature change, head-side
   `incomingCalls`/`implementation` go empty (call sites and overrides no longer
   bind); RippleLens therefore queries the **base revision** for
   `signature`/`renamed` seeds (same path as removed entities).
9. **Windows path case:** `uri_to_path` uses `normcase`; report paths are restored
   to on-disk spelling via `true_case` so cards show `orderService.ts`, not
   `orderservice.ts`.

## 3. Pipeline

```
git diff -U0 -M (base..head) ─▶ changed files + line ranges
      │
      ▼  per file, via its server
documentSymbol(head)   documentSymbol(base text as overlay at same URI)
      │                         │
      └──── innermost entity per changed line, pair base↔head by (qualname, kind) ───▶ EntityChange
      ▼
classification (hover signature + normalized text)
      ▼
traversal (incomingCalls, references, implementation, dispatch) with scoring + truncation
      ▼
breakage check (diagnostics at call sites of signature/removed/value/type changes)
      ▼
impact.json ─▶ web UI · Markdown · MCP · AI review context
```

### 3.1 What is compared

* `--base` omitted: working tree vs `HEAD` when dirty, else `HEAD~1..HEAD`.
* `--base REF`: compared at `merge-base(REF, head)` (pull-request semantics).
* `--head` omitted: working tree including untracked, non-ignored files.
* A head commit other than the checked-out one is analyzed in a detached
  worktree under the cache directory.

### 3.2 Entities

Entities are documentSymbol nodes of kind function, method, constructor,
operator, class, interface, struct, enum, field, property, constant, variable,
enum member — excluding locals (data symbols nested in a callable). A changed
line belongs to the innermost entity containing it; lines outside every entity
(imports, top-level statements) are reported as module-level lines.

Base entities are obtained from the **same head server** by opening the base
text at the file's URI, reading symbols and hovers, and restoring the head text.
Both sides are parsed by the same compiler front end, and no second server is
needed for classification.

### 3.3 Change classes

| Class | Rule | Seed weight | Propagation |
|---|---|---|---|
| `signature` | callable/type whose hover signature differs | 1.0 | callers up to `--depth` (3), implementations, breakage check |
| `removed` | in base, not in head | 1.0 | callers **on the base revision** (lazy base worktree server), mapped back to head |
| `renamed` | removed + added in the same container with ≥ 85 % similar body | 1.0 | as removed |
| `value` | data entity whose declaration changed (hover shows type/value) | 0.9 | references up to depth 3 |
| `type` | class/struct/interface lines outside its members changed | 0.8 | references up to depth 3 |
| `body` | callable whose text changed but signature did not | 0.6 | callers up to `--body-depth` (2) |
| `added` | in head, not in base | 0.3 | callers (normally none) + dispatch: a new override changes what base callers run |
| `cosmetic` | normalized text identical (whitespace; Python keeps relative indentation) | 0 | none |

Signatures are the code blocks of `textDocument/hover`, whitespace-normalized.
When hover is unavailable the declaration header text is compared and a note is
recorded. Comments are *not* stripped: a comment-only edit inside a function is
classified `body` (safe direction — over-report rather than miss).

### 3.4 Edges

| Edge | Source | Risk |
|---|---|---|
| `call` | `callHierarchy/incomingCalls` | 1.0 |
| `ref` | `textDocument/references` minus call sites (value use, field/type/constant use) | 0.8 |
| `dispatch` | callers of an overridden/implemented method | 0.7 (confidence `possible`) |
| `implements` | `textDocument/implementation` of a changed abstract/interface method (signature/removed only) | 1.0 |

`ref` edges for callables are collected for seeds only (one extra request per
seed); deeper levels use incomingCalls.

### 3.5 Scoring and truncation

```
score(seed)  = weight(change class)
score(child) = score(parent) × 0.6 × risk(edge)
```

Traversal is breadth-first, so the first time an entity is reached is through a
shortest path; that path is stored as the "why" chain (`paths` in impact.json).
Expansion stops at:

* depth limit of the seed's class,
* `score × 0.6 < min_score` (0.05),
* node budget `max_nodes` (400),
* hubs: an entity with more than `hub_threshold` (25) callers keeps the
  `hub_keep` (8) nearest (same directory first) and records `total/shown`.

Every stop is recorded in `truncation[]` and on the node (`truncated`), so a
reviewer always knows when the list is incomplete.

### 3.6 Confidence

* `precise` — every request for this node succeeded on a ready server.
* `possible` — reached through a `dispatch` edge.
* `degraded` — with `reasons[]`: server not ready, request failed or timed out,
  removed entity whose base server was unavailable, etc.

Language-level blind spots (reflection, function pointers, `any`, dynamic
attribute access, …) are listed in `blind_spots` for the languages in the diff.

### 3.7 Breakage

For targets of `call`/`ref`/`implements` edges from `signature`, `removed`,
`renamed`, `value` and `type` seeds, ripplelens opens the target file and reads
diagnostics (pull model when offered, else `publishDiagnostics`). Error-severity
diagnostics overlapping a call site are attached to the node as `breaking[]`.

## 4. impact.json (`ripplelens/impact@1`)

```jsonc
{
  "schema": "ripplelens/impact@1",
  "tool": {"name": "ripplelens", "version": "0.1.0"},
  "generated_at": "2026-11-21T15:00:00+00:00",
  "repo": {"root": "...", "analyzed_root": "...", "base": {"ref": "HEAD", "sha": "..."}, "head": {"ref": "working tree", "sha": null}},
  "options": {"max_depth": 3, "body_depth": 2, "max_nodes": 400, "min_score": 0.05, "...": "..."},
  "servers": [{"name": "gopls", "language": "go", "status": "ok|degraded|unavailable", "reason": null,
               "version": "...", "ready_seconds": 1.2, "project_notes": []}],
  "summary": {"files_changed": 2, "entities_changed": 3, "by_change": {"signature": 1},
              "impacted": 7, "impacted_files": 3, "breaking": 2, "degraded": 0, "truncated": false},
  "files": [{"path": "lib/lib.go", "old_path": null, "status": "M", "language": "go", "server": "gopls",
             "added": 2, "removed": 1, "entities": ["lib/lib.go#Helper@17"],
             "module_lines": [[3, 3]], "degraded": [],
             "impacted_files": [{"path": "app/app.go", "score": 0.6}]}],
  "nodes": [{
      "id": "lib/lib.go#Helper@17", "path": "lib/lib.go", "name": "Helper", "qualname": "Helper",
      "kind": "function", "lines": [17, 19],
      "change": "signature",            // null for impacted (unchanged) entities
      "signature": {"before": "func Helper(x int) int", "after": "func Helper(x, y int) int"},
      "renamed_from": null, "lines_changed": 2, "on_base": false, "impacted": 3, "notes": [],
      "depth": 0, "score": 1.0, "via": null, "parent": null, "seeds": ["lib/lib.go#Helper@17"],
      "confidence": "precise", "reasons": [], "breaking": [], "truncated": null
  }],
  "edges": [{"source": "lib/lib.go#Helper@17", "target": "app/app.go#A@5", "kind": "call", "sites": [6]}],
  "paths": {"app/app.go#B@9": ["lib/lib.go#Helper@17", "app/app.go#B@9"]},
  "truncation": [{"at": "...", "reason": "hub|budget|min_score", "total": 120, "shown": 8}],
  "blind_spots": {"go": ["calls through reflect", "..."]},
  "groups": [{
      "id": "pkg:core|src/pricing", "module": "core", "pkg": "src/pricing",
      "units": ["u:…"], "files": ["packages/core/src/pricing/discount.ts"],
      "changed": true, "stats": {"files": 3, "callers": 6, "errors": 2, "contract": 1}
  }],
  "locate": {"entityId": {"group": "…", "unit": "…"}},
  "warnings": [],
  "timing": {"total_seconds": 4.1, "requests": 23, "request_seconds": 1.3, "timeouts": 0}
}
```

Node ids are `<repo-relative path>#<qualname>@<1-based start line>`; edges point
from the upstream (changed) entity to the impacted one; `sites` are 1-based
lines in the target file.

### 4.1 Grouping

Files are grouped for the board so reviewers see structure instead of a flat
list (`analysis/grouping.py`):

| Field | Rule |
|---|---|
| `module` | nearest directory (up to the repo root) with a build marker (`pom.xml`, `go.mod`, `package.json`, …) |
| `pkg` | Java: package after `src/{main,test}/{java,kotlin}`; Python: dotted package; else directory inside the module |
| `unit` | Angular `*.component.{ts,html,scss}`, React `Button.tsx` + `Button.module.css` + test, `foo.h`+`foo.c`, `Foo.java`+`FooTest.java` |

`groups[]` and `locate` are written into `impact.json` so the web UI can collapse
to the groups or files tier when the graph is large.

## 5. Interfaces

### CLI

```
ripplelens analyze [--base REF] [--head REF] [--format markdown|json|context] [--json FILE] [--fail-on-breaking]
ripplelens review  [--base REF] [--head REF] [--port N] [--no-open]
ripplelens view    impact.json
ripplelens doctor  [--json] [--no-start]
ripplelens mcp     [--repo PATH]
```

### MCP tools (`ripplelens mcp`, stdio)

| Tool | Arguments | Returns |
|---|---|---|
| `analyze_diff` | `base?`, `head?`, `depth?` | summary + changed entities + top impacted (compact JSON) |
| `get_impact` | `symbol` (id, qualname or name), `depth?` | the node, its why-path, upstream and downstream edges |
| `explain` | `from`, `to` | shortest path between two entities with edge kinds and call-site lines |
| `get_review_context` | `budget_tokens?` (4000) | prioritized plain text: compile errors, signature changes, top impacted |
| `open_review_ui` | — | starts/refreshes the local web UI and returns its URL |

The MCP process keeps language servers warm between calls and re-runs analysis
when the PostToolUse hook reports edited files.

### HTTP review UI (`ripplelens review`)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/impact` | current `impact.json` |
| GET | `/api/source?path=&start=&end=` | excerpt of a file that appears in the report |
| GET | `/api/markdown` | Markdown report |
| GET | `/api/context?budget=` | token-budgeted review context |
| GET | `/api/ai/status` | public AI settings (never the API key) |
| POST | `/api/ai/config` | update AI settings; empty key keeps the stored one |
| POST | `/api/ai/review` | SSE stream of `{type: start\|delta\|finding\|done\|error}` |

Every `/api/ai/*` request must come from this server's own page: `Host` is
loopback on this port, `Origin` (when present) must match, and
`X-RippleLens-Token` must equal the per-launch session token injected into
`index.html`. The API key lives only in the user-level config file
(`RIPPLELENS_CONFIG_DIR` or the OS app-config directory).

Environment overrides: `RIPPLELENS_AI_PROVIDER`, `RIPPLELENS_AI_BASE_URL`,
`RIPPLELENS_AI_MODEL`, `RIPPLELENS_AI_API_KEY`.

### Plugin layout

```
.claude-plugin/plugin.json        Claude Code manifest (skills, commands, hooks, mcpServers)
.claude-plugin/marketplace.json   single-plugin marketplace
.cursor-plugin/plugin.json        Cursor manifest (same skills, MCP)
.codex-plugin/plugin.json         Codex manifest (same skills, MCP)
skills/ripplelens/SKILL.md        shared by all three
commands/review.md                /ripplelens:review [base]
hooks/hooks.json                  PostToolUse(Edit|Write|MultiEdit) → mark dirty; Stop summary (opt-in)
.mcp.json                         uvx ripplelens mcp
```

## 6. Evaluation

`eval/scip_reader.py` reads SCIP indexes without generated protobuf code.
`eval/scip_judge.py` plus `eval/repos.json` compare a RippleLens report's
impacted files to SCIP reference occurrences (optional; skipped when the
indexer is not installed). CI runs the judge with `continue-on-error`.

## 7. Out of scope for v0

Cross-language edges (e.g. TS → Python over HTTP); runtime traces; incremental
re-analysis of only the touched seeds (the MCP server re-runs the whole diff,
with warm servers). Mandatory cloud AI — the AI panel stays optional and local-config.
