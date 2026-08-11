from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mcp_cnes"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    package_parts = ["mcp_cnes", *relative.parts[:-1]]
    if relative.name == "__init__":
        package_parts = ["mcp_cnes", *relative.parts[:-1]]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module)
            elif node.level > 0:
                retained = package_parts[: len(package_parts) - (node.level - 1)]
                suffix = node.module.split(".") if node.module else []
                modules.add(".".join([*retained, *suffix]))
    return modules


def test_domain_has_no_infrastructure_dependencies() -> None:
    forbidden = {"mcp", "sqlite3", "requests", "playwright", "pandas"}
    forbidden_layers = (
        "mcp_cnes.application",
        "mcp_cnes.infrastructure",
        "mcp_cnes.interfaces",
    )

    violations = {
        str(path.relative_to(PACKAGE_ROOT)): sorted(
            module
            for module in imported_modules(path)
            if module.split(".")[0] in forbidden or module.startswith(forbidden_layers)
        )
        for path in (PACKAGE_ROOT / "domain").rglob("*.py")
    }

    assert not {path: imports for path, imports in violations.items() if imports}


def test_application_depends_only_on_domain_and_its_own_ports() -> None:
    stdlib = {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "pathlib",
        "typing",
    }
    violations: dict[str, list[str]] = {}
    for path in (PACKAGE_ROOT / "application").rglob("*.py"):
        disallowed = []
        for module in imported_modules(path):
            if module.split(".")[0] in stdlib:
                continue
            if module == "mcp_cnes.domain" or module.startswith("mcp_cnes.domain."):
                continue
            if module == "mcp_cnes.application" or module.startswith("mcp_cnes.application."):
                continue
            disallowed.append(module)
        if disallowed:
            violations[str(path.relative_to(PACKAGE_ROOT))] = sorted(disallowed)

    assert not violations


def test_importing_production_modules_has_no_runtime_side_effects(tmp_path: Path) -> None:
    script = """
import asyncio
import importlib
import pkgutil
import socket
import subprocess

# Dependencias podem realizar introspeccao interna no primeiro import. O gate
# abaixo mede somente efeitos iniciados pelos modulos do projeto.
import mcp
import pandas
import pydantic
import requests

def forbidden(*args, **kwargs):
    raise AssertionError('efeito colateral detectado durante import')

class ForbiddenPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        forbidden(*args, **kwargs)

socket.socket.connect = forbidden
subprocess.Popen = ForbiddenPopen
asyncio.run = forbidden

import mcp_cnes
for module in pkgutil.walk_packages(mcp_cnes.__path__, mcp_cnes.__name__ + '.'):
    importlib.import_module(module.name)
for name in ('clean_leads',):
    importlib.import_module(name)
"""
    project_root = PACKAGE_ROOT.parents[1]
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join((str(project_root), str(PACKAGE_ROOT.parent))),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_optional_browser_dependency_is_not_loaded_by_package_import() -> None:
    assert importlib.util.find_spec("mcp_cnes") is not None
    command = "import mcp_cnes, sys; assert 'playwright' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", command], check=False)
    assert result.returncode == 0
