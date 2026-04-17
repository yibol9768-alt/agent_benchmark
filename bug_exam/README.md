# Bug Exam Bench (BEB)

动态、抗污染、仓库级代码评测框架。LLM **injector** 在 GitHub 仓库中按结构化难度注入
bug,多个 **solver** 竞争修复,由仓库自身测试套件在 Docker 沙箱中判定 pass/fail,
最终通过 Bradley-Terry + Elo 排名。

基于 `../SWE-bench_Pro-os/` 的 Docker 镜像和评测脚本,但 schema、注入、评分全部重写。

---

## 当前进展一览

| 模块 | 状态 | 说明 |
|------|------|------|
| **Schema + DB** | ✅ 完成 | Pydantic dataclass + sqlite,8 表,支持断点续跑 |
| **Bug 注入器** | ✅ 完成 | LLM planner+executor,15-op 变异分类,并发 draw |
| **Scrubber** | ✅ 已加固 | 不再泄露 test assertion 内容,只传测试数量 |
| **8 验证门控 (G1-G7)** | ✅ 完成 | apply check / F2P scope / P2P preservation / AST 校验 |
| **Docker 评测** | ✅ 完成 | 基于 SWE-Pro 镜像的 baseline/bug_only/solver 三模式 |
| **BT + Elo + Leaderboard** | ✅ 完成 | Bradley-Terry MLE + bootstrap CI + streaming Elo |
| **反污染探针** | ✅ 完成 | original vs fresh bug pass-rate 对比脚本 |
| **Solver: claude_direct** | ✅ 可用 | GLM-5.1 直接调用 |
| **Solver: openhands** | ✅ 可用 | OpenHands SDK v1.x,独立 Python 3.12 venv |
| **LLM 重试** | ✅ 完成 | 指数退避 + jitter,处理 429/5xx |
| **Instance 筛选脚本** | ✅ 完成 | 系统性扫描 SWE-Pro JSONL,找 baseline 可通过的 instance |
| **Batch driver 改进** | ✅ 完成 | n_draws=6,断点续跑,接受筛选结果文件 |
| **部署/回收脚本** | ✅ 完成 | deploy.sh + pull_results.sh |
| **单测** | ✅ 35/35 绿 | Mac 无需 Docker 即可跑 |
| **M1 E2E** | ✅ 完成 | qutebrowser 1 个实例,两个 solver 均通过 |
| **M2 数据** | ⚠️ 5/30 | 2 repo × 2 band,两 solver 全过,无区分度 |
| **M3 反污染** | ⚠️ 假设反转 | n=4,fresh bug 反而更易修(详见下方) |
| **Instance 筛选执行** | 🔄 进行中 | 3/266 Python instance 已筛(2 viable),需全量跑 |
| **Solver: aider** | ⬜ 未启用 | 代码存在,需 anthropic key plumbing |
| **Solver: mini_swe_agent** | ⬜ 未启用 | 代码存在,需 anthropic key plumbing |
| **4 个计划中 solver** | ⬜ 无代码 | swe_agent, agentless, autocoderover, moatless |

---

## 架构

### Pipeline

```
harvest → envbuild → baseline → inject → validate →
freeze  → solve    → grade    → score  → report
```

每阶段是幂等的 `bug-exam <stage>` CLI 子命令,通过 sqlite `data/status.db` 实现断点续跑。

### 当前实际工作路径 (SWE-Bench Pro 集成)

不走 harvest/envbuild/baseline,直接复用 SWE-Pro 的 Docker 镜像 (`jefzda/sweap-images:<tag>`)
和 per-instance `run_script.sh` / `parser.py`:

```
SWE-Pro JSONL (731 instances)
    ↓ screen_swebench_pro.py  →  screened_instances.json (可用 instance 列表)
    ↓ run_swebench_pro_batch.py
        ├─ load_instance()    →  解析 JSONL + Docker tag
        ├─ baseline 测试      →  确认测试能通过
        ├─ draw_injections()  →  LLM 注入 6 个候选 bug
        ├─ 验证门控 G1-G7     →  选第一个通过的 draw
        ├─ scrub_problem_statement() → 隐藏根因的 bug 描述
        ├─ solver.solve()     →  每个 solver 尝试修复
        └─ grade              →  Docker 内跑测试判定
    ↓ render_leaderboard.py
LeaderboardEntry (BT rating + Elo + pass-rate)
```

### 数据流

```
SwebenchProInstance → ExamInstance (DRAFT→VALIDATED→FROZEN)
    → SolverResult → Grade → LeaderboardEntry
```

---

## 目录结构

