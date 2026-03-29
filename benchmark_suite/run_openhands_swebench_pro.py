from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from datasets import load_dataset
from openhands.sdk import Agent, Conversation, LLM
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.tools.preset.default import get_default_tools
from openhands.workspace import DockerDevWorkspace, DockerWorkspace

from vendor.openhands_benchmarks_compat import (
    fake_user_response,
    run_conversation_with_fake_user_response,
)


GIT_USER_EMAIL = "agent-benchmark@example.com"
GIT_USER_NAME = "agent-benchmark"
GIT_COMMIT_MESSAGE = "openhands patch"


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def load_pro_rows(split: str, limit: int | None, instance_ids: set[str] | None) -> list[dict[str, Any]]:
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


def create_problem_statement(row: dict[str, Any]) -> str:
    return f"""{row['problem_statement']}

Requirements:
{row['requirements']}

New interfaces introduced:
{row['interface']}"""


def get_docker_image(row: dict[str, Any], dockerhub_username: str) -> str:
    docker_tag = row.get("dockerhub_tag")
    if docker_tag:
        return f"{dockerhub_username}/sweap-images:{docker_tag}"

    repo_base, repo_name_only = row["repo"].lower().split("/")
    instance_id = row["instance_id"]
    hsh = instance_id.replace("instance_", "")
    if instance_id == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan":
        repo_name_only = "element-web"
    elif "element-hq" in row["repo"].lower() and "element-web" in row["repo"].lower():
        repo_name_only = "element"
        if hsh.endswith("-vnan"):
            hsh = hsh[:-5]
    elif hsh.endswith("-vnan"):
        hsh = hsh[:-5]
    tag = f"{repo_base}.{repo_name_only}-{hsh}"
    if len(tag) > 128:
        tag = tag[:128]
    return f"{dockerhub_username}/sweap-images:{tag}"


def get_instruction(row: dict[str, Any]) -> str:
    issue = create_problem_statement(row)
    return f"""I have access to a software repository inside /app in the container. You can explore and modify files using the available tools.

Consider the following issue description:

<issue_description>
{issue}
</issue_description>

You must solve this issue by making minimal changes to non-test files under /app.
Do not modify any tests.
Use the repository and the issue description to determine what to change.
You should inspect the codebase, locate the relevant files, implement the fix, and verify your work with relevant commands.

Before finishing:
1. Re-read the issue and requirements.
2. Ensure you only changed non-test files.
3. Run focused validation commands relevant to the files you changed.
4. If validation fails, keep working until it passes.

When you believe the task is solved, use the finish tool."""


def to_jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump())
    return str(value)


def dump_history(path: Path, events: list[Any]) -> None:
    serializable = [to_jsonable(event) for event in events]
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2))


def extract_patch(workspace: DockerDevWorkspace, base_commit: str) -> str:
    workspace.execute_command("cd /app && git add -A")
    workspace.execute_command(
        "cd /app && "
        f"git config --global user.email '{GIT_USER_EMAIL}' && "
        f"git config --global user.name '{GIT_USER_NAME}' && "
        f"git commit --no-verify -m '{GIT_COMMIT_MESSAGE}'"
    )
    diff = workspace.execute_command(f"cd /app && git --no-pager diff --no-color {base_commit} HEAD")
    if diff.exit_code != 0:
        raise RuntimeError(f"git diff failed: {diff.stderr}")
    return diff.stdout


