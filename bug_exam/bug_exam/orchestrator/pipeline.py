"""End-to-end pipeline implementations.

Each function is a stage that can be called idempotently. They all write
their progress to the sqlite DB, so they're resumable — re-running a stage
picks up where the last call left off.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

import yaml

from ..db import Database
from ..envbuild.detector import detect as detect_framework
from ..envbuild.runner import EnvBuilder, dockerhub_safe_tag, instance_id_for
from ..evaluator.docker_runner import run_exam_in_docker
from ..evaluator.scoring import grade_run
from ..harvester.github_search import harvest as harvest_repos
from ..injector.agent import draw_injections
from ..injector.scrubber import scrub_problem_statement
from ..schema import (
    BreakPlan,
    ExamInstance,
    ExamStatus,
    Language,
    RepoManifest,
    RepoStatus,
    RunStatus,
    make_instance_id,
)
from ..solvers.base import load_solver
from ..validator.ast_diff import files_touched
from ..validator.dedup import patch_hash
from ..validator.test_gates import validate_injection

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CONFIGS = ROOT / "configs"


# ---------------------------------------------------------------------------
# Stage: harvest
# ---------------------------------------------------------------------------

def stage_harvest(db: Database, language: str | None = None, max_candidates: int | None = None) -> int:
    """Query GitHub, insert RepoManifests with status=CANDIDATE."""
    cfg_path = CONFIGS / "harvester.yaml"
    n = 0
    for repo in harvest_repos(str(cfg_path), language=language, max_candidates=max_candidates):
        db.upsert_repo(repo)
        n += 1
    log.info("harvest: inserted %d candidate repos", n)
    return n


# ---------------------------------------------------------------------------
# Stage: envbuild (clone, detect, dockerfile, build image)
# ---------------------------------------------------------------------------

def _clone_repo(repo: RepoManifest, dest: Path) -> bool:
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "200", repo.url, str(dest)],
            capture_output=True, text=True, timeout=300, check=True,
        )
        subprocess.run(
            ["git", "checkout", repo.base_commit],
            cwd=str(dest), capture_output=True, text=True, timeout=60, check=True,
        )
        return True
    except Exception as e:
        log.warning("clone failed for %s: %r", repo.id, e)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        return False


def stage_envbuild(db: Database, limit: int | None = None) -> int:
    """For every CANDIDATE repo: clone, detect framework, render + build image."""
    builder = EnvBuilder(DATA)
    n_ok = 0
    for repo in db.list_repos(RepoStatus.CANDIDATE):
        if limit is not None and n_ok >= limit:
            break
        clone_dir = DATA / "repo_cache" / repo.id
        if not _clone_repo(repo, clone_dir):
            db.set_repo_status(repo.id, RepoStatus.REJECTED)
            continue

        spec = detect_framework(clone_dir, CONFIGS / "languages.yaml")
        if spec is None:
            log.warning("no test framework detected in %s", repo.id)
            db.set_repo_status(repo.id, RepoStatus.REJECTED)
            continue
        repo.test_framework = spec.name
        db.upsert_repo(repo)

        df_path, rs_path, parser_path, info_path, iid = builder.materialize(repo, spec)
        image_tag = dockerhub_safe_tag(repo)

        ok = builder.build_image(df_path, image_tag)
        if not ok:
            db.upsert_envbuild(repo.id, image_tag, "", str(df_path), "", "build_failed", str(df_path.parent / "build.log"))
            db.set_repo_status(repo.id, RepoStatus.REJECTED)
            continue

        # Baseline runs
        runs_dir = DATA / "runs" / f"envbuild_{repo.id}"
        baseline = builder.run_baseline(image_tag, rs_path, parser_path, runs_dir)
        if not baseline.stable:
            log.warning("baseline unstable for %s: %d stable / %d flaky",
                        repo.id, len(baseline.passing_tests), len(baseline.flaky_tests))
            db.upsert_envbuild(repo.id, image_tag, image_tag, str(df_path), str(df_path), "unstable_baseline", "")
            db.set_repo_status(repo.id, RepoStatus.REJECTED)
            continue

        # Store baseline passing set next to run_scripts for later reuse
        baseline_path = rs_path.parent / "baseline_passing.json"
        baseline_path.write_text(json.dumps({
            "passing": sorted(baseline.passing_tests),
            "flaky": sorted(baseline.flaky_tests),
            "all_seen": sorted(baseline.all_tests_seen),
        }, indent=2))

        repo.baseline_test_count = len(baseline.passing_tests)
        db.upsert_repo(repo)
        db.upsert_envbuild(repo.id, image_tag, image_tag, str(df_path), str(df_path), "ok", "")
        db.set_repo_status(repo.id, RepoStatus.BASELINE_OK)
        n_ok += 1

    log.info("envbuild: %d repos reached BASELINE_OK", n_ok)
    return n_ok


# ---------------------------------------------------------------------------
# Stage: inject (+ validate)
# ---------------------------------------------------------------------------

def _load_baseline(repo_id: str) -> set[str]:
    iid = next(
        (p.name for p in (DATA / "run_scripts").iterdir() if p.name.startswith(f"bexam__{repo_id}__")),
        None,
    )
    if iid is None:
        return set()
    path = DATA / "run_scripts" / iid / "baseline_passing.json"
    if not path.exists():
        return set()
    return set(json.loads(path.read_text()).get("passing", []))


def stage_inject_and_validate(
    db: Database,
    bands: list[tuple[int, int, str]],
    n_draws: int = 4,
    injector_model: str = "claude-opus-4-6",
    limit_repos: int | None = None,
) -> int:
    """For every BASELINE_OK repo, draw bugs at each (F, S) band and validate."""
    n_exams = 0
    repos = db.list_repos(RepoStatus.BASELINE_OK)
    if limit_repos:
        repos = repos[:limit_repos]
    for repo in repos:
        repo_dir = DATA / "repo_cache" / repo.id
        if not repo_dir.exists():
            continue
        image_tag = dockerhub_safe_tag(repo)
        baseline_passing = _load_baseline(repo.id)
        if not baseline_passing:
            log.warning("no baseline for %s, skipping", repo.id)
            continue

        for target_F, target_S, band_id in bands:
            log.info("injecting (F=%d, S=%d) on %s", target_F, target_S, repo.id)
            draws = draw_injections(
                repo_dir=repo_dir,
                target_F=target_F,
                target_S=target_S,
                n_draws=n_draws,
                model=injector_model,
                image_tag=image_tag,
            )
            winner = _pick_winner(repo, draws, band_id, db, image_tag, baseline_passing)
            if winner is None:
                log.warning("no valid draw for %s at band %s", repo.id, band_id)
                continue
            db.upsert_exam(winner)
            n_exams += 1
    log.info("inject+validate: %d exams created", n_exams)
    return n_exams


def _pick_winner(
    repo: RepoManifest,
    draws: list,
    band_id: str,
    db: Database,
    image_tag: str,
    baseline_passing: set[str],
) -> ExamInstance | None:
    repo_dir = DATA / "repo_cache" / repo.id

    # Try each draw in order. On validation pass, return an ExamInstance.
    for draw in draws:
        if draw.plan is None or not draw.diff:
            continue

        # Reset repo_dir to base_commit so validator sees a clean starting state
        try:
            subprocess.run(["git", "reset", "--hard", repo.base_commit],
                           cwd=str(repo_dir), capture_output=True, timeout=60)
            subprocess.run(["git", "clean", "-fd"], cwd=str(repo_dir), capture_output=True, timeout=60)
        except Exception:
            pass

        # G6/G7 runner closure: apply the candidate diff inside a throwaway
        # container built from the repo image, run tests, return passing set.
        def run_tests_fn(diff: str) -> tuple[list[str], list[str]]:
            return _run_tests_with_patch(repo, diff, image_tag)

        try:
            report = validate_injection(
                repo_dir=repo_dir,
                base_commit=repo.base_commit,
                plan=draw.plan,
                diff=draw.diff,
                db=db,
                image_tag=image_tag,
                run_tests_fn=run_tests_fn,
                run_baseline_passing=baseline_passing,
            )
        except Exception as e:
            log.warning("validation raised for draw on %s: %r", repo.id, e)
            continue

        # Reset again so the next draw (or the solve stage) starts clean
        try:
            subprocess.run(["git", "reset", "--hard", repo.base_commit],
                           cwd=str(repo_dir), capture_output=True, timeout=60)
            subprocess.run(["git", "clean", "-fd"], cwd=str(repo_dir), capture_output=True, timeout=60)
        except Exception:
            pass

        if not report.ok:
            log.info("draw rejected on gate %s: %s", report.gate_failed, report.failure_reason)
            continue

        uniq_files = files_touched(draw.diff)
        # Scrub problem statement
        scrubbed = scrub_problem_statement(
            draft=draw.plan.summary,
            failing_test_assertions=report.fail_to_pass[:3],
        )
        ph = patch_hash(draw.diff)
        iid = make_instance_id(repo.id, band_id, ph)
        df_root = DATA / "dockerfiles" / "base"
        rs_root = DATA / "run_scripts"
        bexam_iid = instance_id_for(repo)
        exam = ExamInstance(
            instance_id=iid,
            repo_id=repo.id,
            repo_url=repo.url,
            language=repo.language,
            base_commit=repo.base_commit,
            injection_patch=draw.diff,
            break_plan=draw.plan,
            injector_model="claude-opus-4-6",
            patch_hash=ph,
            difficulty_band=band_id,
            F=len(uniq_files),
            S=report.validated_steps,
            FAIL_TO_PASS=report.fail_to_pass,
            PASS_TO_PASS=report.pass_to_pass,
            selected_test_files=[],  # empty = run full suite
            problem_statement=scrubbed,
            base_dockerfile_path=str(df_root / bexam_iid / "Dockerfile"),
            instance_dockerfile_path=str(df_root / bexam_iid / "Dockerfile"),
            run_script_path=str(rs_root / bexam_iid / "run_script.sh"),
            parser_path=str(rs_root / bexam_iid / "parser.py"),
            test_framework=repo.test_framework or "",
            post_cutoff=repo.post_cutoff,
            mutation_op_histogram={
                op.value: sum(1 for s in draw.plan.steps if s.op == op)
                for op in {s.op for s in draw.plan.steps}
            },
            status=ExamStatus.VALIDATED,
        )
        return exam
    return None


def _run_tests_with_patch(repo: RepoManifest, diff: str, image_tag: str) -> tuple[list[str], list[str]]:
    """Apply diff to an ephemeral workspace and run the repo's test suite."""
    try:
        import docker
    except ImportError:
        raise RuntimeError("docker SDK not installed")

    # Produce a minimal Exam-like stand-in so we can reuse entryscript
    # Much simpler: just run in a container with the diff applied.
    client = docker.from_env()
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        (w / "patch.diff").write_text(diff)
        shutil.copy(DATA / "run_scripts" / instance_id_for(repo) / "run_script.sh", w / "run_script.sh")
        shutil.copy(DATA / "run_scripts" / instance_id_for(repo) / "parser.py", w / "parser.py")
        (w / "entryscript.sh").write_text(f"""#!/bin/bash
set -uo pipefail
cd /app
git reset --hard {repo.base_commit}
git clean -fd
git apply -v /workspace/patch.diff || true
bash /workspace/run_script.sh > /workspace/stdout.log 2> /workspace/stderr.log || true
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
""")
        plat = "linux/amd64"
        try:
            container = client.containers.run(
                image_tag,
                entrypoint="/bin/bash",
                command=["-c", "bash /workspace/entryscript.sh"],
                volumes={str(w): {"bind": "/workspace", "mode": "rw"}},
                working_dir="/app",
                detach=True,
                platform=plat,
                mem_limit="8g",
                nano_cpus=4 * 10**9,
            )
            try:
                container.wait(timeout=1200)
            finally:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
        except Exception as e:
            log.warning("test-run container failed: %r", e)
            return [], []

        out_path = w / "output.json"
        if not out_path.exists():
            return [], []
        tests = json.loads(out_path.read_text()).get("tests", [])
        passing = [t["name"] for t in tests if t.get("status") == "PASSED"]
        failing = [t["name"] for t in tests if t.get("status") in ("FAILED", "ERROR")]
        return passing, failing


