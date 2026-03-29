"""Evaluate SWE-Bench Pro patches using DockerHub images + official run scripts."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(os.environ.get("SWEBENCH_PRO_SCRIPTS_DIR", "run_scripts"))
DOCKERHUB_USERNAME = "jefzda"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get_docker_image(instance_id: str) -> str:
    tag = instance_id.replace("instance_", "")
    parts = tag.split("__", 1)
    if len(parts) == 2:
        owner, rest = parts
        repo_and_hash = rest
        repo_name = repo_and_hash.split("-")[0]
        tag = f"{owner.lower()}.{repo_name.lower()}-{tag}"
    if tag.endswith("-vnan"):
        tag = tag[:-5]
    if len(tag) > 128:
        tag = tag[:128]
    return f"{DOCKERHUB_USERNAME}/sweap-images:{tag}"


def get_docker_image_from_info(instance_id: str) -> str | None:
    info_file = SCRIPTS_DIR / instance_id / "instance_info.txt"
    if not info_file.exists():
        return None
    text = info_file.read_text()
    for line in text.splitlines():
        if line.startswith("DockerHub Tag:"):
            tag = line.split(":", 1)[1].strip()
            if tag:
                return f"{DOCKERHUB_USERNAME}/sweap-images:{tag}"
    return None


def get_fail_to_pass(instance_id: str) -> list[str]:
    info_file = SCRIPTS_DIR / instance_id / "instance_info.txt"
    if not info_file.exists():
        return []
    text = info_file.read_text()
    for line in text.splitlines():
        if line.startswith("FAIL_TO_PASS:"):
            raw = line.split(":", 1)[1].strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return [raw]
    return []


def get_test_patch(instance_id: str) -> str:
    """Load test_patch from HuggingFace dataset (cached after first call)."""
    if not hasattr(get_test_patch, "_cache"):
        get_test_patch._cache = {}
    if instance_id in get_test_patch._cache:
        return get_test_patch._cache[instance_id]
    try:
        from datasets import load_dataset
        ds = load_dataset("ScaleAI/SWE-bench_Pro", split="test")
        for row in ds:
            get_test_patch._cache[row["instance_id"]] = row.get("test_patch", "")
        return get_test_patch._cache.get(instance_id, "")
    except Exception:
        return ""


def run_eval_in_docker(
    instance_id: str,
    patch: str,
    output_dir: Path,
    timeout_sec: int = 600,
) -> dict[str, Any]:
    instance_dir = output_dir / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)

    run_script = SCRIPTS_DIR / instance_id / "run_script.sh"
    parser_script = SCRIPTS_DIR / instance_id / "parser.py"
    if not run_script.exists():
        return {"instance_id": instance_id, "status": "error", "error": "run_script.sh not found"}

    # Combine test_patch (new test cases) + model patch
    test_patch = get_test_patch(instance_id)
    combined_patch = (test_patch + "\n" + patch) if test_patch.strip() else patch

    patch_path = (instance_dir / "patch.diff").resolve()
    patch_path.write_text(combined_patch, encoding="utf-8")

    run_script_path = run_script.resolve()
    parser_path = parser_script.resolve() if parser_script.exists() else None

    image = get_docker_image_from_info(instance_id) or get_docker_image(instance_id)

    fail_to_pass = get_fail_to_pass(instance_id)
    test_files = list({t.split(" | ")[0] for t in fail_to_pass if " | " in t})
    if not test_files:
        test_files_arg = ""
    else:
        test_files_arg = ",".join(test_files)

    run_script_content = run_script_path.read_text()

    docker_cmd = f"""
