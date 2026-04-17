"""Run an ExamInstance against a SWE-Bench Pro per-instance Docker image.

This is a thin wrapper around ``run_exam_in_docker``. The only reason it
exists separately is to (a) document the contract — the ExamInstance MUST
have its ``*_dockerfile_path`` / ``run_script_path`` / ``parser_path``
populated from a SWE-Bench Pro row, and the ``image_tag`` is the public
``jefzda/sweap-images:<tag>`` mirror — and (b) handle the
"image not found locally, pull via configured proxy" path with a friendly
error when the proxy isn't set on the Docker daemon.

The container runs the SWE-Bench-Pro-style entryscript:
  1. git reset --hard <base_commit>
  2. git apply bug_patch.diff   (the bug_exam-injected synthetic bug)
  3. git apply patch.diff       (the solver's candidate fix; empty when grading bug-only)
  4. before_repo_set_cmd
  5. bash run_script.sh
  6. python parser.py -> output.json

Returns the same DockerRunResult as docker_runner.run_exam_in_docker.
"""
from __future__ import annotations

import logging
import platform
from pathlib import Path

from ..schema import ExamInstance
from .docker_runner import DockerRunResult, run_exam_in_docker

log = logging.getLogger(__name__)


def _default_platform() -> str | None:
    if platform.machine() in ("arm64", "aarch64"):
        return "linux/amd64"
    return None


def run_swebench_pro_exam(
    exam: ExamInstance,
    *,
    image_tag: str,
    solver_patch: str,
    runs_root: Path,
    run_id: str,
    patch_kind: str = "solver",
    timeout_s: int = 1800,
    block_network: bool = False,
    docker_platform: str | None = None,
) -> DockerRunResult:
    """Convenience wrapper: ``run_exam_in_docker`` against a SWE-Bench Pro image."""
    plat = docker_platform or _default_platform()
    log.info(
        "swebench_pro: running %s/%s in %s (image=%s)",
        exam.instance_id, run_id, runs_root, image_tag,
    )
    return run_exam_in_docker(
        exam=exam,
        solver_patch=solver_patch,
        image_tag=image_tag,
        runs_root=runs_root,
        run_id=run_id,
        patch_kind=patch_kind,
        timeout_s=timeout_s,
        block_network=block_network,
        docker_platform=plat,
    )
