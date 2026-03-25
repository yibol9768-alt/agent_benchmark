# Agent Harness Benchmark 评测方案 v3.0

**核心问题**：同一个模型，套不同的 harness，在从简单到复杂的任务上，谁表现更好、更省、更稳——结论要能直接指导"下一步该改 harness 的哪里"。

---

## 一、研究问题的精确定义

当前市面上所有 benchmark 数字（SWE-bench 80.9%、Terminal-Bench 77%……）测的都是 **模型 × harness 的捆绑成绩**，无法拆分各自贡献。这导致两个实际问题：

1. **选工具时没有依据**：Claude Code 比 OpenHands 高 8%，是模型好还是 loop 设计好？
2. **优化方向不清楚**：我改了 harness 的上下文管理策略，到底有没有用？

本工作的唯一研究问题：

> **固定同一开源模型，不同 harness 在不同复杂度任务圈层上的表现差异是多少，差异来自哪里？**

这个问题的答案，直接给出"哪个 harness 的哪个设计决策值得借鉴或规避"。

---

## 二、任务圈层：从内到外

任务按人机交互的抽象层级划分，越往外越接近真实用户场景，也越难。

```
圈1 代码单元级    单函数生成 / 补全 / 修复          → 已饱和，不测
圈2 仓库/Issue级  在真实仓库定位+修复Bug，多文件编辑  → ★ v1 主测
圈3 全栈项目级    从需求到可运行软件（设计→实现→测试） → ★ v1 主测
圈4 桌面GUI级     跨App工作流，操作真实桌面/浏览器    →   v2 扩展
圈5 知识工作级    文献综述、PPT制作、数据分析报告     →   v2 扩展（需自建）
```

**v1 聚焦圈2 + 圈3 的理由**：

- 圈2 有高质量公开 leaderboard 可作对齐锚点，且"固定模型换 harness"的测法几乎没有先例
- 圈3（全栈软件开发）有现成 benchmark（DevBench），但几乎没有 agent harness 横评数据——是真正的空白窗口
- 圈2→圈3 形成完整的软件开发能力谱：从"修一个 bug"到"从需求写出整个软件"，是真实世界最常见的开发者场景

---

## 三、Benchmark 选择（v1 仅两个）

### 3.1 圈2：SWE-bench Pro（Public Set）

| 项目 | 说明 |
|---|---|
| 仓库 | github.com/SWE-bench/SWE-bench |
| 规模 | 731 tasks，41 个专业开源仓库 |
| 任务类型 | Bug fix、feature request、security patch、优化，需跨多文件编辑 |
| 评测指标 | Resolve Rate（fail-to-pass + no regression）|
| 选它而非 Verified 的理由 | GPL 仓库设计降低训练污染；任务更难、更接近工业代码；Track B 数据可与 Scale AI 官方榜对齐 |
| 运行方式 | Docker 容器，sb-cli 云端评测；建议先跑 100-task 子集控制成本 |

**为什么不选 SWE-bench Verified**：已接近饱和（最高 80.9%），头部 agent 差距压缩到 3-5%，区分度不足。Pro 的难度梯度更能暴露 harness 设计的真实差距。

### 3.2 圈3：DevBench

| 项目 | 说明 |
|---|---|
| 仓库 | github.com/open-compass/DevBench |
| 规模 | 22 个精选仓库，Python / C++ / Java / JavaScript |
| 任务类型 | 软件设计 → 环境搭建 → 多文件实现 → 验收测试 → 单元测试（全 SDLC） |
| 评测指标 | 各阶段通过率；实现阶段用 acceptance test + unit test 双验证 |
| 选它的理由 | 唯一覆盖完整软件开发生命周期的 benchmark；agent harness 横评数据几乎空白，是差异化窗口 |
| 运行方式 | Docker 隔离环境，Apache 2.0 开源，支持本地运行 |

**两个 benchmark 的互补关系**：

```
SWE-bench Pro  → 测 harness 在"已有代码库中定向修改"时的能力
                  关键能力：代码库导航、精准定位、最小化改动、回归不破坏
DevBench       → 测 harness 在"从零构建完整软件"时的能力
                  关键能力：需求理解、架构分解、跨文件协调、自我验证循环
```

---

## 四、待测 Harness 清单

**准入硬性要求：必须支持接入第三方开源模型**（Track A 统一用 DeepSeek V3.2）。

强绑定单一闭源模型的 agent（Claude Code、Codex CLI、Devin）不参与 Track A，仅作 Track B 天花板参考。

### 4.1 Code-focused Harness（圈2+圈3）