def run_instance(
    row: dict[str, Any],
    output_root: Path,
    llm: LLM,
    max_iterations: int,
    dockerhub_username: str,
    enable_condenser: bool,
) -> dict[str, Any]:
    instance_id = row["instance_id"]
    instance_dir = output_root / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)

    image = get_docker_image(row, dockerhub_username)
    summary_path = instance_dir / "summary.json"
    pred_path = instance_dir / f"{instance_id}.pred"
    history_path = instance_dir / "history.json"
    instruction_path = instance_dir / "instruction.txt"

    if summary_path.exists():
        return json.loads(summary_path.read_text())

    instruction = get_instruction(row)
    instruction_path.write_text(instruction)

    server_image_override = os.environ.get("OPENHANDS_SERVER_IMAGE", "").strip()
    if server_image_override:
        workspace = DockerWorkspace(
            server_image=server_image_override,
            working_dir="/app",
            platform="linux/amd64",
        )
    else:
        workspace = DockerDevWorkspace(
            base_image=image,
            working_dir="/app",
            platform="linux/amd64",
            target="source-minimal",
        )
    start = time.time()
    error: str | None = None
    patch = ""
    status = "ok"
    conversation = None
    try:
        if os.environ.get("OPENHANDS_DOCKER_USER", "").strip() != "root":
            ownership = workspace.execute_command("sudo chown -R openhands:openhands /app")
            if ownership.exit_code != 0:
                raise RuntimeError(f"workspace chown failed: {ownership.stderr}")
        safe_dir = workspace.execute_command("git config --global --add safe.directory /app")
        if safe_dir.exit_code != 0:
            raise RuntimeError(f"git safe.directory failed: {safe_dir.stderr}")
        reset = workspace.execute_command(f"cd /app && git reset --hard {row['base_commit']} && git checkout {row['base_commit']}")
        if reset.exit_code != 0:
            raise RuntimeError(f"git reset failed: {reset.stderr}")

        tools = get_default_tools(enable_browser=False)
        condenser = None
        if enable_condenser:
            condenser = LLMSummarizingCondenser(
                llm=llm.model_copy(update={"usage_id": "condenser"}),
                max_size=240,
                keep_first=2,
            )
        agent = Agent(
            llm=llm,
            tools=tools,
            system_prompt_kwargs={"cli_mode": True},
            condenser=condenser,
        )
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            max_iteration_per_run=max_iterations,
            delete_on_close=True,
        )
        conversation.send_message(instruction)
        run_conversation_with_fake_user_response(conversation, fake_user_response_fn=fake_user_response)
        patch = extract_patch(workspace, row["base_commit"])
        pred_path.write_text(patch)
        dump_history(history_path, list(conversation.state.events))
    except Exception as exc:
        status = "error"
        error = str(exc)
        if conversation is not None:
            try:
                dump_history(history_path, list(conversation.state.events))
            except Exception:
                pass
    finally:
        elapsed = time.time() - start

    summary = {
        "instance_id": instance_id,
        "repo": row["repo"],
        "image": image,
        "status": status,
        "error": error,
        "elapsed_sec": round(elapsed, 2),
        "patch_len": len(patch),
        "pred_path": str(pred_path) if patch else None,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def build_llm(model: str, base_url: str, api_key: str) -> LLM:
    model_name = model if "/" in model else f"openai/{model}"
    return LLM.model_validate(
        {
            "model": model_name,
            "base_url": base_url.rstrip("/") + "/",
            "api_key": api_key,
            "stream": False,
        }
    )


def write_patch_bundle(output_root: Path, summaries: list[dict[str, Any]], prefix: str) -> Path:
    bundle: list[dict[str, str]] = []
    for summary in summaries:
        pred_path = summary.get("pred_path")
        if not pred_path:
            continue
        patch = Path(pred_path).read_text()
        bundle.append(
            {
                "instance_id": summary["instance_id"],
                "patch": patch,
                "prefix": prefix,
            }
        )
    out = output_root / f"{prefix}_patches.json"
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2))
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OpenHands agent on SWE-bench Pro instances.")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--max-iterations", type=int, default=100)
    parser.add_argument("--dockerhub-username", default="jefzda")
    parser.add_argument("--prefix", default="openhands_pro")
    parser.add_argument("--enable-condenser", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uv_bin_dir = str(Path.home() / ".local" / "bin")
    current_path = os.environ.get("PATH", "")
    if uv_bin_dir not in current_path.split(":"):
        os.environ["PATH"] = f"{uv_bin_dir}:{current_path}" if current_path else uv_bin_dir
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    llm = build_llm(args.model, args.base_url, args.api_key)
    instance_ids = set(args.instance_id) if args.instance_id else None
    limit = None if instance_ids else args.limit
    rows = load_pro_rows(args.split, limit, instance_ids)

    summaries: list[dict[str, Any]] = []
    for row in rows:
        log(f"running {row['instance_id']}")
        summary = run_instance(
            row=row,
            output_root=output_root,
            llm=llm,
            max_iterations=args.max_iterations,
            dockerhub_username=args.dockerhub_username,
            enable_condenser=args.enable_condenser,
        )
        summaries.append(summary)
        log(json.dumps(summary, ensure_ascii=False))

    patch_bundle = write_patch_bundle(output_root, summaries, args.prefix)
    manifest = {
        "output_root": str(output_root),
        "patch_bundle": str(patch_bundle),
        "count": len(summaries),
        "instances": [summary["instance_id"] for summary in summaries],
    }
    (output_root / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
