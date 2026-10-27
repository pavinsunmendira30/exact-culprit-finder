from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from agent.agent_loop import run_agent
from agent.tools import AgentContext, apply_fix, run_offline_investigation, tool_definitions
from app.main import app
from runner.replay import evaluate_requests, load_traffic
from runner.sandbox import cleanup_sandbox

ROOT = Path(__file__).resolve().parents[1]


def make_project_copy(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(ROOT / "app", project / "app")
    shutil.copytree(ROOT / "traffic_logs", project / "traffic_logs")
    return project


def test_seeded_replay_baseline_and_incident_rates() -> None:
    traffic = load_traffic(ROOT / "traffic_logs" / "traffic.json")
    assert len(traffic) == 1000
    healthy = evaluate_requests(traffic, {"db_timeout_seconds": 5.0})
    broken = evaluate_requests(traffic, {"db_timeout_seconds": 1.0})
    assert healthy["failure_rate"] == 0.3
    assert broken["failure_rate"] == 42.0


def test_offline_agent_proves_single_change(tmp_path: Path) -> None:
    project = make_project_copy(tmp_path)
    context = AgentContext(project_root=project, scenario="single")
    result = run_agent(context)
    assert result["confidence"] == "PROVEN"
    assert result["kind"] == "single"
    assert result["culprit_ids"] == [8]
    assert result["tests"]
    cleanup_sandbox(context.sandbox)


def test_offline_agent_proves_interacting_pair(tmp_path: Path) -> None:
    project = make_project_copy(tmp_path)
    context = AgentContext(project_root=project, scenario="pair")
    result = run_offline_investigation(context)
    assert result["confidence"] == "PROVEN"
    assert result["kind"] == "pair"
    assert result["culprit_ids"] == [2, 5]
    cleanup_sandbox(context.sandbox)


def test_unproven_scenario_does_not_apply_fix(tmp_path: Path) -> None:
    project = make_project_copy(tmp_path)
    context = AgentContext(project_root=project, scenario="unproven")
    result = run_offline_investigation(context)
    assert result["confidence"] == "NOT PROVEN"
    assert result["culprit_ids"] == []
    assert apply_fix(context, "db_timeout_seconds", 5.0) == {"error": "APPROVAL_REQUIRED"}
    cleanup_sandbox(context.sandbox)


def test_approval_is_required_before_config_write(tmp_path: Path) -> None:
    project = make_project_copy(tmp_path)
    context = AgentContext(project_root=project)
    blocked = apply_fix(context, "db_timeout_seconds", 1.0)
    assert blocked["error"] == "APPROVAL_REQUIRED"

    context.approval_granted = True
    applied = apply_fix(context, "db_timeout_seconds", 1.0)
    assert applied["status"] == "applied"
    assert "db_timeout_seconds: 1.0" in (project / "app" / "config.yaml").read_text()


def test_api_health_config_and_search() -> None:
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "healthy"
    assert "db_timeout_seconds" in client.get("/config").json()["config"]

    response = client.post(
        "/search",
        json={"request_id": "test-1", "query": "local replay", "query_cost_ms": 600},
    )
    assert response.status_code == 200
    assert response.json()["request_id"] == "test-1"


def test_fourteen_allowlisted_tools() -> None:
    definitions = tool_definitions()
    assert len(definitions) == 14
    assert len({definition["name"] for definition in definitions}) == 14
    assert next(item for item in definitions if item["name"] == "apply_fix")
