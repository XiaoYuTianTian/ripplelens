## Summary

## Test plan
- [ ] `pytest tests/ -m "not lsp"`
- [ ] `npm --prefix web run build` (if UI changed)
- [ ] `npx @anthropic-ai/claude-code plugin validate .` (if plugin files changed)
- [ ] `CHANGELOG.md` updated under `[Unreleased]` when user-visible
