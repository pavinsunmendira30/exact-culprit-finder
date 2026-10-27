"""Disposable local sandboxes with opt-in Docker and sandbox-only Git helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SANDBOX_MARKER = ".exact-culprit-sandbox"


@dataclass
class Sandbox:
    sandbox_id: str
    path: Path
    config: dict
    feature_flags: dict
    baseline: dict


def create_sandbox(project_root: Path = PROJECT_ROOT) -> Sandbox:
    """Copy only service config into a disposable temporary sandbox."""
    temp_path = Path(tempfile.mkdtemp(prefix="exact-culprit-sandbox-"))
    shutil.copy2(project_root / "app" / "config.yaml", temp_path / "config.yaml")
    shutil.copy2(project_root / "app" / "feature_flags.json", temp_path / "feature_flags.json")
    (temp_path / SANDBOX_MARKER).write_text("local sandbox\n", encoding="utf-8")
    config = yaml.safe_load((temp_path / "config.yaml").read_text(encoding="utf-8")) or {}
    flags = json.loads((temp_path / "feature_flags.json").read_text(encoding="utf-8"))
    return Sandbox(
        sandbox_id=temp_path.name,
        path=temp_path,
        config=config,
        feature_flags=flags,
        baseline={"config": config.copy(), "feature_flags": flags.copy()},
    )


def write_sandbox_state(sandbox: Sandbox) -> None:
    (sandbox.path / "config.yaml").write_text(
        yaml.safe_dump(sandbox.config, sort_keys=False), encoding="utf-8"
    )
    (sandbox.path / "feature_flags.json").write_text(
        json.dumps(sandbox.feature_flags, indent=2) + "\n", encoding="utf-8"
    )


def cleanup_sandbox(sandbox: Sandbox) -> None:
    if (sandbox.path / SANDBOX_MARKER).is_file():
        shutil.rmtree(sandbox.path, ignore_errors=True)


def docker_build_local(project_root: Path = PROJECT_ROOT, image: str = "exact-culprit-finder:local") -> None:
    """Build the supplied image only after an explicit local Docker opt-in."""
    if os.getenv("EXACTCULPRIT_ENABLE_DOCKER") != "1":
        raise RuntimeError("Set EXACTCULPRIT_ENABLE_DOCKER=1 to enable Docker operations")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*:[a-z0-9][a-z0-9._-]*", image):
        raise ValueError("Use a simple local image name and tag")
    subprocess.run(
        ["docker", "build", "-f", "app/Dockerfile", "-t", image, "."],
        cwd=project_root,
        check=True,
        timeout=180,
    )


def docker_start_local(image: str = "exact-culprit-finder:local", port: int = 8000) -> str:
    """Start the local image with its host port bound to loopback only."""
    if os.getenv("EXACTCULPRIT_ENABLE_DOCKER") != "1":
        raise RuntimeError("Set EXACTCULPRIT_ENABLE_DOCKER=1 to enable Docker operations")
    if not 1024 <= port <= 65535:
        raise ValueError("Choose a non-privileged local port")
    completed = subprocess.run(
        ["docker", "run", "--rm", "-d", "-p", f"127.0.0.1:{port}:8000", image],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout.strip()


def git_revert_in_sandbox(sandbox: Sandbox, patch_text: str) -> None:
    """Reverse a Git patch only inside a marked disposable sandbox directory."""
    if not (sandbox.path / SANDBOX_MARKER).is_file():
        raise RuntimeError("Refusing Git operations outside a marked sandbox")
    check = subprocess.run(
        ["git", "apply", "--reverse", "--check", "-"],
        input=patch_text,
        text=True,
        cwd=sandbox.path,
        capture_output=True,
        timeout=15,
    )
    if check.returncode:
        raise RuntimeError(check.stderr.strip() or "Patch cannot be reversed in this sandbox")
    subprocess.run(
        ["git", "apply", "--reverse", "-"],
        input=patch_text,
        text=True,
        cwd=sandbox.path,
        check=True,
        timeout=15,
    )
