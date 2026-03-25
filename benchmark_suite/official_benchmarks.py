from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OfficialBenchmark:
    slug: str
    repo_url: str
    notes: str


OFFICIAL_BENCHMARKS: dict[str, OfficialBenchmark] = {
    "swebench-pro": OfficialBenchmark(
        slug="swebench-pro",
        repo_url="https://github.com/scaleapi/SWE-bench_Pro-os.git",
        notes="Official local/docker evaluator for ScaleAI/SWE-bench_Pro.",
    ),
    "webarena-verified": OfficialBenchmark(
        slug="webarena-verified",
        repo_url="https://github.com/ServiceNow/webarena-verified.git",
        notes="Official verified WebArena benchmark with CLI and Docker support.",
    ),
    "toolathlon": OfficialBenchmark(
        slug="toolathlon",
        repo_url="https://github.com/hkust-nlp/Toolathlon.git",
        notes="Official Toolathlon benchmark and deployment scripts.",
    ),
}


def _require_datasets() -> Any:
    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'datasets' package is required for this command. Install it with: pip install datasets"
        ) from exc
    return load_dataset


def clone_official_repo(benchmark: str, destination: str | Path) -> Path:
    spec = OFFICIAL_BENCHMARKS[benchmark]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return destination
    subprocess.run(
        ["git", "clone", spec.repo_url, str(destination)],
        check=True,
        text=True,
        capture_output=True,
    )
    return destination


def export_swebench_pro_dataset(
    output_path: str | Path,
    split: str = "test",
    limit: int | None = None,
) -> Path:
    load_dataset = _require_datasets()
    dataset = load_dataset("ScaleAI/SWE-bench_Pro", split=split)
    if limit is not None:
        dataset = dataset.select(range(min(limit, len(dataset))))

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in dataset:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    return output


def export_swebench_pro_gold_patches(
    output_path: str | Path,
    split: str = "test",
    limit: int | None = None,
) -> Path:
    load_dataset = _require_datasets()
    dataset = load_dataset("ScaleAI/SWE-bench_Pro", split=split)
    if limit is not None:
        dataset = dataset.select(range(min(limit, len(dataset))))

    payload = [
        {
            "instance_id": row["instance_id"],
            "patch": row["patch"],
            "prefix": "gold",
        }
        for row in dataset
    ]
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def export_webarena_verified_dataset(
    output_path: str | Path,
    split: str = "full",
    limit: int | None = None,
) -> Path:
    load_dataset = _require_datasets()
    dataset = load_dataset("AmineHA/WebArena-Verified", split=split)
    if limit is not None:
        dataset = dataset.select(range(min(limit, len(dataset))))

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in dataset:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    return output


def official_runbook(benchmark: str) -> str:
    if benchmark == "swebench-pro":
        return "\n".join(
            [
                "Official source: https://github.com/scaleapi/SWE-bench_Pro-os",
                "Dataset: ScaleAI/SWE-bench_Pro",
                "Typical flow:",
                "1. Clone the official repo.",
                "2. Generate patch predictions with your agent.",
                "3. Gather patches into a JSON file.",
                "4. Run swe_bench_pro_eval.py with --use_local_docker.",
            ]
        )
    if benchmark == "webarena-verified":
        return "\n".join(
            [
                "Official source: https://github.com/ServiceNow/webarena-verified",
                "Package: webarena-verified / browsergym-webarena-verified",
                "Typical flow:",
                "1. Install the official package.",
                "2. Export the dataset or hard subset.",
                "3. Start the required site containers.",
                "4. Run webarena-verified eval-tasks with your agent config.",
            ]
        )
    if benchmark == "toolathlon":
        return "\n".join(
            [
                "Official source: https://github.com/hkust-nlp/Toolathlon",
                "Typical flow:",
                "1. Clone the official repo.",
                "2. Configure TOOLATHLON_OPENAI_BASE_URL and TOOLATHLON_OPENAI_API_KEY.",
                "3. Deploy app containers with global_preparation/deploy_containers.sh.",
                "4. Run scripts/run_single_containerized.sh or scripts/run_parallel.sh.",
            ]
        )
    raise ValueError(f"Unsupported benchmark: {benchmark}")
