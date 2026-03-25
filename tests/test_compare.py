from benchmark_suite.reporting import compare_result_files


def test_compare_result_files_orders_by_resolve_rate() -> None:
    rows = compare_result_files(["runs/mock_results.jsonl", "runs/openclaw_results.jsonl"])
    assert len(rows) == 2
    assert rows[0]["agent_name"] == "mock"
    assert rows[1]["agent_name"] == "openclaw-cmd"
