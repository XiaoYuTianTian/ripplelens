"""End-to-end scenarios against real language servers (skipped when a server is missing)."""

from __future__ import annotations

import pytest
from scenarios import SCENARIOS, check, materialize

from ripplelens.analysis.impact import Options
from ripplelens.analysis.pipeline import analyze
from ripplelens.lsp.pool import ServerPool
from ripplelens.lsp.registry import Registry

SERVER_OF_LANG = {"go": "gopls", "ts": "typescript", "python": "pyright", "java": "jdtls", "c": "clangd", "cpp": "clangd"}


def _available(lang: str) -> bool:
    spec = Registry.load().servers[SERVER_OF_LANG[lang]]
    return spec.build_command(__import__("pathlib").Path(".")) is not None


@pytest.mark.lsp
@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario(sc, tmp_path):
    if not _available(sc.lang):
        pytest.skip(f"{SERVER_OF_LANG[sc.lang]} not installed")
    repo = materialize(sc, tmp_path / sc.name)
    pool = ServerPool(Registry.load(repo))
    try:
        rep = analyze(repo, options=Options(), pool=pool, progress=lambda m: None)
    finally:
        pool.shutdown()
    unavailable = [s for s in rep["servers"] if s["status"] == "unavailable"]
    if unavailable:
        pytest.skip(unavailable[0]["reason"])
    assert check(sc, rep) == []
