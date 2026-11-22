---
name: ripplelens
description: Find which functions a code change actually affects (callers, overriders, users of changed constants) and any compile errors it causes at those call sites, using compiler-backed language servers. Use before declaring a change in Java, Go, TypeScript/JavaScript, Python, C or C++ done, when reviewing a diff or pull request, or when the user asks what a change could break or what its blast radius is.
---

# ripplelens

ripplelens maps the git diff to changed functions, classes and constants, classifies each change
(signature, removed, renamed, value, type, body, added, cosmetic), then asks the language server
(gopls, tsc/typescript-language-server, basedpyright, clangd, jdtls) for real callers, references,
implementations and overriders. Results are evidence from the compiler's own index, not guesses.

## Workflow

1. Call `analyze_diff` (no arguments = uncommitted changes, or the last commit when the tree is clean;
   pass `base` such as `main` to review a branch). Results are cached and recomputed automatically
   when files change.
2. Read `compile_errors` first. Each one is a call site the change already breaks. Fix them or report them.
3. For every entry in `changed` with change `signature`, `removed`, `renamed`, `value` or `type`,
   call `get_impact(<id>)` and check each downstream call site (the `code` field shows the line).
   Decide whether the caller still behaves correctly; do not stop at "it compiles".
4. For `body` changes, look at the direct callers in `top_impacted` (depth 1) and check that they do
   not rely on the old behavior (return values, errors raised, side effects, ordering).
5. Use `explain(from_symbol, to_symbol)` when you need to tell the user why something is impacted.
6. Use `get_review_context(budget_tokens)` when you need a compact summary for a review comment.
7. Call `open_review_ui` only when the user wants to look at the impact visually.

## Reading the results

- `confidence: precise` means the language server resolved the edge. `possible` means dynamic dispatch
  (a caller of the overridden base method may reach this override at runtime); verify by reading the
  call site. `degraded` lists reasons (server timeout, missing index, unsupported request): treat that
  part of the result as incomplete and say so.
- `servers[].status` other than `ok` means a language is not analyzed. Do not claim a change is safe
  for files of that language; suggest `ripplelens doctor`.
- `truncation` means a hub (a function with many callers) was folded or the node budget was hit:
  the listed callers are a sample, not the full set.
- `blind_spots` lists what no static analysis can see for that language (reflection, dependency
  injection, string-based dispatch, macros, build tags). Mention the relevant ones when you summarize.
- An empty impact for a `body` change means nothing else in the project calls that function, not
  that the change is correct.

## Rules

- Report impact with file:line references from the tool output, not from memory.
- Never describe a change as safe beyond what the evidence shows: name the degraded parts, the
  unavailable servers and the relevant blind spots.
- If `analyze_diff` fails because a server is missing, run `ripplelens doctor` in the terminal and
  relay its install hint.
