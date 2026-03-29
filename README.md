# Agent Benchmark - GLM-5 评测框架

本仓库用于评测 **glm-5** 在三个主流 Agent Benchmark 上的表现。

| 数据集 | 任务数 | 任务类型 | Agent 框架 | 评测方式 |
|--------|--------|----------|-----------|---------|
| **SWE-Bench Pro** | 731 | 代码 bug 修复 | opencode (代码 Agent) | 官方 Docker 评测 |
| **Toolathlon** | 108 | 多步骤工具调用 | 官方 eval_client (远程服务器) | 官方远程评测 |
| **WebArena-Verified** | 812 | Web 浏览交互 | glm-5 API 直接调用 | 官方 webarena-verified |

---

## 前置要求

- **Python 3.13+**
- **uv**（包管理器）：`curl -LsSf https://astral.sh/uv/install.sh | sh`
- **opencode**（仅 SWE-Bench 需要）：`npm install -g opencode-ai`
- **Docker**（SWE-Bench 评测 + WebArena 环境需要）
- **Git**

---

## 安装

```bash
git clone https://github.com/yibol9768-alt/agent_benchmark.git
cd agent_benchmark
uv sync
```

### 配置环境变量

```bash
export GLM_API_KEY="你的API Key"
export GLM_BASE_URL="http://35.220.164.252:3888/v1/"
export GLM_MODEL="glm-5"
```

### 验证 API

```bash
.venv/bin/python -c "
from openai import OpenAI
client = OpenAI(base_url='http://35.220.164.252:3888/v1/', api_key='$GLM_API_KEY')
r = client.chat.completions.create(model='glm-5', messages=[{'role':'user','content':'hello'}], max_tokens=10)
print('OK:', r.choices[0].message.content)
"
```

---

## 一、SWE-Bench Pro（代码修复）

### 原理

opencode CLI 调用 glm-5 读代码、分析 bug、自动编辑文件，产出 git diff patch。然后用官方 Docker 环境跑测试判定 pass/fail。

### 流程

```
opencode + glm-5 ──▶ 分析代码 ──▶ 编辑修复 ──▶ patch.diff
                                                    │
                         官方 Docker 镜像 ◀─────────┘
                              │
                         跑 fail-to-pass 测试
                              │
                         判定 PASS / FAIL
```

### 跑法

#### 单题测试（验证环境）

```bash
bash scripts/run_opencode_swebench_pro_smoke.sh
```

#### 全量跑 731 题

```bash
# 默认 8 并发，每题最多 900 秒，支持断点续跑
bash scripts/run_opencode_swebench_pro_full.sh

# 自定义参数
LIMIT=20 MAX_WORKERS=4 TIMEOUT_SEC=1200 bash scripts/run_opencode_swebench_pro_full.sh
```

#### 官方评测

需要 SWE-Bench Pro 官方评测仓库（`SWE-bench_Pro-os`）。

```bash
# macOS 需要设 Docker 环境变量
export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"
colima start

# 导出数据集
.venv/bin/python -c "
from datasets import load_dataset; import json
ds = load_dataset('ScaleAI/SWE-bench_Pro', split='test')
with open('dumps/opencode_swebench_pro_full/raw_samples.jsonl', 'w') as f:
    for row in ds:
        row = dict(row)
        for k, v in row.items():
            if isinstance(v, list): row[k] = json.dumps(v)
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
"

# 跑官方评测
cd /path/to/SWE-bench_Pro-os
source SWE-agent/.venv/bin/activate
python swe_bench_pro_eval.py \
  --raw_sample_path="<路径>/raw_samples.jsonl" \
  --patch_path="<路径>/patches_for_eval.json" \
  --output_dir="<路径>/eval_output" \
  --scripts_dir=run_scripts \
  --num_workers=4 \
  --dockerhub_username=jefzda \
  --use_local_docker \
  --docker_platform=linux/amd64
```

### 输出

```
dumps/opencode_swebench_pro_full/
├── run_manifest.json         # 运行配置
├── results_summary.json      # 汇总统计
├── patches_for_eval.json     # 所有 patch（送评测用）
└── instance_<id>/
    ├── summary.json          # 单题摘要
    ├── patch.diff            # glm-5 生成的 patch
    └── opencode_stdout.txt   # 运行日志
```

### 已验证结果

| 实例 | 语言 | 测试数 | 结果 |
|------|------|--------|------|
| NodeBB | JS | 300 | **PASS** (300/300) |
| qutebrowser | Python | 56 | **PASS** (56/56) |

---

## 二、Toolathlon（工具调用）

### 原理

Toolathlon 提供**公共远程评测服务器**（47.253.6.47），上面部署了 32 个真实应用（Google Calendar、Slack、Kubernetes、BigQuery 等）。评测时服务器发任务给 glm-5，glm-5 通过 API 调用工具完成任务，服务器验证结果。

不需要本地搭环境，直接调官方 eval_client 就行。

### 流程

```
eval_client.py ──▶ 远程服务器 (47.253.6.47)
                       │
                  发任务给 glm-5 API
                       │
                  glm-5 调用 MCP 工具
                       │
                  服务器验证最终状态
                       │
                  返回 PASS / FAIL
```

### 跑法

#### 安装

```bash
bash scripts/setup_toolathlon.sh
```

#### 单题测试

```bash
bash scripts/run_toolathlon_smoke.sh
```

#### 全量跑 108 题

```bash
# 默认 10 并发
bash scripts/run_toolathlon_full.sh

# 自定义
WORKERS=5 bash scripts/run_toolathlon_full.sh dumps/toolathlon_glm5 glm-5
```