| Harness | 主导方 | Loop 设计特点 | Stars |
|---|---|---|---|
| **OpenHands** | All-Hands AI | LiteLLM 模型路由，Docker 沙盒，多 agent 协调，事件溯源状态 | 65k+ |
| **SWE-agent** | Princeton NLP | ACI（Agent-Computer Interface），结构化文件编辑工具，SWE-bench 官方 scaffold | — |
| **Aider** | Paul Gauthier | git-native，architect+editor 双模式，全模型支持 | 39k+ |
| **OpenCode** | Anomaly/SST | Go TUI，client/server 架构，LSP 集成，会话持久化 | 120k+ |
| **II-Agent** | Intelligent Internet | Terminal-Bench SOTA，production-ready，自称强工具使用能力 | — |
| **Live-SWE-agent** | UIUC | 运行时自进化（在线生成新工具），自适应 loop | — |

### 4.2 General Harness（圈3+，兼顾全栈项目）

| Harness | 主导方 | Loop 设计特点 | Stars |
|---|---|---|---|
| **DeerFlow 2.0** | 字节跳动 | LangGraph，lead agent + sub-agent 并行，内置 slide/report skill | 43k+ |
| **Suna** | Kortix AI | LiteLLM，浏览器自动化 + 代码执行 + 文件管理，Docker 沙盒 | — |
| **AutoGPT** | Significant Gravitas | 自主迭代循环，Agent Builder，插件市场 | 170k+ |
| **OpenClaw** | 开源社区 | 通用任务执行，连接任意 LLM，WeChat/企业生态 | 爆款 |

### 4.3 Track B 参考（模型绑定，仅作天花板对齐）

| Harness | 最佳公开成绩 | 绑定模型 |
|---|---|---|
| Claude Code | SWE-Pro 45.8%，SWE-Verified 80.9% | Anthropic Opus |
| Codex CLI | Terminal-Bench 77.3% | GPT-5.x |
| Devin 2.0 | PR merge rate 67% | 闭源 |

---

## 五、评测设计

### 5.1 两条轨道

| 轨道 | 模型配置 | 目的 |
|---|---|---|
| **Track A（核心）** | 统一 DeepSeek V3.2 | 单独测 harness loop 质量，去除模型变量 |
| **Track B（参考）** | 各 harness 官方推荐最佳模型 | 了解天花板，计算"模型依赖度" |

**分差的意义**：

```
模型依赖度 = Track B 得分 − Track A 得分

分差大（>15%）→ 该 harness loop 设计对强模型高度依赖，换开源模型后能力断崖
分差小（< 5%）→ harness 设计鲁棒，工具使用和上下文管理策略更强，迁移成本低
```

分差本身是核心研究结论之一，直接指向"harness 的哪些设计决策在补偿模型能力不足"。

### 5.2 统一测评协议（Track A）

```
模型      : DeepSeek V3.2（OpenAI 兼容 API）
温度      : 0（确定性输出，保证可复现）
Token 上限: 2M uncached（统一预算，防止 harness 因预算不同产生偏差）
环境      : 统一 Docker 容器
并发      : 各 harness 相同并发数

记录的指标（每个 task）:
  - resolved（0/1）
  - tokens_input / tokens_output（拆分，便于分析上下文膨胀）
  - cost_usd
  - steps（loop 步数）
  - wall_time（秒）
  - failure_type（若失败：navigation_error / tool_fail / context_overflow / wrong_patch / other）
```

### 5.3 分析维度

除了最终 Resolve Rate，重点分析以下四个 harness 质量维度：

**① 上下文管理效率**
同等任务，token 消耗越少说明 harness 的上下文压缩、工具调用策略越好。
指标：tokens_input per resolved task

**② 错误恢复能力**
任务失败时，harness 是否能检测错误并调整策略，还是在错误路径上反复循环。
指标：failure_type 分布；loop 步数标准差（高方差 = 不稳定）

**③ 难度敏感性**
在简单 task（单文件改动）vs 复杂 task（跨 5 个文件、需要环境搭建）上，各 harness 的表现衰减曲线是否一致。
指标：按 task 复杂度分组的 resolve rate 曲线

**④ 工具使用策略**
harness 触发工具调用的模式（是否过度调用、是否有效并行、是否存在 loop stuck 模式）。
指标：tools_called per task；loop stuck 比例（连续 3 步相同工具相同参数）

---

## 六、执行计划

### Phase 1：快速出结果（2-3 周）

**目标**：在 SWE-bench Pro 100-task 子集上跑通所有 Track A harness，出第一版排行榜。

