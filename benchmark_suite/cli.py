from __future__ import annotations

import argparse
import json
import sys

from benchmark_suite.factory import build_agent
from benchmark_suite.io_utils import dump_jsonl, load_jsonl
from benchmark_suite.official_benchmarks import (
    OFFICIAL_BENCHMARKS,
    clone_official_repo,
    export_swebench_pro_dataset,
    export_swebench_pro_gold_patches,
    export_webarena_verified_dataset,
    official_runbook,
)
from benchmark_suite.reporting import (
    compare_result_files,
    load_and_summarize,
    render_compare_table,
    render_table,
)
from benchmark_suite.runner import run_tasks
from benchmark_suite.tasks import load_tasks, validate_tasks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run tasks with an agent adapter")
    run_parser.add_argument("--tasks", required=True, help="Path to task JSONL")
    run_parser.add_argument(
        "--agent",
        required=True,
        choices=["mock", "bare-llm", "openclaw-cmd", "codex-cmd"],
    )
    run_parser.add_argument("--agent-command", help="Command for command-backed agents")
    run_parser.add_argument("--output", required=True, help="Output JSONL path")

    report_parser = subparsers.add_parser("report", help="Summarize result JSONL")
    report_parser.add_argument("--input", required=True, help="Result JSONL path")
    report_parser.add_argument("--format", choices=["table", "json"], default="table")

    compare_parser = subparsers.add_parser("compare", help="Compare multiple result JSONL files")
    compare_parser.add_argument("--inputs", nargs="+", required=True, help="Result JSONL paths")
    compare_parser.add_argument("--format", choices=["table", "json"], default="table")

    validate_parser = subparsers.add_parser("validate", help="Validate task JSONL")
    validate_parser.add_argument("--tasks", required=True, help="Task JSONL path")

    cat_parser = subparsers.add_parser("cat", help="Pretty print JSONL rows")
    cat_parser.add_argument("--input", required=True, help="JSONL path")

    clone_parser = subparsers.add_parser(
        "clone-official",
        help="Clone an official benchmark repository",
    )
    clone_parser.add_argument(
        "--benchmark",
        required=True,
        choices=sorted(OFFICIAL_BENCHMARKS),
        help="Official benchmark slug",
    )
    clone_parser.add_argument("--dest", required=True, help="Destination directory")

    runbook_parser = subparsers.add_parser(
        "official-runbook",
        help="Print the official workflow for a supported benchmark",
    )
    runbook_parser.add_argument(
        "--benchmark",
        required=True,
        choices=sorted(OFFICIAL_BENCHMARKS),
        help="Official benchmark slug",
    )

    swe_export = subparsers.add_parser(
        "export-swebench-pro",
        help="Export the real SWE-Bench Pro dataset from Hugging Face",
    )
    swe_export.add_argument("--output", required=True, help="Output JSONL path")
    swe_export.add_argument("--split", default="test", help="Dataset split")
    swe_export.add_argument("--limit", type=int, help="Optional row limit")

    swe_gold = subparsers.add_parser(
        "export-swebench-pro-gold",
        help="Export official SWE-Bench Pro gold patches for evaluator smoke tests",
    )
    swe_gold.add_argument("--output", required=True, help="Output JSON path")
    swe_gold.add_argument("--split", default="test", help="Dataset split")
    swe_gold.add_argument("--limit", type=int, help="Optional row limit")

    web_export = subparsers.add_parser(
        "export-webarena-verified",
        help="Export the real WebArena-Verified dataset from Hugging Face",
    )
    web_export.add_argument("--output", required=True, help="Output JSONL path")
    web_export.add_argument("--split", default="full", help="Dataset split: full or hard")
    web_export.add_argument("--limit", type=int, help="Optional row limit")
    return parser


def handle_run(args: argparse.Namespace) -> int:
    tasks = load_tasks(args.tasks)
    agent = build_agent(args.agent, agent_command=args.agent_command)
    results = run_tasks(tasks, agent)
    dump_jsonl(args.output, [item.to_dict() for item in results])
    return 0


def handle_report(args: argparse.Namespace) -> int:
    summary = load_and_summarize(args.input)
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render_table(summary))
    return 0


def handle_validate(args: argparse.Namespace) -> int:
    errors = validate_tasks(args.tasks)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    print("task file valid")
    return 0


def handle_compare(args: argparse.Namespace) -> int:
    rows = compare_result_files(args.inputs)
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(render_compare_table(rows))
    return 0


def handle_cat(args: argparse.Namespace) -> int:
    rows = load_jsonl(args.input)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def handle_clone_official(args: argparse.Namespace) -> int:
    path = clone_official_repo(args.benchmark, args.dest)
    print(path)
    return 0


def handle_official_runbook(args: argparse.Namespace) -> int:
    print(official_runbook(args.benchmark))
    return 0


def handle_export_swebench_pro(args: argparse.Namespace) -> int:
    path = export_swebench_pro_dataset(args.output, split=args.split, limit=args.limit)
    print(path)
    return 0


def handle_export_swebench_pro_gold(args: argparse.Namespace) -> int:
    path = export_swebench_pro_gold_patches(args.output, split=args.split, limit=args.limit)
    print(path)
    return 0


def handle_export_webarena_verified(args: argparse.Namespace) -> int:
    path = export_webarena_verified_dataset(args.output, split=args.split, limit=args.limit)
    print(path)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "run":
        return handle_run(args)
    if args.command == "report":
        return handle_report(args)
    if args.command == "validate":
        return handle_validate(args)
    if args.command == "compare":
        return handle_compare(args)
    if args.command == "cat":
        return handle_cat(args)
    if args.command == "clone-official":
        return handle_clone_official(args)
    if args.command == "official-runbook":
        return handle_official_runbook(args)
    if args.command == "export-swebench-pro":
        return handle_export_swebench_pro(args)
    if args.command == "export-swebench-pro-gold":
        return handle_export_swebench_pro_gold(args)
    if args.command == "export-webarena-verified":
        return handle_export_webarena_verified(args)
    parser.error(f"unsupported command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
