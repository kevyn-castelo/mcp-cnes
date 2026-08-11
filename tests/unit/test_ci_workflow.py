from pathlib import Path


def test_pull_request_and_main_workflow_run_locked_quality_gates() -> None:
    workflow = (
        Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:\n    branches: [main]" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "uv sync --locked" in workflow
    assert "uv run ruff check src tests benchmarks" in workflow
    assert "uv run pyright" in workflow
    assert "Unit tests" in workflow
    assert "Integration tests" in workflow
    assert "Contract tests" in workflow
    assert "tests/unit/test_mcp_sdk_contract.py" in workflow
    assert "--cov=mcp_cnes.domain --cov=mcp_cnes.application" in workflow
    assert "--cov-fail-under=80" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in workflow
    assert "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10" in workflow
    assert "actions/checkout@v6" not in workflow


def test_live_smoke_is_manual_and_explicitly_authorized() -> None:
    workflow = (
        Path(__file__).parents[2] / ".github" / "workflows" / "live-smoke.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert 'CNES_RUN_LIVE_TESTS: "1"' in workflow
    assert "uv run pytest tests/live -m live -q" in workflow
    assert "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10" in workflow
    assert "actions/checkout@v6" not in workflow

    elasticnes_test = (
        Path(__file__).parents[1] / "live" / "test_elasticnes_smoke.py"
    ).read_text(encoding="utf-8")
    assert "KibanaHttpCollector" in elasticnes_test
    assert ".collect(" in elasticnes_test
    assert "requests.get" not in elasticnes_test

    portal_sus_test = (
        Path(__file__).parents[1] / "live" / "test_portal_sus_smoke.py"
    ).read_text(encoding="utf-8")
    assert "PortalSUSRemoteSource" in portal_sus_test
    assert "ListRemoteResources" in portal_sus_test
    assert "assert resources" in portal_sus_test
    assert "requests.get" not in portal_sus_test
