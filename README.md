# Agent Benchmark Suite

Agent Benchmark Suite is a local workspace for running real agent evaluations against official benchmark infrastructure.

This repository supports two different use cases:

- generic JSONL task runs for adapter debugging
- official benchmark workflows for `SWE-Bench Pro`, `WebArena-Verified`, and `Toolathlon`

The important distinction is:

- generic JSONL results in this repository are only local smoke tests
- official benchmark scores must come from the upstream benchmark evaluator

## Repository Scope

This repository provides:

- adapters for `bare-llm`, `codex-cmd`, and `openclaw-cmd`
- commands to clone official benchmark repositories
- dataset export helpers for official benchmark inputs
- a reproducible `GLM-5` runner for `SWE-Bench Pro` patch generation
- lightweight result reporting for local JSONL runs

This repository does not replace the official evaluators.

## Directory Layout

- [benchmark_suite](/Users/liuyibo/Desktop/d/test/benchmark_suite): core package
- [benchmark_suite/cli.py](/Users/liuyibo/Desktop/d/test/benchmark_suite/cli.py): command-line entrypoint
- [benchmark_suite/official_benchmarks.py](/Users/liuyibo/Desktop/d/test/benchmark_suite/official_benchmarks.py): official repo and dataset helpers
- [benchmark_suite/run_glm_swebench_official.py](/Users/liuyibo/Desktop/d/test/benchmark_suite/run_glm_swebench_official.py): fixed-prompt `GLM-5` runner for official `SWE-Bench Pro`
- [fixtures](/Users/liuyibo/Desktop/d/test/fixtures): local smoke-test fixtures
- [tests](/Users/liuyibo/Desktop/d/test/tests): unit tests for the local framework

## Install

### Base environment

Use this for local JSONL runs and CLI utilities:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

If you want dataset export commands:

```bash
pip install datasets
```

### Official SWE-Bench Pro local evaluation environment

The official local evaluator requires Docker access and a recent Python.

Example on macOS:

```bash
brew install docker colima python@3.11
colima start
python3.11 -m venv .venv311
source .venv311/bin/activate
pip install swebench datasets docker
```

If Docker runs through Colima, export the socket before official evaluation:

```bash
export DOCKER_HOST=unix:///Users/$USER/.colima/docker.sock
```

## Generic Adapters

### `bare-llm`

This is a direct OpenAI-compatible chat-completions baseline. It is useful as a weak baseline, not as a strong agent benchmark.

Required environment variables:

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

### `codex-cmd`

Runs the local `codex exec` CLI in non-interactive mode.

### `openclaw-cmd`

Runs an external OpenClaw-compatible command. The command must:

- read one task JSON object from `stdin`
- return one result JSON object on `stdout`

## Generic CLI

These commands are for local JSONL tasks only.

Run:

```bash
agent-benchmark run \
  --tasks fixtures/sample_tasks.jsonl \
  --agent codex-cmd \
  --output runs/codex_results.jsonl
```

Report:

```bash
agent-benchmark report --input runs/codex_results.jsonl
```

Compare:

```bash
agent-benchmark compare \
  --inputs runs/mock_results.jsonl runs/codex_results.jsonl
```

Validate a JSONL task file:

```bash
agent-benchmark validate --tasks fixtures/sample_tasks.jsonl
```

## Official Benchmarks

## SWE-Bench Pro

### 1. Clone the official evaluator

```bash
agent-benchmark clone-official \
  --benchmark swebench-pro \
  --dest ../benchmarks/SWE-bench_Pro-os
```

Official source:

- repo: `https://github.com/scaleapi/SWE-bench_Pro-os`
- dataset: `ScaleAI/SWE-bench_Pro`

### 2. Export the official dataset

Export a small subset:

```bash
agent-benchmark export-swebench-pro \
  --output data/swebench_pro_test_10.jsonl \
  --limit 10
```

Export official gold patches for evaluator smoke tests:

```bash
agent-benchmark export-swebench-pro-gold \
  --output data/swebench_pro_gold_10.json \
  --limit 10
```

### 3. Read the official runbook

```bash
agent-benchmark official-runbook --benchmark swebench-pro
```

### 4. Generate predictions

This repository currently includes a fixed-protocol runner for `GLM-5`.

Example:

```bash
python benchmark_suite/run_glm_swebench_official.py \
  --samples /path/to/swebench_samples.jsonl \
  --manifest /path/to/instance_manifest.json \
  --batches /path/to/batches.json \
  --output-root /path/to/output_dir \
  --base-url https://open.bigmodel.cn/api/coding/paas/v4 \
  --api-key "$GLM_API_KEY" \
  --model glm-5 \
  --timeout 180 \
  --max-retries 3 \
  --retry-backoff-sec 5 \
  --max-workers 1
```

Important protocol notes:

- `--manifest` fixes the instance list
- `--batches` fixes batch boundaries
- `--max-workers 1` means strictly one active instance request at a time
- retry is automatic only for timeout and transient network errors
- raw model output is preserved
- extracted patch is preserved
- no manual patch repair is performed

### 5. Run the official evaluator

From the cloned `SWE-bench_Pro-os` repository:

```bash
export DOCKER_HOST=unix:///Users/$USER/.colima/docker.sock

python swe_bench_pro_eval.py \
  --raw_sample_path /path/to/batch_01/samples.csv \
  --patch_path /path/to/batch_01/patches.json \
  --output_dir /path/to/batch_01/eval_output \
  --scripts_dir run_scripts \
  --num_workers 3 \
  --dockerhub_username jefzda \
  --use_local_docker
```

### 6. How official scoring works

For each instance, the official evaluator checks whether all required tests pass.

That means:

- if all required tests pass, the instance is `true`
- if even one required test fails, the instance is `false`

There is no partial credit for one instance.

### 7. Output files produced by the `GLM-5` runner

Each batch directory contains:

- `samples.jsonl`: fixed official dataset rows used for generation
- `samples.csv`: official evaluator input table for that batch
- `patches.json`: generated patch payload consumed by the official evaluator
- `<instance_id>.raw.txt`: raw model output
- `<instance_id>.diff`: extracted git diff
- `generation_summary.json`: generation metadata, attempts, and retry status

The output root also contains:

- `experiment_manifest.json`: experiment-level protocol metadata

### 8. Reproducibility checklist

If you want to cite results in a paper, record:

- benchmark name and official repo URL
- official repo commit
- dataset name and split
- instance manifest
- batch definition
- model name
- base URL
- timeout
- retry count
- worker count
- raw outputs
- extracted patches
- official evaluator output directory

## WebArena-Verified

Clone the official repository:

```bash
agent-benchmark clone-official \
  --benchmark webarena-verified \
  --dest ../benchmarks/webarena-verified
```

Export the real dataset:

```bash
agent-benchmark export-webarena-verified \
  --output data/webarena_verified_full.jsonl \
  --split full
```

Export the hard subset:

```bash
agent-benchmark export-webarena-verified \
  --output data/webarena_verified_hard.jsonl \
  --split hard
```

Print the official workflow:

```bash
agent-benchmark official-runbook --benchmark webarena-verified
```

Official sources:

- repo: `https://github.com/ServiceNow/webarena-verified`
- package: `webarena-verified`
- BrowserGym package: `browsergym-webarena-verified`
- dataset: `AmineHA/WebArena-Verified`

## Toolathlon

Clone the official repository:

```bash
agent-benchmark clone-official \
  --benchmark toolathlon \
  --dest ../benchmarks/Toolathlon
```

Print the official workflow:

```bash
agent-benchmark official-runbook --benchmark toolathlon
```

Official source:

- repo: `https://github.com/hkust-nlp/Toolathlon`

Toolathlon is environment-heavy. The normal path is:

- clone the official repo
- configure the model endpoint and key
- deploy the required application containers
- run the official scripts from the upstream repository

## Recommended Workflow

For serious evaluation:

1. Export a fixed official subset.
2. Freeze the instance list in a manifest file.
3. Freeze batching strategy.
4. Generate raw outputs without manual intervention.
5. Extract patches automatically.
6. Run the upstream official evaluator.
7. Archive raw outputs, extracted patches, and evaluator outputs.

For quick local debugging:

1. Use `fixtures/sample_tasks.jsonl`.
2. Run `agent-benchmark run`.
3. Use `agent-benchmark report` and `agent-benchmark compare`.

## Current Limitations

- this repository does not implement the official `WebArena-Verified` runtime
- this repository does not replace official `Toolathlon` deployment scripts
- `bare-llm` is intentionally weak and should not be interpreted as an agent scaffold
- the local JSONL evaluator is not an official benchmark scorer

## Notes

- keep secrets in environment variables, not in files
- do not commit local virtual environments
- do not report generic JSONL smoke-test numbers as official benchmark results
