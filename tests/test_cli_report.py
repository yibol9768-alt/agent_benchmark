from benchmark_suite.io_utils import dump_jsonl
from benchmark_suite.reporting import summarize_results


def test_report_summary() -> None:
    rows = [
        {
            "task_id": "swe-001",
            "benchmark_family": "swe",
            "agent_name": "mock",
            "model_name": "mock-model",
            "resolved": True,
            "score_raw": 1.0,
            "steps": 2,
            "wall_time_sec": 0.1,
            "tokens_in": 20,
            "tokens_out": 10,
            "cost_usd": 0.01,
            "tool_calls": 2,
            "failure_type": "none",
            "final_output": "ok",
            "trace": [],
            "metadata": {},
        },
        {
            "task_id": "web-001",
            "benchmark_family": "web",
            "agent_name": "mock",
            "model_name": "mock-model",
            "resolved": False,
            "score_raw": 0.5,
            "steps": 3,
            "wall_time_sec": 0.2,
            "tokens_in": 30,
            "tokens_out": 15,
            "cost_usd": 0.02,
            "tool_calls": 1,
            "failure_type": "validation_error",
            "final_output": "partial",
            "trace": [],
            "metadata": {},
        },
    ]
    dump_jsonl("tests/tmp_results.jsonl", rows)
    summary = summarize_results(rows)
    assert summary["swe"]["resolve_rate"] == 1.0
    assert summary["web"]["resolve_rate"] == 0.0
    assert summary["overall"]["tasks"] == 2