# ---------------------------------------------------------------------------
# Stage: freeze (snapshot a named exam set as JSONL)
# ---------------------------------------------------------------------------

def stage_freeze(db: Database, exam_set_name: str) -> Path:
    out_dir = DATA / "exam_set"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{exam_set_name}.jsonl"
    exams = db.list_exams(ExamStatus.VALIDATED)
    with open(out_path, "w") as f:
        for exam in exams:
            exam.status = ExamStatus.FROZEN
            db.set_exam_status(exam.instance_id, ExamStatus.FROZEN)
            f.write(exam.model_dump_json() + "\n")
    log.info("froze %d exams into %s", len(exams), out_path)
    return out_path


# ---------------------------------------------------------------------------
# Stage: solve (run each enabled solver against each frozen exam)
# ---------------------------------------------------------------------------

def stage_solve(db: Database, solver_names: list[str], limit_exams: int | None = None) -> int:
    """Run selected solvers against frozen exams. Fresh workspace per run."""
    cfg_path = CONFIGS / "solvers.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    solver_cfgs = cfg["solvers"]

    solvers = []
    for name in solver_names:
        spec = solver_cfgs.get(name)
        if not spec:
            log.warning("unknown solver %s", name)
            continue
        try:
            solvers.append(load_solver(spec))
        except Exception as e:
            log.warning("could not load solver %s: %r", name, e)

    exams = db.list_exams(ExamStatus.FROZEN)
    if not exams:
        exams = db.list_exams(ExamStatus.VALIDATED)
    if limit_exams:
        exams = exams[:limit_exams]

    n_runs = 0
    for exam in exams:
        for solver in solvers:
            # Prepare a fresh buggy workspace: copy repo_cache, apply injection patch
            workdir = DATA / "runs" / exam.instance_id / solver.name / "workspace"
            _prepare_buggy_workspace(exam, workdir)
            try:
                result = solver.solve(exam, workdir, timeout_s=solver_cfgs[solver.name].get("timeout_s", 1800))
            except Exception as e:
                result = None
                log.warning("solver %s crashed on %s: %r", solver.name, exam.instance_id, e)

            run_id = f"{exam.instance_id}__{solver.name}__{uuid.uuid4().hex[:8]}"
            status = RunStatus.COMPLETED if result and not result.errored else RunStatus.ERRORED
            db.upsert_run(run_id, exam.instance_id, solver.name, result, status,
                          error_message=(result.error_message if result else "solver crashed"))
            n_runs += 1
    log.info("solve: recorded %d runs", n_runs)
    return n_runs


