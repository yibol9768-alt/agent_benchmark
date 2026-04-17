"""Bug injector agent — parallel-sample strategy, provider-agnostic.

Given a RepoManifest (already envbuilt) and a target (F, S), this module:
  1. Draws N independent break plans in parallel, each with an agentic
     exploration loop over repo tools (read_file / list_dir / grep / list_tests).
  2. For each plan, asks the executor role to emit a unified diff.
  3. Returns the (plan, diff) pairs; the validator downstream picks the
     first one that passes all 8 gates.

The LLM is accessed through `bug_exam.llm.LLMClient`, so the agent works
identically against Claude, GLM, or any future provider.
"""
from __future__ import annotations

import concurrent.futures
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..llm import LLMClient, ToolDef, make_client
from ..schema import BreakPlan
from .break_plan import load_break_plan
from .tools import RepoTools

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
PLANNER_PROMPT = (PROMPTS_DIR / "planner.md").read_text()
EXECUTOR_PROMPT = (PROMPTS_DIR / "executor.md").read_text()


@dataclass
class InjectorDraw:
    plan: BreakPlan | None
    diff: str
    planner_error: str | None = None
    executor_error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


# Tool schemas (OpenAI function-calling format; anthropic client translates) -

PLANNER_TOOLS: list[ToolDef] = [
    ToolDef(
        name="read_file",
        description="Read a file from the repo. Returns line-numbered text.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "relative path from repo root"},
                "start": {"type": "integer", "default": 1},
                "end": {"type": "integer"},
            },
            "required": ["path"],
        },
    ),
    ToolDef(
        name="list_dir",
        description="List the entries of a directory in the repo.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
            "required": [],
        },
    ),
    ToolDef(
        name="grep",
        description="Grep the repo for a regex pattern. Returns file:line:excerpt lines.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string", "default": "**/*.py"},
            },
            "required": ["pattern"],
        },
    ),
    ToolDef(
        name="list_tests",
        description="List pytest test file paths in the repo.",
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
    ToolDef(
        name="emit_break_plan",
        description=(
            "Finalize and emit the break plan. Calling this ends the session. "
            "Every step's `file` + `line` must correspond to real bytes in the repo."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_F": {"type": "integer"},
                "target_S": {"type": "integer"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string"},
                            "file": {"type": "string"},
                            "line": {"type": "integer"},
                            "anchor_snippet": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["op", "file", "line", "anchor_snippet", "rationale"],
                    },
                },
                "summary": {"type": "string"},
            },
            "required": ["target_F", "target_S", "steps", "summary"],
        },
    ),
]


TERMINAL_TOOLS = {"emit_break_plan"}


# Planner session ------------------------------------------------------------

def _run_planner(
    client: LLMClient,
    repo_tools: RepoTools,
    target_F: int,
    target_S: int,
    *,
    max_turns: int = 20,
    extra_user_hint: str = "",
) -> tuple[BreakPlan | None, str | None, int, int]:
    """One agentic planner session. Returns (plan, error, in_toks, out_toks)."""
    handlers = {
        "read_file": lambda args: repo_tools.read_file(
            args["path"], args.get("start", 1), args.get("end"),
        ),
        "list_dir": lambda args: repo_tools.list_dir(args.get("path", ".")),
        "grep": lambda args: repo_tools.grep(args["pattern"], args.get("glob", "**/*.py")),
        "list_tests": lambda args: repo_tools.list_tests(),
    }

    user_init = (
        f"Your target is F={target_F} (distinct files touched) and "
        f"S={target_S} (break steps). The repo is already checked out at "
        f"its HEAD commit. Use the tools to explore, pick break sites, and "
        f"call `emit_break_plan` when ready. Be efficient — read at most a "
        f"handful of files before committing to a plan. You MUST call "
        f"`emit_break_plan` within {max_turns} turns; otherwise this draw "
        f"is wasted."
    )
    if extra_user_hint:
        user_init = user_init + "\n\n" + extra_user_hint

    result = client.run_agent_loop(
        system=PLANNER_PROMPT,
        user=user_init,
        tools=PLANNER_TOOLS,
        tool_handlers=handlers,
        terminal_tools=TERMINAL_TOOLS,
        max_turns=max_turns,
        max_tokens=8000,
    )

    if result.error:
        return None, result.error, result.input_tokens, result.output_tokens
    if result.terminal_tool_name != "emit_break_plan" or result.terminal_tool_args is None:
        return None, "planner did not emit a break plan", result.input_tokens, result.output_tokens
    try:
        plan = load_break_plan(result.terminal_tool_args)
    except Exception as e:
        return None, f"break plan parse error: {e}", result.input_tokens, result.output_tokens
    return plan, None, result.input_tokens, result.output_tokens


# Executor: plan -> unified diff --------------------------------------------

_DIFF_FENCE_RE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL)


def _extract_diff(text: str) -> str:
    m = _DIFF_FENCE_RE.search(text)
    if m:
        body = m.group(1).strip()
        return body + "\n" if not body.endswith("\n") else body
    stripped = text.strip()
    if stripped.startswith("diff --git"):
        return stripped + ("\n" if not stripped.endswith("\n") else "")
    return stripped


def _run_executor(client: LLMClient, repo_tools: RepoTools, plan: BreakPlan) -> tuple[str, str | None, int, int]:
    context_parts: list[str] = []
    seen_files: set[str] = set()
    for step in plan.steps:
        if step.file in seen_files:
            continue
        seen_files.add(step.file)
        content = repo_tools.read_file(step.file, 1, 9999)
        context_parts.append(f"=== {step.file} ===\n{content}\n")
    context = "\n".join(context_parts)[:60000]

    plan_json = plan.model_dump_json()
    user = (
        f"Break plan:\n{plan_json}\n\n"
        f"Relevant files (line-numbered):\n\n{context}\n\n"
        f"Emit the unified diff now. Nothing else."
    )
    try:
        text = client.complete_text(system=EXECUTOR_PROMPT, user=user, max_tokens=8000)
    except Exception as e:
        return "", f"executor API error: {e}", 0, 0
    return _extract_diff(text), None, 0, 0


# Parallel draws -------------------------------------------------------------

def draw_injections(
    repo_dir: Path,
    target_F: int,
    target_S: int,
    *,
    n_draws: int = 4,
    client: LLMClient | None = None,
    provider: str | None = None,
    model: str | None = None,
    image_tag: str | None = None,
    max_turns: int = 20,
    extra_user_hint: str = "",
) -> list[InjectorDraw]:
    """Produce n_draws independent (plan, diff) candidates in parallel.

    Either pass an explicit `client` or let the factory pick one from
    provider/model/env.
    """
    if client is None:
        client = make_client(provider=provider, model=model)
    tools = RepoTools(repo_dir=repo_dir, image_tag=image_tag)

    def one_draw(_i: int) -> InjectorDraw:
        plan, err, in_t, out_t = _run_planner(
            client, tools, target_F, target_S,
            max_turns=max_turns, extra_user_hint=extra_user_hint,
        )
        if plan is None:
            return InjectorDraw(plan=None, diff="", planner_error=err,
                                input_tokens=in_t, output_tokens=out_t)
        diff, exec_err, _, _ = _run_executor(client, tools, plan)
        return InjectorDraw(
            plan=plan, diff=diff, executor_error=exec_err,
            input_tokens=in_t, output_tokens=out_t,
        )

    if n_draws == 1:
        return [one_draw(0)]

    # Cap concurrency to avoid overwhelming the LLM API with parallel streams
    max_workers = min(n_draws, 3)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(one_draw, range(n_draws)))
