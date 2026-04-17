# bug_exam — agent session quick-reference

See `README.md` for full project docs, architecture, milestone status, and next steps.

## Provider defaults (copy-paste)

```bash
export GLM_API_KEY=sk-EZaFIa6PKx3VDyWBFWvur3irS8K6lRI7qexqIAFcQyXtD1eD
export GLM_BASE_URL=http://35.220.164.252:3888/v1/
export GLM_MODEL=glm-5
export BUG_EXAM_PROVIDER=glm
# Anthropic-compatible (同一个中转)
export ANTHROPIC_API_KEY=$GLM_API_KEY
export ANTHROPIC_BASE_URL=http://35.220.164.252:3888/
export ANTHROPIC_MODEL=glm-5
export ANTHROPIC_AUTH_TOKEN=$GLM_API_KEY
```

可用模型: glm-5, MiniMax-M2.7, qwen3.5-plus

## Remote execution (westd)

Full remote host rules in `/Users/liuyibo/Desktop/lyb/CLAUDE.md`. Key points:
- SSH: `ssh westd` (vicp.fun direct, no proxy)
- HTTP inside WSL needs `http_proxy=http://172.30.48.1:7890` (Docker pull 等)
- LLM 中转 API (`35.220.164.252:3888`) 从 WSL 直连即可,不需要代理
- Script transfer: Mac `/tmp/*.sh` → `scp westd:C:/tools/` → WSL runs with `tr -d '\r'`
- OpenHands requires Python 3.12 at `/root/openhands_venv/bin/python`

## Known gotchas (M2/M3 era)

- **`code:1234` GLM 网络错误**:OpenHands 在 batch 里会周期性抛这个,但 patch 可能
  已写成功,所以 `errored=True` 但 F2P/P2P 仍命中。grading 结果里忽略 errored
  字段,只看 final_passed。根治要加更猛的 LLM retry(`bug_exam/llm/retry.py` 已有,
  可能需要增加 retry 次数 / 扩 exception 列表)。
- **SWE-Pro instance 大多 baseline 就红**:测试依赖 gold patch 才绿,不是所有
  instance 都能当 bug 注入底座。必须先跑 `screen_swebench_pro.py` 筛一轮。
- **OpenHands subprocess 没回传 token_usage**:stats 在 OpenHands v1.15 的字段
  对不上,`_openhands_runner.py` 里留了 TODO。
- **第三个 solver 未启用**:aider / mini_swe_agent adapter 代码在
  `bug_exam/solvers/`,但从未在真实 SWE-Pro instance 上验证。