```
bug_exam/
├── bug_exam/                          # 核心 Python 包
│   ├── schema.py                      #   数据模型: ExamInstance, Grade, BreakPlan 等
│   ├── db.py                          #   Sqlite3 封装 (5 表)
│   ├── cli.py                         #   Typer CLI (8 子命令)
│   ├── swebench_helpers.py            #   脚本共用的 git/solver 工具函数
│   ├── adapters/swebench_pro_source.py    # SWE-Pro → bug_exam 适配器
│   ├── evaluator/                     #   Docker 评测 (runner, entryscript, parser)
│   ├── injector/                      #   LLM bug 注入 (planner + executor + scrubber)
│   │   └── prompts/                   #     planner.md, executor.md, scrubber.md
│   ├── llm/                           #   LLM 客户端 (Anthropic + GLM + retry)
│   ├── scoring/                       #   Bradley-Terry, Elo, leaderboard
│   ├── solvers/                       #   Solver 适配器 (claude_direct ✅, openhands ✅, aider ⬜, ...)
│   └── validator/                     #   AST diff, 变异算子校验, 8 门控
│
├── scripts/
│   ├── screen_swebench_pro.py         #   筛选可用 instance (需 Docker)
│   ├── run_swebench_pro_batch.py      #   批量注入 + 求解 (主力脚本)
│   ├── run_contamination_probe.py     #   反污染探针 (original vs fresh)
│   ├── deploy.sh / pull_results.sh    #   Mac ↔ remote 代码/结果同步
│   └── ...                            #   M1 driver, E2E, solver comparison 等
│
├── configs/                           #   solvers.yaml, difficulty_bands.yaml, mutation_operators.yaml
├── tests/                             #   36 单测 (unit + integration)
├── docs/m1_swe_bench_pro_integration.md
└── dumps/                             #   运行结果 (m1, m2, m3, _archive)
```

---

## 快速上手

```bash
pip install -e ".[dev]"
python -m pytest -q           # 36 passing, Mac 无需 Docker
```

### 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `ANTHROPIC_BASE_URL` | 是 | GLM: `https://open.bigmodel.cn/api/anthropic` |
| `ANTHROPIC_API_KEY` | 是 | LLM API key |
| `ANTHROPIC_MODEL` | 否 | 默认 `glm-5.1` |
| `BUG_EXAM_PROVIDER` | 否 | `anthropic` 或 `glm` |
| `BUG_EXAM_OPENHANDS_PYTHON` | 否 | OpenHands 用的 Python 3.12 路径 |

### 远程部署

```bash
bash scripts/deploy.sh              # 推代码到 westd
bash scripts/deploy.sh --screen     # + 启动 instance 筛选
bash scripts/deploy.sh --batch      # + 启动批量注入求解
bash scripts/pull_results.sh        # 拉回结果 + 重建 leaderboard
```

---

## 已有数据

### M1: 单实例 E2E — 完成

qutebrowser 上完成了一次完整自博弈循环:注入 → 验证 → 冻结 → 求解 → 评分。
两个 solver 均通过。输出在 `dumps/swebench_pro_m1/qutebrowser/`。

### M2: 多实例批量 — 5 ExamRun (目标 30)

| solver | BT | Elo | pass-rate |
|---|---:|---:|---:|
| `claude_direct` | +0.000 | 1500.0 | 100% (3/3) |
| `openhands` | +0.000 | 1500.0 | 100% (2/2) |

2 repo (qutebrowser, ansible)。qutebrowser 只有 1x1,ansible 有 1x1+2x2。OpenHands 在多个
run 上触发 GLM `code:1234` 网络错误,但巧合在于 grade 阶段也通过(errored=True 但
F2P/P2P 预测都命中)—— 本质是数据噪声,不可作为 solver 能力信号。BT 无区分度。

### M3: 反污染探针 — 假设反转

| solver | 原始 SWE-Pro bug | fresh 注入 bug | delta |
|---|---:|---:|---:|
| `claude_direct` | 50% (1/2) | 50% (1/2) | 0pp |
| `openhands` | 0% (0/2) | 100% (2/2) | -100pp |

n=4,CI 极宽。Fresh bug 反而更容易修。

**根因分析:**
1. 单点 AST 突变机械性太强,强 solver 看 failing test 就能反推
2. 旧版 scrubber 将 test assertion 原文传给 solver,信息密度比真实 PR 描述还高
3. 样本量太小,无法得出统计结论

---

## 已做的改进 (Option D) — ⚠️ 仅代码层面,未重跑验证

针对 M3 反转结果已完成以下代码改动,但**尚未用这些改动重跑一遍 M3 探针**,
无法确认 delta 是否由正转负。下次 session 第一件事就该做这个验证。

### 1. Scrubber 加固 ✅

- `prompts/scrubber.md`: 移除了 "Failing tests" 输出要求,新增规则禁止包含
  assertion messages / expected-actual values / test function names
- `scrubber.py`: 不再传 `failing_test_assertions[:5]` 的内容,改为只传测试数量

### 2. 复合 Mutation 指引 ✅

- `prompts/planner.md`: 新增 S≥2 的 "Multi-step bugs" 章节,要求语义耦合的多步 bug
  (推荐组合: InvertedCondition+ShadowedVariable, RemovedGuard+WrongBinaryOperator)
- `run_swebench_pro_batch.py`: S≥2 时 hint 强调 "mutations INTERACT across call boundaries"

### 3. LLM 重试 ✅

- `llm/retry.py`: `retrying_call()` 指数退避 + jitter,捕获 429 / 5xx / GLM 网络错误
- 已接入 `glm_client.py` 和 `anthropic_client.py` 的所有 API 调用点

