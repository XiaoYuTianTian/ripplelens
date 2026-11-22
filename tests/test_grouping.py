from __future__ import annotations

from pathlib import Path

import pytest

from ripplelens.analysis.grouping import Grouper, is_test, unit_stem


@pytest.mark.parametrize(
    "name, stem",
    [
        ("checkout.component.ts", "checkout.component"),
        ("checkout.component.html", "checkout.component"),
        ("checkout.component.spec.ts", "checkout.component"),
        ("Button.tsx", "Button"),
        ("Button.module.css", "Button"),
        ("Button.test.tsx", "Button"),
        ("foo.h", "foo"),
        ("foo.cpp", "foo"),
        ("foo_test.go", "foo"),
        ("OrderService.java", "OrderService"),
        ("OrderServiceTest.java", "OrderService"),
        ("Contest.java", "Contest"),
        ("test_lib.py", "lib"),
        ("index.d.ts", "index"),
        ("Makefile", "Makefile"),
        (".eslintrc", ".eslintrc"),
    ],
)
def test_unit_stem(name, stem):
    assert unit_stem(name) == stem


def test_is_test():
    assert is_test("svc/src/test/java/a/FooTest.java")
    assert is_test("web/src/app/x.component.spec.ts")
    assert is_test("pkg/foo_test.go")
    assert is_test("tests/test_x.py")
    assert not is_test("svc/src/main/java/a/Foo.java")
    assert not is_test("web/src/app/contest.ts")


def _touch(root: Path, *rels: str) -> None:
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")


def test_grouper_modules_packages_units(tmp_path):
    _touch(
        tmp_path,
        "order-service/pom.xml",
        "order-service/src/main/java/com/shop/order/OrderService.java",
        "order-service/src/main/java/com/shop/order/api/OrderController.java",
        "order-service/src/test/java/com/shop/order/OrderServiceTest.java",
        "web/package.json",
        "web/src/app/checkout/checkout.component.ts",
        "web/src/app/checkout/checkout.component.html",
        "web/src/app/checkout/checkout.component.scss",
        "web/src/app/core/cart.service.ts",
        "py/pyproject.toml",
        "py/fx/__init__.py",
        "py/fx/sub/__init__.py",
        "py/fx/sub/lib.py",
        "go.mod",
        "cmd/tool/main.go",
    )
    files = [
        "order-service/src/main/java/com/shop/order/OrderService.java",
        "order-service/src/main/java/com/shop/order/api/OrderController.java",
        "order-service/src/test/java/com/shop/order/OrderServiceTest.java",
        "web/src/app/checkout/checkout.component.ts",
        "web/src/app/checkout/checkout.component.html",
        "web/src/app/checkout/checkout.component.scss",
        "web/src/app/core/cart.service.ts",
        "py/fx/sub/lib.py",
        "cmd/tool/main.go",
    ]
    out = Grouper(tmp_path, "shop").build(files)
    by_id = {g["id"]: g for g in out["groups"]}
    loc = out["locate"]

    java = by_id[loc[files[0]]["group"]]
    assert (java["module"], java["package"], java["language"]) == ("order-service", "com.shop.order", "java")
    assert loc[files[1]]["group"] == "order-service|com.shop.order.api"
    # main and test sources of one class are one unit in the same package
    assert loc[files[2]]["group"] == java["id"]
    assert loc[files[2]]["unit"] == loc[files[0]]["unit"]
    assert loc[files[2]]["test"] and not loc[files[0]]["test"]

    checkout = {loc[f]["unit"] for f in files[3:6]}
    assert len(checkout) == 1
    unit = next(u for u in by_id["web|src/app/checkout"]["units"] if u["id"] in checkout)
    assert unit["name"] == "checkout.component" and len(unit["files"]) == 3 and not unit["test"]
    assert loc[files[6]]["unit"] == files[6]

    assert loc[files[7]]["group"] == "py|fx.sub"
    assert loc[files[8]]["group"] == "|cmd/tool"
    assert by_id["|cmd/tool"]["module"] == "shop"