def _prepare_buggy_workspace(exam: ExamInstance, workdir: Path) -> None:
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    src = DATA / "repo_cache" / exam.repo_id
    if not src.exists():
        raise RuntimeError(f"repo_cache missing for {exam.repo_id}")
    # Clean copy
    subprocess.run(["git", "clone", str(src), str(workdir)],
                   capture_output=True, text=True, timeout=120, check=True)
    subprocess.run(["git", "reset", "--hard", exam.base_commit],
                   cwd=str(workdir), capture_output=True, text=True, timeout=60)
    subprocess.run(["git", "clean", "-fd"], cwd=str(workdir), capture_output=True, timeout=60)
    # Apply injection patch
    p = workdir / ".inject.diff"
    p.write_text(exam.injection_patch)
    subprocess.run(["git", "apply", str(p.name)], cwd=str(workdir),
                   capture_output=True, text=True, timeout=60)
    p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Stage: grade (run tests on each solver's patch inside docker)
# ---------------------------------------------------------------------------

def stage_grade(db: Database) -> int:
    """For every run in the DB, evaluate its patch and record a Grade."""
    from ..evaluator.docker_runner import run_exam_in_docker

    runs_raw = db.list_runs()
    n = 0
    for row in runs_raw:
        exam = db.get_exam(row["exam_id"])
        if not exam:
            continue
        solver_patch = row.get("patch_diff") or ""
        image_tag = _find_image_tag_for(exam.repo_id)
        if not image_tag:
            continue
        try:
            res = run_exam_in_docker(
                exam=exam,
                solver_patch=solver_patch,
                image_tag=image_tag,
                runs_root=DATA / "runs",
                run_id=row["id"],
                patch_kind="solver",
            )
        except Exception as e:
            log.warning("grade failed for run %s: %r", row["id"], e)
            continue
        grade = grade_run(
            exam=exam,
            passed_tests=res.passed_tests,
            failed_tests=res.failed_tests,
            run_id=row["id"],
            solver_name=row["solver_name"],
            stderr_excerpt=res.stderr,
        )
        db.upsert_grade(grade)
        n += 1
    log.info("grade: wrote %d grades", n)
    return n


def _find_image_tag_for(repo_id: str) -> str | None:
    envbuild = None
    with Database(DATA / "status.db").connect() as conn:
        row = conn.execute("SELECT * FROM envbuilds WHERE repo_id=?", (repo_id,)).fetchone()
        if row:
            envbuild = dict(row)
    return envbuild.get("instance_image_tag") if envbuild else None


# ---------------------------------------------------------------------------
# Stage: score (fit BT + write leaderboard JSON)
# ---------------------------------------------------------------------------

def stage_score(db: Database, out_dir: Path | None = None) -> Path:
    from ..scoring.leaderboard import write_leaderboard
    out_dir = out_dir or (DATA / "runs" / "leaderboard")
    return write_leaderboard(db, out_dir)
