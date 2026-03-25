# Agent Benchmark Suite

一个面向 `agent/harness` 的混合 benchmark 框架，覆盖三类任务：

- `SWE-Bench Pro` 风格的代码仓库任务
- `WebArena-Verified` 风格的网页操作任务
- `Toolathlon` 风格的多工具任务

这个仓库默认比较的是 `agent runtime`，不是裸模型。底层模型通过 OpenAI-compatible API 统一接入；主入口是 agent adapter。

## Features

- 统一 task/result schema
- 可扩展 agent adapter 接口
- `bare-llm` baseline
- `openclaw-cmd` 命令行式 agent adapter
- `mock` agent 便于本地演示和测试
- JSONL 任务输入、JSONL 结果输出
- 汇总报表与分榜

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pytest
```

运行内置 mock agent：

```bash
agent-benchmark run \
  --tasks fixtures/sample_tasks.jsonl \
  --agent mock \
  --output runs/mock_results.jsonl
```

生成汇总：

```bash
agent-benchmark report \
  --input runs/mock_results.jsonl
```

对比多个 agent 结果：

```bash
agent-benchmark compare \
  --inputs runs/mock_results.jsonl runs/openclaw_results.jsonl
```

## Agent Adapters

### 1. bare-llm

直接调用 OpenAI-compatible Chat Completions API。适合做 baseline，不适合作为主榜执行入口。

必填环境变量：

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

### 2. openclaw-cmd

通过命令行程序调用 agent。该 adapter 假设 agent 能从 stdin 读取任务 JSON，并向 stdout 输出结果 JSON。

请求格式：

```json
{
  "task": {
    "task_id": "swe-001",
    "benchmark_family": "swe",
    "prompt": "Fix the failing test",
    "metadata": {},
    "expected": {"must_contain": ["patch", "tests passed"]},
    "budget": {"max_steps": 30, "max_runtime_sec": 900}
  }
}
```

响应格式：

```json
{
  "final_output": "Applied patch and verified tests passed",
  "steps": 12,
  "tool_calls": 18,
  "tokens_in": 4500,
  "tokens_out": 900,
  "cost_usd": 0.11,
  "trace": ["opened repo", "edited file", "ran tests"],
  "metadata": {"agent_version": "local-dev"}
}
```

## CLI

### Run benchmark

```bash
agent-benchmark run \
  --tasks fixtures/sample_tasks.jsonl \
  --agent openclaw-cmd \
  --agent-command "/path/to/openclaw_runner" \
  --output runs/openclaw_results.jsonl
```

### Report

```bash
agent-benchmark report \
  --input runs/openclaw_results.jsonl \
  --format table
```

### Compare

```bash
agent-benchmark compare \
  --inputs runs/mock_results.jsonl runs/openclaw_results.jsonl \
  --format table
```

### Validate task file

```bash
agent-benchmark validate --tasks fixtures/sample_tasks.jsonl
```

## Task Schema

每条任务为一行 JSON：

```json
{
  "task_id": "tool-001",
  "benchmark_family": "tool",
  "title": "Summarize an error log",
  "prompt": "Read the tool output and produce a concise diagnosis.",
  "metadata": {"difficulty": "easy"},
  "expected": {"must_contain": ["diagnosis"]},
  "budget": {"max_steps": 8, "max_runtime_sec": 120}
}
```

`expected.must_contain` 是内置 evaluator 的最小验证规则。真正接入官方 benchmark 时，应替换为该 benchmark 的官方 verifier。

## Roadmap

- 接入官方 `SWE-Bench Pro` verifier
- 接入 `WebArena-Verified` browser harness
- 接入 `Toolathlon` task pack
- 增加 HTML/Markdown 报告输出
