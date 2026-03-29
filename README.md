# Agent Benchmark - OpenCode + GLM-5 评测框架

本仓库用于评测 **opencode + glm-5** 在多个主流 Agent Benchmark 上的表现。

支持三条评测线：

1. **SWE-Bench Pro** — 731 道真实软件工程 bug 修复任务（JS/Python/Go/TS）
2. **WebArena-Verified** — 812 道 web 浏览交互任务
3. **Toolathlon** — 108 道多步骤工具调用任务（32 个真实应用）

---

## 目录结构

```
agent_benchmark/
├── benchmark_suite/              # 核心评测代码
│   ├── run_opencode_swebench.py      # SWE-Bench Pro patch 生成器
│   ├── run_openhands_swebench_pro.py # OpenHands 方案（备选）
│   ├── run_webarena_verified.py      # WebArena-Verified 评测
│   ├── run_toolathlon.py             # Toolathlon 评测
│   └── evaluate_swebench_pro.py      # SWE-Bench Pro 自定义评测脚本
├── scripts/                      # 一键运行脚本
│   ├── run_opencode_swebench_pro_smoke.sh   # SWE-Bench 单题测试
│   ├── run_opencode_swebench_pro_full.sh    # SWE-Bench 全量跑
│   ├── run_webarena_verified_smoke.sh       # WebArena smoke test
│   ├── run_toolathlon_smoke.sh              # Toolathlon smoke test
│   ├── setup_webarena.sh                    # WebArena 环境安装
│   ├── setup_toolathlon.sh                  # Toolathlon 环境安装
│   └── ...
├── configs/                      # 配置文件
│   ├── webarena/env_urls.json        # WebArena web 环境地址
│   └── swebench_pro/                 # SWE-agent 配置
├── vendor/                       # 第三方依赖（git clone）
├── dumps/                        # 运行结果输出目录
└── pyproject.toml                # Python 依赖管理（uv）
```

---

## 前置要求

- **Python 3.13+**
- **uv**（Python 包管理器）：`curl -LsSf https://astral.sh/uv/install.sh | sh`
- **opencode**（代码 Agent CLI）：`npm install -g opencode-ai`
- **Docker**（评测需要）：macOS 用 colima，Linux 直接装 docker
- **Git**

---

## 快速开始

### 1. 克隆仓库并安装依赖

```bash
git clone https://github.com/yibol9768-alt/agent_benchmark.git
cd agent_benchmark
uv sync
```

### 2. 配置环境变量

```bash
export GLM_API_KEY="你的API Key"
export GLM_BASE_URL="http://35.220.164.252:3888/v1/"
export GLM_MODEL="glm-5"
```

API 使用 OpenAI 兼容格式，支持 `glm-5` 和 `MiniMax-M2.7` 模型。

### 3. 验证 API 连通性

```bash
.venv/bin/python -c "
from openai import OpenAI
client = OpenAI(base_url='$GLM_BASE_URL', api_key='$GLM_API_KEY')
r = client.chat.completions.create(model='glm-5', messages=[{'role':'user','content':'hello'}], max_tokens=10)
print('API OK:', r.choices[0].message.content)
"
```

---

## SWE-Bench Pro 评测

### 数据集

- HuggingFace: `ScaleAI/SWE-bench_Pro`
- 731 道题，覆盖 11 个开源仓库
- 语言分布：Go 280 / Python 266 / JS 165 / TS 20

### 评测流程

整个流程分两步：**生成 patch** 和 **跑测试评分**。

#### 第一步：生成 patch

opencode 调用 glm-5 分析代码、定位 bug、自动修改文件，产出 git diff 格式的 patch。

```bash
# 先跑单题 smoke test，确认环境没问题
bash scripts/run_opencode_swebench_pro_smoke.sh

# 跑 N 题试水（可调 LIMIT 和并发数）
LIMIT=20 MAX_WORKERS=4 bash scripts/run_opencode_swebench_pro_full.sh

# 全量跑 731 题（默认 8 并发，每题最多 900s）
bash scripts/run_opencode_swebench_pro_full.sh
```

输出在 `dumps/opencode_swebench_pro_full/`，每道题一个目录：
```
instance_<id>/
├── summary.json          # 运行摘要（耗时、exit code、diff 大小）
├── patch.diff            # glm-5 生成的 patch
├── prompt.txt            # 给模型的 prompt
├── opencode_stdout.txt   # opencode 运行日志
└── opencode_stderr.txt   # opencode 错误日志
```

跑完会自动生成 `patches_for_eval.json`（标准格式）和 `results_summary.json`（汇总统计）。

#### 第二步：官方评测

需要 SWE-Bench Pro 官方评测仓库（找学长要或联系负责人获取路径）。

```bash
# macOS colima 用户需要先设 DOCKER_HOST
export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"

# 启动 Docker
colima start   # macOS
# Linux 不需要这步

# 生成评测用的数据集文件
.venv/bin/python -c "
from datasets import load_dataset
import json
ds = load_dataset('ScaleAI/SWE-bench_Pro', split='test')
with open('dumps/opencode_swebench_pro_full/raw_samples.jsonl', 'w') as f:
    for row in ds:
        row = dict(row)
        for k, v in row.items():
            if isinstance(v, list):
                row[k] = json.dumps(v)
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
print('Done')
"

# 用官方脚本评测
cd /path/to/SWE-bench_Pro-os   # 官方评测仓库路径
source SWE-agent/.venv/bin/activate
python swe_bench_pro_eval.py \
  --raw_sample_path="<agent_benchmark>/dumps/opencode_swebench_pro_full/raw_samples.jsonl" \
  --patch_path="<agent_benchmark>/dumps/opencode_swebench_pro_full/patches_for_eval.json" \
  --output_dir="<agent_benchmark>/dumps/opencode_swebench_pro_full/eval_output" \
  --scripts_dir=run_scripts \
  --num_workers=4 \
  --dockerhub_username=jefzda \
  --use_local_docker \
  --docker_platform=linux/amd64
```

