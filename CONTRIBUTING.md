# Contributing to RippleLens

Thanks for helping. This document covers the local loop and what CI expects.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate
pip install -e ".[dev]"
npm --prefix web ci
npm --prefix web run build
```

Language servers for end-to-end tests go on `PATH` or in `RIPPLELENS_TOOLS`
(semicolon-separated on Windows, colon-separated elsewhere). See the README
language table.

## Tests

```bash
pytest tests/ -m "not lsp"     # unit + HTTP/AI/plugin — no servers needed
pytest -m lsp                  # scenarios; skips languages whose server is missing
ruff check src tests
```

For the review UI:

```bash
python eval/demo.py shop
# AI findings without a cloud key:
python eval/fake_model.py --port 8799
```

Refresh README screenshots / GIF with the system Edge or Chrome (no Playwright
browser download):

```bash
pip install playwright pillow
python eval/record.py
```

## Claude Code plugin

After changing `.claude-plugin/`, `skills/`, `commands/`, `hooks/` or `.mcp.json`:

```bash
npx @anthropic-ai/claude-code plugin validate .
```

## Pull requests

- Keep the package id `ripplelens`; the display name is **RippleLens**.
- Do not put API keys in `impact.json`, fixtures, or the browser bundle.
- Rebuild `src/ripplelens/web_dist` when you change `web/`.
- Add a `CHANGELOG.md` entry under `[Unreleased]` for user-visible changes.
