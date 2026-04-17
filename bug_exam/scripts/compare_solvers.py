"""Run multiple solvers through the live E2E pipeline and dump a summary JSON.

Small wrapper around ``scripts.run_e2e_live`` that reuses its stages
verbatim, then serializes exam + grades + timings to a summary file for
later inspection.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/compare_solvers.py \\
        --solvers claude_direct,openhands \\
        --output dumps/openhands_vs_direct/summary.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from bug_exam.db import Database
from bug_exam.evaluator.local_runner import run_pytest, run_with_patch
from bug_exam.llm import make_client, resolve_provider
from bug_exam.schema import Language, RepoManifest, RepoStatus

from run_e2e_live import (  # type: ignore
    FIXTURE,
    PKG_SRC,
    _clone_fresh,
    _load_solver_classes,
    _stage_grade,
    _stage_inject,
    _stage_solve,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solvers", default="claude_direct,openhands")
    ap.add_argument("--n-draws", type=int, default=3)
    ap.add_argument("--target-f", type=int, default=1)
    ap.add_argument("--target-s", type=int, default=1)
    ap.add_argument("--output", required=True, help="path to write summary JSON")
    args = ap.parse_args()

    provider = resolve_provider(None)
    client = make_client(provider=provider)

    solver_names = [s.strip() for s in args.solvers.split(",") if s.strip()]
    solvers = _load_solver_classes(solver_names)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        repo_dir = tmp_path / "work"
        base_commit = _clone_fresh(FIXTURE, repo_dir)
        baseline = run_pytest(repo_dir, extra_pythonpath=str(repo_dir / PKG_SRC))
        baseline_passing = set(baseline.passed_tests)
        db = Database(tmp_path / "status.db")
        manifest = RepoManifest(
            id="fixture__tiny_py_repo",
            url="file://" + str(FIXTURE),
            owner="fixture", name="tiny_py_repo",
            language=Language.PYTHON,
            stars=0, size_kb=1, license="MIT",
            created_at=datetime(2025, 10, 1), pushed_at=datetime(2025, 10, 1),
            base_commit=base_commit, default_branch="main",
            status=RepoStatus.BASELINE_OK, post_cutoff=True,
            test_framework="pytest", baseline_test_count=len(baseline_passing),
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

        solver_results = _stage_solve(exam, repo_dir, base_commit, solvers, tmp_path, db)
        _stage_grade(exam, repo_dir, base_commit, solver_results, db)

        # Collect per-solver grades directly from db
        summary = {
            "exam_id": exam.instance_id,
            "F2P": exam.FAIL_TO_PASS,
            "P2P_count": len(exam.PASS_TO_PASS),
            "ops": [s.op.value for s in exam.break_plan.steps],
            "solvers": [],
        }
        for run_id, solver_name, solver_patch in solver_results:
            with db.connect() as c:
                grade_row = c.execute(
                    "SELECT f2p_pass, p2p_pass, final_passed "
                    "FROM grades WHERE run_id=?", (run_id,),
                ).fetchone()
                run_row = c.execute(
                    "SELECT wall_clock_s, token_usage_json, status, error_message "
                    "FROM runs WHERE id=?", (run_id,),
                ).fetchone()
            g = {"solver_name": solver_name, "patch_len": len(solver_patch or "")}
            if grade_row:
                g["f2p_pass"] = bool(grade_row["f2p_pass"])
                g["p2p_pass"] = bool(grade_row["p2p_pass"])
                g["final_passed"] = bool(grade_row["final_passed"])
            if run_row:
                g["wall_clock_s"] = run_row["wall_clock_s"]
                try:
                    g["token_usage"] = json.loads(run_row["token_usage_json"]) if run_row["token_usage_json"] else {}
                except Exception:
                    g["token_usage"] = {}
                g["status"] = run_row["status"]
                g["error_message"] = run_row["error_message"]
            summary["solvers"].append(g)

        output.write_text(json.dumps(summary, indent=2, default=str))
        print(f"\n== wrote summary to {output}")
        for s in summary["solvers"]:
            print(f"  [{s['solver_name']}] final={s.get('final_passed')} "
                  f"f2p={s.get('f2p_pass')} p2p={s.get('p2p_pass')} "
                  f"t={s.get('wall_clock_s',0):.1f}s tokens={s.get('token_usage')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