评测结果会输出 `Overall accuracy: X.XX`，这就是最终成绩。

### 已验证的结果

在本地 2 道题测试中：
- **NodeBB** (JS, 300 个测试): PASS
- **qutebrowser** (Python, 56 个测试): PASS
- **Accuracy: 100% (2/2)**

注意：Go 语言的 repo 在 arm64 Mac (QEMU 模拟) 上可能有环境兼容性问题，建议用 x86 Linux 机器跑全量评测。

---

## WebArena-Verified 评测

### 数据集

- HuggingFace: `AmineHA/WebArena-Verified`
- 812 道 web 浏览交互任务
- 覆盖：GitLab、购物站、Reddit、Wikipedia、地图等网站

### 运行

```bash
# 安装（克隆官方 repo）
bash scripts/setup_webarena.sh

# 跑 smoke test（默认 3 题）
bash scripts/run_webarena_verified_smoke.sh

# 自定义参数
LIMIT=10 bash scripts/run_webarena_verified_smoke.sh \
  dumps/webarena_test \
  glm-5
```

### 说明

- 当前 runner 通过 OpenAI API 调用 glm-5，模型返回结构化 JSON 响应
- 完整的交互式评测需要部署 Web 环境（GitLab、购物站等 Docker 服务），参见 `vendor/webarena-verified/` 的文档
- 环境 URL 配置在 `configs/webarena/env_urls.json`

---

## Toolathlon 评测

### 数据集

- HuggingFace: `hkust-nlp/Toolathlon-Trajectories`
- 108 道多步骤工具调用任务
- 覆盖 32 个真实应用（Google Calendar、Notion、Slack、Kubernetes 等）

### 运行

```bash
# 安装（克隆官方 repo）
bash scripts/setup_toolathlon.sh

# 跑 smoke test（默认 3 题）
bash scripts/run_toolathlon_smoke.sh

# 使用官方 eval_client（需要 Docker 环境）
.venv/bin/python benchmark_suite/run_toolathlon.py \
  --output-root dumps/toolathlon_official \
  --model glm-5 \
  --base-url "$GLM_BASE_URL" \
  --api-key "$GLM_API_KEY" \
  --use-official-client \
  --server-host 47.253.6.47
```

### 说明

- 完整评测需要 Docker 容器（32 个应用环境）
- `--use-official-client` 模式直接调用官方 `eval_client.py`
- 默认模式通过 OpenAI API 调用 glm-5 做结构化推理

---

## 关键配置说明

### opencode 配置

`run_opencode_swebench.py` 会在每个 worktree 里自动生成 `opencode.json`，包含：
- glm-5 的 API 地址和 Key
- 模型参数（context 262144 tokens, output 32768 tokens）
- 权限控制（禁止修改测试文件）

### 并发控制

```bash
# 调整并发数（默认 8）
MAX_WORKERS=4 bash scripts/run_opencode_swebench_pro_full.sh

# 调整单题超时（默认 900s）
TIMEOUT_SEC=1200 bash scripts/run_opencode_swebench_pro_full.sh
```

### 断点续跑

全量跑脚本默认开启 `--resume`，中断后重新运行会跳过已完成的题目。

---

## 常见问题

### Q: Docker 连不上？

macOS 用 colima 的需要设环境变量：
```bash
export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"
```

### Q: 磁盘空间不够？

Docker 镜像很大（单个 1-12GB），建议至少预留 50GB。清理方法：
```bash
docker system prune -af
rm -rf dumps/*/instance_*/workspace   # 清理评测临时文件
```

### Q: Go 语言的题目评测失败？

arm64 Mac 上通过 QEMU 模拟 x86 跑 Go 测试会有兼容性问题。建议：
- 用 x86 Linux 机器跑评测
- 或只评测 JS/Python 的题目

### Q: `ModuleNotFoundError: No module named 'datasets'`？

没装依赖。运行：
```bash
cd agent_benchmark
uv sync
```
然后用 `.venv/bin/python` 代替 `python3` 执行。

### Q: opencode 命令找不到？

安装 opencode：
```bash
npm install -g opencode-ai
```

---

## 全量评测建议

1. 使用 **x86 Linux 服务器**（避免 QEMU 兼容性问题）
2. 磁盘预留 **100GB+**（Docker 镜像 + 731 个 repo 的 worktree）
3. 设置合理并发：`MAX_WORKERS=8`（取决于 API 速率限制）
4. 预估时间：8 并发 x 731 题 x ~15min/题 ≈ **23 小时**
5. 开启 `--resume`，支持中断后继续

---

## 技术架构

```
用户输入题目
    │
    ▼
opencode CLI ──调用──▶ glm-5 API (OpenAI 兼容)
    │                      │
    │                      ▼
    │                 模型分析代码、生成修复
    │                      │
    ▼                      ▼
git worktree ◀── 自动编辑文件
    │
    ▼
git diff ──▶ patch.diff
    │
    ▼
Docker 容器 ──▶ 跑 fail-to-pass 测试
    │
    ▼
判定 PASS / FAIL
```
