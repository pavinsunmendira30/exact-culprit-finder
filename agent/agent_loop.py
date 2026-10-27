"""Offline deterministic orchestration with an optional Anthropic tool loop."""

from __future__ import annotations

import json
import os

from agent.tools import AgentContext, dispatch_tool, generate_report, run_offline_investigation, tool_definitions

SYSTEM_PROMPT = """You investigate only this local demo service. Use replay results as proof; hypotheses are not proof. You may inspect evidence and run sandbox tests, but apply_fix is always blocked until a human approves it in the dashboard. Never target a non-loopback service or claim a recovery that verify_recovery did not confirm."""


def run_agent(context: AgentContext, use_llm: bool = False) -> dict:
    """Run with Anthropic only when explicitly selected and configured."""
    if not use_llm or not os.getenv("ANTHROPIC_API_KEY"):
        return run_offline_investigation(context)
    return _run_anthropic_loop(context)


def _run_anthropic_loop(context: AgentContext) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    messages = [
        {
            "role": "user",
            "content": (
                f"Investigate the {context.scenario} local incident. Gather evidence, "
                "create a sandbox, and prove a single cause or interacting pair by replay. "
                "Do not apply a fix."
            ),
        }
    ]

    for _ in range(10):
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            tools=tool_definitions(),
            messages=messages,
        )
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = dispatch_tool(context, block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )
        if not tool_results:
            return run_offline_investigation(context)
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    return run_offline_investigation(context)
