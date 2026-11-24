"""Paths to skip when treating git-untracked files as changes.

Respects gitignore / exclude-standard first; this is an extra guardrail for AI
tool output, caches, and other noise that is often left untracked.
"""

from __future__ import annotations

import fnmatch
import os
# Directory name segments anywhere in the path (repo-relative, forward slashes).
SKIP_DIR_NAMES = frozenset(
    {
        ".cursor",
        ".claude",
        ".continue",
        ".copilot",
        ".specstory",
        ".gemini",
        ".turbo",
        ".next",
        ".nuxt",
        ".vercel",
        ".cache",
        ".idea",
        ".vscode",
        ".vs",
        ".fleet",
        ".gradle",
        ".settings",
        "node_modules",
        "target",
        "dist",
        "build",
        "out",
        "cmake-build-debug",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "coverage",
        ".nyc_output",
        "agent-transcripts",
        "ai-output",
        "ai_outputs",
        "design-docs",
    }
)

# Prefixes on a single path segment (e.g. `.aider*`).
SKIP_DIR_PREFIXES = (".aider",)

# Extra globs (matched on the full repo-relative path).
SKIP_GLOBS = (
    ".cursor/**",
    "**/agent-transcripts/**",
    "**/.specstory/**",
    "**/.aider*/**",
)


def skip_untracked_enabled() -> bool:
    return os.environ.get("RIPPLELENS_NO_UNTRACKED_SKIP", "").lower() not in ("1", "true", "yes")


def should_skip_untracked(rel: str) -> bool:
    if not skip_untracked_enabled():
        return False
    rel = rel.replace("\\", "/").strip("/")
    if not rel:
        return True
    parts = rel.split("/")
    if parts[0] in SKIP_DIR_NAMES or any(p in SKIP_DIR_NAMES for p in parts):
        return True
    if any(any(p.startswith(pref) for pref in SKIP_DIR_PREFIXES) for p in parts):
        return True
    for pat in SKIP_GLOBS:
        if fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(rel, pat.removeprefix("**/")):
            return True
    return False