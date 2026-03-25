# Agent Benchmark Suite

Agent Benchmark Suite is a pragmatic workspace for comparing agent runtimes and connecting them to real benchmarks.

The repository now treats these as first-class official integrations:

- `SWE-Bench Pro`: official evaluator and dataset from ScaleAI
- `WebArena-Verified`: official verified web benchmark from ServiceNow
- `Toolathlon`: official multi-tool benchmark from HKUST NLP

The generic JSONL runner is still included for local smoke tests and adapter debugging, but the primary direction is real benchmark integration instead of benchmark-style mock tasks.

## What This Repository Does

- Provides a unified adapter layer for `bare-llm`, `openclaw-cmd`, and `codex-cmd`
- Exports real benchmark datasets from official sources
- Clones official benchmark repositories into local workspaces
- Prints benchmark-specific runbooks so the official evaluator is always the source of truth
- Keeps lightweight reporting utilities for generic JSONL runs

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

If you want to use Hugging Face dataset exports:

```bash
pip install datasets
```

If you want to run the official SWE-Bench Pro evaluator locally:

```bash
brew install docker colima python@3.11
colima start
python3.11 -m venv .venv311
source .venv311/bin/activate
pip install swebench datasets docker
```

## Generic Adapters

### `bare-llm`

Calls an OpenAI-compatible Chat Completions endpoint directly. This is useful as a baseline, not as the main agent track.

Required environment variables:

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

### `openclaw-cmd`

Runs an external OpenClaw-compatible command. The command must read a task JSON payload from `stdin` and return a result JSON object on `stdout`.

### `codex-cmd`

Runs the local `codex exec` CLI in non-interactive mode.

## Generic CLI

Run a generic JSONL task set:

```bash
agent-benchmark run \
  --tasks fixtures/sample_tasks.jsonl \
  --agent codex-cmd \
  --output runs/codex_results.jsonl
```

Summarize one run:

```bash
agent-benchmark report --input runs/codex_results.jsonl
```

Compare multiple runs:

```bash
agent-benchmark compare \
  --inputs runs/minimax_bare_llm_results.jsonl runs/codex_results.jsonl
```

## Real Benchmarks

### SWE-Bench Pro

Clone the official evaluator:

```bash
agent-benchmark clone-official \
  --benchmark swebench-pro \
  --dest ../benchmarks/SWE-bench_Pro-os
```

Export the real dataset:

```bash
agent-benchmark export-swebench-pro \
  --output data/swebench_pro_test.jsonl \
  --limit 10
```

Export gold patches for evaluator smoke tests:

```bash
agent-benchmark export-swebench-pro-gold \
  --output data/swebench_pro_gold.json \
  --limit 10
```

Print the official workflow:

```bash
agent-benchmark official-runbook --benchmark swebench-pro
```

Official sources:

- Repo: `https://github.com/scaleapi/SWE-bench_Pro-os`
- Dataset: `ScaleAI/SWE-bench_Pro`

### WebArena-Verified

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

- Repo: `https://github.com/ServiceNow/webarena-verified`
- Package: `webarena-verified`
- BrowserGym package: `browsergym-webarena-verified`
- Dataset: `AmineHA/WebArena-Verified`

### Toolathlon

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

- Repo: `https://github.com/hkust-nlp/Toolathlon`

Toolathlon does not currently expose a lightweight dataset-only export flow like SWE-Bench Pro or WebArena-Verified. The intended workflow is to clone the repo, configure credentials, deploy the required application containers, and run the provided task scripts.

## Notes

- The generic JSONL evaluator in this repository is intentionally simple and should not be confused with official benchmark scoring.
- Official benchmark scoring should always come from the upstream benchmark repository or package.
- `fixtures/sample_tasks.jsonl` remains in the repo only as a local adapter smoke test.
