"""Human review and approval interface for the local incident demo."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.agent_loop import run_agent
from agent.tools import AgentContext, CHANGE_SET, apply_fix, generate_report, verify_recovery

st.set_page_config(page_title="Exact Culprit Finder", page_icon="🎯", layout="wide")
st.title("Exact Culprit Finder")
st.caption("Local deterministic incident investigation with human-controlled remediation")

if "context" not in st.session_state:
    st.session_state.context = AgentContext(project_root=PROJECT_ROOT)
if "result" not in st.session_state:
    st.session_state.result = None

context: AgentContext = st.session_state.context
with st.sidebar:
    st.subheader("Incident controls")
    context.scenario = st.selectbox("Scenario", ["single", "pair", "unproven"], index=["single", "pair", "unproven"].index(context.scenario))
    use_llm = st.checkbox("Use Anthropic tool loop", value=False, disabled=not bool(__import__("os").getenv("ANTHROPIC_API_KEY")))
    if st.button("Run investigation", type="primary", use_container_width=True):
        context.approval_granted = False
        st.session_state.result = run_agent(context, use_llm=use_llm)
    if st.button("Reset local config", use_container_width=True):
        config_path = PROJECT_ROOT / "app" / "config.yaml"
        config_path.write_text(
            "db_timeout_seconds: 5.0\ncache_ttl_seconds: 60\nmax_results: 50\npagination_default: 20\nlog_format: json_v1\nranking_algorithm: v1\n",
            encoding="utf-8",
        )
        context.approval_granted = False
        st.session_state.result = None
        st.rerun()

if st.session_state.result:
    result = st.session_state.result
    left, right = st.columns(2)
    with left:
        st.metric("Confidence", result["confidence"])
        st.metric("Cause type", result["kind"].upper())
    with right:
        culprit_ids = result.get("culprit_ids", [])
        st.metric("Proven change IDs", ", ".join(map(str, culprit_ids)) or "None")
        st.metric("Sandbox replays", len(result.get("tests", [])))

    st.subheader("Deterministic replay evidence")
    if result.get("tests"):
        st.dataframe(result["tests"], use_container_width=True, hide_index=True)
    else:
        st.info("No candidate replay proved a cause. Production configuration remains unchanged.")

    if result["confidence"] == "PROVEN":
        change_map = {change["id"]: change for change in CHANGE_SET}
        recommended_patch = {
            change_map[change_id]["key"]: change_map[change_id]["before"]
            for change_id in result["culprit_ids"]
            if change_map[change_id].get("section", "config") == "config"
        }
        st.subheader("Proposed local patch")
        st.json(recommended_patch)
        approved = st.checkbox("I reviewed this patch and approve applying it to the local demo config")
        if st.button("Apply approved patch", disabled=not approved):
            context.approval_granted = True
            applied = [apply_fix(context, key, value) for key, value in recommended_patch.items()]
            verification = verify_recovery(context)
            result["recovery"] = verification
            result["applied"] = applied
            st.session_state.result = generate_report(context)
            st.success("Local recovery verified." if verification["recovered"] else "Patch applied, but recovery did not meet the SLA.")
            st.rerun()

    if result.get("recovery"):
        st.subheader("Recovery verification")
        st.json(result["recovery"])
    with st.expander("Incident report JSON"):
        st.json(result)
else:
    st.info("Choose a scenario and run the local investigation. Remediation stays blocked until you approve a proven patch.")
