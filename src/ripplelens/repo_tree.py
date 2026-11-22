"""List tracked (or walkable) files under a repo root for the review UI heat tree."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vscode", "target", "dist", "build", ".cache", ".settings", ".gradle", "out", "cmake-build-debug"}


def list_repo_files(root: Path) -> list[str]:
    """Return repo-relative POSIX paths for files under `root` (files only; dirs implied by paths)."""
    root = root.resolve()
    files = _git_ls_files(root)
    if files is None:
        files = _walk_files(root)
    else:
        files = files + _git_untracked(root)
    return sorted(set(files))


def tree_payload(root: Path) -> dict:
    files = list_repo_files(root)
    dirs: set[str] = set()
    for rel in files:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    entries = [{"path": d, "kind": "dir"} for d in sorted(dirs)]
    entries += [{"path": f, "kind": "file"} for f in files]
    return {"root": str(root), "entries": entries}


def _git_ls_files(root: Path) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out: list[str] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "replace").replace("\\", "/")
        if rel.startswith(".git/") or any(p in SKIP_DIRS for p in rel.split("/")):
            continue
        out.append(rel)
    return out


def _git_untracked(root: Path) -> list[str]:
    """Untracked files the analysis also treats as changes (not gitignored)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    out: list[str] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "replace").replace("\\", "/")
        if rel.endswith("/") or any(p in SKIP_DIRS for p in rel.split("/")):
            continue
        if rel.split("/")[-1] in {".classpath", ".project", ".factorypath"} or rel.endswith(".class"):
            continue
        out.append(rel)
    return out


def _walk_files(root: Path) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        base = Path(dirpath)
        for name in filenames:
            if name.startswith("."):
                continue
            rel = (base / name).relative_to(root).as_posix()
            out.append(rel)
    return out
