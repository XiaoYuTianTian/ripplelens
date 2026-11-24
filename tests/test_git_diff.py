from pathlib import Path

import pytest

from ripplelens.git import diff as gitdiff
from ripplelens.git.untracked_skip import should_skip_untracked


def test_diff_includes_ignore_cr_at_eol(monkeypatch, tmp_path: Path):
    recorded: list[tuple] = []

    def fake_git(root, *args, **kwargs):
        recorded.append(args)
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args[0] == "rev-parse":
            return "abc" * 10 + "0"
        if args[:2] == ("merge-base",):
            return "abc" * 10 + "0"
        if args[0] == "status":
            return ""
        if args[0] == "diff":
            return ""
        if args[0] == "ls-files":
            return ""
        raise AssertionError(args)

    sha = "a" * 40
    monkeypatch.setattr(gitdiff, "git", fake_git)
    monkeypatch.setattr(gitdiff, "repo_root", lambda p: tmp_path)
    monkeypatch.setattr(gitdiff, "is_dirty", lambda p: False)
    monkeypatch.setattr(gitdiff, "rev_parse", lambda r, ref: sha if ref in ("HEAD", f"{sha}~1", "HEAD~1") else None)
    spec = gitdiff.resolve(tmp_path, base=None, head="HEAD")
    diff_args = next(a for a in recorded if a and a[0] == "diff")
    assert "--ignore-cr-at-eol" in diff_args
    assert spec.untracked_skipped == 0


def test_untracked_skip_patterns():
    assert should_skip_untracked(".cursor/plans/x.md")
    assert should_skip_untracked("packages/foo/.aider.tags")
    assert not should_skip_untracked("src/main/java/App.java")
    assert not should_skip_untracked("apps/web/package.json")