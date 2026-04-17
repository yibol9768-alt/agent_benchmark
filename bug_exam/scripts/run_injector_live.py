"""Live injector + validator run against the tiny_py_repo fixture.

Builds a fresh git checkout of tests/fixtures/tiny_py_repo/, runs the
injector agent (via whichever LLMClient provider the env selects — GLM by
default), validates each draw with all 8 gates using the local_runner
backend (no Docker), and prints the winning ExamInstance.

Usage:
    export GLM_API_KEY=...           # or ZHIPUAI_API_KEY / ZAI_API_KEY
    export GLM_MODEL=glm-4.5         # optional, default glm-4.5
    # optional: point at z.ai instead of bigmodel.cn
    # export GLM_BASE_URL=https://api.z.ai/api/paas/v4/

    PYTHONPATH=. .venv/bin/python scripts/run_injector_live.py \
        --target-f 1 --target-s 1 --n-draws 2
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from bug_exam.db import Database
from bug_exam.evaluator.local_runner import run_pytest, run_with_patch
from bug_exam.injector.agent import draw_injections
from bug_exam.injector.scrubber import scrub_problem_statement
from bug_exam.llm import make_client, resolve_provider
from bug_exam.schema import (
    ExamInstance,
    ExamStatus,
    Language,
    RepoManifest,
    RepoStatus,
    make_instance_id,
)
from bug_exam.validator.dedup import patch_hash
from bug_exam.validator.test_gates import validate_injection


FIXTURE = ROOT / "tests" / "fixtures" / "tiny_py_repo"
PKG_SRC = "src"


def _log_setup(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _make_git_checkout(src: Path, dest: Path) -> str:
    shutil.copytree(src, dest)
    env = {
        "GIT_AUTHOR_NAME": "bug-exam", "GIT_AUTHOR_EMAIL": "b@e.x",
        "GIT_COMMITTER_NAME": "bug-exam", "GIT_COMMITTER_EMAIL": "b@e.x",
        "HOME": str(dest),
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(dest), check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=str(dest), check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(dest), check=True, env=env)
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(dest), capture_output=True, text=True, check=True, env=env,
    )
    return res.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-f", type=int, default=1)
    ap.add_argument("--target-s", type=int, default=1)
    ap.add_argument("--n-draws", type=int, default=2)
    ap.add_argument("--provider", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    _log_setup(args.verbose)

    provider = resolve_provider(args.provider)
    print(f"== provider: {provider}")
    try:
        client = make_client(provider=provider, model=args.model)
    except Exception as e:
        print(f"!! could not initialize LLM client: {e}", file=sys.stderr)
        print(
            "   Set one of: GLM_API_KEY, ZHIPUAI_API_KEY, ZAI_API_KEY, "
            "ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
        return 2
    print(f"== model:    {client.model}")
    print(f"== target:   F={args.target_f}, S={args.target_s}, draws={args.n_draws}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        repo_dir = tmp_path / "work"
        base_commit = _make_git_checkout(FIXTURE, repo_dir)
        print(f"== checkout: {repo_dir} @ {base_commit[:12]}")

        # Baseline
        baseline = run_pytest(repo_dir, extra_pythonpath=str(repo_dir / PKG_SRC))
        baseline_passing = set(baseline.passed_tests)
        print(f"== baseline: {len(baseline_passing)} passing tests")
        if len(baseline_passing) < 16:
            print(f"!! unexpected baseline size; stdout:\n{baseline.stdout[-1000:]}", file=sys.stderr)
            return 3

        # Inject
        print(f"== injecting bugs ...")
        draws = draw_injections(
            repo_dir=repo_dir,
            target_F=args.target_f,
            target_S=args.target_s,
            n_draws=args.n_draws,
            client=client,
        )
        for i, d in enumerate(draws):
            if d.plan is None:
                print(f"  draw {i}: planner FAILED — {d.planner_error}")
                continue
            print(
                f"  draw {i}: plan ok, {len(d.plan.steps)} step(s), "
                f"diff len={len(d.diff)}, "
                f"in_toks={d.input_tokens}, out_toks={d.output_tokens}"
            )
            for s in d.plan.steps:
                print(f"      - {s.op.value} @ {s.file}:{s.line} :: {s.anchor_snippet[:40]!r}")
            if d.executor_error:
                print(f"      executor error: {d.executor_error}")

        # Validate each draw
        db = Database(tmp_path / "status.db")
        manifest = RepoManifest(
            id="fixture__tiny_py_repo",
            url="file://" + str(FIXTURE),
            owner="fixture", name="tiny_py_repo",
            language=Language.PYTHON,
            stars=0, size_kb=1, license="MIT",
            created_at=datetime(2025, 10, 1),
            pushed_at=datetime(2025, 10, 1),
            base_commit=base_commit,
            default_branch="main",
            status=RepoStatus.BASELINE_OK,
            post_cutoff=True,
            test_framework="pytest",
            baseline_test_count=len(baseline_passing),
        )
        db.upsert_repo(manifest)

        def run_tests_fn(candidate_diff: str) -> tuple[list[str], list[str]]:
            res = run_with_patch(
                repo_dir,
                candidate_diff,
                base_commit=base_commit,
                extra_pythonpath=str(repo_dir / PKG_SRC),
            )
            return res.passed_tests, res.failed_tests

        winner = None
        for i, d in enumerate(draws):
            if d.plan is None or not d.diff:
                continue
            print(f"== validating draw {i} ...")
            try:
                report = validate_injection(
                    repo_dir=repo_dir,
                    base_commit=base_commit,
                    plan=d.plan,
                    diff=d.diff,
                    db=db,
                    image_tag="local",
                    run_tests_fn=run_tests_fn,
                    run_baseline_passing=baseline_passing,
                )
            except Exception as e:
                print(f"   validator crashed: {e}")
                continue
            if report.ok:
                print(f"   ✓ PASS — F={report.unique_files}, S={report.validated_steps}, "
                      f"|F2P|={len(report.fail_to_pass)}, |P2P|={len(report.pass_to_pass)}")
                winner = (d, report)
                break
            else:
                print(f"   × rejected at {report.gate_failed}: {report.failure_reason}")
                print("   --- raw diff ---")
                for ln, raw in enumerate(d.diff.splitlines(), 1):
                    print(f"   {ln:3d} | {raw!r}")
                print("   ----------------")

        if winner is None:
            print("!! no draw passed all gates; see above for gate details", file=sys.stderr)
            return 4

        d, report = winner
        print()
        print("== winning break plan ==")
        print(d.plan.model_dump_json(indent=2))
        print()
        print("== FAIL_TO_PASS ==")
        for t in report.fail_to_pass:
            print(f"  - {t}")
        print()
        print("== scrubbing problem statement ...")
        scrubbed = scrub_problem_statement(
            draft=d.plan.summary,
            failing_test_assertions=report.fail_to_pass[:3],
            client=client,
        )
        print()
        print("== scrubbed problem statement ==")
        print(scrubbed)
        print()

        # Build the ExamInstance
        ph = patch_hash(d.diff)
        exam = ExamInstance(
            instance_id=make_instance_id(manifest.id, "trivial", ph),
            repo_id=manifest.id,
            repo_url=manifest.url,
            language=Language.PYTHON,
            base_commit=base_commit,
            injection_patch=d.diff,
            break_plan=d.plan,
            injector_model=client.model,
            patch_hash=ph,
            difficulty_band="trivial",
            F=report.unique_files,
            S=report.validated_steps,
            FAIL_TO_PASS=report.fail_to_pass,
            PASS_TO_PASS=report.pass_to_pass,
            selected_test_files=[],
            problem_statement=scrubbed,
            base_dockerfile_path="",
            instance_dockerfile_path="",
            run_script_path="",
            parser_path="",
            test_framework="pytest",
            post_cutoff=True,
            status=ExamStatus.VALIDATED,
            mutation_op_histogram={
                op.value: sum(1 for s in d.plan.steps if s.op == op)
                for op in {s.op for s in d.plan.steps}
            },
        )
        db.upsert_exam(exam)
        print(f"== wrote exam {exam.instance_id}")
        print()
        print("== injection diff ==")
        print(d.diff)
        return 0


if __name__ == "__main__":
    sys.exit(main())
