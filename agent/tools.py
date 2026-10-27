"""Fourteen local investigation, sandbox, test, action, and report tools."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from time import time

import yaml

from agent.bisect_engine import find_culprit, revert_changes
from runner.replay import evaluate_request, evaluate_requests, load_traffic
from runner.sandbox import (
    Sandbox,
    cleanup_sandbox,
    create_sandbox as make_sandbox,
    write_sandbox_state,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CHANGE_SET = [
    {"id": 1, "key": "ranking_algorithm", "before": "v1", "after": "v2", "source": "commit"},
    {"id": 2, "key": "cache_ttl_seconds", "before": 60, "after": 120, "source": "config"},
    {"id": 3, "key": "new_ui_banner", "before": False, "after": True, "section": "feature_flags", "source": "flag"},
    {"id": 4, "key": "log_format", "before": "json_v1", "after": "json_v2", "source": "commit"},
    {"id": 5, "key": "max_results", "before": 50, "after": 100, "source": "config"},
    {"id": 6, "key": "beta_autocomplete", "before": False, "after": True, "section": "feature_flags", "source": "flag"},
    {"id": 7, "key": "pagination_default", "before": 20, "after": 50, "source": "commit"},
    {"id": 8, "key": "db_timeout_seconds", "before": 5.0, "after": 1.0, "source": "config"},
]

TOOL_NAMES = (
    "get_logs",
    "get_metrics",
    "get_deploys",
    "get_git_commits",
    "get_alerts",
    "get_dependencies",
    "create_sandbox",
    "revert_changes",
    "run_replay",
    "diff_config",
    "test_single_value",
    "apply_fix",
    "verify_recovery",
    "generate_report",
)


@dataclass
class AgentContext:
    project_root: Path = PROJECT_ROOT
    scenario: str = "single"
    approval_granted: bool = False
    sandbox: Sandbox | None = None
    baseline: dict = field(default_factory=dict)
    deployed: dict = field(default_factory=dict)
    investigation: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time)


def _read_state(project_root: Path) -> dict:
    config = yaml.safe_load((project_root / "app" / "config.yaml").read_text(encoding="utf-8")) or {}
    flags = json.loads((project_root / "app" / "feature_flags.json").read_text(encoding="utf-8"))
    return {"config": config, "feature_flags": flags}


def _scenario_changes(scenario: str) -> list[dict]:
    changes = deepcopy(CHANGE_SET)
    if scenario == "pair":
        changes[1]["after"] = 5
        changes[4]["after"] = 500
        changes[7]["after"] = 4.8
    elif scenario == "unproven":
        return []
    return changes


def _deployed_state(baseline: dict, changes: list[dict]) -> dict:
    state = deepcopy(baseline)
    for change in changes:
        state[change.get("section", "config")][change["key"]] = change["after"]
    return state


def _replay(state: dict, project_root: Path) -> dict:
    return evaluate_requests(
        load_traffic(project_root / "traffic_logs" / "traffic.json"),
        state["config"],
        state["feature_flags"],
    )


def get_logs(context: AgentContext, limit: int = 100, level: str = "ALL") -> dict:
    requests = load_traffic(context.project_root / "traffic_logs" / "traffic.json")
    state = context.deployed or _read_state(context.project_root)
    logs = []
    for request in requests:
        result = evaluate_request(request, state["config"], state["feature_flags"])
        entry_level = "ERROR" if result["failed"] else "INFO"
        if level != "ALL" and level != entry_level:
            continue
        logs.append(
            {
                "request_id": request["request_id"],
                "level": entry_level,
                "status_code": result["status_code"],
                "query_cost_ms": result["query_cost_ms"],
            }
        )
        if len(logs) >= limit:
            break
    return {"count": len(logs), "logs": logs}


def get_metrics(context: AgentContext) -> dict:
    state = context.deployed or _read_state(context.project_root)
    return _replay(state, context.project_root)


def get_deploys(context: AgentContext) -> dict:
    return {"changes": _scenario_changes(context.scenario), "spike_at": "14:04"}


def get_git_commits(context: AgentContext) -> dict:
    return {
        "commits": [
            {"change_id": change["id"], "source": change["source"], "key": change["key"]}
            for change in _scenario_changes(context.scenario)
        ]
    }


def get_alerts(context: AgentContext) -> dict:
    metrics = get_metrics(context)
    observed_rate = 42.0 if context.scenario == "unproven" else metrics["failure_rate"]
    return {
        "alert": "HighErrorRate" if observed_rate > 1 else None,
        "failure_rate": observed_rate,
        "threshold": 1.0,
        "onset": "14:04",
    }


def get_dependencies(context: AgentContext) -> dict:
    return {"edges": [["api-gateway", "search-service"], ["search-service", "sqlite-db"]]}


def create_sandbox(context: AgentContext) -> dict:
    changes = _scenario_changes(context.scenario)
    if not context.baseline:
        context.baseline = _read_state(context.project_root)
    context.deployed = _deployed_state(context.baseline, changes)
    if context.sandbox is not None:
        cleanup_sandbox(context.sandbox)
    context.sandbox = make_sandbox(context.project_root)
    context.sandbox.config = deepcopy(context.deployed["config"])
    context.sandbox.feature_flags = deepcopy(context.deployed["feature_flags"])
    write_sandbox_state(context.sandbox)
    return {"sandbox_id": context.sandbox.sandbox_id, "change_count": len(changes)}


def revert_changes_tool(context: AgentContext, sandbox_id: str, change_ids: list[int]) -> dict:
    sandbox = context.sandbox
    if sandbox is None or sandbox.sandbox_id != sandbox_id:
        return {"error": "SANDBOX_NOT_FOUND"}
    state = revert_changes(
        {"config": sandbox.config, "feature_flags": sandbox.feature_flags},
        _scenario_changes(context.scenario),
        set(change_ids),
    )
    sandbox.config = state["config"]
    sandbox.feature_flags = state["feature_flags"]
    write_sandbox_state(sandbox)
    return {"sandbox_id": sandbox_id, "reverted_ids": sorted(set(change_ids))}


def run_replay(context: AgentContext, sandbox_id: str) -> dict:
    sandbox = context.sandbox
    if sandbox is None or sandbox.sandbox_id != sandbox_id:
        return {"error": "SANDBOX_NOT_FOUND"}
    return _replay(
        {"config": sandbox.config, "feature_flags": sandbox.feature_flags},
        context.project_root,
    )


def diff_config(context: AgentContext, change_id: int) -> dict:
    change = next((item for item in _scenario_changes(context.scenario) if item["id"] == change_id), None)
    if change is None:
        return {"error": "CHANGE_NOT_FOUND"}
    return {"change": change, "diff": f"- {change['key']}: {change['before']}\n+ {change['key']}: {change['after']}"}


def test_single_value(context: AgentContext, sandbox_id: str, key: str) -> dict:
    sandbox = context.sandbox
    if sandbox is None or sandbox.sandbox_id != sandbox_id:
        return {"error": "SANDBOX_NOT_FOUND"}
    change = next((item for item in _scenario_changes(context.scenario) if item["key"] == key), None)
    if change is None:
        return {"error": "KEY_NOT_FOUND"}
    state = revert_changes(
        {"config": sandbox.config, "feature_flags": sandbox.feature_flags},
        _scenario_changes(context.scenario),
        {change["id"]},
    )
    return _replay(state, context.project_root)


def apply_fix(context: AgentContext, key: str, value: object) -> dict:
    if not context.approval_granted:
        return {"error": "APPROVAL_REQUIRED"}
    config_path = context.project_root / "app" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if key not in config:
        return {"error": "UNKNOWN_CONFIG_KEY"}
    config[key] = value
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return {"status": "applied", "key": key, "value": value}


def verify_recovery(context: AgentContext) -> dict:
    state = _read_state(context.project_root)
    result = _replay(state, context.project_root)
    return {**result, "recovered": result["failure_rate"] <= 1.0}


def generate_report(context: AgentContext) -> dict:
    analysis = context.investigation
    verified = verify_recovery(context) if context.approval_granted else None
    return {
        "confidence": "PROVEN" if analysis.get("proven") else "NOT PROVEN",
        "culprit_ids": analysis.get("culprit_ids", []),
        "kind": analysis.get("kind", "unproven"),
        "tests": analysis.get("tests", []),
        "recovery": verified,
        "duration_seconds": round(time() - context.started_at, 2),
    }


def run_offline_investigation(context: AgentContext) -> dict:
    """Run the deterministic investigation workflow without any LLM or network."""
    context.started_at = time()
    context.baseline = _read_state(context.project_root)
    changes = _scenario_changes(context.scenario)
    create_sandbox(context)
    deployed_replay = _replay(context.deployed, context.project_root)
    if deployed_replay["failure_rate"] <= 1.0:
        context.investigation = {"proven": False, "kind": "unproven", "culprit_ids": [], "tests": []}
    else:
        context.investigation = find_culprit(
            changes,
            context.baseline,
            context.deployed,
            lambda state: _replay(state, context.project_root),
        )
    return generate_report(context)


def dispatch_tool(context: AgentContext, name: str, arguments: dict) -> dict:
    """Dispatch one of the allowlisted tools used by the optional LLM loop."""
    tools = {
        "get_logs": lambda: get_logs(context, **arguments),
        "get_metrics": lambda: get_metrics(context),
        "get_deploys": lambda: get_deploys(context),
        "get_git_commits": lambda: get_git_commits(context),
        "get_alerts": lambda: get_alerts(context),
        "get_dependencies": lambda: get_dependencies(context),
        "create_sandbox": lambda: create_sandbox(context),
        "revert_changes": lambda: revert_changes_tool(context, **arguments),
        "run_replay": lambda: run_replay(context, **arguments),
        "diff_config": lambda: diff_config(context, **arguments),
        "test_single_value": lambda: test_single_value(context, **arguments),
        "apply_fix": lambda: apply_fix(context, **arguments),
        "verify_recovery": lambda: verify_recovery(context),
        "generate_report": lambda: generate_report(context),
    }
    if name not in tools:
        return {"error": "UNKNOWN_TOOL"}
    try:
        return tools[name]()
    except (KeyError, TypeError, ValueError) as error:
        return {"error": "INVALID_TOOL_ARGUMENTS", "detail": str(error)}


def tool_definitions() -> list[dict]:
    """Anthropic-compatible schemas for all fourteen tools."""
    empty = {"type": "object", "properties": {}, "required": []}
    schemas = {
        "get_logs": {"type": "object", "properties": {"limit": {"type": "integer"}, "level": {"type": "string"}}},
        "get_metrics": empty,
        "get_deploys": empty,
        "get_git_commits": empty,
        "get_alerts": empty,
        "get_dependencies": empty,
        "create_sandbox": empty,
        "revert_changes": {"type": "object", "properties": {"sandbox_id": {"type": "string"}, "change_ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["sandbox_id", "change_ids"]},
        "run_replay": {"type": "object", "properties": {"sandbox_id": {"type": "string"}}, "required": ["sandbox_id"]},
        "diff_config": {"type": "object", "properties": {"change_id": {"type": "integer"}}, "required": ["change_id"]},
        "test_single_value": {"type": "object", "properties": {"sandbox_id": {"type": "string"}, "key": {"type": "string"}}, "required": ["sandbox_id", "key"]},
        "apply_fix": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": ["string", "number", "boolean"]}}, "required": ["key", "value"]},
        "verify_recovery": empty,
        "generate_report": empty,
    }
    return [
        {"name": name, "description": f"Local incident tool: {name}.", "input_schema": schemas[name]}
        for name in TOOL_NAMES
    ]
