---
description: Review the blast radius of the current changes (or of a branch against a base ref) with ripplelens
argument-hint: "[base-ref]"
---

Review the impact of the code changes with the ripplelens MCP tools.

1. Call `analyze_diff` with `base` set to `$ARGUMENTS` if it is not empty; otherwise call it with no arguments.
2. If any server is not `ok`, say which languages were not analyzed and why.
3. List every compile error at an impacted call site with its file:line and message.
4. For each contract change (signature, removed, renamed, value, type), call `get_impact` with its id and
   check every downstream call site. Say which callers need updating and why.
5. For body changes, check the direct callers for reliance on the old behavior.
6. Finish with a short review: blocking problems first, then risks that need a human look (items with
   `possible` or `degraded` confidence, truncated hubs, and the blind spots that apply), then what is
   verified as unaffected.
7. Call `open_review_ui` so the user can inspect the impact graph, and give them the URL.
