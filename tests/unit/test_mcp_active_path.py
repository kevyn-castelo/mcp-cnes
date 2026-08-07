from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mcp_cnes"
ACTIVE_FILES = [
    PACKAGE_ROOT / "__main__.py",
    PACKAGE_ROOT / "mcp_app.py",
    PACKAGE_ROOT / "interfaces" / "mcp" / "server.py",
]


def test_active_path_uses_sdk_without_manual_jsonrpc_dispatch() -> None:
    forbidden_calls = {"input", "print"}
    for path in ACTIVE_FILES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not calls.intersection(forbidden_calls), path
        assert '"jsonrpc"' not in source
        assert "json.loads" not in source

    entrypoint = (PACKAGE_ROOT / "__main__.py").read_text(encoding="utf-8")
    assert 'mcp.run(transport="stdio")' in entrypoint
