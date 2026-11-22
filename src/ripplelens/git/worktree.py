"""Detached git worktrees for querying a language server on another revision."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from ..lsp.registry import cache_dir
from .diff import GitError, git


def worktree_for(repo: Path, sha: str) -> Path:
    """Return a detached worktree of `sha`, creating it if needed (reused across runs)."""
    tag = hashlib.sha1(str(repo).lower().encode()).hexdigest()[:10]
    dest = cache_dir() / "worktrees" / f"{repo.name}-{tag}" / sha[:12]
    if (dest / ".git").exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    git(repo, "worktree", "prune", check=False)
    try:
        git(repo, "worktree", "add", "--detach", "--force", str(dest), sha)
    except GitError:
        git(repo, "worktree", "prune", check=False)
        git(repo, "worktree", "add", "--detach", "--force", str(dest), sha)
    return dest


def remove_worktree(repo: Path, path: Path) -> None:
    git(repo, "worktree", "remove", "--force", str(path), check=False)
    shutil.rmtree(path, ignore_errors=True)
    git(repo, "worktree", "prune", check=False)
