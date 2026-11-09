# 简化 IdeaScientist

只需输入完整研究问题，程序从其中的 `Problem statement:` 自动提取 Challenge，再依次找论文、找研究空白、生成构想、写研究方案。

不训练模型。三个角色共用配置中的模型，由 LangGraph 按顺序调度。默认只检索 2025 年底之前的文献；只分析摘要，不声称已读全文。


## 论文来源与项目定位

本项目基于 Jiarui Liu 等人于 2026 年发表的论文 **[IdeaScientist: Orchestrating Agents for Grounded Scientific Ideation](https://arxiv.org/abs/2610.04074)**（arXiv:2610.04074）进行简化实现，参考其“研究空白发现（Gap Finder）→ 创新构想（Innovator）→ 研究方案撰写（Report Writer）”的三角色协作框架。

- 论文：https://arxiv.org/abs/2610.04074
- 作者代码仓库：https://github.com/jiarui-liu/IdeaScientist
- 公开数据集：https://huggingface.co/datasets/Jerry999/SvalbardIdeaVault

本仓库是面向学习与演示的简化实现。当前版本通过 LangGraph 串联流程，调用配置中的模型，并使用 Semantic Scholar 检索论文摘要；未实现论文中的角色强化学习训练，也未复现论文的完整检索系统和实验指标。

## 可视化网页界面

仓库已包含完整的 Streamlit 界面源码：`app.py` 为网页入口，`ui/` 提供引用展示与任务管理功能。界面支持研究问题输入、阶段进度与结果查看、文献引用、方案下载、历史任务管理和检查点恢复。

在已准备好 Python 环境的项目目录中安装依赖并创建配置文件：

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在 `.env` 中填写模型接口地址、模型名称和 API 密钥后，启动网页：

```powershell
python -m streamlit run app.py
```

按终端显示的本地地址访问，通常为 `http://localhost:8501`。GitHub 仓库存放网页源码；目前尚未部署公开在线演示站点。

## 当前代码结构与页面启动

项目根目录只有八个职责明确的 Python 文件：

| 文件 | 职责 |
|---|---|
| `roles.py` | 三个角色的名称、说明、原始提示词与静态输出 Schema |
| `corpus.py` | Semantic Scholar 检索、去重、日期限制、目标论文排除、缓存 |
| `tools.py` | 公共操作：引用/结构校验、证据压缩、阶段完成契约 |
| `client.py` | 模型配置、调用、超时重试、请求/响应记录、文件保存 |
| `runner.py` | 三个角色的输入组装、执行与传递，LangGraph 调度和检查点恢复 |
| `state.py` | 所有阶段共用的状态结构 |
| `workflow.py` | 页面和批处理共用的运行入口，导出 `run_research()` 等接口 |
| `app.py` | Streamlit 页面入口 |

`gap_finder.py`、`innovator.py`、`report_writer.py` 的执行函数已合并到 `runner.py`。
模型配置原来位于 `llm.py`，现在位于 `client.py`；旧的 `runtime.py` 和 `literature_search.py` 也已移除。

辅助文件分别归入子目录：

- `ui/`：页面引用展示、任务命名、删除和撤销。
- `experiments/`：数据准备、固定五例批处理、运行统计与案例分析。
- `tests/`：必要的离线回归测试。
- `data/examples/`：五份独立输入样例；固定五例清单在 `data/benchmark/inputs.jsonl`。
- `results/`、`cache/`、`backups/`：历史结果、检索缓存和变更前备份。

在项目目录启动页面：

```powershell
.\.venv\python.exe -m streamlit run app.py
```

页面支持新任务、历史结果、检查点恢复、任务命名和删除。原始提示词及结果结构保持不变。
这次整理改变了代码指纹，旧的未完成任务需要原代码版本才能续跑；历史结果仍可浏览。

## 1. 七步怎么衔接

| 步骤 | 做什么 | 保存的新内容 |
|---|---|---|
| plan_search | 把问题变成检索词 | search_queries |
| retrieve_literature | 查目标领域论文 | papers、search_history |
| find_gap | 找具体方法缺陷 | research_gap |
| plan_innovation | 选一个 Gap，规划跨领域查询 | selected_gap、cross_domain_queries |
| retrieve_cross_domain | 查可借鉴的机制 | cross_domain_papers |
| generate_idea | 解释怎么把机制迁移过来 | research_idea |
| write_proposal | 把构想写成可执行方案 | research_proposal |

所有步骤共用 `state.py` 里的状态。每一步只返回新增字段，下一步接着读取。每完成一步立即保存，断线后不用从头跑。

## 2. 环境

你现有的 Python 在 `.venv\python.exe`，这是一个放在 `.venv` 文件夹里的 Conda 环境。

在 PowerShell 中，先进入项目目录：

```powershell
Set-Location 'C:\Users\HUAWEI\Desktop\项目\IdeaScientist'
```

如果已有环境，直接使用。需要新装依赖时：

```powershell
.\.venv\python.exe -m pip install -r requirements.txt
```

`.env.example` 展示需要哪些配置；你已有 `.env` 会保留。密钥不要放进代码、截图或报告。

## 3. 自己输入一个题目

```powershell
.\.venv\python.exe -B workflow.py
```

页面只需填写一个 Problem definition 输入框；命令行一次粘贴完整问题定义，最后独立输入 `END` 结束。无需另写 `Problem definition:` 和 `Challenge:` 两个外层标题。例如：

```text
Formal setting: Given a user request and a subagent capability inventory, return a valid result or an explicit abstention. Inputs: User request and capability inventory. Outputs: Answer or structured abstention.

Problem statement: Subagents sometimes answer requests requiring unavailable data instead of reporting their limits. Why it matters: Unsupported answers can propagate errors.
END
```

完整问题定义会保留；Challenge 原样提取自 `Problem statement:` 到 `Why it matters:` 之间的文字（若无后者则提取至末尾），不调用大模型。缺少或留空 `Problem statement:` 会提示补全。旧的显式 Challenge 输入仍兼容；Agent 提示词与七步流程不变。

也可以写成 JSON 文件：

```json
{
  "sample_id": "custom_001",
  "problem_definition": "Formal setting: Given a user request and a subagent capability inventory, return a valid result or an explicit abstention. Inputs: User request and capability inventory. Outputs: Answer or structured abstention.\n\nProblem statement: Subagents sometimes answer requests requiring unavailable data instead of reporting their limits. Why it matters: Unsupported answers can propagate errors."
}
```

```powershell
.\.venv\python.exe -B workflow.py --input research_input.json --run-dir results/custom_001
```

## 4. 中断后怎么继续

保留原目录和原代码配置，执行：

```powershell
.\.venv\python.exe -B workflow.py --resume --run-dir results/custom_001
```

完成的步骤会显示“复用已完成步骤”。成功的检索和已保存的模型回答也能复用。代码、模型或输入改变时，程序要求新目录，避免把不同实验混在一起。

连接错误可以修好网络后恢复。`insufficient_evidence` 表示证据不足，不等同于普通断线；可分析原因并在新目录调整检索设置，原记录保留。

每次模型调用最长默认 180 秒，客户端最多重试一次；回答格式最多再修复一次，调用量和 token 会保存。额度和账单以接口服务商记录为准。

电脑强制关机可能留下 `.running.lock`。先确认没有程序还在跑，再删这个锁文件。不要删除已经生成的步骤文件。

旧版结果继续保留，不能直接作为新版结构化检查点恢复。

## 5. 固定五例实验

公开数据分片已校验 SHA256；当前文件有 272 条 test，和论文写的 277 条有版本差异。

`data/benchmark/inputs.jsonl` 已固定五个真实输入：

1. EARS：子 Agent 何时应该弃答，对应附录 Table 15。
2. MOSAIC：不同安全规则如何组合，对应附录 Table 13。
3. TABED：多模态推测解码如何适应场景，对应附录 Table 16。
4. Strategy Executability：数学推理中策略如何落实。
5. Same Signal, Opposite Meaning：Agent 学习中相同信号的不同含义。

前三例用于论文案例对照，后两例按固定种子补足。选择发生在运行之前，不根据生成结果换题。

公开数据没有单独 Challenge，因此从 `problem_definition` 的 `Problem statement` 原样提取。没有读取目标方法来补写问题。目标标题和 arXiv ID 仅用于检索排除，不传给模型。

参考答案放在 `references_for_review_only.jsonl`，生成程序不读取它。不要把该文件内容复制到输入中。

先试跑一例，确认网络和额度：

```powershell
.\.venv\python.exe -B -m experiments.run_batch --limit 1
```

再恢复同一批次，继续五例：

```powershell
.\.venv\python.exe -B -m experiments.run_batch --resume
```

一例失败会记录并继续下一例；不会悄悄换样本。汇总分母始终是五例。

如果需要重新选数据，使用新输出目录，不能覆盖当前冻结清单：

```powershell
.\.venv\python.exe -B -m experiments.dataset_adapter --source data/test.jsonl.zst --output data/another_benchmark
```

## 6. 结果在哪里

每例一个目录，如 `results/test5/2606.18668/`：

- `input.json`、`manifest.json`：问题、配置、代码指纹。
- `steps/步骤名/input.json`、`output.json`：每一步的输入和返回结果。
- `retrieval/`：每条查询的结果、日期、零结果和错误。
- `model_calls/`：原始回答、校验错误、token 和用时。
- `research_gap.md`、`research_idea.md`：中间结果。
- `research_proposal.json`、`.md`：最终方案。
- `run_status.json`：成功、失败或证据不足。

批次汇总在 `results/test5/summary.md` 和 `summary.json`。不会覆盖其他任务目录的结果。

## 7. 怎么验证效果

自动检查覆盖：流程完成率、论文数量、摘要覆盖、查询错误、引用 ID、耗时和 token。

人工还要检查三件事：Gap 是否被文献支持，Idea 是否真在解决该 Gap，Proposal 是否把这个机制写清楚。引用 ID 存在不代表引用结论一定正确。

先对照前三例的论文附录，比较核心机制、迁移假设、可证伪预测和局限。模型与文献环境不同，这叫同题机制对照，不是复现论文分数。五例不能证明总体领先。

不需要执行 Proposal 里提出的科研训练或数据采集。我们验证的是构想生成系统。

生成后整理案例材料：

```powershell
.\.venv\python.exe -B -m experiments.case_analysis
```

结果在 `results/test5/case_comparison.md`。这个脚本才会读取参考答案，生成角色不会读取。它整理实际构想与论文附录机制，不自动打分；人工核查意见应在阅读真实证据后填写。

## 8. 离线测试

```powershell
.\.venv\python.exe -B -m unittest discover -s tests -v
```

测试不会调用真实模型。检查中途断线、恢复不重跑、不同样本互不覆盖、假引用拒绝、目标论文排除、缓存复用和参考答案隔离。

本次备份位于项目下 `backups/`；旧实验不会删除。`app.py` 是可运行的 Streamlit 演示页面。
