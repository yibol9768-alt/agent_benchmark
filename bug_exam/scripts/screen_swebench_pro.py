"""Screen SWE-Bench Pro instances for baseline viability.

Iterates through JSONL, pulls Docker images, runs baseline tests, and produces
a ranked list of instances where baseline tests pass (= viable for injection).

Designed to run on remote (needs Docker). Incremental: skip already-screened
instance_ids when --skip-existing is set.

Usage (remote):
    PYTHONPATH=. .venv/bin/python scripts/screen_swebench_pro.py \
        --jsonl /root/SWE-bench_Pro-os/helper_code/sweap_eval_full_v2.jsonl \
        --swepro-root /root/SWE-bench_Pro-os \
        --out configs/screened_instances.json \
        --repos ansible/ansible,qutebrowser/qutebrowser,internetarchive/openlibrary

Dry-run (Mac, no Docker):
    PYTHONPATH=. .venv/bin/python scripts/screen_swebench_pro.py \
        --jsonl ../SWE-bench_Pro-os/helper_code/sweap_eval_full_v2.jsonl \
        --swepro-root ../SWE-bench_Pro-os \
        --out /tmp/screened_dry.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from bug_exam.adapters.swebench_pro_source import (
    get_dockerhub_image_uri,
    load_instance,
)
from bug_exam.evaluator.swe_bench_pro_runner import run_swebench_pro_exam

log = logging.getLogger("screen")

LANG_MAP = {
    "ansible/ansible": "python",
    "qutebrowser/qutebrowser": "python",
    "internetarchive/openlibrary": "python",
    "flipt-io/flipt": "go",
    "gravitational/teleport": "go",
    "future-architect/vuls": "go",
    "navidrome/navidrome": "go",
    "protonmail/webclients": "typescript",
    "element-hq/element-web": "typescript",
    "tutao/tutanota": "typescript",
    "NodeBB/NodeBB": "javascript",
}


def _docker_pull(image_tag: str, max_retries: int = 3) -> bool:
    """Pull a Docker image with retries."""
    for attempt in range(max_retries):
        log.info("pulling %s (attempt %d/%d)", image_tag, attempt + 1, max_retries)
        try:
            res = subprocess.run(
                ["docker", "pull", "--platform", "linux/amd64", image_tag],
                capture_output=True, text=True, timeout=600,
            )
            if res.returncode == 0:
                return True
            log.warning("pull failed: %s", res.stderr.strip()[-300:])
        except subprocess.TimeoutExpired:
            log.warning("pull timed out (attempt %d/%d)", attempt + 1, max_retries)
        except Exception as e:
            log.warning("pull error: %r", e)
        if attempt < max_retries - 1:
            time.sleep(10 * (attempt + 1))
    return False


def _docker_image_exists(image_tag: str) -> bool:
    res = subprocess.run(
        ["docker", "image", "inspect", image_tag],
        capture_output=True, timeout=30,
    )
    return res.returncode == 0


def screen_one(
    instance_id: str,
    jsonl_path: Path,
    swepro_root: Path,
    dockerhub_username: str,
    timeout_s: int,
    runs_root: Path,
    dry_run: bool = False,
) -> dict:
    """Screen a single instance. Returns a record dict."""
    record = {
        "instance_id": instance_id,
        "repo": "",
        "language": "",
        "baseline_pass_count": 0,
        "selected_test_count": 0,
        "image_tag": "",
        "viable": False,
        "screen_error": None,
    }
    t0 = time.time()

    try:
        inst = load_instance(
            jsonl_path=jsonl_path,
            instance_id=instance_id,
            swebench_pro_root=swepro_root,
            dockerhub_username=dockerhub_username,
        )
    except Exception as e:
        record["screen_error"] = f"load failed: {e!r}"[:300]
        return record

    record["repo"] = inst.repo
    record["language"] = LANG_MAP.get(inst.repo, "unknown")
    record["selected_test_count"] = len(inst.selected_test_files)
    record["image_tag"] = inst.image_tag

    if dry_run:
        record["screen_error"] = "dry_run"
        record["elapsed_s"] = round(time.time() - t0, 2)
        return record

    try:
        # Pull Docker image if needed
        if not _docker_image_exists(inst.image_tag):
            if not _docker_pull(inst.image_tag):
                record["screen_error"] = "docker pull failed"
                record["elapsed_s"] = round(time.time() - t0, 2)
                return record

        # Run baseline
        skel = inst.to_exam_skeleton()
        runs_dir = runs_root / instance_id
        runs_dir.mkdir(parents=True, exist_ok=True)

        baseline = run_swebench_pro_exam(
            exam=skel,
            image_tag=inst.image_tag,
            solver_patch="",
            runs_root=runs_dir,
            run_id="screen_baseline",
            patch_kind="baseline",
            timeout_s=timeout_s,
        )
        record["baseline_pass_count"] = len(baseline.passed_tests)
        record["viable"] = len(baseline.passed_tests) > 0
    except Exception as e:
        record["screen_error"] = f"failed: {e!r}"[:300]

    record["elapsed_s"] = round(time.time() - t0, 2)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description="Screen SWE-Bench Pro instances for baseline viability")
    ap.add_argument("--jsonl", required=True, help="Path to sweap_eval_full_v2.jsonl")
    ap.add_argument("--swepro-root", required=True, help="SWE-bench_Pro-os root directory")
    ap.add_argument("--out", default="configs/screened_instances.json", help="Output JSON path")
    ap.add_argument("--repos", default=None,
                    help="Comma-separated repo filter (e.g. ansible/ansible,qutebrowser/qutebrowser)")
    ap.add_argument("--dockerhub-username", default="jefzda")
    ap.add_argument("--timeout-s", type=int, default=600, help="Baseline test timeout")
    ap.add_argument("--runs-root", default="/tmp/screen_runs", help="Temp dir for Docker runs")
    ap.add_argument("--max-instances", type=int, default=None, help="Stop after N instances")
    ap.add_argument("--skip-existing", action="store_true", help="Skip already-screened instance_ids")
    ap.add_argument("--dry-run", action="store_true", help="Skip Docker (just load metadata)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log.setLevel(logging.INFO)

    jsonl_path = Path(args.jsonl)
    swepro_root = Path(args.swepro_root)
    out_path = Path(args.out)
    runs_root = Path(args.runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)

    # Load existing results for skip-existing
    existing_ids: set[str] = set()
    existing_records: list[dict] = []
    if args.skip_existing and out_path.exists():
        data = json.loads(out_path.read_text())
        existing_records = data.get("instances", [])
        existing_ids = {r["instance_id"] for r in existing_records}
        log.info("loaded %d existing records from %s", len(existing_ids), out_path)

    # Load all instance IDs from JSONL
    repo_filter = set(args.repos.split(",")) if args.repos else None
    candidates: list[str] = []
    with jsonl_path.open() as f:
        for line in f:
            row = json.loads(line)
            iid = row["instance_id"]
            repo = row.get("repo", "")
            if repo_filter and repo not in repo_filter:
                continue
            if iid in existing_ids:
                continue
            candidates.append(iid)

    if args.max_instances:
        candidates = candidates[:args.max_instances]

    log.info("screening %d candidates (skipped %d existing)", len(candidates), len(existing_ids))

    # Screen each instance
    new_records: list[dict] = []
    for i, iid in enumerate(candidates):
        log.info("[%d/%d] screening %s", i + 1, len(candidates), iid[:60])
        record = screen_one(
            instance_id=iid,
            jsonl_path=jsonl_path,
            swepro_root=swepro_root,
            dockerhub_username=args.dockerhub_username,
            timeout_s=args.timeout_s,
            runs_root=runs_root,
            dry_run=args.dry_run,
        )
        new_records.append(record)
        status = "VIABLE" if record["viable"] else f"SKIP ({record.get('screen_error', 'no passing tests')})"
        log.info("  -> %s (baseline_pass=%d, elapsed=%.1fs)",
                 status, record["baseline_pass_count"], record.get("elapsed_s", 0))

        # Write incrementally
        all_records = existing_records + new_records
        all_records.sort(key=lambda r: (-int(r.get("viable", False)), -r.get("baseline_pass_count", 0)))
        output = {
            "screened_at": datetime.now(timezone.utc).isoformat(),
            "total": len(all_records),
            "viable": sum(1 for r in all_records if r.get("viable")),
            "instances": all_records,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2, default=str))

    # Final summary
    all_records = existing_records + new_records
    viable = sum(1 for r in all_records if r.get("viable"))
    log.info("done. total=%d viable=%d output=%s", len(all_records), viable, out_path)
    print(f"screened {len(new_records)} new instances. total={len(all_records)}, viable={viable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
