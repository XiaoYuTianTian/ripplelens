"""Group files into module / package / unit so reviewers see structure instead of a flat file list.

module   nearest directory (up to the repo root) holding a build marker (pom.xml, go.mod, package.json, ...)
package  Java: the package after src/{main,test}/{java,kotlin}; Python: the dotted package of __init__.py
         directories; everything else: the directory inside the module
unit     files in one directory that share a stem: Angular x.component.{ts,html,scss,spec.ts},
         React Button.tsx + Button.module.css + Button.test.tsx, foo.h + foo.c, foo.go + foo_test.go,
         Foo.java + FooTest.java. A single file is its own unit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

MODULE_MARKERS = (
    "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "package.json", "pyproject.toml", "setup.py",
    "setup.cfg", "CMakeLists.txt", "meson.build", "Cargo.toml", "BUILD.bazel", "BUILD",
)
JVM_ROOTS = ("src/main/java/", "src/test/java/", "src/main/kotlin/", "src/test/kotlin/")
_TEST_DIRS = {"test", "tests", "__tests__", "spec", "testdata", "e2e"}
_TEST_FILE = re.compile(
    r"(^test_.*\.py$|_test\.(py|go|cc|cpp|c)$|\.(spec|test|e2e)\.[cm]?[jt]sx?$|(Test|Tests|IT)\.(java|kt)$)"
)
_STEM_SUFFIXES = {"spec", "test", "stories", "module", "styles", "e2e", "d"}
_LANG_OF_EXT = {
    ".go": "go", ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".py": "python", ".pyi": "python", ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
}


def is_test(rel: str) -> bool:
    p = PurePosixPath(rel)
    return bool(_TEST_FILE.search(p.name)) or any(part in _TEST_DIRS for part in p.parts[:-1]) or "/src/test/" in f"/{rel}"


def unit_stem(name: str) -> str:
    """'checkout.component.spec.ts' -> 'checkout.component'; 'FooTest.java' -> 'Foo'; 'test_foo.py' -> 'foo'."""
    parts = name.split(".")
    if len(parts) > 1 and parts[0]:
        parts = parts[:-1]
        while len(parts) > 1 and parts[-1] in _STEM_SUFFIXES:
            parts.pop()
    base = ".".join(parts)
    base = re.sub(r"_test$", "", base)
    base = re.sub(r"^test_", "", base)
    if base[:1].isupper():
        base = re.sub(r"(?<=[a-z0-9])(Tests?|IT)$", "", base)
    return base or name


@dataclass
class Unit:
    id: str
    name: str
    files: list[str] = field(default_factory=list)
    test: bool = False


@dataclass
class Group:
    id: str
    module: str
    module_path: str
    package: str
    language: str | None
    units: dict[str, Unit] = field(default_factory=dict)


class Grouper:
    def __init__(self, root: Path, repo_name: str | None = None):
        self.root = root
        self.repo_name = repo_name or root.name
        self._module_cache: dict[str, str] = {}

    def module_dir(self, rel: str) -> str:
        d = PurePosixPath(rel).parent
        chain = []
        found = ""
        while True:
            key = d.as_posix() if str(d) != "." else ""
            if key in self._module_cache:
                found = self._module_cache[key]
                break
            chain.append(key)
            if any((self.root / key / m).is_file() for m in MODULE_MARKERS):
                found = key
                break
            if not key:
                break
            d = d.parent
        for k in chain:
            self._module_cache[k] = found
        return found

    def package(self, rel: str, module: str, language: str | None) -> str:
        inner = rel[len(module) + 1 :] if module else rel
        directory = PurePosixPath(inner).parent.as_posix()
        directory = "" if directory == "." else directory
        if language in ("java", "kotlin"):
            for r in JVM_ROOTS:
                i = f"/{inner}".find(f"/{r}")
                if i >= 0:
                    pkg = PurePosixPath(inner[i + len(r) :]).parent.as_posix()
                    return "" if pkg == "." else pkg.replace("/", ".")
            return directory.replace("/", ".")
        if language == "python":
            parts = PurePosixPath(rel).parent.parts
            pkg: list[str] = []
            for k in range(len(parts), 0, -1):
                if (self.root.joinpath(*parts[:k]) / "__init__.py").is_file():
                    pkg.insert(0, parts[k - 1])
                else:
                    break
            if pkg:
                return ".".join(pkg)
        return directory

    def build(self, paths: Iterable[str], language_of=None) -> dict[str, Any]:
        lang_of = language_of or (lambda p: _LANG_OF_EXT.get(Path(p).suffix.lower()))
        groups: dict[str, Group] = {}
        placed: list[tuple[str, Group, str]] = []
        for rel in sorted(set(paths)):
            module = self.module_dir(rel)
            lang = lang_of(rel)
            pkg = self.package(rel, module, lang)
            gid = f"{module}|{pkg}"
            g = groups.get(gid)
            if g is None:
                g = groups[gid] = Group(
                    id=gid, module=PurePosixPath(module).name if module else self.repo_name, module_path=module,
                    package=pkg, language=lang,
                )
            elif g.language is None and lang:
                g.language = lang
            p = PurePosixPath(rel)
            placed.append((rel, g, f"{gid}/{unit_stem(p.name)}"))
        stem_count: dict[str, int] = {}
        for _, _, key in placed:
            stem_count[key] = stem_count.get(key, 0) + 1
        locate: dict[str, dict[str, Any]] = {}
        for rel, g, key in placed:
            uid = key if stem_count[key] > 1 else rel
            name = PurePosixPath(key).name if stem_count[key] > 1 else PurePosixPath(rel).name
            u = g.units.get(uid)
            if u is None:
                u = g.units[uid] = Unit(id=uid, name=name, test=True)
            u.files.append(rel)
            t = is_test(rel)
            u.test = u.test and t
            locate[rel] = {"group": g.id, "unit": uid, "test": t}
        return {
            "groups": [
                {
                    "id": g.id, "module": g.module, "module_path": g.module_path, "package": g.package,
                    "language": g.language,
                    "units": [{"id": u.id, "name": u.name, "files": u.files, "test": u.test} for u in g.units.values()],
                }
                for g in sorted(groups.values(), key=lambda g: (g.module_path, g.package))
            ],
            "locate": locate,
        }
