from benchmark_suite.tasks import validate_tasks


def test_validate_sample_tasks() -> None:
    errors = validate_tasks("fixtures/sample_tasks.jsonl")
    assert errors == []
