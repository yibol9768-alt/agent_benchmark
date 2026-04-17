"""SWE-Bench Pro -> bug_exam shim.

A SWE-Bench Pro instance row already carries everything bug_exam needs to
treat the upstream repo as a candidate for fresh-bug injection: a pinned
base_commit, a per-instance Docker image with all build deps, the
``before_repo_set_cmd`` to bring the repo to a runnable state, and a
shippable ``run_script.sh`` + ``parser.py`` pair under
``SWE-bench_Pro-os/run_scripts/<instance_id>/``.

This module:

  - Loads one row from the SWE-Bench Pro JSONL by ``instance_id``.
  - Resolves the per-instance scripts directory.
  - Resolves the Docker Hub image URI (defaults to ``jefzda``'s public mirror).
  - Materializes a clean checkout of the repo at ``base_commit`` and (for the
    bug_exam injector's purposes) leaves the working tree as-is — the
    upstream gold patch is intentionally NOT applied; we want our injector
    to introduce its own NEW bug into the same base state that an upstream
    PR was expected to fix.
  - Builds a partially-populated ``ExamInstance`` skeleton; downstream code
    fills in ``injection_patch`` / ``break_plan`` / ``FAIL_TO_PASS`` /
    ``PASS_TO_PASS`` after the injector + validator run.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..schema import (
    BreakPlan,
    ExamInstance,
    ExamStatus,
    Language,
    make_instance_id,
)

log = logging.getLogger(__name__)

DEFAULT_DOCKERHUB_USERNAME = "jefzda"


def _download_file(url: str, dest: Path, timeout: int = 120) -> bool:
    """Download a file from a URL. Returns True on success."""
    import urllib.request
    if not url or not url.startswith("http"):
        return False
    try:
        # Respect http_proxy / https_proxy env vars for download.
        proxy_url = os.environ.get("http_proxy") or os.environ.get("https_proxy")
        if proxy_url:
            proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = urllib.request.build_opener(proxy_handler)
        else:
            opener = urllib.request.build_opener()
        req = urllib.request.Request(url, headers={"User-Agent": "bug_exam/1.0"})
        resp = opener.open(req, timeout=timeout)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.read())
        log.info("downloaded %s -> %s", url, dest)
        return True
    except Exception as e:
        log.warning("failed to download %s: %r", url, e)
        return False


def _ensure_scripts_from_urls(row: dict, scripts_dir: Path, rs: Path, parser: Path) -> None:
    """If run_script / parsing_script are URLs in the JSONL, download them."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    if not rs.exists():
        url = row.get("run_script", "") or ""
        if url.startswith("http"):
            if not _download_file(url, rs):
                raise FileNotFoundError(f"could not download run_script from {url}")
        else:
            # Inline content (unlikely for this dataset, but handle it)
            if url:
                rs.write_text(url)
            else:
                raise FileNotFoundError(f"no run_script URL/content in row {row.get('instance_id')}")
    if not parser.exists():
        url = row.get("parsing_script", "") or ""
        if url.startswith("http"):
            if not _download_file(url, parser):
                raise FileNotFoundError(f"could not download parsing_script from {url}")
        else:
            if url:
                parser.write_text(url)
            else:
                raise FileNotFoundError(f"no parsing_script URL/content in row {row.get('instance_id')}")


def _parse_list_field(x: Any) -> list[str]:
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        s = x.strip()
        if s.startswith("["):
            return json.loads(s)
        # SWE-Bench Pro CSV-style stringified python list
        return eval(s) if s else []
    return []


def get_dockerhub_image_uri(uid: str, repo: str, dockerhub_username: str = DEFAULT_DOCKERHUB_USERNAME) -> str:
    """Mirror of helper_code/image_uri.py:get_dockerhub_image_uri."""
    repo_base, repo_name_only = repo.lower().split("/")
    hsh = uid.replace("instance_", "")
    if "element-hq" in repo.lower() and "element-web" in repo.lower():
        repo_name_only = "element"
        if hsh.endswith("-vnan"):
            hsh = hsh[:-5]
    elif hsh.endswith("-vnan"):
        hsh = hsh[:-5]
    tag = f"{repo_base}.{repo_name_only}-{hsh}"
    if len(tag) > 128:
        tag = tag[:128]
    return f"{dockerhub_username}/sweap-images:{tag}"


@dataclass
class SwebenchProInstance:
    """Everything bug_exam needs from a SWE-Bench Pro row."""

    instance_id: str
    repo: str                         # "owner/name"
    repo_url: str
    base_commit: str
    problem_statement: str
    gold_patch: str                   # ignored for bug-injection; kept for ref
    test_patch: str                   # original instance's test patch (unused)
    before_repo_set_cmd: str
    selected_test_files: list[str]
    fail_to_pass_orig: list[str]      # the original PR's F2P (not ours)
    pass_to_pass_orig: list[str]      # the original PR's P2P (large clean set)
    image_tag: str
    base_dockerfile_path: str
    instance_dockerfile_path: str
    run_script_path: str
    parser_path: str

    def to_exam_skeleton(self) -> ExamInstance:
        """Empty break_plan / patch — to be filled by the injector pipeline."""
        return ExamInstance(
            instance_id=f"bexam_swepro__{self.instance_id}",
            repo_id=self.instance_id,
            repo_url=self.repo_url,
            language=Language.PYTHON,
            base_commit=self.base_commit,
            injection_patch="",
            break_plan=BreakPlan(target_F=1, target_S=1, steps=[], summary=""),
            injector_model="(pending)",
            patch_hash="0" * 16,
            difficulty_band="swebench_pro_m1",
            F=0,
            S=0,
            FAIL_TO_PASS=[],
            PASS_TO_PASS=[],
            selected_test_files=self.selected_test_files,
            problem_statement="",
            base_dockerfile_path=self.base_dockerfile_path,
            instance_dockerfile_path=self.instance_dockerfile_path,
            run_script_path=self.run_script_path,
            parser_path=self.parser_path,
            test_framework="pytest",
            before_repo_set_cmd=self.before_repo_set_cmd,
            status=ExamStatus.DRAFT,
        )