cat > /tmp/patch.diff
cat > /tmp/run.sh <<'RUNSCRIPT'
{run_script_content}
RUNSCRIPT
chmod +x /tmp/run.sh
cd /app
git apply /tmp/patch.diff 2>/dev/null || git apply --3way /tmp/patch.diff || echo "PATCH_APPLY_WARNING"
bash /tmp/run.sh {test_files_arg}
"""

    cmd = [
        "docker", "run", "--rm", "-i",
        "--platform", "linux/amd64",
        "--entrypoint", "/bin/bash",
        image,
        "-c", docker_cmd,
    ]

    stdout_path = instance_dir / "test_stdout.txt"
    stderr_path = instance_dir / "test_stderr.txt"

    log(f"{instance_id}: pulling image {image.split('/')[-1]}...")
    pull_result = subprocess.run(
        ["docker", "pull", "--platform", "linux/amd64", image],
        capture_output=True, text=True, timeout=300,
    )
    if pull_result.returncode != 0:
        return {
            "instance_id": instance_id,
            "status": "error",
            "error": f"docker pull failed: {pull_result.stderr[:500]}",
        }

    log(f"{instance_id}: running tests...")
    try:
        with stdout_path.open("w") as out, stderr_path.open("w") as err:
            proc = subprocess.run(
                cmd, stdout=out, stderr=err, text=True,
                input=combined_patch, timeout=timeout_sec,
            )
    except subprocess.TimeoutExpired:
        return {
            "instance_id": instance_id,
            "status": "timeout",
            "error": f"Test execution timed out after {timeout_sec}s",
        }

    stdout_text = stdout_path.read_text(errors="ignore")
    stderr_text = stderr_path.read_text(errors="ignore")

    # Parse results - extract from mocha JSON or pytest output
    passed_tests = []
    failed_tests = []

    # Try direct JSON parsing first (mocha --reporter=json)
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(stdout_text):
        pos = stdout_text.find("{", idx)
        if pos < 0:
            break
        try:
            obj, end = decoder.raw_decode(stdout_text, pos)
            for t in obj.get("passes", []):
                passed_tests.append(t.get("fullTitle", t.get("title", "")))
            for t in obj.get("failures", []):
                failed_tests.append(t.get("fullTitle", t.get("title", "")))
            idx = end
        except json.JSONDecodeError:
            idx = pos + 1

    # Fallback: use official parser if no JSON found
    if not passed_tests and not failed_tests and parser_path and parser_path.exists():
        try:
            parse_result = subprocess.run(
                [sys.executable, str(parser_path), str(stdout_path), str(stderr_path)],
                capture_output=True, text=True, timeout=30,
            )
            if parse_result.returncode == 0:
                parsed = json.loads(parse_result.stdout)
                for t in parsed.get("tests", []):
                    if t.get("status") == "PASSED":
                        passed_tests.append(t["name"])
                    else:
                        failed_tests.append(t["name"])
        except Exception:
            pass

    # Check fail-to-pass
    f2p_passed = 0
    f2p_failed = 0
    f2p_details = []
    for test_name in fail_to_pass:
        # Try exact match first
        test_passed = test_name in passed_tests
        # Try partial match on the descriptive part after "::" or " | "
        if not test_passed and passed_tests:
            parts = test_name.replace(" | ", "::").split("::")
            search_terms = [p.strip() for p in parts if len(p.strip()) > 10]
            if search_terms:
                test_passed = any(
                    all(term in p for term in search_terms)
                    for p in passed_tests
                )
        if test_passed:
            f2p_passed += 1
            f2p_details.append({"test": test_name, "result": "PASSED"})
        else:
            f2p_failed += 1
            f2p_details.append({"test": test_name, "result": "FAILED"})

    instance_passed = f2p_failed == 0 and f2p_passed > 0

    result = {
        "instance_id": instance_id,
        "status": "passed" if instance_passed else "failed",
        "exit_code": proc.returncode,
        "image": image,
        "total_tests_parsed": len(passed_tests) + len(failed_tests),
        "fail_to_pass_total": len(fail_to_pass),
        "fail_to_pass_passed": f2p_passed,
        "fail_to_pass_failed": f2p_failed,
        "fail_to_pass_details": f2p_details,
        "instance_resolved": instance_passed,
    }

    (instance_dir / "eval_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate SWE-Bench Pro patches.")
    parser.add_argument("--patches", required=True, help="Path to patches JSON file")
    parser.add_argument("--output-dir", required=True, help="Output directory for results")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout per instance (seconds)")
    parser.add_argument("--scripts-dir", help="Override run_scripts directory")
    args = parser.parse_args()

    if args.scripts_dir:
        global SCRIPTS_DIR
        SCRIPTS_DIR = Path(args.scripts_dir)

    patches = json.loads(Path(args.patches).read_text())
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    log(f"Evaluating {len(patches)} patches...")

    results = []
    for p in patches:
        instance_id = p["instance_id"]
        patch = p.get("patch") or p.get("model_patch", "")
        if not patch.strip():
            results.append({"instance_id": instance_id, "status": "empty_patch"})
            continue
        result = run_eval_in_docker(
            instance_id=instance_id,
            patch=patch,
            output_dir=output_dir,
            timeout_sec=args.timeout,
        )
        results.append(result)
        status = result.get("status", "unknown")
        f2p = f"{result.get('fail_to_pass_passed', 0)}/{result.get('fail_to_pass_total', 0)}"
        log(f"{instance_id}: {status} (fail-to-pass: {f2p})")

    resolved = sum(1 for r in results if r.get("instance_resolved"))
    total = len(results)
    accuracy = resolved / total * 100 if total else 0

    print(f"\n{'='*50}")
    print(f" SWE-Bench Pro Evaluation Results")
    print(f"{'='*50}")
    print(f" Total instances:  {total}")
    print(f" Resolved:         {resolved}")
    print(f" Accuracy:         {accuracy:.1f}%")
    print(f"{'='*50}")
    for r in results:
        iid = r["instance_id"].split("__")[1][:40] if "__" in r["instance_id"] else r["instance_id"][:40]
        status = r.get("status", "?")
        f2p = f"{r.get('fail_to_pass_passed', 0)}/{r.get('fail_to_pass_total', 0)}"
        print(f"  {iid:42s} {status:8s} f2p={f2p}")
    print(f"{'='*50}")

    summary = {
        "total": total,
        "resolved": resolved,
        "accuracy_pct": round(accuracy, 2),
        "results": results,
    }
    (output_dir / "eval_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
