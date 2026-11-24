# Mixed-language monorepos (Java + frontend)

RippleLens can analyze several languages in one git repo. For **Java + Angular/React in
subfolders**, set up tooling once so diagnostics and caller chains match what you ship.

## TypeScript in a subfolder

- Put `tsconfig.json` and `node_modules/typescript` under the frontend app (e.g. `apps/web/`).
- Run `ripplelens doctor` — it lists **TypeScript projects** it found and the pinned TypeScript
  version; nested projects are used instead of only the repo root.
- If doctor warns about a version mismatch, install dependencies in that app directory so
  `node_modules/typescript` exists locally.

## Java and Lombok

jdtls does not see Lombok-generated members unless you start it with the Lombok Java agent.

1. Run `ripplelens doctor` on a Java repo. If Lombok is on the classpath and no agent is
   configured, doctor prints the exact `java.jdt.ls.vmargs` line.
2. Create **`.lsp.json`** at the repo root (not committed if you prefer):

```json
{
  "jdtls": {
    "settings": {
      "java.jdt.ls.vmargs": "-javaagent:C:\\Users\\you\\.m2\\repository\\org\\projectlombok\\lombok\\1.18.32\\lombok-1.18.32.jar"
    }
  }
}
```

Or set `LOMBOK_JAR` / `RIPPLELENS_LOMBOK_JAR` and let doctor suggest a path from your local
Maven cache.

## Ignoring AI / doc noise (untracked files)

Untracked paths still respect `.gitignore`. RippleLens also skips common AI and cache
directories when building the change set (`.cursor/`, `.aider*`, etc.).

To force every untracked file through:

```bash
set RIPPLELENS_NO_UNTRACKED_SKIP=1
```

For permanent repo-specific rules, use **`.git/info/exclude`** (local only) or **`.gitignore`**.

## Large changes

When `--max-nodes` (default 400) is smaller than the number of changed entities, impact
traversal is **not run** and the report says so. Raise the budget, e.g.:

```bash
ripplelens analyze --max-nodes 1200
```

Markdown output separates **call** vs **ref** impacts and summarizes hub/budget truncation at
the top.