```
Week 1  环境搭建
        - 为每个 Track A harness 完成 DeepSeek V3.2 接入验证
        - 统一 Docker 镜像，确认 token 计量一致
        - 跑 5 task 冒烟测试，确认 failure_type 分类逻辑

Week 2  SWE-bench Pro 100-task 子集（Track A）
        - 并行跑所有 harness
        - 产出：Resolve Rate 表 + failure_type 分布图

Week 3  分析 + 初版报告
        - 计算四个 harness 质量维度
        - Track B 引用公开数据，计算模型依赖度分差
        - 产出：第一版"harness 横评报告"，可对外分享
```

**预计成本**（Track A，100 tasks，DeepSeek V3.2，按 ~$0.28/M token 估算）：
- 每个 harness 约 $3-8
- 10 个 harness 全跑：$30-80
- 总成本可控在 $100 以内

### Phase 2：加 DevBench，扩展圈3（第 4-6 周）

**目标**：在全栈项目场景下验证 Phase 1 的排行是否稳定，或有反转。

```
Week 4-5  DevBench 全量（22 repos × 5 阶段）
          重点关注：
          - "设计+环境搭建"阶段（General Harness 的优势区间）
          - "实现"阶段（Code Harness 的优势区间）
          - 哪些 harness 在全 SDLC 完成率上有显著差距

Week 6    交叉分析
          - SWE-bench Pro vs DevBench：同一 harness 的排名是否一致？
          - 不一致的 harness → 深挖其 loop 设计，找出能力边界
          - 产出：harness 能力雷达图（导航/生成/验证/恢复四维）
```

### Phase 3：自动化 pipeline（第 7-8 周）

**目标**：建立持续测评机制，新爆款 agent 出现后自动触发评测。

```
触发条件  监控 GitHub Trending（每日）+ arXiv cs.AI（每周）
          检测到新 agent → 自动检查是否支持 OpenAI 兼容 API
          → 符合准入 → 自动跑 SWE-bench Pro 50-task 子集

元 agent  用 DeerFlow 或 OpenHands 驱动整个 pipeline：
          环境初始化 → 跑 benchmark → 解析结果 → 生成对比 Markdown → 推送通知

首个测试对象  OpenClaw（已于 2026-03-25 触发，腾讯 WeChat 集成事件）
```

---

## 七、预期产出与独特性

### 产出

1. **harness 横评表**：10 个 harness × 2 个 benchmark 的 Track A 成绩，附 failure_type 分布
2. **模型依赖度分析**：每个 harness 的 Track B − Track A 分差，量化"harness 在多大程度上补偿了模型能力"
3. **harness 能力雷达图**：上下文管理 / 错误恢复 / 难度敏感性 / 工具策略 四维可视化
4. **优化建议清单**：基于 failure_type 分析，指出每个 harness 最值得改进的 loop 设计决策

### 与已有工作的区别

| 维度 | 已有工作（HAL、SWE-bench 等）| 本工作 |
|---|---|---|
| 研究对象 | 模型能力（harness 作为固定变量）| Harness 设计质量（模型作为固定变量）|
| Benchmark 范围 | 单一圈层（主要是圈2）| 圈2 + 圈3，覆盖完整软件开发谱 |
| General Agent | 不覆盖 | 纳入 DeerFlow、Suna、AutoGPT 等 |
| 分析深度 | 最终得分 | failure_type 分布 + harness 质量四维分析 |
| 持续性 | 静态快照 | 自动化 pipeline，新 agent 爆款自动触发 |
| 用户视角 | AI 研究员 | 想用 agent 提效的开发者 / 科研工作者 |

---

## 八、参考资源

**Benchmark**
- SWE-bench Pro：labs.scale.com/leaderboard/swe_bench_pro_public
- DevBench：github.com/open-compass/DevBench
- SWE-bench Live（v2 扩展）：swe-bench-live.github.io
- OSWorld（v2 扩展，圈4）：os-world.github.io

**Harness 仓库**
- OpenHands：github.com/All-Hands-AI/OpenHands
- SWE-agent：github.com/SWE-bench/SWE-agent
- OpenCode：github.com/anomalyco/opencode
- DeerFlow 2.0：github.com/bytedance/deer-flow
- Suna：github.com/kortix-ai/suna
- AutoGPT：github.com/Significant-Gravitas/AutoGPT
- II-Agent：github.com/Intelligent-Internet/ii-agent
- Aider：github.com/paul-gauthier/aider

**相关工作**
- HAL（Princeton）：hal.cs.princeton.edu — 三维评测基础设施，最接近本工作的前置研究
- SWE-rebench：swe-rebench.com — 含 token cost 的细粒度 leaderboard
- OpenHands Index：openhands.dev/blog/openhands-index — 多维度 agent 对比

---

*v3.0 | 2026-03-25 | 核心重构：以"harness 设计质量"为唯一研究目标，围绕圈2+圈3两个 benchmark 精简执行路径，增加 failure_type 和 harness 质量四维分析框架*
