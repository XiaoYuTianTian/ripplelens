---
description: Pre-review the current changes (or a branch against a base ref) with ripplelens, then hand the findings to the human reviewer in the review UI
argument-hint: "[base-ref]"
---

Do an agent pre-review of the code changes with the ripplelens MCP tools. A human confirms or rejects
each finding afterwards in the review UI, so every finding must stand on evidence from the tools.

1. Call `analyze_diff` with `base` set to `$ARGUMENTS` if it is not empty; otherwise call it with no arguments.
   If any server is not `ok`, note which languages were not analyzed; do not call those parts safe.
2. Call `get_architecture`. Review the module/package level first:
   - every entry in `violations` is a finding (kind `architecture`, severity `high` for `deny`, `medium` for `layer`);
   - every entry in `cycles` is a finding (kind `architecture`, severity `high`);
   - for each cross-module or external entry in `deps_added`, decide whether the new dependency fits the
     structure in `modules` (and `rules_text` when present): a core/shared module starting to depend on a
     feature module, a library depending on an app, or a new third-party dependency in a core module deserves
     a finding. Without a rules file phrase these as suggestions (severity `medium` or `low`);
   - every entry in `contracts` with `external_modules` is a change other modules rely on: check it in step 3.
3. For each contract change (signature, removed, renamed, value, type), call `get_impact` with its id and check
   every downstream call site. Callers that break or silently change behavior are findings (kind `contract`).
4. For body changes, check the direct callers for reliance on the old behavior (kind `behavior`).
5. Call `submit_review` with:
   - `summary`: 2-5 sentences on what the change does to the structure (modules touched, dependencies added
     or removed, contracts other modules see) and the main risks;
   - `verdict`: `request_changes` if any high finding, `comment` if only medium/low, `approve` if none;
   - `findings`: at most 15, most severe first; each with `file` and `line` taken from the tool output,
     `scope` set to the module or package directory it concerns, `evidence` quoting the tool output,
     and a concrete `suggestion`.
6. Call `open_review_ui` and give the user the URL. Tell them the pre-review is in the review UI under
   "Agent pre-review", and that they should accept or reject each finding and record the final verdict there.
7. Finish in chat with a short list: blocking findings first, then the risks a human must look at (`possible`
   or `degraded` confidence, truncated hubs, applicable blind spots), then what is verified as unaffected.
