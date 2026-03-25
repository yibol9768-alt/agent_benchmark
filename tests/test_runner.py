from benchmark_suite.factory import build_agent
from benchmark_suite.runner import run_tasks
from benchmark_suite.tasks import load_tasks


def test_mock_agent_resolves_sample_tasks() -> None:
    tasks = load_tasks("fixtures/sample_tasks.jsonl")
    agent = build_agent("mock")
    results = run_tasks(tasks, agent)
    assert len(results) == 3
    assert all(item.resolved for item in results)
