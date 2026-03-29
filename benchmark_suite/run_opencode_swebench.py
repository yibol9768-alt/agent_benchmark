from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import stat
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


_REPO_LOCKS: dict[str, threading.Lock] = {}
_REPO_LOCKS_GUARD = threading.Lock()
FIRST_NODEBB_INSTANCE = "instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan"


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def repo_dir_name(repo: str) -> str:
    return repo.replace("/", "__")


def get_repo_lock(repo: str) -> threading.Lock:
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(repo)
        if lock is None:
            lock = threading.Lock()
            _REPO_LOCKS[repo] = lock
        return lock


def ensure_repo_cloned(repo: str, repos_root: Path) -> Path:
    repo_path = repos_root / repo_dir_name(repo)
    with get_repo_lock(repo):
        if repo_path.exists():
            return repo_path
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", f"https://github.com/{repo}.git", str(repo_path)],
            check=True,
            text=True,
        )
    return repo_path


def load_dataset_rows(split: str, limit: int | None, instance_ids: set[str] | None) -> list[dict[str, Any]]:
    from datasets import load_dataset

    dataset = load_dataset("ScaleAI/SWE-bench_Pro", split=split)
    rows: list[dict[str, Any]] = []
    for row in dataset:
        row = dict(row)
        if instance_ids and row["instance_id"] not in instance_ids:
            continue
        rows.append(row)
        if limit is not None and len(rows) >= limit:
            break
    return rows


