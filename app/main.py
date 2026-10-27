"""A local FastAPI service used as the deterministic incident target."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from runner.replay import evaluate_request

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.yaml"
FLAGS_PATH = APP_DIR / "feature_flags.json"

app = FastAPI(title="Exact Culprit Finder Local Target", version="1.0.0")


class SearchRequest(BaseModel):
    request_id: str = "manual-request"
    query: str = "incident replay"
    query_cost_ms: int = Field(default=250, ge=0)


def read_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def read_feature_flags() -> dict:
    return json.loads(FLAGS_PATH.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    config = read_config()
    return {
        "status": "healthy",
        "service": "search-service",
        "db_timeout_seconds": config.get("db_timeout_seconds"),
    }


@app.get("/config")
def get_config() -> dict:
    return {"config": read_config(), "feature_flags": read_feature_flags()}


@app.post("/search")
def search(request: SearchRequest) -> JSONResponse:
    result = evaluate_request(
        request.model_dump(),
        read_config(),
        read_feature_flags(),
    )
    result["query"] = request.query
    return JSONResponse(status_code=result["status_code"], content=result)
