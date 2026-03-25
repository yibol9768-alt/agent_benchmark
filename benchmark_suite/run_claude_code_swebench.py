from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def repo_dir_name(repo: str) -> str:
    return repo.replace("/", "__")


def ensure_repo_cloned(repo: str, repos_root: Path) -> Path:
    repo_path = repos_root / repo_dir_name(repo)
    if repo_path.exists():
        return repo_path
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", f"https://github.com/{repo}.git", str(repo_path)],
        check=True,
        text=True,
    )
    return repo_path


def load_samples(samples_path: Path) -> dict[str, dict]:
    rows = {}
    with samples_path.open() as handle:
        for line in handle:
            row = json.loads(line)
            rows[row["instance_id"]] = row
    return rows


def run_instance(
    row: dict,
    output_root: Path,
    repos_root: Path,
    empty_mcp_config: Path,
    timeout_sec: int,
    resume: bool,
) -> None:
    instance_id = row["instance_id"]
    task_dir = output_root / instance_id
    task_dir.mkdir(parents=True, exist_ok=True)
    summary_path = task_dir / "summary.json"
    if resume and summary_path.exists():
        log(f"{instance_id}: skipped, summary already exists")
        return

    repo_path = ensure_repo_cloned(row["repo"], repos_root)
    worktree = task_dir / "repo"
    if worktree.exists():
        shutil.rmtree(worktree)

    stdout_path = task_dir / "claude_stdout.txt"
    stderr_path = task_dir / "claude_stderr.txt"
    prompt_path = task_dir / "prompt.txt"
    patch_path = task_dir / "patch.diff"
    patch_json_path = task_dir / "patch.json"

    prompt = f"""You are solving one real SWE-Bench Pro instance inside a checked-out repository.

Rules:
- Modify files directly in the repository.
- Run only the most relevant targeted tests when useful.
- Do not create a commit.
- Do not print a patch.
- When finished, print a short summary of what you changed and whether tests passed.

Instance ID: {row['instance_id']}
Repository: {row['repo']}
Language: {row['repo_language']}

Problem statement:
{row['problem_statement']}

Requirements:
{row['requirements']}

Interface:
{row['interface']}

Fail-to-pass tests:
{row['fail_to_pass']}

Pass-to-pass tests:
{row['pass_to_pass']}

Suggested targeted test files:
{row['selected_test_files_to_run']}
"""
    prompt_path.write_text(prompt)

    log(f"{instance_id}: creating worktree")
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", "--detach", str(worktree), row["base_commit"]],
        check=True,
        text=True,
    )

    exit_code = -1
    elapsed = 0.0
    status_output = ""
    diff_stat = ""
    diff = ""

    try:
        log(f"{instance_id}: preparing repository state")
        subprocess.run(row["before_repo_set_cmd"], shell=True, cwd=worktree, check=True, text=True)

        log(f"{instance_id}: starting Claude Code")
        started = time.time()
        with stdout_path.open("w") as out_handle, stderr_path.open("w") as err_handle:
            proc = subprocess.Popen(
                [
                    "claude",
                    "-p",
                    "--model",
                    "opus",
                    "--permission-mode",
                    "bypassPermissions",
                    "--dangerously-skip-permissions",
                    "--strict-mcp-config",
                    "--mcp-config",
                    str(empty_mcp_config),
                    "--",
                    prompt,
                ],
                cwd=worktree,
                text=True,
                stdout=out_handle,
                stderr=err_handle,
            )
            try:
                proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=30)
                err_handle.write("\n[TIMEOUT]\n")
        elapsed = time.time() - started
        exit_code = proc.returncode
        log(f"{instance_id}: Claude Code finished exit_code={exit_code} elapsed={elapsed:.2f}s")

        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=worktree,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        status_output = subprocess.run(
            ["git", "status", "--short"],
            cwd=worktree,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        diff_stat = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=worktree,
            text=True,
            capture_output=True,
            check=True,
        ).stdout

        patch_path.write_text(diff)
        patch_json_path.write_text(
            json.dumps(
                [
                    {
                        "instance_id": instance_id,
                        "patch": diff,
                        "prefix": "claude-glm",
                    }
                ],
                indent=2,
            )
        )
    finally:
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree)],
            check=False,
            text=True,
        )

    summary = {
        "instance_id": instance_id,
        "repo": row["repo"],
        "exit_code": exit_code,
        "elapsed_sec": elapsed,
        "diff_len": len(diff),
        "status": status_output,
        "diff_stat": diff_stat,
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    log(f"{instance_id}: wrote summary diff_len={len(diff)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SWE-Bench Pro with Claude Code.")
    parser.add_argument("--samples", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--repos-root", required=True)
    parser.add_argument("--empty-mcp-config", required=True)
    parser.add_argument("--timeout-sec", type=int, default=1200)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    samples = load_samples(Path(args.samples))
    manifest = json.loads(Path(args.manifest).read_text())
    if args.limit is not None:
        manifest = manifest[: args.limit]

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    repos_root = Path(args.repos_root)
    repos_root.mkdir(parents=True, exist_ok=True)

    run_manifest = {
        "samples": args.samples,
        "manifest": manifest,
        "repos_root": str(repos_root),
        "empty_mcp_config": args.empty_mcp_config,
        "timeout_sec": args.timeout_sec,
        "resume": args.resume,
    }
    (output_root / "run_manifest.json").write_text(json.dumps(run_manifest, indent=2))

    for instance_id in manifest:
        run_instance(
            row=samples[instance_id],
            output_root=output_root,
            repos_root=repos_root,
            empty_mcp_config=Path(args.empty_mcp_config),
            timeout_sec=args.timeout_sec,
            resume=args.resume,
        )


if __name__ == "__main__":
    main()