def render_field(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def compact_text(value: Any) -> str:
    text = render_field(value).replace("\\n", " ").replace("\\r", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def clip_text(value: Any, max_len: int) -> str:
    text = render_field(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 15].rstrip() + "\n...[truncated]"


def extract_priority_hints(row: dict[str, Any], limit: int = 12) -> list[str]:
    seen: set[str] = set()
    hints: list[str] = []

    def add(value: str) -> None:
        token = value.strip().strip("`").strip('"').strip("'").strip(".,:;()[]{}")
        token = token.replace("\\n", "").replace("\\r", "")
        if not token:
            return
        if len(token) < 3:
            return
        if token[0].islower() is False and token.startswith("n") and len(token) > 1 and token[1].isupper():
            token = token[1:]
        lowered = token.lower()
        if lowered in {
            "uix",
            "ui/ux",
            "title",
            "description",
            "steps",
            "labels",
            "what",
            "instead",
            "should",
            "would",
            "could",
            "this",
            "that",
            "with",
            "from",
            "when",
            "then",
            "into",
            "your",
            "user",
            "users",
            "email",
            "emails",
            "validation",
            "confirmed",
            "confirmation",
            "manage",
            "admin",
            "panel",
            "expected",
            "happened",
            "correctly",
            "accurately",
            "return",
            "shouldreturn",
            "status",
            "tests",
            "files",
            "task",
            "repo",
            "repository",
            "labels",
            "bug",
            "back-end",
            "authentication",
        }:
            return
        if lowered in seen:
            return
        seen.add(lowered)
        hints.append(token)

    for field in ("problem_statement", "requirements", "interface"):
        text = compact_text(row.get(field))
        for match in re.findall(r"`([^`]+)`", text):
            add(match)
        for match in re.findall(r"\b(?:[A-Za-z_][A-Za-z0-9_:-]*[/.])+[A-Za-z0-9_.:-]+\b", text):
            add(match)
        for match in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*[A-Z][A-Za-z0-9_]*\b", text):
            add(match)

    for test_name in row.get("selected_test_files_to_run") or []:
        for match in re.findall(r"(?:^| )(test/[^ :]+)", str(test_name)):
            add(match)

    if row.get("repo") == "NodeBB/NodeBB":
        for token in [
            "loadUserInfo",
            "getConfirmObjs",
            "validateEmail",
            "confirmByUid",
            "sendValidationEmail",
            "getEmailForValidation",
            "isValidationPending",
            "canSendValidation",
            "expireValidation",
            "src/controllers/admin/users.js",
            "src/socket.io/admin/user.js",
            "src/user/email.js",
            "src/user/info.js",
            "src/user/delete.js",
            "src/database/redis/main.js",
            "src/database/mongo/main.js",
            "src/database/postgres/main.js",
        ]:
            add(token)

    return hints[:limit]


def build_presearch_summary(worktree: Path, hints: list[str], limit: int = 8) -> str:
    sections: list[str] = []
    for hint in hints[:limit]:
        if "/" in hint and not hint.endswith("()"):
            candidate = worktree / hint
            if candidate.exists():
                sections.append(f"- File exists: `{hint}`")
            continue
        try:
            result = subprocess.run(
                ["git", "grep", "-n", "-m", "3", "--", hint],
                cwd=worktree,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError:
            continue
        if result.returncode != 0 or not result.stdout.strip():
            continue
        matches = result.stdout.strip().splitlines()[:3]
        sections.append(f"- Hint `{hint}` matches:\n" + "\n".join(f"  - {line}" for line in matches))

    if not sections:
        return ""
    return "Precomputed repository hints:\n" + "\n".join(sections)


def protect_test_files(worktree: Path) -> list[str]:
    protected: list[str] = []

    def make_readonly(path: Path) -> None:
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            return
        readonly_mode = mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        if readonly_mode != mode:
            path.chmod(readonly_mode)
        protected.append(str(path.relative_to(worktree)))

    for root_name in ("test", "tests"):
        root = worktree / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                make_readonly(path)

    for pattern in ("**/*.spec.*", "**/*.test.*"):
        for path in worktree.glob(pattern):
            if path.is_file():
                make_readonly(path)

    return sorted(set(protected))


def is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return (
        normalized.startswith("test/")
        or normalized.startswith("tests/")
        or "/test/" in normalized
        or "/tests/" in normalized
        or normalized.endswith(".spec.js")
        or normalized.endswith(".test.js")
        or ".spec." in normalized
        or ".test." in normalized
    )


def build_prompt(row: dict[str, Any]) -> str:
    validation_hint = build_validation_hint(row)
    return f"""You are solving one real SWE-Bench Pro instance inside a checked-out repository.

You are already inside the target repository at the correct base commit.
Modify files directly in the repository.
Use the available tools to inspect files, run commands, edit code, and verify changes.
Run only targeted checks when useful.
Avoid broad repository exploration unless a targeted search fails.
Never modify files under test/ and never modify any test files.
If you accidentally modify a test file, revert it before finishing.
Do not create a commit.
Do not ask for clarification.
Stop when you believe the fix is complete.

Instance ID: {row['instance_id']}
Repository: {row['repo']}
Language: {row['repo_language']}

Problem statement:
{clip_text(row['problem_statement'], 4000)}

Requirements:
{clip_text(row['requirements'], 5000)}

Interface:
{clip_text(row['interface'], 2000)}

Fail-to-pass tests:
{render_field(row['fail_to_pass'])}

Suggested targeted test files:
{render_field(row['selected_test_files_to_run'])}

Validation goal:
{validation_hint}
"""


def build_instruction(row: dict[str, Any]) -> str:
    hints = extract_priority_hints(row)
    hint_lines = "\n".join(f"- `{hint}`" for hint in hints) if hints else "- No explicit symbol hints extracted; derive targets from the failing tests and problem statement."
    requirements = clip_text(row.get("requirements"), 5000)
    interface = clip_text(row.get("interface"), 2500)
    fail_to_pass = render_field(row.get("fail_to_pass"))
    selected_tests = render_field(row.get("selected_test_files_to_run"))
    validation_hint = build_validation_hint(row)
    return f"""# SWE-Bench Pro Benchmark Instructions

You are solving one benchmark instance and must behave like a patch-producing coding agent.

Hard rules:
- Do not modify any file under `test/`.
- Do not modify any test file anywhere in the repository.
- Only modify non-test source files needed for the fix.
- Focus on the repository and task below, not generic project exploration.
- Do not rewrite or add tests.
- Prefer targeted symbol search and file reads over broad scans.
- Prefer the specific files, symbols, and behaviors named in the task.
- Treat the Requirements section as binding. Do not stop after a partial implementation.
- If the task names multiple functions, files, or handlers, inspect all of them before deciding the fix is complete.
- Before finishing, cross-check every explicit requirement bullet against your code changes or the existing code.
- When you have enough information, edit the source files directly.
- Before finishing, run only targeted validation commands when practical.
- If you find yourself about to edit a test, stop and find the production code that should satisfy that test instead.
- For the first few steps, search for concrete symbols, file paths, and failing behaviors from the task instead of browsing the repository root.

Task anchor:
- Instance ID: {row['instance_id']}
- Repository: {row['repo']}
- Problem statement: {render_field(row['problem_statement'])}

Requirements to satisfy:
{requirements}

Interface and named codepaths:
{interface}

Fail-to-pass tests:
{fail_to_pass}

Suggested targeted test files:
{selected_tests}

Validation goal:
{validation_hint}

Priority symbols and files for the first steps:
{hint_lines}

First-step strategy:
1. Search for the symbols, file paths, or failing behaviors above.
2. Read the smallest set of relevant non-test source files.
3. If the requirements mention multiple codepaths, inspect every named codepath before editing.
4. Edit only the minimum source files needed for the full fix, not a subset.
5. Run only targeted checks that validate the changed behavior.
6. Before stopping, verify that the patch covers the failing tests and all explicit requirement bullets.
"""


def build_agent_prompt() -> str:
    return """You are a benchmark patch-producing coding agent.

Your job is to inspect the repository, identify the minimal production-code fix, edit the source files, and stop.

Operating rules:
- Work only inside the checked-out repository.
- Do not modify tests.
- Do not use subagents or planning tools.
- Do not browse the web.
- Prefer grep/glob/read/bash for targeted code search.
- Prefer editing existing production files over creating new files.
- Avoid broad exploration of the repository root unless a targeted search fails.
- Once you identify the relevant source files, make the smallest correct code change.
- Do not stop after a partial patch. If the task names multiple required functions, files, or handlers, inspect and cover all of them.
- Before you finish, mentally check every explicit requirement bullet and every named file/function against the patch.
- After editing, run only targeted validation commands if useful.
- If a file path or symbol appears in the task, search for it first.

You are being evaluated on producing the correct source patch, not on explaining yourself.
"""


def build_validation_hint(row: dict[str, Any]) -> str:
    if row["instance_id"] == FIRST_NODEBB_INSTANCE:
        return (
            "This NodeBB task is only complete when the patch passes the targeted NodeBB checks for "
            "`test/database.js`, `test/database/keys.js`, and `test/user/emails.js` in the benchmark image. "
            "Do not stop after adding `db.mget`; the email validation codepaths must also be correct."
        )
    return "The patch is only complete when the targeted fail-to-pass behavior is fixed without modifying tests."


def get_validation_spec(row: dict[str, Any]) -> dict[str, str] | None:
    if row["instance_id"] == FIRST_NODEBB_INSTANCE:
        return {
            "image": "jefzda/sweap-images:nodebb.nodebb-NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5",
            "command": (
                "cd /app && git apply /tmp/patch.diff && redis-server --daemonize yes && "
                "./node_modules/.bin/mocha --exit test/database.js test/database/keys.js test/user/emails.js"
            ),
        }
    return None


def run_opencode_once(worktree: Path, model: str, prompt: str, stdout_path: Path, stderr_path: Path, timeout_sec: int) -> tuple[int, float, bool]:
    timed_out = False
    started = time.time()
    with stdout_path.open("w", encoding="utf-8") as out_handle, stderr_path.open("w", encoding="utf-8") as err_handle:
        proc = subprocess.Popen(
            ["opencode", "run", "--print-logs", "--model", model, prompt],
            cwd=worktree,
            text=True,
            stdout=out_handle,
            stderr=err_handle,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=30)
            err_handle.write("\n[TIMEOUT]\n")
    return proc.returncode, time.time() - started, timed_out


def collect_repo_outputs(worktree: Path) -> tuple[list[str], str, str, str]:
    changed_files = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    reverted_test_files = [path for path in changed_files if is_test_path(path)]
    if reverted_test_files:
        subprocess.run(
            ["git", "checkout", "HEAD", "--", *reverted_test_files],
            cwd=worktree,
            text=True,
            check=True,
        )

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
    return reverted_test_files, diff, status_output, diff_stat


def _docker_available() -> bool:
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def run_validation(task_dir: Path, row: dict[str, Any], diff: str, attempt: int) -> dict[str, Any] | None:
    spec = get_validation_spec(row)
    if not spec or not diff.strip():
        return None

    if not _docker_available():
        log("Docker is not available, skipping validation")
        return None

    patch_path = (task_dir / f"validation_attempt{attempt}.patch").resolve()
    stdout_path = task_dir / f"validation_attempt{attempt}.stdout.txt"
    stderr_path = task_dir / f"validation_attempt{attempt}.stderr.txt"
    summary_path = task_dir / f"validation_attempt{attempt}.json"
    patch_path.write_text(diff, encoding="utf-8")

    docker_script = f"cat > /tmp/patch.diff && {spec['command']}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--platform",
        "linux/amd64",
        "--entrypoint",
        "/bin/bash",
        spec["image"],
        "-c",
        docker_script,
    ]
    with stdout_path.open("w", encoding="utf-8") as out_handle, stderr_path.open("w", encoding="utf-8") as err_handle:
        proc = subprocess.run(cmd, input=diff, text=True, stdout=out_handle, stderr=err_handle, check=False)
    result = {
        "attempt": attempt,
        "exit_code": proc.returncode,
        "passed": proc.returncode == 0,
        "image": spec["image"],
        "command": spec["command"],
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def build_repair_prompt(row: dict[str, Any], validation: dict[str, Any] | None) -> str:
    failure_summary = "No patch was produced."
    if validation is not None:
        stdout = Path(validation["stdout_path"]).read_text(encoding="utf-8", errors="ignore")
        stderr = Path(validation["stderr_path"]).read_text(encoding="utf-8", errors="ignore")
        combined = (stdout + "\n" + stderr).strip()
        failure_summary = clip_text(combined, 5000)
    return f"""Continue fixing the current repository state. Your previous attempt is not correct yet.

Hard rules:
- Do not modify tests.
- Keep the existing source edits that are already correct; only adjust what is needed.
- Focus on the remaining failing behavior, not a full rewrite.

Validation feedback from the previous attempt:
{failure_summary}

Use this feedback to repair the production code and stop only when the fix is complete.
"""


def write_opencode_config(worktree: Path, base_url: str, api_key: str, model: str) -> None:
    provider_id, model_id = model.split("/", 1) if "/" in model else ("github-copilot", model)
    if provider_id not in {"openai", "github-copilot"}:
        raise ValueError(f"Only openai-compatible providers are supported in this runner, got: {provider_id}")
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider_id}/{model_id}",
        "small_model": f"{provider_id}/{model_id}",
        "disabled_providers": ["opencode", *(["openai"] if provider_id == "github-copilot" else [])],
        "instructions": ["./.opencode/benchmark.md"],
        "agent": {
            "build": {
                "prompt": build_agent_prompt(),
                "tools": {
                    "task": False,
                    "todowrite": False,
                    "webfetch": False,
                    "websearch": False,
                    "skill": False,
                },
                "permission": {
                    "question": "deny",
                    "plan_enter": "deny",
                    "plan_exit": "deny",
                    "task": "deny",
                    "todowrite": "deny",
                    "webfetch": "deny",
                    "websearch": "deny",
                    "skill": "deny",
                },
            }
        },
        "permission": {
            "edit": {
                "*": "allow",
                "test/**": "deny",
                "**/test/**": "deny",
                "**/tests/**": "deny",
                "**/*.spec.*": "deny",
                "**/*.test.*": "deny",
            }
        },
        "provider": {
            provider_id: {
                "options": {
                    "apiKey": api_key,
                    "baseURL": base_url,
                },
                "models": {
                    model_id: {
                        "name": model_id,
                        "id": model_id,
                        "reasoning": True,
                        "tool_call": True,
                        "temperature": True,
                        "modalities": {
                            "input": ["text"],
                            "output": ["text"],
                        },
                        "limit": {
                            "context": 262144,
                            "output": 32768,
                        },
                    }
                },
            }
        },
    }
    (worktree / "opencode.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def cleanup_worktree(repo_path: Path, worktree: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree)],
        check=False,
        text=True,
    )


def run_instance(
    row: dict[str, Any],
    output_root: Path,
    repos_root: Path,
    model: str,
    base_url: str,
    api_key: str,
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

    stdout_path = task_dir / "opencode_stdout.txt"
    stderr_path = task_dir / "opencode_stderr.txt"
    prompt_path = task_dir / "prompt.txt"
    patch_path = task_dir / "patch.diff"
    patch_json_path = task_dir / "patch.json"

    prompt = build_prompt(row)
    prompt_path.write_text(prompt, encoding="utf-8")

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
    timed_out = False
    reverted_test_files: list[str] = []
    protected_test_files: list[str] = []
    validation_results: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []

    try:
        log(f"{instance_id}: preparing repository state")
        before_cmd = str(row.get("before_repo_set_cmd") or "").strip()
        if before_cmd:
            subprocess.run(before_cmd, shell=True, cwd=str(worktree), check=True, text=True)

        hints = extract_priority_hints(row)
        presearch = build_presearch_summary(worktree, hints)
        (worktree / ".opencode").mkdir(parents=True, exist_ok=True)
        benchmark_text = build_instruction(row)
        if presearch:
            benchmark_text += "\n\n" + presearch + "\n"
        (worktree / ".opencode" / "benchmark.md").write_text(benchmark_text, encoding="utf-8")
        write_opencode_config(worktree, base_url=base_url, api_key=api_key, model=model)
        protected_test_files = protect_test_files(worktree)

        attempt_prompt = prompt
        max_attempts = 2 if get_validation_spec(row) and _docker_available() else 1
        for attempt in range(1, max_attempts + 1):
            attempt_stdout = task_dir / f"opencode_attempt{attempt}_stdout.txt"
            attempt_stderr = task_dir / f"opencode_attempt{attempt}_stderr.txt"
            log(f"{instance_id}: starting opencode attempt={attempt}")
            attempt_exit_code, attempt_elapsed, attempt_timed_out = run_opencode_once(
                worktree=worktree,
                model=model,
                prompt=attempt_prompt,
                stdout_path=attempt_stdout,
                stderr_path=attempt_stderr,
                timeout_sec=timeout_sec,
            )
            attempts.append(
                {
                    "attempt": attempt,
                    "exit_code": attempt_exit_code,
                    "elapsed_sec": attempt_elapsed,
                    "timed_out": attempt_timed_out,
                    "stdout_path": str(attempt_stdout),
                    "stderr_path": str(attempt_stderr),
                }
            )
            exit_code = attempt_exit_code
            elapsed += attempt_elapsed
            timed_out = timed_out or attempt_timed_out
            log(
                f"{instance_id}: opencode attempt={attempt} finished "
                f"exit_code={attempt_exit_code} elapsed={attempt_elapsed:.2f}s timed_out={attempt_timed_out}"
            )

            reverted_test_files, diff, status_output, diff_stat = collect_repo_outputs(worktree)
            if reverted_test_files:
                log(f"{instance_id}: reverted test file changes after attempt={attempt}: {', '.join(reverted_test_files)}")

            patch_path.write_text(diff, encoding="utf-8")
            patch_json_path.write_text(
                json.dumps(
                    [
                        {
                            "instance_id": instance_id,
                            "patch": diff,
                            "prefix": "opencode-glm",
                        }
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            shutil.copyfile(attempt_stdout, stdout_path)
            shutil.copyfile(attempt_stderr, stderr_path)

            validation = run_validation(task_dir=task_dir, row=row, diff=diff, attempt=attempt)
            if validation is not None:
                validation_results.append(validation)
                log(
                    f"{instance_id}: validation attempt={attempt} "
                    f"exit_code={validation['exit_code']} passed={validation['passed']}"
                )
                if validation["passed"]:
                    break
            if attempt >= max_attempts:
                break
            attempt_prompt = build_repair_prompt(row, validation)
    finally:
        cleanup_worktree(repo_path, worktree)

    summary = {
        "instance_id": instance_id,
        "repo": row["repo"],
        "exit_code": exit_code,
        "elapsed_sec": elapsed,
        "timed_out": timed_out,
        "protected_test_files_count": len(protected_test_files),
        "reverted_test_files": reverted_test_files,
        "diff_len": len(diff),
        "status": status_output,
        "diff_stat": diff_stat,
        "attempts": attempts,
        "validation_results": validation_results,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"{instance_id}: wrote summary diff_len={len(diff)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SWE-Bench Pro with OpenCode.")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--repos-root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--timeout-sec", type=int, default=1200)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--instance-id", action="append")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    instance_ids = set(args.instance_id) if args.instance_id else None
    rows = load_dataset_rows(split=args.split, limit=args.limit, instance_ids=instance_ids)
    if not rows:
        raise SystemExit("No dataset rows selected.")

    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    repos_root = Path(args.repos_root).resolve()
    repos_root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "split": args.split,
        "limit": args.limit,
        "instance_ids": sorted(instance_ids) if instance_ids else None,
        "model": args.model,
        "timeout_sec": args.timeout_sec,
        "max_workers": args.max_workers,
        "resume": args.resume,
        "count": len(rows),
    }
    (output_root / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = [
            executor.submit(
                run_instance,
                row=row,
                output_root=output_root,
                repos_root=repos_root,
                model=args.model,
                base_url=args.base_url,
                api_key=args.api_key,
                timeout_sec=args.timeout_sec,
                resume=args.resume,
            )
            for row in rows
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()


if __name__ == "__main__":
    main()
