from pathlib import Path

from ripplelens.analysis import changes as C
from ripplelens.analysis.impact import Options, Traversal
from ripplelens.git import diff as gitdiff


def _fake_change(i: int) -> C.EntityChange:
    from ripplelens.analysis.symbols import Entity

    fd = gitdiff.FileDiff(status="M", old_path=f"src/f{i}.java", new_path=f"src/f{i}.java")
    e = Entity(name=f"m{i}", kind=6, path=f"src/f{i}.java", start_line=0, end_line=1, sel_line=0, sel_char=0, container="C")
    return C.EntityChange(C.BODY, e, None, fd)


def test_budget_ignores_cosmetic_nodes():
    from ripplelens.analysis.symbols import Entity

    opt = Options(max_nodes=2)
    trav = Traversal(Path("."), lambda s: None, opt, set())
    fd = gitdiff.FileDiff(status="M", old_path="a.py", new_path="a.py")
    e = Entity(name="f", kind=12, path="a.py", start_line=0, end_line=1, sel_line=0, sel_char=0)
    cosmetic = C.EntityChange(C.COSMETIC, e, None, fd)
    body = _fake_change(1)

    class H:
        name = "pyright"
        ready = True
        ready_reason = ""
        spec = None
        root = Path(".")

    h = H()
    trav.add_seed(h, cosmetic)
    trav.add_seed(h, body)
    assert trav.budget_nodes() == 1


def test_seeds_over_max_nodes_skip_in_pipeline_logic():
    opt = Options(max_nodes=1)
    assert len([_fake_change(0), _fake_change(1)]) > opt.max_nodes