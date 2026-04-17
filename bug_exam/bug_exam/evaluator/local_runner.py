"""Local (non-Docker) test runner.

Runs pytest against a repo checkout on the host. Used for:
  - offline integration tests (no Docker needed)
  - quick iteration during development
  - the solvability oracle's private test execution

Same contract as docker_runner: given a repo_dir + patch text, apply the
patch, run the test suite, parse junit.xml, return (passing, failing) lists.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class LocalRunResult:
    status_code: int
    stdout: str
    stderr: str
    passed_tests: list[str]
    failed_tests: list[str]


def _parse_junit(xml_path: Path) -> tuple[list[str], list[str]]:
    """Parse a junit.xml file into (passing, failing) test-id lists."""
    import xml.etree.ElementTree as ET
    if not xml_path.exists():
        return [], []
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as e:
        log.warning("junit parse failed: %r", e)
        return [], []
    passed: list[str] = []
    failed: list[str] = []
    suites = root.findall(".//testsuite") or [root]
    for suite in suites:
        for case in suite.findall("testcase"):
            classname = case.get("classname", "")
            name = case.get("name", "")
            full = f"{classname}::{name}" if classname else name
            if case.find("failure") is not None or case.find("error") is not None:
                failed.append(full)
            elif case.find("skipped") is not None:
                pass  # skipped tests are neither passing nor failing
            else:
                passed.append(full)
    return passed, failed


def apply_patch(repo_dir: Path, patch_text: str) -> tuple[bool, str]:
    """git apply the patch in repo_dir. Returns (ok, stderr_excerpt).

    Uses --recount so LLM-generated diffs with wrong hunk-header counts still
    apply (LLMs get those counts wrong a lot).
    """
    if not patch_text.strip():
        return True, ""
    patch_file = repo_dir / ".bug_exam_localrun.diff"
    patch_file.write_text(patch_text)
    try:
        res = subprocess.run(
            ["git", "apply", "--recount", "--whitespace=nowarn", str(patch_file.name)],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
        return res.returncode == 0, res.stderr
    finally:
        try:
            patch_file.unlink()
        except Exception:
            pass


def reset_checkout(repo_dir: Path, base_commit: str | None = None) -> None:
    """git reset --hard to the base commit (or HEAD) + git clean -fd."""
    target = base_commit or "HEAD"
    subprocess.run(["git", "reset", "--hard", target], cwd=str(repo_dir),
                   capture_output=True, timeout=60)
    subprocess.run(["git", "clean", "-fd"], cwd=str(repo_dir),
                   capture_output=True, timeout=60)


def run_pytest(
    repo_dir: Path,
    *,
    python_executable: str | None = None,
    extra_pythonpath: str | None = None,
    test_paths: list[str] | None = None,
    timeout_s: int = 600,
) -> LocalRunResult:
    """Run pytest against repo_dir with junit output.

    Does NOT apply or revert patches; caller is responsible for state.
    """
    py = python_executable or sys.executable
    junit_path = repo_dir / ".bug_exam_junit.xml"
    if junit_path.exists():
        junit_path.unlink()

    env = os.environ.copy()
    if extra_pythonpath:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{extra_pythonpath}{os.pathsep}{existing}" if existing else extra_pythonpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    cmd = [py, "-m", "pytest", "--tb=no", "-q", f"--junitxml={junit_path.name}"]
    if test_paths:
        cmd += test_paths

    try:
        res = subprocess.run(
            cmd, cwd=str(repo_dir), capture_output=True, text=True,
            timeout=timeout_s, env=env,
        )
        status = res.returncode
        stdout = res.stdout
        stderr = res.stderr
    except subprocess.TimeoutExpired as e:
        status = -1
        stdout = (e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = f"pytest timed out after {timeout_s}s"

    passed, failed = _parse_junit(junit_path)
    # Clean up junit file so the workdir is ready for the next run
    if junit_path.exists():
        try:
            junit_path.unlink()
        except Exception:
            pass

    return LocalRunResult(
        status_code=status, stdout=stdout, stderr=stderr,
        passed_tests=passed, failed_tests=failed,
    )


def run_with_patch(
    repo_dir: Path,
    patch_text: str,
    base_commit: str | None = None,
    *,
    python_executable: str | None = None,
    extra_pythonpath: str | None = None,
    test_paths: list[str] | None = None,
    timeout_s: int = 600,
) -> LocalRunResult:
    """Apply patch, run pytest, reset to base. Stateful helper — the caller's
    checkout is left in its original state after the call returns."""
    reset_checkout(repo_dir, base_commit)
    ok, stderr = apply_patch(repo_dir, patch_text)
    if not ok:
        reset_checkout(repo_dir, base_commit)
        return LocalRunResult(
            status_code=-2, stdout="", stderr=f"apply failed: {stderr}",
            passed_tests=[], failed_tests=[],
        )
    try:
        result = run_pytest(
            repo_dir,
            python_executable=python_executable,
            extra_pythonpath=extra_pythonpath,
            test_paths=test_paths,
            timeout_s=timeout_s,
        )
    finally:
        reset_checkout(repo_dir, base_commit)
    return result
