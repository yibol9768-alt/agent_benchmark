"""Run our claude_direct solver against one SWE-Bench Pro instance.

The goal: apples-to-apples baseline. The same glm-5 + claude_direct scaffold
that just passed the bug_exam tiny_py_repo pipeline now attempts one real
SWE-Bench Pro bug. Output is a SWE-Bench-Pro-format patch JSON that can be
fed into `swe_bench_pro_eval.py --use_local_docker` for grading.

Usage:
    set -a && source .env && set +a
    PYTHONPATH=. .venv/bin/python scripts/run_one_swebench_pro.py \\
        --jsonl /Users/liuyibo/Desktop/lyb/benchmarks/SWE-bench_Pro-os/helper_code/sweap_eval_full_v2.jsonl \\
        --instance-id instance_qutebrowser__qutebrowser-0b621cb0ce2b54d3f93d8d41d8ff4257888a87e5-v2ef375ac784985212b1805e1d0431dc8f1b3c171
"""
from __future__ import annotations

import argparse
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

from bug_exam.schema import BreakPlan, ExamInstance, ExamStatus, Language
from bug_exam.solvers.claude_direct import ClaudeDirectSolver


log = logging.getLogger("run_one_swebench_pro")


def parse_list(x) -> list[str]:
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        return json.loads(x) if x.strip().startswith("[") else eval(x)
    return []


def clone_and_prepare(repo_url: str, workdir: Path, base_commit: str, before_cmd: str) -> None:
    if workdir.exists():
        subprocess.run(["rm", "-rf", str(workdir)], check=True)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    print(f"[clone] {repo_url} -> {workdir}")
    subprocess.run(["git", "clone", "--quiet", repo_url, str(workdir)], check=True)
    print(f"[prep] running before_repo_set_cmd ({before_cmd.count(chr(10))+1} lines)")
    subprocess.run(
        ["bash", "-c", before_cmd],
        cwd=str(workdir),
        check=True,
    )


def make_exam(row: dict) -> ExamInstance:
    problem_statement = row["problem_statement"]
    # SWE-Bench Pro sometimes bundles requirements/interface in separate cols;
    # the JSONL we have only has problem_statement.
    return ExamInstance(
        instance_id=row["instance_id"],
        repo_id=row["instance_id"],
        repo_url=f"https://github.com/{row['repo']}.git",
        language=Language.PYTHON,   # placeholder; solver doesn't actually branch on this
        base_commit=row["base_commit"],
        injection_patch=row["patch"],   # gold patch, for reference only
        break_plan=BreakPlan(
            target_F=1, target_S=1, steps=[],
            summary="(external: SWE-Bench Pro real bug)",
        ),
        injector_model="swebench_pro_gold",
        patch_hash="0" * 16,
        difficulty_band="swebench_pro",
        F=1, S=1,
        FAIL_TO_PASS=parse_list(row["FAIL_TO_PASS"]),
        PASS_TO_PASS=parse_list(row["PASS_TO_PASS"]),
        selected_test_files=parse_list(row["selected_test_files_to_run"]),
        problem_statement=problem_statement,
        base_dockerfile_path="",
        instance_dockerfile_path="",
        run_script_path="",
        parser_path="",
        test_framework="pytest",
        before_repo_set_cmd=row.get("before_repo_set_cmd", ""),
        status=ExamStatus.FROZEN,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--workdir", default="/tmp/bugexam_swebench_pro/work")
    ap.add_argument("--out", default="/tmp/bugexam_swebench_pro/out")
    ap.add_argument("--timeout-s", type=int, default=900)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--skip-clone", action="store_true",
                    help="Workdir is pre-prepared (already at base_commit + test-patch). Skip clone + before_cmd.")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 1. Load instance
    with open(args.jsonl) as f:
        for line in f:
            r = json.loads(line)
            if r["instance_id"] == args.instance_id:
                break
        else:
            print(f"instance {args.instance_id} not found in {args.jsonl}", file=sys.stderr)
            sys.exit(2)

    print(f"=== instance: {r['instance_id']}")
    print(f"=== repo:     {r['repo']}")
    print(f"=== base:     {r['base_commit'][:12]}")
    print(f"=== F2P:      {len(parse_list(r['FAIL_TO_PASS']))}   P2P: {len(parse_list(r['PASS_TO_PASS']))}")
    print(f"=== stmt len: {len(r['problem_statement'])}")

    # 2. Prepare workdir (clone + before_cmd) — or reuse pre-prepared
    workdir = Path(args.workdir)
    if args.skip_clone:
        assert workdir.exists() and (workdir / ".git").exists(), \
            f"--skip-clone requires {workdir} to be an existing git checkout"
        print(f"[reuse] pre-prepared workdir at {workdir}")
    else:
        clone_and_prepare(
            repo_url=f"https://github.com/{r['repo']}.git",
            workdir=workdir,
            base_commit=r["base_commit"],
            before_cmd=r["before_repo_set_cmd"],
        )
        print(f"[ready] workdir at {workdir}")

    # 3. Build ExamInstance + solver
    exam = make_exam(r)
    solver = ClaudeDirectSolver(
        provider="glm",
        model=os.environ.get("GLM_MODEL", "glm-5"),
        max_turns=30,
        timeout_s=args.timeout_s,
    )
    print(f"\n=== solve: {solver.name} via {solver.provider}/{solver.model}")

    t0 = time.time()
    result = solver.solve(exam, workdir, timeout_s=args.timeout_s)
    elapsed = time.time() - t0
    print(f"=== solver done in {elapsed:.1f}s, patch len={len(result.patch)}, "
          f"turns={getattr(result, 'n_turns', '?')}, "
          f"tokens={result.token_usage}, errored={result.errored}")
    if result.errored:
        print(f"=== error: {result.error_message}")

    # 4. Save patch in SWE-Bench-Pro-eval JSON format
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    patch_path = out_dir / "patches.json"
    patch_path.write_text(json.dumps([{
        "instance_id": r["instance_id"],
        "patch": result.patch,
        "prefix": "glm5_claude_direct",
    }], indent=2))
    print(f"\n=== patch saved to {patch_path}")

    # Also save the raw solver trace
    (out_dir / f"{r['instance_id']}.diff").write_text(result.patch or "")
    (out_dir / f"{r['instance_id']}.meta.json").write_text(json.dumps({
        "instance_id": r["instance_id"],
        "wall_clock_s": elapsed,
        "token_usage": result.token_usage,
        "errored": result.errored,
        "error_message": result.error_message,
        "patch_len": len(result.patch),
        "gold_patch_len": len(r["patch"]),
    }, indent=2))
    print(f"=== trace saved to {out_dir}")

    print(f"\nnext step: eval via SWE-Bench-Pro harness:")
    print(f"  cd /Users/liuyibo/Desktop/lyb/benchmarks/SWE-bench_Pro-os")
    print(f"  python swe_bench_pro_eval.py \\\\")
    print(f"    --raw_sample_path helper_code/sweap_eval_full_v2.jsonl \\\\")
    print(f"    --patch_path {patch_path} \\\\")
    print(f"    --output_dir /tmp/bugexam_swebench_pro/eval \\\\")
    print(f"    --scripts_dir run_scripts \\\\")
    print(f"    --use_local_docker \\\\")
    print(f"    --dockerhub_username jefzda \\\\")
    print(f"    --num_workers 1")


if __name__ == "__main__":
    main()
