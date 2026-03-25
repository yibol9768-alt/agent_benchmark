from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from benchmark_suite.io_utils import load_jsonl


def summarize_results(rows: list[dict]) -> dict:
    by_family: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_family[row["benchmark_family"]].append(row)

    summary: dict[str, dict] = {}
    for family, items in by_family.items():
        total = len(items)
        resolved = sum(1 for item in items if item["resolved"])
        summary[family] = {
            "tasks": total,
            "resolved": resolved,
            "resolve_rate": round(resolved / total, 4) if total else 0.0,
            "avg_score_raw": round(sum(item["score_raw"] for item in items) / total, 4) if total else 0.0,
            "avg_steps": round(sum(item["steps"] for item in items) / total, 2) if total else 0.0,
            "avg_wall_time_sec": round(
                sum(item["wall_time_sec"] for item in items) / total, 3
            ) if total else 0.0,
            "avg_tokens_in": round(sum(item["tokens_in"] for item in items) / total, 2) if total else 0.0,
            "avg_tokens_out": round(sum(item["tokens_out"] for item in items) / total, 2) if total else 0.0,
            "total_cost_usd": round(sum(item["cost_usd"] for item in items), 6),
        }

    total = len(rows)
    resolved = sum(1 for item in rows if item["resolved"])
    summary["overall"] = {
        "tasks": total,
        "resolved": resolved,
        "resolve_rate": round(resolved / total, 4) if total else 0.0,
        "total_cost_usd": round(sum(item["cost_usd"] for item in rows), 6),
    }
    return summary


def render_table(summary: dict) -> str:
    lines = [
        "family      tasks  resolved  resolve_rate  avg_score  avg_steps  avg_time  total_cost",
        "----------  -----  --------  ------------  ---------  ---------  --------  ----------",
    ]
    for family, metrics in summary.items():
        lines.append(
            f"{family:<10}  "
            f"{metrics['tasks']:<5}  "
            f"{metrics['resolved']:<8}  "
            f"{metrics['resolve_rate']:<12}  "
            f"{metrics.get('avg_score_raw', '-'): <9}  "
            f"{metrics.get('avg_steps', '-'): <9}  "
            f"{metrics.get('avg_wall_time_sec', '-'): <8}  "
            f"{metrics['total_cost_usd']}"
        )
    return "\n".join(lines)


def load_and_summarize(path: str) -> dict:
    return summarize_results(load_jsonl(path))


def compare_result_files(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        result_rows = load_jsonl(path)
        summary = summarize_results(result_rows)
        overall = summary["overall"]
        sample = result_rows[0] if result_rows else {}
        rows.append(
            {
                "run_name": Path(path).stem,
                "agent_name": sample.get("agent_name", "unknown"),
                "model_name": sample.get("model_name", "unknown"),
                "tasks": overall["tasks"],
                "resolved": overall["resolved"],
                "resolve_rate": overall["resolve_rate"],
                "total_cost_usd": overall["total_cost_usd"],
            }
        )
    rows.sort(key=lambda item: (-item["resolve_rate"], item["total_cost_usd"], item["agent_name"]))
    return rows


def render_compare_table(rows: list[dict]) -> str:
    lines = [
        "run_name          agent         model             tasks  resolved  resolve_rate  total_cost",
        "----------------  ------------  ----------------  -----  --------  ------------  ----------",
    ]
    for row in rows:
        lines.append(
            f"{row['run_name']:<16}  "
            f"{row['agent_name']:<12}  "
            f"{row['model_name']:<16}  "
            f"{row['tasks']:<5}  "
            f"{row['resolved']:<8}  "
            f"{row['resolve_rate']:<12}  "
            f"{row['total_cost_usd']}"
        )
    return "\n".join(lines)
