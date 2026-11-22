"""Plugin manifests (Claude Code, Cursor, Codex) stay consistent with the package."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ripplelens import __version__

ROOT = Path(__file__).parents[1]
HOOK_EVENTS = {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop", "SessionStart", "SessionEnd", "Notification", "PreCompact"}


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, f"{path} has no frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"')
    return out


@pytest.mark.parametrize("manifest", [".claude-plugin/plugin.json", ".cursor-plugin/plugin.json", ".codex-plugin/plugin.json"])
def test_manifest(manifest):
    path = ROOT / manifest
    if not path.exists():
        pytest.skip(f"{manifest} not present")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "ripplelens"
    assert data["version"] == __version__
    assert re.fullmatch(r"[a-z0-9-]+", data["name"])


def test_marketplace():
    data = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    assert data["owner"]["name"]
    (plugin,) = data["plugins"]
    assert plugin["name"] == "ripplelens" and plugin["source"] == "./" and plugin["version"] == __version__


def test_mcp_and_hooks():
    mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["ripplelens"]
    assert server["args"][-1] == "mcp"
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) <= HOOK_EVENTS
    for groups in hooks.values():
        for g in groups:
            for h in g["hooks"]:
                assert h["type"] == "command" and "ripplelens hook" in h["command"]


def test_skill_and_command():
    skill = frontmatter(ROOT / "skills/ripplelens/SKILL.md")
    assert skill["name"] == "ripplelens" and len(skill["description"]) > 50
    cmd = frontmatter(ROOT / "commands/review.md")
    assert cmd["description"]
    body = (ROOT / "commands/review.md").read_text(encoding="utf-8")
    assert "$ARGUMENTS" in body


def test_tools_named_in_docs_exist():
    src = (ROOT / "src/ripplelens/mcp_server.py").read_text(encoding="utf-8")
    tools = set(re.findall(r"async def (\w+)\(", src))
    for doc in ("skills/ripplelens/SKILL.md", "commands/review.md"):
        named = set(re.findall(r"`(analyze_diff|get_impact|explain|get_review_context|open_review_ui)", (ROOT / doc).read_text(encoding="utf-8")))
        assert named <= tools, (doc, named - tools)