### 4. 注入并发控制 ✅

- `injector/agent.py`: `draw_injections()` 的 `max_workers` 限制为 `min(n_draws, 3)`,
  避免 6 个并发 API 流同时触发限流

### 5. 扩展基础设施 ✅

- `screen_swebench_pro.py`: 系统性筛选脚本,增量运行,输出 `screened_instances.json`
- `run_swebench_pro_batch.py`: n_draws 默认 6,断点续跑,`--instance-file` 输入
- `deploy.sh` / `pull_results.sh`: 一键部署/回收

---

## Instance 筛选进展

SWE-Pro JSONL 共 731 instances,按语言分布:

| 语言 | Repo | Instance 数 | 可筛选 |
|------|------|------------|--------|
| Python | ansible, qutebrowser, openlibrary | 266 | ✅ AST 校验支持 |
| Go | flipt, teleport, vuls, navidrome | 280 | ⬜ 需扩展 operator_check |
| TypeScript | protonmail, element-web, tutanota | 141 | ⬜ 需扩展 operator_check |
| JavaScript | NodeBB | 44 | ⬜ 需扩展 operator_check |

**已筛选 (smoke test):** 3 个 qutebrowser instance → 2 viable (baseline pass=52, 191)

**待做:** 跑完全部 266 个 Python instance (~6-7 小时)。注意 WSL 的 nohup 不可靠,
建议用 `tmux` 或 `screen` 保持会话。

---

## 下一步 — 下个 agent 的明确 checklist

### 立即做(验证 Option D 代码改动是否有效)

- [ ] **重跑 M3 探针**(最高优先级):用**加固后的** scrubber / 复合 mutation /
      LLM 重试,在已验证的 qutebrowser + ansible-b748e 两个 instance 上重跑
      `run_contamination_probe.py`。看 delta (original - fresh) 是否由负转正。
      输出落在 `dumps/swebench_pro_m3/contamination_v2/`。
- [ ] **抓一次手动 scrubber 输出样本**:跑完后人眼检查 `problem_statement.md`
      有没有泄露 operator 名 / 方向 / specific assertion —— 老版本就是这里出坑。

### 短期(数据扩展,n=4 → n≥30)

1. **全量筛选** 266 个 Python instance(用 `tmux` 保持会话,WSL nohup 不稳)
2. **批量注入**:取 top 20-50 viable instance,`--bands 1x1,2x2 --n-draws 6`
3. **扩展到第三个 solver**:`aider` 或 `mini_swe_agent`(adapter 已写,
   需要 anthropic key plumbing —— 看 `solvers/base.py` 的加载方式)

### 如果 Option D 不够 (delta 仍反向)

- **A. 重设计 bug 生成器**:放弃单点 AST 突变,用多步骤语义驱动的 bug
- **B. 扩大样本源**:去 SWE-Bench Pro 之外找题源
- **C. 换论文定位**:主打"可控、可扩展的动态评测框架",不依赖反污染显著性

### 长期

- 扩展 `operator_check` 到 Go / TypeScript(当前只支持 Python AST)
- 实现 4 个计划中的 solver adapter:swe_agent, agentless, autocoderover, moatless
- 接入 IRT 难度拟合(`scoring/irt.py` 是 stub)

---

## 难度 Band

`(F, S)`: F=注入涉及的文件数, S=验证通过的变异算子数

| Band | F | S | 当前状态 |
|------|---|---|---------|
| trivial (1x1) | 1 | 1 | ✅ M2 已使用 |
| easy (2x1) | 2 | 1 | ⬜ 未跑 |
| medium (2x2) | 2 | 2 | ✅ M2 已使用 (1 exam) |
| hard (3x2) | 3 | 2 | ⬜ 未跑 |
| expert (5x3) | 5 | 3 | ⬜ 未跑 |

---

## 变异算子 (15-op 分类)

Phase 1 已启用 (4): OffByOne, InvertedCondition, WrongBinaryOperator, SwappedArgs

Phase 2 计划启用 (11): RemovedGuard, DroppedReturn, SwitchedConstant, FlippedBoolean,
WrongExceptionType, MissingAwait, WrongLoopBound, StateReorder, ShadowedVariable,
IncorrectTypeCast, OmittedSideEffect

---

## 测试

```bash
python -m pytest -q                    # 36 tests, Mac

# 远程 E2E
PYTHONPATH=. .venv/bin/python scripts/run_e2e_live.py --solvers claude_direct

# 远程 solver 对比
PYTHONPATH=. .venv/bin/python scripts/compare_solvers.py \
    --solvers claude_direct,openhands --n-draws 2

# 远程反污染探针
PYTHONPATH=. .venv/bin/python scripts/run_contamination_probe.py \
    --instances <id1>,<id2> --solvers claude_direct,openhands \
    --jsonl <path>/sweap_eval_full_v2.jsonl --swepro-root <path>/SWE-bench_Pro-os \
    --workdir-root /tmp/workdirs --runs-root /tmp/runs \
    --out-dir dumps/swebench_pro_m3/contamination
```
