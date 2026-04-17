"""M2 batch driver: multi-instance × multi-band × multi-solver end-to-end.

For each (instance, band, draw):
  - inject candidate bugs (agentic planner+executor)
  - run SWE-Bench Pro image to discover induced F2P; pick first draw that
    satisfies 1 <= |F2P| <= 10 AND F2P is a subset of selected_test_files
  - scrub the problem statement with bug_exam.injector.scrubber
  - freeze ExamInstance
  - for each solver: prepare buggy workdir, run solver, grade in docker
  - append one row per (exam_id, solver) to dumps/.../summary.jsonl

Concurrency: --max-docker limits parallel docker runs to avoid daemon
contention. Solvers within an exam are sequential (to keep disk + CPU sane);
instances x bands are sequential too (simpler, less to debug).

Typical remote invocation:

    cd /root/bug_exam
    PYTHONPATH=. .venv/bin/python scripts/run_swebench_pro_batch.py \\
        --instances instance_qutebrowser__qutebrowser-...,instance_... \\
        --bands 1x1,2x2 \\
        --solvers claude_direct,openhands \\
        --jsonl /root/SWE-bench_Pro-os/helper_code/sweap_eval_full_v2.jsonl \\
        --swepro-root /root/SWE-bench_Pro-os \\
        --workdir-root /root/bugexam_m2/work \\
        --runs-root /root/bugexam_m2/runs \\
        --out-root /root/bug_exam/dumps/swebench_pro_m2
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import traceback
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
from bug_exam.solvers.base import load_solver
from bug_exam.swebench_helpers import (
    git_apply_check,
    git_reset,
    prepare_buggy_workdir,
    solver_cfg,
    test_in_selected_files,
)
from bug_exam.validator.ast_diff import files_touched

log = logging.getLogger("m2")


def _parse_band(band: str) -> tuple[int, int]:
    F, S = band.lower().split("x")
    return int(F), int(S)


# --------------------------- core ---------------------------

def process_one(
    *,
    instance_id: str,
    band: str,
    draw_idx: int,
    args,
    dumps_inst_band: Path,
) -> dict:
    """Run one (instance, band) — inject, validate, freeze, solve, grade.

    Returns a list of ExamRun rows (one per solver). Also writes files under
    dumps_inst_band/<solver>/{patch.diff, run.json}.
    """
    rows: list[dict] = []
    ticket = {
        "instance_id": instance_id,
        "band": band,
        "started_at": time.time(),
    }
    F_tgt, S_tgt = _parse_band(band)

    try:
        inst = load_instance(
            jsonl_path=Path(args.jsonl),
            instance_id=instance_id,
            swebench_pro_root=Path(args.swepro_root),
            dockerhub_username=args.dockerhub_username,
        )
    except Exception as e:
        log.error("%s: load_instance failed: %r", instance_id, e)
        ticket["fatal"] = f"load_instance failed: {e!r}"
        return {"ticket": ticket, "rows": []}

    workdir = Path(args.workdir_root) / instance_id
    runs_root = Path(args.runs_root) / instance_id / band
    runs_root.mkdir(parents=True, exist_ok=True)

    # 1. checkout (reuse if present)
    try:
        checkout_repo(inst, workdir, fresh=not args.reuse_checkout)
    except Exception as e:
        ticket["fatal"] = f"checkout_repo failed: {e!r}"
        return {"ticket": ticket, "rows": []}

    skel = inst.to_exam_skeleton()

    # 2. baseline
    log.info("[%s/%s] baseline test run", instance_id, band)
    try:
        baseline = run_swebench_pro_exam(
            exam=skel, image_tag=inst.image_tag, solver_patch="",
            runs_root=runs_root, run_id="baseline",
            patch_kind="baseline", timeout_s=args.timeout_s,
        )
    except Exception as e:
        ticket["fatal"] = f"baseline crashed: {e!r}"
        return {"ticket": ticket, "rows": []}
    baseline_passing = set(baseline.passed_tests)
    if not baseline_passing:
        ticket["fatal"] = "baseline produced no passing tests"
        return {"ticket": ticket, "rows": []}

    # 3. inject (n_draws candidates)
    hint = (
        "Scope: only failures of these test files matter for grading:\n  "
        + "\n  ".join(inst.selected_test_files[:40])
        + f"\n\nTarget: F={F_tgt} (distinct source files touched), S={S_tgt} (distinct break steps)."
          " Read one of the selected test files first to understand what it covers,"
          " then pick source file(s) the tests exercise."
          " Do NOT modify any test file. Call emit_break_plan as soon as you have"
          " identified the (file, line) targets — do not over-explore."
    )
    if S_tgt >= 2:
        hint += (
            "\n\nIMPORTANT: Since S>=2, inject a semantically coherent multi-step bug"
            " where the mutations INTERACT across call boundaries. The solver should"
            " not be able to fix each mutation independently. Trace the call graph from"
            " test → source, then place mutations along the same data flow."
        )
    try:
        draws = draw_injections(
            repo_dir=workdir,
            target_F=F_tgt, target_S=S_tgt,
            n_draws=args.n_draws,
            model=args.injector_model,
            max_turns=40,
            extra_user_hint=hint,
        )
    except Exception as e:
        ticket["fatal"] = f"injector crashed: {e!r}"
        return {"ticket": ticket, "rows": []}

    # 4. validator gates (apply + 1..10 F2P within selected_test_files + >=90% P2P kept)
    chosen = None
    chosen_eval = None
    chosen_idx = -1
    gate_log: list[dict] = []
    for i, draw in enumerate(draws):
        entry = {"idx": i, "has_plan": draw.plan is not None, "has_diff": bool(draw.diff)}
        if draw.plan is None or not draw.diff:
            entry["skip"] = "missing plan/diff"
            entry["planner_error"] = draw.planner_error
            entry["executor_error"] = draw.executor_error
            gate_log.append(entry)
            continue
        git_reset(workdir, inst.base_commit)
        applies, apply_err = git_apply_check(workdir, draw.diff)
        if not applies:
            entry["gate_failed"] = "G1"
            entry["reason"] = apply_err
            gate_log.append(entry)
            continue
        files = files_touched(draw.diff)
        entry["files"] = sorted(files)
        if any(f in inst.selected_test_files or f.endswith(".py") and "/test" in f
               for f in files):
            # sanity: disallow touching test files directly
            if any(("/test" in f or f.startswith("test")) for f in files):
                entry["gate_failed"] = "G_NoTestEdit"
                entry["reason"] = "injection touched test file(s)"
                gate_log.append(entry)
                continue

        cand_exam = skel.model_copy(update={"injection_patch": draw.diff})
        t0 = time.time()
        try:
            cand_eval = run_swebench_pro_exam(
                exam=cand_exam, image_tag=inst.image_tag, solver_patch="",
                runs_root=runs_root, run_id=f"inject_cand_{i}",
                patch_kind="bug_only", timeout_s=args.timeout_s,
            )
        except Exception as e:
            entry["gate_failed"] = "G_docker"
            entry["reason"] = f"candidate eval crashed: {e!r}"
            gate_log.append(entry)
            continue
        entry["eval_elapsed_s"] = round(time.time() - t0, 2)
        passing = set(cand_eval.passed_tests)
        new_failing = baseline_passing - passing
        # G6 scope filter: F2P must be in selected_test_files
        new_failing_in_scope = {t for t in new_failing
                                if test_in_selected_files(t, inst.selected_test_files)}
        entry["n_new_failing"] = len(new_failing)
        entry["n_new_failing_in_scope"] = len(new_failing_in_scope)
        if not (1 <= len(new_failing_in_scope) <= 10):
            entry["gate_failed"] = "G6"
            entry["reason"] = f"|F2P_in_scope|={len(new_failing_in_scope)} not in [1,10]"
            gate_log.append(entry)
            continue
        p2p_target = baseline_passing - new_failing
        p2p_kept = p2p_target & passing
        if len(p2p_kept) < int(0.9 * len(p2p_target)):
            entry["gate_failed"] = "G7"
            entry["reason"] = f"P2P kept {len(p2p_kept)}/{len(p2p_target)}"
            gate_log.append(entry)
            continue
        entry["passed"] = True
        entry["f2p"] = sorted(new_failing_in_scope)
        chosen = draw
        chosen_eval = cand_eval
        chosen_idx = i
        gate_log.append(entry)
        break

    ticket["gate_log"] = gate_log
    if chosen is None:
        ticket["fatal"] = "no draw passed validator gates"
        return {"ticket": ticket, "rows": []}

    # 5. freeze ExamInstance — with scrubbed statement
    new_failing = baseline_passing - set(chosen_eval.passed_tests)
    f2p = sorted(t for t in new_failing if test_in_selected_files(t, inst.selected_test_files))
    p2p = sorted(baseline_passing - set(f2p))
    draft_statement = chosen.plan.summary or "A regression has been introduced; tests are failing."
    # collect assertion excerpts from stderr/stdout of baseline bug-only run
    excerpts: list[str] = []
    stderr_blob = (chosen_eval.stderr or "") + "\n" + (chosen_eval.stdout or "")
    # small heuristic: each test_id's 'FAILED ...' / 'assert ' context
    for t in f2p[:3]:
        # grab ~6 lines of assertion context for each failing test
        short = t.split("::")[-1]
        lines = stderr_blob.splitlines()
        for idx, line in enumerate(lines):
            if short in line and ("FAIL" in line or "assert" in line.lower() or "Error" in line):
                ctx = "\n".join(lines[max(0, idx - 1): idx + 5])
                excerpts.append(f"[{t}]\n{ctx}")
                break
    try:
        statement = scrub_problem_statement(
            draft=draft_statement, failing_test_assertions=excerpts,
        )
    except Exception as e:
        log.warning("[%s/%s] scrubber crashed: %r — using draft", instance_id, band, e)
        statement = draft_statement

    try:
        exam = finalize_exam(
            inst,
            injection_patch=chosen.diff,
            plan=chosen.plan,
            injector_model=args.injector_model or os.environ.get("ANTHROPIC_MODEL", "glm-5.1"),
            fail_to_pass=f2p, pass_to_pass=p2p,
            problem_statement=statement,
            band_id=f"swepro_{band}",
        )
    except Exception as e:
        ticket["fatal"] = f"finalize_exam failed: {e!r}"
        return {"ticket": ticket, "rows": []}

    dumps_inst_band.mkdir(parents=True, exist_ok=True)
    (dumps_inst_band / "exam.json").write_text(exam.model_dump_json(indent=2))
    (dumps_inst_band / "injection.diff").write_text(chosen.diff)
    (dumps_inst_band / "problem_statement.md").write_text(statement)
    ticket["exam_id"] = exam.instance_id
    ticket["f2p"] = f2p
    ticket["n_p2p"] = len(p2p)
    ticket["bug_files"] = sorted(files_touched(chosen.diff))

    # 6. solvers
    solver_names = [s for s in args.solvers.split(",") if s]
    for name in solver_names:
        slot: dict = {
            "exam_id": exam.instance_id,
            "instance_id": instance_id,
            "band": band,
            "solver_name": name,
            "final_passed": False,
        }
        solver_dir = dumps_inst_band / name
        solver_dir.mkdir(parents=True, exist_ok=True)
        try:
            spec = solver_cfg(name)
            solver = load_solver(spec)
        except Exception as e:
            slot["errored"] = True
            slot["error_message"] = f"load failed: {e!r}"
            (solver_dir / "run.json").write_text(json.dumps(slot, indent=2, default=str))
            rows.append(slot)
            continue
        sw = runs_root / "solver_workdirs" / name
        try:
            prepare_buggy_workdir(workdir, sw, inst.base_commit, chosen.diff)
        except Exception as e:
            slot["errored"] = True
            slot["error_message"] = f"workdir prep failed: {e!r}"
            (solver_dir / "run.json").write_text(json.dumps(slot, indent=2, default=str))
            rows.append(slot)
            continue
        t0 = time.time()
        try:
            sres = solver.solve(exam, sw, timeout_s=spec.get("timeout_s", args.timeout_s))
        except Exception as e:
            slot["errored"] = True
            slot["error_message"] = f"solver crashed: {e!r}\n{traceback.format_exc()[-1500:]}"
            slot["wall_clock_s"] = round(time.time() - t0, 2)
            (solver_dir / "run.json").write_text(json.dumps(slot, indent=2, default=str))
            rows.append(slot)
            continue
        slot["wall_clock_s"] = round(time.time() - t0, 2)
        slot["patch_bytes"] = len(sres.patch or "")
        slot["token_usage"] = sres.token_usage
        slot["errored"] = sres.errored
        slot["error_message"] = sres.error_message
        (solver_dir / "patch.diff").write_text(sres.patch or "")

        # grade
        t0 = time.time()
        try:
            graded = run_swebench_pro_exam(
                exam=exam, image_tag=inst.image_tag,
                solver_patch=sres.patch or "",
                runs_root=runs_root, run_id=f"grade_{name}",
                patch_kind="solver", timeout_s=args.timeout_s,
            )
        except Exception as e:
            slot["grade_error"] = f"grade crashed: {e!r}"
            slot["grade_elapsed_s"] = round(time.time() - t0, 2)
            (solver_dir / "run.json").write_text(json.dumps(slot, indent=2, default=str))
            rows.append(slot)
            continue
        slot["grade_elapsed_s"] = round(time.time() - t0, 2)
        passed = set(graded.passed_tests)
        f2p_pass = set(f2p).issubset(passed)
        p2p_pass = len(set(p2p) - passed) == 0
        slot["n_passed"] = len(passed)
        slot["f2p_pass_count"] = len(set(f2p) & passed)
        slot["p2p_pass_count"] = len(set(p2p) & passed)
        slot["f2p_pass"] = f2p_pass
        slot["p2p_pass"] = p2p_pass
        slot["final_passed"] = bool(f2p_pass and p2p_pass)
        log.info("[%s/%s/%s] F2P %d/%d, P2P %d/%d, passed=%s",
                 instance_id, band, name, slot["f2p_pass_count"], len(f2p),
                 slot["p2p_pass_count"], len(p2p), slot["final_passed"])
        (solver_dir / "run.json").write_text(json.dumps(slot, indent=2, default=str))
        rows.append(slot)

    ticket["finished_at"] = time.time()
    ticket["wall_clock_s"] = round(ticket["finished_at"] - ticket["started_at"], 2)
    return {"ticket": ticket, "rows": rows}


# --------------------------- main ---------------------------

def _load_completed(summary_path: Path) -> set[tuple[str, str]]:
    """Parse summary.jsonl for (instance_id, band) pairs that have at least
    one graded solver row."""
    done: set[tuple[str, str]] = set()
    if not summary_path.exists():
        return done
    with summary_path.open() as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = row.get("instance_id")
            band = row.get("band")
            if iid and band and "final_passed" in row:
                done.add((iid, band))
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", default=None, help="csv of SWE-Pro instance ids")
    ap.add_argument("--instance-file", default=None,
                    help="JSON file with screened instances (overrides --instances)")
    ap.add_argument("--bands", default="1x1", help="csv of FxS, e.g. 1x1,2x2")
    ap.add_argument("--solvers", default="claude_direct,openhands")
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--swepro-root", required=True)
    ap.add_argument("--workdir-root", required=True)
    ap.add_argument("--runs-root", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--n-draws", type=int, default=6)
    ap.add_argument("--injector-model", default=None)
    ap.add_argument("--dockerhub-username", default="jefzda")
    ap.add_argument("--timeout-s", type=int, default=1800)
    ap.add_argument("--reuse-checkout", action="store_true")
    ap.add_argument("--no-resume", action="store_true", help="Disable resume; re-run all instances")
    ap.add_argument("--max-docker", type=int, default=2, help="(currently sequential; reserved)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log.setLevel(logging.INFO)

    # Resolve instance list
    if args.instance_file:
        with open(args.instance_file) as f:
            data = json.loads(f.read())
        instances = [i["instance_id"] for i in data["instances"] if i.get("viable")]
        log.info("loaded %d viable instances from %s", len(instances), args.instance_file)
    elif args.instances:
        instances = [s for s in args.instances.split(",") if s]
    else:
        log.error("must provide --instances or --instance-file")
        return 1

    bands = [s for s in args.bands.split(",") if s]
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    summary_path = out_root / "summary.jsonl"
    tickets_path = out_root / "tickets.jsonl"

    # Resume: skip completed (instance, band) pairs
    completed = set() if args.no_resume else _load_completed(summary_path)
    if completed:
        log.info("resume: %d (instance, band) pairs already completed", len(completed))

    for iid in instances:
        for band in bands:
            if (iid, band) in completed:
                log.info("SKIP %s / %s (already completed)", iid, band)
                continue
            log.info(">>> %s / %s", iid, band)
            dumps_inst_band = out_root / iid / band
            result = process_one(
                instance_id=iid, band=band, draw_idx=0,
                args=args, dumps_inst_band=dumps_inst_band,
            )
            with tickets_path.open("a") as f:
                f.write(json.dumps(result["ticket"], default=str) + "\n")
            with summary_path.open("a") as f:
                for row in result.get("rows", []):
                    f.write(json.dumps(row, default=str) + "\n")

    print(f"done. summary={summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