#### 直接用 eval_client

```bash
.venv/bin/python benchmark_suite/run_toolathlon.py \
  --model glm-5 \
  --base-url "$GLM_BASE_URL" \
  --api-key "$GLM_API_KEY" \
  --output-dir dumps/toolathlon_glm5 \
  --workers 10
```

### 输出

```
dumps/toolathlon_glm5/
├── client.log              # 实时日志（用 tail -f 监控）
├── server.log              # 服务器日志
├── eval_stats.json         # 最终评测统计
├── traj_log_all.jsonl      # 所有任务的执行轨迹
└── finalpool/              # 每个任务的详细结果
    ├── task_name_1/
    └── task_name_2/
```

### 注意事项

- 公共服务器有速率限制：每 IP 每 24 小时最多 3 次评测，累计 180 分钟
- 需要更高限额联系：jlini@cse.ust.hk
- 全量 108 题大约需要 2-4 小时

---

## 三、WebArena-Verified（Web 浏览）

### 原理

WebArena 是 web 浏览交互评测。任务要求 agent 在真实网站上操作（搜索商品、管理 GitLab 项目等）。

当前方案：glm-5 通过 API 分析任务，产出结构化响应。完整交互评测需要部署 Web 环境。

### 流程

```
任务描述 ──▶ glm-5 API ──▶ 结构化响应 (agent_response.json)
                                │
                    webarena-verified eval ◀──┘
                         │
                    判定 PASS / FAIL
```

### 跑法

#### Smoke test（不需要 Web 环境）

```bash
bash scripts/run_webarena_verified_smoke.sh
```

#### 完整评测（需要 Web 环境）

先部署 Web 环境：

```bash
# 购物站
docker run -d --name webarena-shopping -p 7770:80 am1n3e/webarena-verified-shopping

# Reddit
docker run -d --name webarena-reddit -p 9999:80 am1n3e/webarena-verified-reddit

# GitLab
docker run -d --name webarena-gitlab -p 8023:8023 am1n3e/webarena-verified-gitlab
```

创建配置文件 `configs/webarena/webarena_config.json`：

```json
{
  "__GITLAB__": {
    "urls": ["http://localhost:8023"],
    "credentials": {"username": "root", "password": "demopass"}
  },
  "__SHOPPING__": {
    "urls": ["http://localhost:7770"]
  },
  "__REDDIT__": {
    "urls": ["http://localhost:9999"]
  }
}
```

跑评测：

```bash
bash scripts/run_webarena_verified_full.sh
```

### 输出

```
dumps/webarena_verified_full/
├── summary.json                # 汇总
└── <task_id>/
    ├── agent_response.json     # agent 输出（标准格式）
    ├── raw_response.txt        # 模型原始输出
    └── prompt.txt              # 任务 prompt
```

---

## 配置参数

### 并发与超时

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MAX_WORKERS` | 8 (SWE-Bench) / 10 (Toolathlon) | 并发数 |
| `TIMEOUT_SEC` | 900 | SWE-Bench 单题超时（秒）|
| `LIMIT` | 无 | 限制跑的题目数量 |

### 断点续跑

- SWE-Bench: 默认开启 `--resume`，重跑会跳过已完成的题
- Toolathlon: 用相同 `--job-id` 可以续跑

---

## 全量评测建议

| 项目 | 建议配置 |
|------|---------|
| 机器 | **x86 Linux**（避免 QEMU 兼容性问题）|
| 磁盘 | 100GB+（Docker 镜像 + repo worktree）|
| SWE-Bench 时间 | 8 并发 × 731 题 ≈ **23 小时** |
| Toolathlon 时间 | 10 并发 × 108 题 ≈ **2-4 小时** |
| WebArena 时间 | 812 题 ≈ **3-5 小时**（取决于 API 速度）|

---

## 目录结构

```
agent_benchmark/
├── benchmark_suite/                      # 核心代码
│   ├── run_opencode_swebench.py              # SWE-Bench patch 生成
│   ├── evaluate_swebench_pro.py              # SWE-Bench 自定义评测
│   ├── run_toolathlon.py                     # Toolathlon 官方 eval_client 封装
│   └── run_webarena_verified.py              # WebArena agent + 评测
├── scripts/                              # 一键脚本
│   ├── run_opencode_swebench_pro_full.sh     # SWE-Bench 全量
│   ├── run_toolathlon_full.sh                # Toolathlon 全量
│   ├── run_webarena_verified_full.sh         # WebArena 全量
│   ├── run_*_smoke.sh                        # 各 benchmark smoke test
│   └── setup_*.sh                            # 环境安装
├── configs/                              # 配置文件
│   └── webarena/env_urls.json                # WebArena 环境地址
├── vendor/                               # 第三方依赖
│   └── toolathlon/                           # Toolathlon 官方 repo
├── dumps/                                # 运行结果
└── pyproject.toml                        # Python 依赖
```

---

## 常见问题

### Docker 连不上（macOS）
```bash
export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"
colima start
```

### 磁盘空间不够
```bash
docker system prune -af              # 清 Docker
rm -rf dumps/*/instance_*/workspace  # 清评测临时文件
```

### Go 语言题目评测失败
arm64 Mac 上 QEMU 模拟 x86 跑 Go 测试会有兼容性问题。用 x86 Linux 机器跑。

### ModuleNotFoundError
```bash
uv sync   # 重装依赖
```

### Toolathlon 速率限制
公共服务器每 IP 每天限 3 次。联系 jlini@cse.ust.hk 申请更高限额。
