# Changelog

All notable changes to RippleLens are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- jdtls: strip parameter lists from method names so signature edits pair as one entity; query the base revision for callers/implementations after a signature change.
- Windows: restore on-disk path case in reports (`orderService.ts` instead of `orderservice.ts`).

## [0.1.0] — 2026-11-22

### Added

- LSP-backed function-level blast radius for Go, TypeScript/JavaScript, Python, Java, C and C++.
- Review UI (Porcelain): changed / direct / indirect columns, hover wires, focus drawer, auto collapse to files or groups, hide/restore, search, filters, reviewed marks, light/dark, zh/en.
- Optional AI review over OpenAI-compatible and Anthropic streaming APIs, SSE to the UI, user-level config (API key never returned to the browser).
- Claude Code plugin (MCP, `/ripplelens:review`, skill, stale-after-edit hook).
- `ripplelens doctor|review|analyze|mcp` CLI; `impact.json` schema `ripplelens/impact@1`.
- Multi-module TypeScript demo (`eval/demo.py shop`) and README recording script (`eval/record.py`).
