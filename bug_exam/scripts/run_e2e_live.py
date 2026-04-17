"""End-to-end Bug Exam Bench pipeline against tiny_py_repo, live.

Runs the entire pipeline locally (no Docker, no Modal, no GitHub) using
whichever LLMClient provider the env picks — by default GLM-5.1 via the
Zhipu Anthropic-compat endpoint.

Stages:
  1. Git-init a fresh copy of tests/fixtures/tiny_py_repo/
  2. Baseline pytest run → passing-test set
  3. Injector: draw N candidate bugs in parallel, validate each with all 8
     gates, freeze the first one that passes
  4. For each enabled solver, prepare a fresh buggy checkout, invoke the
     solver, collect its patch
  5. For each solver patch: reset repo → apply bug → apply solver patch →
     run pytest → grade against FAIL_TO_PASS / PASS_TO_PASS
  6. Fit Bradley-Terry + print leaderboard JSON

Usage:
    export ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
    export ANTHROPIC_AUTH_TOKEN=<your-glm-coding-plan-token>
    export BUG_EXAM_PROVIDER=anthropic
    export ANTHROPIC_MODEL=glm-5.1

    PYTHONPATH=. .venv/bin/python scripts/run_e2e_live.py \\
        --solvers claude_direct --n-draws 3
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from bug_exam.db import Database
from bug_exam.evaluator.local_runner import (
    apply_patch,
    reset_checkout,
    run_pytest,
    run_with_patch,
)
from bug_exam.evaluator.scoring import grade_run
from bug_exam.injector.agent import draw_injections
from bug_exam.injector.scrubber import scrub_problem_statement
from bug_exam.llm import make_client, resolve_provider
from bug_exam.schema import (
    ExamInstance,
    ExamStatus,
    Language,
    RepoManifest,
    RepoStatus,
    RunStatus,
    make_instance_id,
)
from bug_exam.scoring.bradley_terry import build_pairwise_from_grades, fit as bt_fit
from bug_exam.scoring.elo import batch_update
from bug_exam.scoring.leaderboard import build_leaderboard
from bug_exam.validator.dedup import patch_hash
from bug_exam.validator.test_gates import validate_injection


FIXTURE = ROOT / "tests" / "fixtures" / "tiny_py_repo"
PKG_SRC = "src"


def _log_setup(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _clone_fresh(src: Path, dest: Path) -> str:
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    (dest / ".gitignore").write_text("__pycache__/\n*.pyc\n*.pyo\n.pytest_cache/\n")
    env = {
        "GIT_AUTHOR_NAME": "bug-exam",
        "GIT_AUTHOR_EMAIL": "b@e.x",
        "GIT_COMMITTER_NAME": "bug-exam",
        "GIT_COMMITTER_EMAIL": "b@e.x",
        "HOME": str(dest),
        "PATH": os.environ.get("PATH", ""),
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(dest), check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=str(dest), check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(dest), check=True, env=env)
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(dest),
        capture_output=True, text=True, check=True, env=env,
    )
    return res.stdout.strip()


def _load_solver_classes(names: list[str]):
    """Load SolverAdapter instances from configs/solvers.yaml."""
    import yaml
    cfg = yaml.safe_load((ROOT / "configs" / "solvers.yaml").read_text())
    out = []
    for name in names:
        spec = cfg["solvers"].get(name)
        if spec is None:
            print(f"!! unknown solver {name!r}", file=sys.stderr)
            continue
        module = importlib.import_module(spec["module"])
        cls = getattr(module, spec["class"])
        kwargs = {k: v for k, v in spec.items() if k not in ("module", "class", "enabled")}
        out.append(cls(**kwargs))
    return out


def _stage_inject(repo_dir: Path, base_commit: str, baseline_passing: set[str],
                  target_f: int, target_s: int, n_draws: int, client, db, manifest) -> ExamInstance | None:
    print(f"\n== stage: inject (F={target_f}, S={target_s}, draws={n_draws})")
    draws = draw_injections(
        repo_dir=repo_dir,
        target_F=target_f,
        target_S=target_s,
        n_draws=n_draws,
        client=client,
    )
    for i, d in enumerate(draws):
        if d.plan is None:
            print(f"  draw {i}: planner FAILED — {d.planner_error}")
            continue
        print(
            f"  draw {i}: plan ok, {len(d.plan.steps)} step(s), "
            f"diff len={len(d.diff)}, tokens in={d.input_tokens} out={d.output_tokens}"
        )
        for s in d.plan.steps:
            print(f"      - {s.op.value} @ {s.file}:{s.line}")

    def run_tests_fn(candidate_diff: str) -> tuple[list[str], list[str]]:
        res = run_with_patch(
            repo_dir, candidate_diff,
            base_commit=base_commit,
            extra_pythonpath=str(repo_dir / PKG_SRC),
        )
        return res.passed_tests, res.failed_tests

    print(f"\n== stage: validate")
    for i, d in enumerate(draws):
        if d.plan is None or not d.diff:
            continue
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
            print(f"  draw {i}: validator crashed: {e}")
            continue
        if report.ok:
            print(
                f"  draw {i}: ✓ PASS — F={report.unique_files}, S={report.validated_steps}, "
                f"|F2P|={len(report.fail_to_pass)}, |P2P|={len(report.pass_to_pass)}"
            )
            # Scrub problem statement
            scrubbed = scrub_problem_statement(
                draft=d.plan.summary,
                failing_test_assertions=report.fail_to_pass[:3],
                client=client,
            )
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
                base_dockerfile_path="", instance_dockerfile_path="",
                run_script_path="", parser_path="",
                test_framework="pytest",
                post_cutoff=True,
                status=ExamStatus.FROZEN,
                mutation_op_histogram={
                    op.value: sum(1 for s in d.plan.steps if s.op == op)
                    for op in {s.op for s in d.plan.steps}
                },
            )
            db.upsert_exam(exam)
            db.set_exam_status(exam.instance_id, ExamStatus.FROZEN)
            return exam
        else:
            print(f"  draw {i}: × {report.gate_failed}: {report.failure_reason}")
    return None


def _make_buggy_checkout(src_repo: Path, bug_patch: str, dest: Path, base_commit: str) -> str:
    """Clone src_repo to dest, apply bug patch, return new HEAD after bug commit."""
    env = {
        "GIT_AUTHOR_NAME": "bug-exam",
        "GIT_AUTHOR_EMAIL": "b@e.x",
        "GIT_COMMITTER_NAME": "bug-exam",
        "GIT_COMMITTER_EMAIL": "b@e.x",
        "HOME": str(dest),
        "PATH": os.environ.get("PATH", ""),
    }
    subprocess.run(["git", "clone", "-q", str(src_repo), str(dest)], check=True, env=env)
    subprocess.run(["git", "checkout", "-q", base_commit], cwd=str(dest), check=True, env=env)
    patch_file = dest / ".inject.diff"
    patch_file.write_text(bug_patch)
    subprocess.run(
        ["git", "apply", "--recount", "--whitespace=nowarn", str(patch_file.name)],
        cwd=str(dest), check=True, env=env,
    )
    patch_file.unlink()
    subprocess.run(["git", "add", "-A"], cwd=str(dest), check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "bug_exam: apply injection"],
        cwd=str(dest), check=True, env=env,
    )
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(dest),
        capture_output=True, text=True, check=True, env=env,
    )
    return res.stdout.strip()


def _stage_solve(exam: ExamInstance, src_repo: Path, base_commit: str,
                 solvers, tmp_path: Path, db: Database) -> list[tuple[str, str, str]]:
    """Run each solver. Returns a list of (run_id, solver_name, solver_patch)."""
    print(f"\n== stage: solve ({len(solvers)} solvers)")
    results: list[tuple[str, str, str]] = []
    for solver in solvers:
        solver_workdir = tmp_path / "solver_workspaces" / solver.name
        solver_workdir.parent.mkdir(parents=True, exist_ok=True)
        buggy_head = _make_buggy_checkout(src_repo, exam.injection_patch, solver_workdir, base_commit)
        print(f"  [{solver.name}] workdir={solver_workdir} buggy_head={buggy_head[:12]}")
        run_id = f"{exam.instance_id}__{solver.name}__{uuid.uuid4().hex[:8]}"
        try:
            result = solver.solve(exam, solver_workdir, timeout_s=900)
            if result.errored:
                print(f"  [{solver.name}] errored: {result.error_message}")
            else:
                print(
                    f"  [{solver.name}] produced patch len={len(result.patch)}, "
                    f"{result.wall_clock_s:.1f}s, tokens={result.token_usage}"
                )
            db.upsert_run(
                run_id=run_id,
                exam_id=exam.instance_id,
                solver_name=solver.name,
                result=result,
                status=RunStatus.COMPLETED if not result.errored else RunStatus.ERRORED,
                error_message=result.error_message,
            )
            results.append((run_id, solver.name, result.patch))
        except Exception as e:
            print(f"  [{solver.name}] crashed: {e}")
            results.append((run_id, solver.name, ""))
    return results


def _stage_grade(exam: ExamInstance, base_repo: Path, base_commit: str,
                 solver_results: list[tuple[str, str, str]], db: Database) -> None:
    """Grade each solver patch: apply bug + apply solver patch + run tests."""
    print(f"\n== stage: grade")
    for run_id, solver_name, solver_patch in solver_results:
        reset_checkout(base_repo, base_commit)
        ok_bug, err_bug = apply_patch(base_repo, exam.injection_patch)
        if not ok_bug:
            print(f"  [{solver_name}] bug patch didn't apply: {err_bug[:200]}")
            reset_checkout(base_repo, base_commit)
            continue
        solver_applied = True
        if solver_patch.strip():
            ok_sol, err_sol = apply_patch(base_repo, solver_patch)
            if not ok_sol:
                print(f"  [{solver_name}] solver patch didn't apply: {err_sol[:200]}")
                solver_applied = False
        res = run_pytest(base_repo, extra_pythonpath=str(base_repo / PKG_SRC))
        grade = grade_run(
            exam=exam,
            passed_tests=res.passed_tests,
            failed_tests=res.failed_tests,
            run_id=run_id,
            solver_name=solver_name,
        )
        db.upsert_grade(grade)
        applied_str = "" if solver_applied else " (solver patch didn't apply)"
        print(
            f"  [{solver_name}] f2p_pass={grade.f2p_pass} p2p_pass={grade.p2p_pass} "
            f"final={grade.final_passed}{applied_str}"
        )
        reset_checkout(base_repo, base_commit)


def _stage_score(db: Database) -> None:
    print(f"\n== stage: score + leaderboard")
    payload = build_leaderboard(db)
    print(f"  n_runs={payload['n_runs']}, n_exams={payload['n_exams']}, n_pairs={payload['n_pairs']}")
    print()
    hdr = f"{'solver':<20} {'BT':>7}  {'BT CI':>18}  {'Elo':>7}  {'pass@1':>8}  {'runs':>5}"
    print(hdr)
    print("-" * len(hdr))
    for e in payload["solvers"]:
        ci = f"[{e['bt_ci_lo']:+.2f},{e['bt_ci_hi']:+.2f}]"
        print(
            f"{e['solver_name']:<20} {e['bt_rating']:+7.3f}  {ci:>18}  "
            f"{e['elo_rating']:7.1f}  {e['pass_rate_overall']*100:7.1f}%  {e['n_runs']:>5}"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-f", type=int, default=1)
    ap.add_argument("--target-s", type=int, default=1)
    ap.add_argument("--n-draws", type=int, default=3)
    ap.add_argument("--solvers", default="claude_direct",
                    help="comma-separated solver names (from configs/solvers.yaml)")
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
        return 2
    print(f"== model:    {client.model}")
    print(f"== fixture:  {FIXTURE}")

    solver_names = [s.strip() for s in args.solvers.split(",") if s.strip()]
    try:
        solvers = _load_solver_classes(solver_names)
    except Exception as e:
        print(f"!! could not load solvers: {e}", file=sys.stderr)
        return 3
    if not solvers:
        print("!! no solvers loaded", file=sys.stderr)
        return 3
    print(f"== solvers:  {[s.name for s in solvers]}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        repo_dir = tmp_path / "work"
        base_commit = _clone_fresh(FIXTURE, repo_dir)
        print(f"== checkout: {repo_dir} @ {base_commit[:12]}")

        baseline = run_pytest(repo_dir, extra_pythonpath=str(repo_dir / PKG_SRC))
        baseline_passing = set(baseline.passed_tests)
        print(f"== baseline: {len(baseline_passing)} passing tests")

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

        exam = _stage_inject(
            repo_dir, base_commit, baseline_passing,
            args.target_f, args.target_s, args.n_draws,
            client, db, manifest,
        )
        if exam is None:
            print("!! no draw passed validation", file=sys.stderr)
            return 4

        print(f"\n== exam {exam.instance_id}")
        print(f"   op(s): {[s.op.value for s in exam.break_plan.steps]}")
        print(f"   F2P:   {exam.FAIL_TO_PASS}")

        solver_results = _stage_solve(exam, repo_dir, base_commit, solvers, tmp_path, db)
        _stage_grade(exam, repo_dir, base_commit, solver_results, db)
        _stage_score(db)

    return 0


if __name__ == "__main__":
    sys.exit(main())
