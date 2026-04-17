"""Docker execution harness.

Adapts swe_bench_pro_eval.py:358-430. Uses the docker SDK directly instead of
subprocess. Reads per-instance artifacts (Dockerfile, run_script, parser)
from disk paths recorded in the ExamInstance.
"""
from __future__ import annotations

import json
import logging
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..schema import ExamInstance
from .entryscript import assemble_workspace

log = logging.getLogger(__name__)


@dataclass
class DockerRunResult:
    status_code: int
    stdout: str
    stderr: str
    parser_output: dict[str, Any]
    workspace_dir: Path
    passed_tests: list[str]
    failed_tests: list[str]


def _default_platform() -> str | None:
    # Match SWE-bench Pro behavior: force linux/amd64 on Apple Silicon.
    if platform.machine() in ("arm64", "aarch64"):
        return "linux/amd64"
    return None


def _load_text(path: Path | str) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    return p.read_text()


def run_exam_in_docker(
    exam: ExamInstance,
    solver_patch: str,
    image_tag: str,
    runs_root: Path,
    run_id: str,
    *,
    patch_kind: str = "solver",
    timeout_s: int = 1800,
    block_network: bool = False,
    docker_platform: str | None = None,
) -> DockerRunResult:
    """Run one (exam, solver_patch) in an ephemeral container.

    Returns a DockerRunResult with parsed tests. The caller is responsible for
    converting the result into a Grade via evaluator.scoring.grade_run.
    """
    try:
        import docker
        from docker.errors import ContainerError, ImageNotFound
    except ImportError as e:
        raise RuntimeError("docker SDK not installed. pip install docker") from e

    # Resolve artifacts
    base_df_text = _load_text(exam.base_dockerfile_path)
    instance_df_text = _load_text(exam.instance_dockerfile_path)
    run_script_text = _load_text(exam.run_script_path)
    parser_text = _load_text(exam.parser_path)
    if not run_script_text or not parser_text:
        raise RuntimeError(
            f"Missing run_script or parser on disk for {exam.instance_id}: "
            f"run_script={exam.run_script_path!r} parser={exam.parser_path!r}"
        )

    # Workspace. instance_ids are guaranteed colon-free by schema.make_instance_id.
    workspace_dir = runs_root / exam.instance_id / run_id / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    entry_path = assemble_workspace(
        exam=exam,
        solver_patch=solver_patch,
        run_script_text=run_script_text,
        parser_text=parser_text,
        base_dockerfile_text=base_df_text,
        instance_dockerfile_text=instance_df_text,
        workspace_dir=workspace_dir,
        patch_kind=patch_kind,
    )

    plat = docker_platform or _default_platform()
    client = docker.from_env()

    # Ensure image is available locally
    try:
        client.images.get(image_tag)
    except ImageNotFound:
        try:
            if plat:
                client.images.pull(image_tag, platform=plat)
            else:
                client.images.pull(image_tag)
        except Exception as pull_err:
            raise RuntimeError(f"Image {image_tag!r} not found locally and pull failed: {pull_err}")

    abs_workspace = os.path.abspath(workspace_dir)
    run_kwargs: dict[str, Any] = {
        "volumes": {abs_workspace: {"bind": "/workspace", "mode": "rw"}},
        "detach": True,
        "entrypoint": "/bin/bash",
        "command": ["-c", "bash /workspace/entryscript.sh"],
        "working_dir": "/app",
        "mem_limit": "8g",
        "nano_cpus": 4 * 10**9,
    }
    if plat:
        run_kwargs["platform"] = plat
    if block_network:
        run_kwargs["network_mode"] = "none"

    container = client.containers.run(image_tag, **run_kwargs)
    try:
        wait_res = container.wait(timeout=timeout_s)
        status_code = int(wait_res.get("StatusCode", 1)) if isinstance(wait_res, dict) else 1
    except Exception as e:
        log.warning("container.wait failed for %s: %r", exam.instance_id, e)
        try:
            container.kill()
        except Exception:
            pass
        status_code = -1
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass

    # Collect outputs
    stdout = _load_text(workspace_dir / "stdout.log")
    stderr = _load_text(workspace_dir / "stderr.log")
    out_json_path = workspace_dir / "output.json"
    parser_output: dict[str, Any] = {}
    if out_json_path.exists():
        try:
            parser_output = json.loads(out_json_path.read_text())
        except json.JSONDecodeError as e:
            log.warning("output.json parse failed for %s: %r", exam.instance_id, e)

    tests = parser_output.get("tests", [])
    passed = [t["name"] for t in tests if t.get("status") == "PASSED"]
    failed = [t["name"] for t in tests if t.get("status") in ("FAILED", "ERROR")]

    return DockerRunResult(
        status_code=status_code,
        stdout=stdout,
        stderr=stderr,
        parser_output=parser_output,
        workspace_dir=workspace_dir,
        passed_tests=passed,
        failed_tests=failed,
    )
