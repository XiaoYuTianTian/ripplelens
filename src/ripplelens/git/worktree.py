"""Detached git worktrees for querying a language server on another revision."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from ..lsp.registry import cache_dir
from .diff import GitError, git


def worktree_for(repo: Path, sha: str) -> Path:
    """Return a detached worktree of `sha`, creating it if needed (reused across runs)."""
    tag = hashlib.sha1(str(repo).lower().encode()).hexdigest()[:10]
    dest = cache_dir() / "worktrees" / f"{repo.name}-{tag}" / sha[:12]
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        git(repo, "worktree", "prune", check=False)
        try:
            git(repo, "worktree", "add", "--detach", "--force", str(dest), sha)
        except GitError:
            git(repo, "worktree", "prune", check=False)
            git(repo, "worktree", "add", "--detach", "--force", str(dest), sha)
    mirror_compile_commands(repo, dest)
    return dest


def mirror_compile_commands(repo: Path, dest: Path) -> None:
    """Copy a working-tree compile_commands.json into a base worktree.

    The database is often untracked, so `git worktree add` does not include it
    and clangd on the base revision cannot see callers of an old signature.
    Paths that point at `repo` are rewritten to `dest`.
    """
    src = repo / "compile_commands.json"
    if not src.is_file():
        return
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, list):
        return
    repo_r = os.path.normpath(str(repo.resolve()))
    dest_r = os.path.normpath(str(dest.resolve()))

    def rewrite(value: str) -> str:
        folded = value.lower()
        for src in (repo_r, str(repo.resolve()), str(repo)):
            if not src:
                continue
            idx = folded.find(src.lower())
            if idx >= 0:
                return value[:idx] + dest_r + value[idx + len(src) :]
        return value

    for entry in data:
        if not isinstance(entry, dict):
            continue
        for key in ("directory", "file", "command"):
            if isinstance(entry.get(key), str):
                entry[key] = rewrite(entry[key])
        if isinstance(entry.get("arguments"), list):
            entry["arguments"] = [rewrite(a) if isinstance(a, str) else a for a in entry["arguments"]]
    try:
        (dest / "compile_commands.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        return


def remove_worktree(repo: Path, path: Path) -> None:
    git(repo, "worktree", "remove", "--force", str(path), check=False)
    shutil.rmtree(path, ignore_errors=True)
    git(repo, "worktree", "prune", check=False)
