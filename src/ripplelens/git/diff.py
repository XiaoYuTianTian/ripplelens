"""git diff -> changed files and line ranges."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class GitError(Exception):
    pass


def git(repo: str | Path, *args: str, check: bool = True, input_text: str | None = None) -> str:
    cmd = ["git", "-c", "core.quotepath=false", "-c", "core.autocrlf=false", *args]
    proc = subprocess.run(
        cmd,
        cwd=str(repo),
        capture_output=True,
        input=input_text.encode("utf-8") if input_text is not None else None,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


def repo_root(path: str | Path) -> Path:
    out = git(path, "rev-parse", "--show-toplevel").strip()
    return Path(out).resolve()


def rev_parse(repo: Path, ref: str) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=str(repo), capture_output=True
    )
    return proc.stdout.decode().strip() or None if proc.returncode == 0 else None


def is_dirty(repo: Path) -> bool:
    return bool(git(repo, "status", "--porcelain", "--untracked-files=normal").strip())


@dataclass
class Hunk:
    old_start: int  # 1-based, as in the diff header
    old_count: int
    new_start: int
    new_count: int

    def new_lines(self) -> range:
        """0-based head lines touched by this hunk."""
        return range(self.new_start - 1, self.new_start - 1 + self.new_count)

    def old_lines(self) -> range:
        return range(self.old_start - 1, self.old_start - 1 + self.old_count)

    def new_anchor(self) -> int:
        """0-based head line nearest to a pure deletion."""
        return max(0, self.new_start - 1 if self.new_count == 0 else self.new_start - 1)


@dataclass
class FileDiff:
    status: str  # A, M, D, R
    old_path: str | None  # repo-relative, forward slashes
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False
    similarity: int | None = None

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    def changed_new_lines(self) -> set[int]:
        out: set[int] = set()
        for h in self.hunks:
            out.update(h.new_lines())
        return out

    def changed_old_lines(self) -> set[int]:
        out: set[int] = set()
        for h in self.hunks:
            out.update(h.old_lines())
        return out

    @property
    def added_lines(self) -> int:
        return sum(h.new_count for h in self.hunks)

    @property
    def removed_lines(self) -> int:
        return sum(h.old_count for h in self.hunks)


@dataclass
class DiffSpec:
    repo: Path
    base: str  # commit sha
    base_label: str
    head: str | None  # commit sha, or None for the working tree
    head_label: str
    files: list[FileDiff]


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _unquote(p: str) -> str:
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape").encode("latin-1").decode("utf-8", "replace")
    return p


def parse_unified(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            cur = FileDiff(status="M", old_path=None, new_path=None)
            files.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("new file mode"):
            cur.status = "A"
        elif line.startswith("deleted file mode"):
            cur.status = "D"
        elif line.startswith("similarity index"):
            cur.similarity = int(line.split()[-1].rstrip("%"))
        elif line.startswith("rename from "):
            cur.status = "R"
            cur.old_path = _unquote(line[len("rename from ") :])
        elif line.startswith("rename to "):
            cur.new_path = _unquote(line[len("rename to ") :])
        elif line.startswith("--- "):
            p = _unquote(line[4:])
            cur.old_path = None if p == "/dev/null" else p.removeprefix("a/")
        elif line.startswith("+++ "):
            p = _unquote(line[4:])
            cur.new_path = None if p == "/dev/null" else p.removeprefix("b/")
        elif line.startswith("Binary files"):
            cur.binary = True
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            if m:
                cur.hunks.append(
                    Hunk(
                        int(m.group(1)),
                        int(m.group(2)) if m.group(2) is not None else 1,
                        int(m.group(3)),
                        int(m.group(4)) if m.group(4) is not None else 1,
                    )
                )
    for f in files:
        if f.status == "A":
            f.old_path = None
        if f.status == "D":
            f.new_path = None
    return files


def resolve(repo: str | Path, base: str | None = None, head: str | None = None) -> DiffSpec:
    """Decide what to compare.

    base=None: working tree vs HEAD when dirty, otherwise HEAD vs HEAD~1.
    base given: compare against merge-base(base, head-or-HEAD) (PR semantics).
    head=None: working tree (staged + unstaged + untracked).
    """
    root = repo_root(repo)
    head_sha = rev_parse(root, head) if head else None
    if head and not head_sha:
        raise GitError(f"unknown head ref: {head}")
    if base is None:
        if head is None and is_dirty(root):
            base_sha, base_label = rev_parse(root, "HEAD"), "HEAD"
        else:
            tip = head or "HEAD"
            head_sha = rev_parse(root, tip)
            head = tip
            base_sha, base_label = rev_parse(root, f"{tip}~1"), f"{tip}~1"
        if not base_sha:
            raise GitError("nothing to compare: repository has no parent commit and no local changes")
    else:
        b = rev_parse(root, base)
        if not b:
            raise GitError(f"unknown base ref: {base}")
        mb = git(root, "merge-base", b, head_sha or "HEAD", check=False).strip()
        base_sha, base_label = (mb or b), base
    args = ["diff", "--no-color", "--no-ext-diff", "-U0", "-M", "--src-prefix=a/", "--dst-prefix=b/", base_sha]
    if head_sha:
        args.append(head_sha)
    files = parse_unified(git(root, *args))
    if head_sha is None:
        files += _untracked(root)
    return DiffSpec(
        repo=root,
        base=base_sha,
        base_label=base_label,
        head=head_sha,
        head_label=head or "working tree",
        files=files,
    )


_GENERATED = {"target", "bin", ".settings", "out", "cmake-build-debug", ".gradle"}
_GENERATED_FILES = {".classpath", ".project", ".factorypath"}


def _generated(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    if parts[0] in _GENERATED or any(p in _GENERATED for p in parts):
        return True
    name = parts[-1]
    return name in _GENERATED_FILES or name.endswith(".class")


def _untracked(root: Path) -> list[FileDiff]:
    out = []
    for rel in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if not rel or _generated(rel):
            continue
        p = root / rel
        try:
            n = len(p.read_bytes().splitlines())
        except OSError:
            continue
        out.append(FileDiff(status="A", old_path=None, new_path=rel, hunks=[Hunk(0, 0, 1, n)] if n else []))
    return out


def read_base(repo: Path, sha: str, rel: str) -> str | None:
    proc = subprocess.run(["git", "show", f"{sha}:{rel}"], cwd=str(repo), capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def read_head(repo: Path, head: str | None, rel: str) -> str | None:
    if head is None:
        p = repo / rel
        try:
            return p.read_bytes().decode("utf-8", "replace")
        except OSError:
            return None
    return read_base(repo, head, rel)


def to_native(repo: Path, rel: str) -> str:
    return os.path.normpath(str(repo / rel))
