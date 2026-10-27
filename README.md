# Exact Culprit Finder

A local-only incident investigation demo. It replays a seeded traffic fixture against a small FastAPI search service, narrows suspect changes with deterministic tests, and requires a human to approve every production-config write. The existing root `index.html` remains available as the original browser prototype.

## Project layout

```text
exact-culprit-finder/
├── .github/workflows/ci.yml
├── app/                 # FastAPI target, config, flags, Dockerfile
├── agent/               # 14 tools, bisect engine, optional LLM loop
├── runner/              # Disposable sandbox and HTTPX replay
├── dashboard/           # Streamlit review and approval UI
├── traffic_logs/        # Seeded request-cost fixture
├── tests/
├── requirements.txt
└── README.md
```

## Run locally

Python 3.11 or newer is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start the target service in one terminal:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the human review dashboard in a second terminal:

```powershell
python -m streamlit run dashboard/app.py
```

The dashboard defaults to offline deterministic mode. Select a scenario and run the investigation. A proven patch is only written to the local `app/config.yaml` after checking the approval box and clicking **Apply approved patch**. Use **Reset local config** to restore the baseline.

## Replay and tests

```powershell
python -m runner.replay --max-requests 100
python -m pytest -q
```

The full fixture contains 1,000 deterministic request costs. With the baseline 5-second timeout, the expected failure rate is 0.3%; the single-change scenario sets it to 1 second and produces 42.0% failures. The pair scenario tests the interaction of `cache_ttl_seconds` and `max_results`.

## Optional Anthropic tool loop

Set `ANTHROPIC_API_KEY` in your local shell to enable the dashboard checkbox. The offline implementation remains the default. The agent only receives the 14 allowlisted local tools, and its `apply_fix` tool still returns `APPROVAL_REQUIRED` until a human grants approval.

```powershell
$env:ANTHROPIC_API_KEY = "your-key"
python -m streamlit run dashboard/app.py
```

Do not commit API keys or other credentials.

## Optional Docker target

The Docker integration is opt-in. Build and run only the included local target:

```powershell
$env:EXACTCULPRIT_ENABLE_DOCKER = "1"
python -c "from runner.sandbox import docker_build_local; docker_build_local()"
python -c "from runner.sandbox import docker_start_local; print(docker_start_local())"
```

The container port is bound to `127.0.0.1`, not a public interface. HTTPX replay rejects non-loopback hosts. Git reverse-patch support is limited to a marked disposable sandbox directory. No tool deploys changes to an external service.

## API

- `GET /health`
- `GET /config`
- `POST /search` with `request_id`, `query`, and `query_cost_ms`
