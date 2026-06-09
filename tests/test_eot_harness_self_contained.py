from __future__ import annotations

import ast
from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parents[1] / "eot_harness"


def test_eot_harness_does_not_import_internal_eot_evals() -> None:
    forbidden_roots = {"eot_evals"}
    violations: list[str] = []

    for path in sorted(HARNESS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in forbidden_roots:
                        violations.append(f"{path}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                root = module.split(".", 1)[0]
                if root in forbidden_roots:
                    violations.append(f"{path}:{node.lineno}: from {module} import ...")

    assert violations == []


def test_eot_harness_has_no_internal_project_references() -> None:
    forbidden_text = (
        "EOT_EVALS",
        "eot_evals",
        "modal_jobs",
        "python -m eot_evals",
        "/Users/jfields",
        ".codex",
    )
    violations: list[str] = []

    for path in sorted(HARNESS_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in forbidden_text:
            if token in text:
                violations.append(f"{path}: contains {token!r}")

    assert violations == []