def load_instance(
    jsonl_path: Path,
    instance_id: str,
    swebench_pro_root: Path,
    dockerhub_username: str = DEFAULT_DOCKERHUB_USERNAME,
) -> SwebenchProInstance:
    """Look up one instance and resolve all the per-instance script paths."""
    row: dict | None = None
    with open(jsonl_path) as f:
        for line in f:
            r = json.loads(line)
            if r["instance_id"] == instance_id:
                row = r
                break
    if row is None:
        raise KeyError(f"instance {instance_id} not in {jsonl_path}")

    scripts_dir = swebench_pro_root / "run_scripts" / instance_id
    rs = scripts_dir / "run_script.sh"
    parser = scripts_dir / "parser.py"
    if not rs.exists() or not parser.exists():
        # Try downloading from URLs stored in the JSONL row.
        _ensure_scripts_from_urls(row, scripts_dir, rs, parser)

    # Optional per-instance dockerfiles (we don't need to build, just reference)
    base_df = swebench_pro_root / "dockerfiles" / "base_dockerfile" / instance_id / "Dockerfile"
    inst_df = swebench_pro_root / "dockerfiles" / "instance_dockerfile" / instance_id / "Dockerfile"

    return SwebenchProInstance(
        instance_id=instance_id,
        repo=row["repo"],
        repo_url=f"https://github.com/{row['repo']}.git",
        base_commit=row["base_commit"],
        problem_statement=row.get("problem_statement", ""),
        gold_patch=row.get("patch", ""),
        test_patch=row.get("test_patch", ""),
        before_repo_set_cmd=row.get("before_repo_set_cmd", ""),
        selected_test_files=_parse_list_field(row.get("selected_test_files_to_run", "[]")),
        fail_to_pass_orig=_parse_list_field(row.get("FAIL_TO_PASS", "[]")),
        pass_to_pass_orig=_parse_list_field(row.get("PASS_TO_PASS", "[]")),
        image_tag=get_dockerhub_image_uri(instance_id, row["repo"], dockerhub_username),
        base_dockerfile_path=str(base_df) if base_df.exists() else "",
        instance_dockerfile_path=str(inst_df) if inst_df.exists() else "",
        run_script_path=str(rs),
        parser_path=str(parser),
    )


def checkout_repo(
    inst: SwebenchProInstance,
    workdir: Path,
    *,
    fresh: bool = True,
) -> None:
    """Clone (or reuse) repo at ``base_commit`` into ``workdir``.

    No before_repo_set_cmd here — the bug_exam injector only needs source
    files visible (no Python packages installed locally; the validator and
    grader run inside the per-instance Docker image which already has them).
    """
    if workdir.exists():
        if fresh:
            subprocess.run(["rm", "-rf", str(workdir)], check=True)
        else:
            return
    workdir.parent.mkdir(parents=True, exist_ok=True)
    # Use a shallow clone with retries — full clones of large repos (qutebrowser
    # is ~150MB of git history) regularly trip GitHub HTTP/2 framing errors.
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
                 inst.repo_url, str(workdir)],
                check=True, timeout=600,
            )
            break
        except subprocess.CalledProcessError as e:
            last_err = e
            log.warning("git clone attempt %d failed: %r", attempt + 1, e)
            if workdir.exists():
                subprocess.run(["rm", "-rf", str(workdir)])
    else:
        raise last_err or RuntimeError("git clone failed")
    # Fetch the specific commit (in case it isn't on the default branch's tip)
    subprocess.run(
        ["git", "fetch", "--quiet", "origin", inst.base_commit],
        cwd=str(workdir),
        check=False, timeout=300,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", inst.base_commit],
        cwd=str(workdir),
        check=True, timeout=300,
    )


def finalize_exam(
    inst: SwebenchProInstance,
    *,
    injection_patch: str,
    plan: BreakPlan,
    injector_model: str,
    fail_to_pass: list[str],
    pass_to_pass: list[str],
    problem_statement: str,
    band_id: str = "swebench_pro_m1",
) -> ExamInstance:
    """Promote the skeleton to a frozen ExamInstance after injection+validation."""
    import hashlib

    ph = hashlib.sha256(injection_patch.encode("utf-8")).hexdigest()
    iid = make_instance_id(inst.instance_id, band_id, ph)
    files = {line[len("+++ b/"):].strip() for line in injection_patch.splitlines()
             if line.startswith("+++ b/")}
    return ExamInstance(
        instance_id=iid,
        repo_id=inst.instance_id,
        repo_url=inst.repo_url,
        language=Language.PYTHON,
        base_commit=inst.base_commit,
        injection_patch=injection_patch,
        break_plan=plan,
        injector_model=injector_model,
        patch_hash=ph,
        difficulty_band=band_id,
        F=len(files),
        S=len(plan.steps),
        FAIL_TO_PASS=fail_to_pass,
        PASS_TO_PASS=pass_to_pass,
        selected_test_files=inst.selected_test_files,
        problem_statement=problem_statement,
        base_dockerfile_path=inst.base_dockerfile_path,
        instance_dockerfile_path=inst.instance_dockerfile_path,
        run_script_path=inst.run_script_path,
        parser_path=inst.parser_path,
        test_framework="pytest",
        before_repo_set_cmd=inst.before_repo_set_cmd,
        status=ExamStatus.FROZEN,
    )
