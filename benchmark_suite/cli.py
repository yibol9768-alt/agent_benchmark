from __future__ import annotations

import argparse
import json
import sys

from benchmark_suite.factory import build_agent
from benchmark_suite.io_utils import dump_jsonl, load_jsonl
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
    run_parser.add_argument("--agent", required=True, choices=["mock", "bare-llm", "openclaw-cmd"])
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
    parser.error(f"unsupported command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
