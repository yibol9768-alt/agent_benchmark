"""DEPRECATED — superseded by `run_swebench_pro_batch.py`.

The batch driver handles single-instance too (pass `--instances <one_id>`);
keep this file only because `docs/m1_swe_bench_pro_integration.md` references
it in the design log. Do not use for new runs.

M1 end-to-end driver: SWE-Bench Pro repo -> bug_exam injector -> validator
gates -> freeze -> 2 solvers -> SWE-Bench Pro Docker eval -> summary JSON.

Designed to run on the remote Ubuntu WSL host (Docker daemon active, x86),
NOT on Mac (no docker, ARM, image pull would fail). Mac side rsyncs and
inspects the resulting summary.json afterwards.

Pre-reqs on the host:

  - bug_exam/.venv with the project requirements
  - Docker daemon up, http/https proxy configured for Docker Hub pulls
  - SWE-bench_Pro-os tree at the path passed via --swepro-root
  - GLM creds in env (ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / ANTHROPIC_MODEL)

Usage (remote):

    cd /root/bug_exam
    PYTHONPATH=. .venv/bin/python scripts/run_swebench_pro_m1.py \\
        --instance-id instance_qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111-v059c6fdc75567943479b23ebca7c07b5e9a7f34c \\
        --swepro-root /root/SWE-bench_Pro-os \\
        --jsonl /root/SWE-bench_Pro-os/helper_code/sweap_eval_full_v2.jsonl \\
        --workdir /root/bugexam_m1/work \\
        --runs-root /root/bugexam_m1/runs \\
        --out /root/bug_exam/dumps/swebench_pro_m1/qutebrowser \\
        --solvers claude_direct,openhands \\
        --n-draws 4
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
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
from bug_exam.schema import ExamInstance
from bug_exam.solvers.base import load_solver
from bug_exam.validator.ast_diff import files_touched

log = logging.getLogger("m1")


def _git_apply_check(workdir: Path, diff: str) -> tuple[bool, str]:
    p = workdir / ".m1_check.diff"
    p.write_text(diff)
    try:
        res = subprocess.run(
            ["git", "apply", "--check", "--recount", "--whitespace=nowarn", p.name],
            cwd=str(workdir), capture_output=True, text=True, timeout=60,
        )
        return res.returncode == 0, res.stderr.strip()[-500:]
    finally:
        p.unlink(missing_ok=True)


def _git_reset(workdir: Path, base_commit: str) -> None:
    subprocess.run(["git", "reset", "--hard", base_commit],
                   cwd=str(workdir), capture_output=True, timeout=60)
    subprocess.run(["git", "clean", "-fd"], cwd=str(workdir), capture_output=True, timeout=60)


def _prepare_buggy_workdir(src: Path, dst: Path, base_commit: str, injection_diff: str) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--quiet", str(src), str(dst)],
                   capture_output=True, text=True, check=True, timeout=120)
    subprocess.run(["git", "checkout", "--quiet", base_commit],
                   cwd=str(dst), capture_output=True, text=True, check=True, timeout=60)
    p = dst / ".inject.diff"
    p.write_text(injection_diff)
    res = subprocess.run(
        ["git", "apply", "--recount", "--whitespace=nowarn", p.name],
        cwd=str(dst), capture_output=True, text=True, timeout=60,
    )
    p.unlink(missing_ok=True)
    if res.returncode != 0:
        raise RuntimeError(f"failed to apply injection in buggy workdir: {res.stderr[-400:]}")


def _solver_cfg(name: str) -> dict:
    import yaml
    cfg = yaml.safe_load((ROOT / "configs" / "solvers.yaml").read_text())
    return cfg["solvers"][name]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--swepro-root", required=True)
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--workdir", required=True, help="injector working repo checkout")
    ap.add_argument("--runs-root", required=True, help="docker workspaces / outputs")
    ap.add_argument("--out", required=True, help="dump dir for summary.json + traces")
    ap.add_argument("--solvers", default="claude_direct,openhands")
    ap.add_argument("--n-draws", type=int, default=4)
    ap.add_argument("--injector-model", default=None)
    ap.add_argument("--dockerhub-username", default="jefzda")
    ap.add_argument("--timeout-s", type=int, default=1800)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--reuse-checkout", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log.setLevel(logging.INFO)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_root = Path(args.runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.workdir)

    summary: dict = {
        "instance_id": args.instance_id,
        "started_at": time.time(),
        "stages": {},
    }

    # ----- 1. load instance + checkout -----
    log.info("loading instance %s", args.instance_id)
    inst = load_instance(
        jsonl_path=Path(args.jsonl),
        instance_id=args.instance_id,
        swebench_pro_root=Path(args.swepro_root),
        dockerhub_username=args.dockerhub_username,
    )
    log.info("repo=%s base=%s image=%s", inst.repo, inst.base_commit[:12], inst.image_tag)
    summary["repo"] = inst.repo
    summary["base_commit"] = inst.base_commit
    summary["image_tag"] = inst.image_tag
    summary["selected_test_files"] = inst.selected_test_files

    log.info("checking out repo to %s", workdir)
    checkout_repo(inst, workdir, fresh=not args.reuse_checkout)

    skel = inst.to_exam_skeleton()

    # ----- 2. baseline test run inside the SWE-Bench Pro image -----
    log.info("baseline test run in %s", inst.image_tag)
    t0 = time.time()
    baseline = run_swebench_pro_exam(
        exam=skel, image_tag=inst.image_tag, solver_patch="",
        runs_root=runs_root, run_id="baseline",
        patch_kind="baseline", timeout_s=args.timeout_s,
    )
    summary["stages"]["baseline"] = {
        "elapsed_s": round(time.time() - t0, 2),
        "status_code": baseline.status_code,
        "n_passed": len(baseline.passed_tests),
        "n_failed": len(baseline.failed_tests),
    }
    log.info("baseline: passed=%d failed=%d (sc=%s)",
             len(baseline.passed_tests), len(baseline.failed_tests), baseline.status_code)
    baseline_passing = set(baseline.passed_tests)
    if not baseline_passing:
        summary["fatal"] = "baseline produced no passing tests"
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        return 2

    # ----- 3. injector: draw N candidates -----
    log.info("injecting (n_draws=%d)", args.n_draws)
    t0 = time.time()
    hint = (
        f"Scope: only failures of these test files matter for grading:\n  "
        + "\n  ".join(inst.selected_test_files)
        + "\n\nRead one of these test files first to understand what it covers,"
          " then pick a single source file the tests exercise and inject a small,"
          " obvious bug there (OffByOne, FlippedBoolean, WrongBinaryOperator, etc.)."
          " Do NOT modify any test file. Call emit_break_plan as soon as you have"
          " identified one (file, line) target — do not over-explore."
    )
    draws = draw_injections(
        repo_dir=workdir,
        target_F=1,
        target_S=1,
        n_draws=args.n_draws,
        model=args.injector_model,
        max_turns=40,
        extra_user_hint=hint,
    )
    summary["stages"]["inject"] = {
        "elapsed_s": round(time.time() - t0, 2),
        "n_draws": len(draws),
        "n_with_diff": sum(1 for d in draws if d.diff and d.plan is not None),
        "input_tokens": sum(d.input_tokens for d in draws),
        "output_tokens": sum(d.output_tokens for d in draws),
    }
    log.info("draws done: %d with diff", summary["stages"]["inject"]["n_with_diff"])

    # ----- 4. validator: pick first draw whose diff applies + induces 1..10 F2P -----
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

        _git_reset(workdir, inst.base_commit)
        applies, apply_err = _git_apply_check(workdir, draw.diff)
        if not applies:
            entry["gate_failed"] = "G1"
            entry["reason"] = apply_err
            gate_log.append(entry)
            continue
        files = files_touched(draw.diff)
        entry["files"] = sorted(files)

        # Run pytest with the candidate bug applied, inside the SWE-Bench Pro image.
        # We re-use the skeleton ExamInstance and just slot in the candidate as
        # the injection_patch so entryscript.py applies it.
        cand_exam = skel.model_copy(update={
            "injection_patch": draw.diff,
        })
        t0 = time.time()
        cand_eval = run_swebench_pro_exam(
            exam=cand_exam, image_tag=inst.image_tag, solver_patch="",
            runs_root=runs_root, run_id=f"inject_cand_{i}",
            patch_kind="bug_only", timeout_s=args.timeout_s,
        )
        entry["eval_elapsed_s"] = round(time.time() - t0, 2)
        entry["status_code"] = cand_eval.status_code
        entry["n_passed_post_bug"] = len(cand_eval.passed_tests)
        entry["n_failed_post_bug"] = len(cand_eval.failed_tests)
        passing = set(cand_eval.passed_tests)
        new_failing = baseline_passing - passing
        entry["new_failing"] = sorted(new_failing)[:20]
        entry["n_new_failing"] = len(new_failing)
        if not (1 <= len(new_failing) <= 10):
            entry["gate_failed"] = "G6"
            entry["reason"] = f"|F2P|={len(new_failing)} not in [1,10]"
            gate_log.append(entry)
            continue
        # Loose G7: at least 90% of baseline P2P tests still passing
        p2p_target = baseline_passing - new_failing
        p2p_kept = p2p_target & passing
        if len(p2p_kept) < int(0.9 * len(p2p_target)):
            entry["gate_failed"] = "G7"
            entry["reason"] = f"P2P kept {len(p2p_kept)}/{len(p2p_target)}"
            gate_log.append(entry)
            continue
        entry["passed"] = True
        gate_log.append(entry)
        chosen = draw
        chosen_eval = cand_eval
        chosen_idx = i
        break

    summary["stages"]["validate"] = {
        "n_evaluated": len([g for g in gate_log if "eval_elapsed_s" in g]),
        "winner_idx": chosen_idx,
        "log": gate_log,
    }
    if chosen is None:
        summary["fatal"] = "no draw passed validator gates"
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        return 3

    # ----- 5. freeze ExamInstance -----
    fail_to_pass = sorted(baseline_passing - set(chosen_eval.passed_tests))
    pass_to_pass = sorted(baseline_passing - set(fail_to_pass))
    # Use the planner's summary as the (un-scrubbed) problem statement for M1
    problem_statement = chosen.plan.summary or "A regression has been introduced; tests are failing."
    exam = finalize_exam(
        inst,
        injection_patch=chosen.diff,
        plan=chosen.plan,
        injector_model=args.injector_model or os.environ.get("ANTHROPIC_MODEL", "glm-5.1"),
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
        problem_statement=problem_statement,
    )
    (out_dir / "exam.json").write_text(exam.model_dump_json(indent=2))
    (out_dir / "injection.diff").write_text(chosen.diff)
    summary["exam_id"] = exam.instance_id
    summary["injected_bug"] = {
        "files": sorted(files_touched(chosen.diff)),
        "steps": [s.model_dump() for s in chosen.plan.steps],
        "summary": chosen.plan.summary,
    }
    summary["fail_to_pass"] = fail_to_pass
    summary["n_pass_to_pass"] = len(pass_to_pass)
    log.info("frozen exam %s with F2P=%d P2P=%d",
             exam.instance_id, len(fail_to_pass), len(pass_to_pass))

    # ----- 6. solvers -----
    solver_names = [s for s in args.solvers.split(",") if s]
    summary["solvers"] = {}
    for name in solver_names:
        slot = summary["solvers"].setdefault(name, {})
        try:
            spec = _solver_cfg(name)
            solver = load_solver(spec)
        except Exception as e:
            slot["error"] = f"load failed: {e!r}"
            log.warning("could not load %s: %r", name, e)
            continue
        sw = runs_root / "solver_workdirs" / name
        try:
            _prepare_buggy_workdir(workdir, sw, inst.base_commit, chosen.diff)
        except Exception as e:
            slot["error"] = f"workdir prep failed: {e!r}"
            continue

        t0 = time.time()
        try:
            sres = solver.solve(exam, sw, timeout_s=spec.get("timeout_s", args.timeout_s))
        except Exception as e:
            slot["error"] = f"solver crashed: {e!r}"
            slot["wall_clock_s"] = round(time.time() - t0, 2)
            continue
        slot["wall_clock_s"] = round(time.time() - t0, 2)
        slot["patch_bytes"] = len(sres.patch or "")
        slot["token_usage"] = sres.token_usage
        slot["errored"] = sres.errored
        slot["error_message"] = sres.error_message
        (out_dir / f"{name}.diff").write_text(sres.patch or "")
        log.info("%s: solve in %.1fs, patch=%dB tokens=%s",
                 name, slot["wall_clock_s"], slot["patch_bytes"], sres.token_usage)

        # ----- 7. grade via SWE-Bench Pro image -----
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
            continue
        slot["grade_elapsed_s"] = round(time.time() - t0, 2)
        passed = set(graded.passed_tests)
        f2p_pass = set(fail_to_pass).issubset(passed)
        p2p_pass = len(set(pass_to_pass) - passed) == 0
        slot["status_code"] = graded.status_code
        slot["n_passed"] = len(passed)
        slot["n_failed"] = len(graded.failed_tests)
        slot["f2p_pass_count"] = len(set(fail_to_pass) & passed)
        slot["p2p_pass_count"] = len(set(pass_to_pass) & passed)
        slot["f2p_pass"] = f2p_pass
        slot["p2p_pass"] = p2p_pass
        slot["passed"] = f2p_pass and p2p_pass
        log.info("%s graded: F2P %d/%d, P2P %d/%d, overall=%s",
                 name, slot["f2p_pass_count"], len(fail_to_pass),
                 slot["p2p_pass_count"], len(pass_to_pass), slot["passed"])

    # ----- 8. write summary -----
    summary["finished_at"] = time.time()
    summary["wall_clock_s"] = round(summary["finished_at"] - summary["started_at"], 2)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log.info("summary written to %s", out_dir / "summary.json")
    print(json.dumps({
        "instance_id": args.instance_id,
        "exam_id": summary.get("exam_id"),
        "fatal": summary.get("fatal"),
        "solvers": {k: {kk: vv for kk, vv in v.items()
                        if kk in ("passed", "wall_clock_s", "patch_bytes",
                                  "f2p_pass_count", "p2p_pass_count", "error")}
                    for k, v in summary["solvers"].items()},
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
