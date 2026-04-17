"""M3: contamination probe.

For each instance, run solvers on two conditions:
  (A) Original SWE-Pro bug:   apply the REVERSE of the gold `patch` (→ buggy state)
                              → original problem_statement (may be in training data)
  (B) Fresh injected bug:      bug_exam injector picks a different site
                              → scrubbed problem_statement

We measure pass-rate for each condition per solver, then compute
pass_rate(A) - pass_rate(B). If positive and significant, contamination is real.

Outputs:
  dumps/swebench_pro_m3/contamination/summary.json
  dumps/swebench_pro_m3/contamination/table.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from bug_exam.adapters.swebench_pro_source import (
    checkout_repo,
    finalize_exam,
    load_instance,
)
from bug_exam.evaluator.swe_bench_pro_runner import run_swebench_pro_exam
from bug_exam.injector.agent import draw_injections
from bug_exam.injector.scrubber import scrub_problem_statement
from bug_exam.schema import BreakPlan, BreakStep, ExamInstance, ExamStatus, Language, MutationOp
from bug_exam.solvers.base import load_solver
from bug_exam.swebench_helpers import (
    git_apply_check,
    git_reset,
    prepare_buggy_workdir,
    solver_cfg,
    test_in_selected_files,
)
from bug_exam.validator.ast_diff import files_touched

log = logging.getLogger("m3")


def _reverse_unified_diff_via_git(workdir: Path, diff: str) -> str:
    """Produce a forward-applicable patch that is the INVERSE of `diff`.

    Strategy: apply `diff` in reverse to a scratch copy of the repo (so the
    tree moves to the pre-`diff` state), then `git diff HEAD` captures the
    inverse as a conventional unified diff. This avoids our own brittle
    line-flipping of hunk headers.

    The workdir is reset to HEAD before applying.
    """
    # Reset first so the untracked patch file we write next isn't wiped.
    subprocess.run(["git", "reset", "--hard", "HEAD"],
                   cwd=str(workdir), capture_output=True, timeout=60)
    subprocess.run(["git", "clean", "-fd"],
                   cwd=str(workdir), capture_output=True, timeout=60)
    pfile = workdir / ".gold_to_reverse.diff"
    pfile.write_text(diff)
    try:
        # git apply -R turns the working tree into HEAD - diff.
        res = subprocess.run(
            ["git", "apply", "-R", "--recount", "--whitespace=nowarn", pfile.name],
            cwd=str(workdir), capture_output=True, text=True, timeout=60,
        )
        if res.returncode != 0:
            return ""
        # Stage + diff against HEAD to produce a clean unified-diff text.
        diff_res = subprocess.run(
            ["git", "diff", "HEAD"], cwd=str(workdir),
            capture_output=True, text=True, timeout=60,
        )
        # Restore the tree so nobody is surprised.
        subprocess.run(["git", "checkout", "--", "."], cwd=str(workdir),
                       capture_output=True, timeout=60)
        return diff_res.stdout
    finally:
        pfile.unlink(missing_ok=True)




def build_original_exam(inst, skel, solver_solvable_statement: str,
                         workdir: Path) -> ExamInstance:
    """Condition A: SWE-Pro's original bug — the one that already exists at
    base_commit. We DO NOT inject anything; the solver starts from base_commit
    and is asked to produce the gold fix (or any equivalent). Oracle is the
    upstream FAIL_TO_PASS / PASS_TO_PASS metadata.
    """
    # No synthetic injection — the bug is the upstream one already baked in.
    # But our entryscript still needs a bug_patch.diff — use empty.
    ph = hashlib.sha256(b"(original_swe_pro_no_injection)").hexdigest()
    plan = BreakPlan(
        target_F=1, target_S=1,
        steps=[BreakStep(
            op=MutationOp.StateReorder, file="(original)", line=1,
            anchor_snippet="(original)",
            rationale="SWE-Pro original bug: reverse of the gold fix.",
        )],
        summary="(original swe-pro bug)",
    )
    from bug_exam.schema import make_instance_id
    iid = make_instance_id(inst.instance_id, "contaminated", ph)
    return ExamInstance(
        instance_id=iid,
        repo_id=inst.instance_id,
        repo_url=inst.repo_url,
        language=Language.PYTHON,
        base_commit=inst.base_commit,
        injection_patch="",
        break_plan=plan,
        injector_model="swepro_original",
        patch_hash=ph,
        difficulty_band="contaminated",
        F=1, S=1,
        FAIL_TO_PASS=inst.fail_to_pass_orig,
        PASS_TO_PASS=inst.pass_to_pass_orig,
        selected_test_files=inst.selected_test_files,
        problem_statement=solver_solvable_statement,
        base_dockerfile_path=inst.base_dockerfile_path,
        instance_dockerfile_path=inst.instance_dockerfile_path,
        run_script_path=inst.run_script_path,
        parser_path=inst.parser_path,
        test_framework="pytest",
        before_repo_set_cmd=inst.before_repo_set_cmd,
        status=ExamStatus.FROZEN,
    )


def run_solver(name: str, exam: ExamInstance, workdir_src: Path, inst, runs_root: Path,
               run_id: str, timeout_s: int) -> dict:
    slot: dict = {
        "solver_name": name, "exam_id": exam.instance_id, "final_passed": False,
    }
    try:
        spec = solver_cfg(name)
        solver = load_solver(spec)
    except Exception as e:
        slot["errored"] = True
        slot["error_message"] = f"load failed: {e!r}"
        return slot
    sw = runs_root / f"solver_workdir_{name}_{run_id}"
    try:
        prepare_buggy_workdir(workdir_src, sw, inst.base_commit, exam.injection_patch)
    except Exception as e:
        slot["errored"] = True
        slot["error_message"] = f"workdir prep failed: {e!r}"
        return slot
    t0 = time.time()
    try:
        sres = solver.solve(exam, sw, timeout_s=spec.get("timeout_s", timeout_s))
    except Exception as e:
        slot["errored"] = True
        slot["error_message"] = f"solver crashed: {e!r}"
        slot["wall_clock_s"] = round(time.time() - t0, 2)
        return slot
    slot["wall_clock_s"] = round(time.time() - t0, 2)
    slot["patch_bytes"] = len(sres.patch or "")
    slot["token_usage"] = sres.token_usage
    slot["errored"] = sres.errored
    try:
        graded = run_swebench_pro_exam(
            exam=exam, image_tag=inst.image_tag,
            solver_patch=sres.patch or "",
            runs_root=runs_root, run_id=run_id,
            patch_kind="solver", timeout_s=timeout_s,
        )
    except Exception as e:
        slot["grade_error"] = f"grade crashed: {e!r}"
        return slot
    passed = set(graded.passed_tests)
    f2p_pass = set(exam.FAIL_TO_PASS).issubset(passed)
    p2p_pass = len(set(exam.PASS_TO_PASS) - passed) == 0
    slot["f2p_pass_count"] = len(set(exam.FAIL_TO_PASS) & passed)
    slot["p2p_pass_count"] = len(set(exam.PASS_TO_PASS) & passed)
    slot["final_passed"] = bool(f2p_pass and p2p_pass)
    return slot


def test_in_selected_files(test_id: str, selected: list[str]) -> bool:
    base = test_id.split("::", 1)[0].lstrip("./")
    return any(base == f.lstrip("./") or base.endswith("/" + f.lstrip("./")) for f in selected)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", required=True)
    ap.add_argument("--solvers", default="claude_direct,openhands")
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--swepro-root", required=True)
    ap.add_argument("--workdir-root", required=True)
    ap.add_argument("--runs-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--n-fresh-draws", type=int, default=3,
                    help="number of fresh injections attempted per instance")
    ap.add_argument("--timeout-s", type=int, default=1800)
    ap.add_argument("--dockerhub-username", default="jefzda")
    ap.add_argument("--reuse-checkout", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log.setLevel(logging.INFO)

    instances = [s for s in args.instances.split(",") if s]
    solvers = [s for s in args.solvers.split(",") if s]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict = {"per_instance": [], "solvers": solvers}

    for iid in instances:
        row: dict = {"instance": iid}
        inst = load_instance(
            jsonl_path=Path(args.jsonl), instance_id=iid,
            swebench_pro_root=Path(args.swepro_root),
            dockerhub_username=args.dockerhub_username,
        )
        workdir = Path(args.workdir_root) / iid
        runs_root = Path(args.runs_root) / iid
        runs_root.mkdir(parents=True, exist_ok=True)
        checkout_repo(inst, workdir, fresh=not args.reuse_checkout)

        skel = inst.to_exam_skeleton()
        # baseline
        try:
            baseline = run_swebench_pro_exam(
                exam=skel, image_tag=inst.image_tag, solver_patch="",
                runs_root=runs_root, run_id="baseline",
                patch_kind="baseline", timeout_s=args.timeout_s,
            )
        except Exception as e:
            row["fatal"] = f"baseline crashed: {e!r}"
            summary["per_instance"].append(row)
            continue
        baseline_passing = set(baseline.passed_tests)

        # --- Condition A: original SWE-Pro bug (reverse of gold patch) ---
        try:
            orig_exam = build_original_exam(
                inst, skel, solver_solvable_statement=inst.problem_statement,
                workdir=workdir,
            )
        except Exception as e:
            row["original_bug_applies"] = False
            row["original_bug_err"] = f"reverse-patch build failed: {e!r}"
            summary["per_instance"].append(row)
            (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
            continue
        # check whether the reverse applies at all via docker bug-only run
        orig_rows: list[dict] = []
        try:
            bug_only = run_swebench_pro_exam(
                exam=orig_exam, image_tag=inst.image_tag, solver_patch="",
                runs_root=runs_root, run_id="orig_bug_only",
                patch_kind="bug_only", timeout_s=args.timeout_s,
            )
            passed = set(bug_only.passed_tests)
            # dynamic oracle fallback: if upstream F2P metadata is empty, infer it
            if not orig_exam.FAIL_TO_PASS:
                induced_fail = sorted(baseline_passing - passed)
                induced_fail = [t for t in induced_fail
                                if test_in_selected_files(t, inst.selected_test_files)]
                orig_exam = orig_exam.model_copy(update={
                    "FAIL_TO_PASS": induced_fail,
                    "PASS_TO_PASS": sorted(baseline_passing - set(induced_fail)),
                })
            row["original_f2p_count"] = len(orig_exam.FAIL_TO_PASS)
            row["original_bug_applies"] = len(orig_exam.FAIL_TO_PASS) > 0
        except Exception as e:
            row["original_bug_applies"] = False
            row["original_bug_err"] = f"{e!r}"

        if row.get("original_bug_applies"):
            for name in solvers:
                slot = run_solver(name, orig_exam, workdir, inst, runs_root,
                                   f"orig_{name}", args.timeout_s)
                slot["condition"] = "original"
                orig_rows.append(slot)
        row["original_runs"] = orig_rows

        # --- Condition B: fresh injected bug ---
        hint = (
            "Scope: only failures of these test files matter:\n  "
            + "\n  ".join(inst.selected_test_files[:20])
            + "\nPick a DIFFERENT file/line from SWE-Pro's original bug."
              " Emit break plan quickly."
        )
        try:
            draws = draw_injections(
                repo_dir=workdir, target_F=1, target_S=1,
                n_draws=args.n_fresh_draws, max_turns=40,
                extra_user_hint=hint,
            )
        except Exception as e:
            row["fresh_err"] = f"injector crashed: {e!r}"
            summary["per_instance"].append(row)
            continue

        chosen = None
        chosen_eval = None
        for i, draw in enumerate(draws):
            if draw.plan is None or not draw.diff:
                continue
            git_reset(workdir, inst.base_commit)
            applies, _ = git_apply_check(workdir, draw.diff)
            if not applies:
                continue
            cand_exam = skel.model_copy(update={"injection_patch": draw.diff})
            try:
                cand = run_swebench_pro_exam(
                    exam=cand_exam, image_tag=inst.image_tag, solver_patch="",
                    runs_root=runs_root, run_id=f"fresh_cand_{i}",
                    patch_kind="bug_only", timeout_s=args.timeout_s,
                )
            except Exception:
                continue
            passing = set(cand.passed_tests)
            new_failing = {t for t in (baseline_passing - passing)
                           if test_in_selected_files(t, inst.selected_test_files)}
            if 1 <= len(new_failing) <= 10:
                chosen = draw
                chosen_eval = cand
                row["fresh_f2p_count"] = len(new_failing)
                row["fresh_idx"] = i
                break

        fresh_rows: list[dict] = []
        if chosen is not None:
            new_failing = sorted(t for t in (baseline_passing - set(chosen_eval.passed_tests))
                                  if test_in_selected_files(t, inst.selected_test_files))
            p2p = sorted(baseline_passing - set(new_failing))
            statement = scrub_problem_statement(
                draft=chosen.plan.summary or "A regression has been introduced.",
                failing_test_assertions=[],
            )
            fresh_exam = finalize_exam(
                inst, injection_patch=chosen.diff, plan=chosen.plan,
                injector_model=os.environ.get("ANTHROPIC_MODEL", "glm-5.1"),
                fail_to_pass=new_failing, pass_to_pass=p2p,
                problem_statement=statement, band_id="m3_fresh",
            )
            for name in solvers:
                slot = run_solver(name, fresh_exam, workdir, inst, runs_root,
                                   f"fresh_{name}", args.timeout_s)
                slot["condition"] = "fresh"
                fresh_rows.append(slot)
        row["fresh_runs"] = fresh_rows

        # per-instance aggregation
        orig_rate = {}
        fresh_rate = {}
        for s in solvers:
            or_ = [r for r in orig_rows if r["solver_name"] == s]
            fr_ = [r for r in fresh_rows if r["solver_name"] == s]
            orig_rate[s] = (sum(1 for r in or_ if r.get("final_passed")) / len(or_)) if or_ else None
            fresh_rate[s] = (sum(1 for r in fr_ if r.get("final_passed")) / len(fr_)) if fr_ else None
        row["original_bug_pass_rate"] = orig_rate
        row["fresh_bug_pass_rate"] = fresh_rate
        row["delta"] = {s: (None if orig_rate[s] is None or fresh_rate[s] is None
                            else orig_rate[s] - fresh_rate[s]) for s in solvers}
        summary["per_instance"].append(row)

        # write incremental
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    # table.md
    md = ["# Contamination probe\n",
          "| instance | solver | original | fresh | delta (orig - fresh) |",
          "|---|---|---:|---:|---:|"]
    for r in summary["per_instance"]:
        for s in solvers:
            o = r.get("original_bug_pass_rate", {}).get(s)
            f = r.get("fresh_bug_pass_rate", {}).get(s)
            d = r.get("delta", {}).get(s)
            md.append(f"| `{r['instance'][:40]}...` | `{s}` | "
                      f"{'n/a' if o is None else f'{o*100:.0f}%'} | "
                      f"{'n/a' if f is None else f'{f*100:.0f}%'} | "
                      f"{'n/a' if d is None else f'{d*100:+.0f}pp'} |")
    (out_dir / "table.md").write_text("\n".join(md) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"done. summary={out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
