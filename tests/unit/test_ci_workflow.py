from pathlib import Path


def test_pull_request_workflow_runs_locked_quality_gates() -> None:
    workflow = (
        Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "uv sync --locked" in workflow
    assert "uv run ruff check src tests mcp_server.py benchmarks" in workflow
    assert "uv run pyright" in workflow
    assert 'uv run pytest -m "not live"' in workflow
    assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in workflow
