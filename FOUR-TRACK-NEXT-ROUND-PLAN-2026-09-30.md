# 四赛道下一轮优化计划

制定于 2026-09-30 23:10 UTC。目标是提高四个 track 的**实际可评分表现**。这是执行计划；本轮没有改参赛代码、发布镜像或上传提交。证据区分官方 Development 结果、组织方诊断、本地可复现实验和待验证假设。

## 新证据与决策

[Track 1 issue #27 的组织方回复](https://github.com/Agenthon-2026/track1-coding-public/issues/27#issuecomment-5920920816)确认：01:16 UTC 上传的 **952541 正常排队**，尚未进入 intake，未被拦截；15:43–22:20 UTC 曾暂停启动新任务，现已恢复；最多九份更早的提交可能先启动。intake 在槽位开放时才检查团队声明。**不重传、不再就同一排队状态追问**；重传只会排在原提交之后。23:00 UTC 后核对的 [提交记录](https://www.codabench.org/api/submissions/952541/)仍为 `Submitting`、`task:null`，不能据此推断运行失败。

[Track 4 issue #2 的组织方回复](https://github.com/Agenthon-2026/track4-analysis-public/issues/2#issuecomment-5920737584)确认：953762 使用 scorer 5.2.2；三个单元退出码 0，却因输出树被拒而成为 `no_output`；另外七个由参赛进程以退出码 2 结束。无超时、OOM 或信号终止。所有 House 请求 HTTP 200，但响应正文和参赛日志未保留，七个退出码 2 的根因仍未知。组织方还[明确说明输出树跨用户可读规则也适用于 T1 `/app/output`](https://github.com/Agenthon-2026/track4-analysis-public/issues/2#issuecomment-5920823959)。

| Track | 当前可证明状态 | 本轮优先目标 |
| --- | --- | --- |
| T1 Coding | [952541](https://www.codabench.org/api/submissions/952541/)正常排队；前次 [948333](https://www.codabench.org/api/submissions/948333/) 为 **7/86**，其中 72 崩溃、7 domain gate 失败 | 等 86 项反馈后按真实故障分支优化；等待期间只做输出权限风险审计 |
| T2 Forecasting | [950513](https://www.codabench.org/api/submissions/950513/) **71/71**，normalized **1.0135349826**，越低越好；月度趋势分支未覆盖此次评分单元 | 改进实际评分的 57 个日频 level 和 14 个日频 log-return 单元 |
| T3 Simulation | [950514](https://www.codabench.org/api/submissions/950514/) **71/71**，**29,447.2373 events/s**；较前次提高 **12.7188%** | 保留四 worker 基线，干净环境下直接比较已有 scalar-latency 候选 |
| T4 Analysis | [953762](https://www.codabench.org/api/submissions/953762/) **0/10 可评分**；三份输出拒收、七份进程退出 2 | 保持本地已证实的权限修复，集中定位并修复退出 2；之后才评价预测质量 |

## 执行顺序与验收

### P0 — T4：先恢复可评分性

**已完成，不重复：**精确旧镜像的原子写入留下 0600 文件，第二 UID 不能读取；本地 `permfix-v1` 在 `umask 077` 下写出 0644，第二 UID 可读。其 11/11 公开形状、Linux 5.2.2 smoke 已通过；镜像尚未发布或提交。[冻结证据](project-evidence/t14-experiments/R0_T4/permfix-v1/README.md)。这为三个退出 0 的 `no_output` 提供强机制解释，但不能证明七个退出 2 已修复，也不能视为官方预测得分。

**下一实验 T4-E2-v1：**在当前权限候选之上，先只加脱敏的阶段/原因分类，不改变预测策略。用公开形状和受控 HTTP 200 响应，逐项触发：空或异常响应正文、缺失或重复实体、错误标签/区间、无权引用、证据无法定位、部分 roster、可用历史不足、请求额度/截止时间耗尽，以及已有完整 checkpoint 后增强步骤失败。逐项记录 `阶段 → 退出码 → 未解决行数 → 是否保留完整有效输出`；不保留题目、prompt、模型正文或凭证。对可重现且会导致退出 2 的机制，仅做小范围修复，并确认没有削弱实体归属、cutoff、引用和 grounding 检查。`analysis-agent/analysis_agent/cli.py`、`pipeline.py`、`contract.py` 是主要检查点。

**晋级门槛：**候选在 11 个公开形状及新增故障矩阵中都能生成完整、可跨用户读取且通过 5.2.2 检查的输出；已知退出 2 的可重现路径有明确修复或有根据的完整回退；不能靠伪造证据或忽略无依据预测来换取退出码 0。若官方原因仍无法回溯，以覆盖面和真实 House 公共输入探针的结果评价剩余风险，不把合成响应当作线上因果证明。通过独立发布复核后，再决定是否使用一次 Development 提交验证可评分单元数；首次目标是从 0/10 提升，再据实际逐单元质量反馈优化 AUC、CPI 等预测方法。

### P1 — T1：等待已有提交，同时排除输出权限风险

**等待期间 T1-PERM-AUDIT-v1：**使用与 952541 相同的冻结镜像和入口，在 `umask 077`、非 root、只读根文件系统下产出代表性的 JSON、CSV、Parquet、Python 和嵌套目录，再由第二 UID 检查 `/app/output` 及子目录可遍历、所有提交文件可读，且没有 symlink 或 world-writable 路径。特别检查 `workspace.py` 的临时 JSON 写入、`os.replace` 后权限、模型生成文件与 `.agent` 辅助产物。审计是前瞻风险检查，**不说明 952541 存在权限故障**。若发现问题，做最小权限候选并在本地完整回归；候选不替换排队中的提交。

**952541 终态触发：**立即冻结 86 项逐题状态、运行阶段、耗时和可见诊断；与 948333 的固定 86 项及既有七个通过项配对。若主要失败是编译、运行或产物契约，而且进入了 C3 针对的修复路径，才测试当前默认关闭的 C3 候选。若主要是语义错误、模型解题、证据不足或时间耗尽，保留 C3 关闭，针对主因设计单一 C4 实验。任何候选都必须守住既有七个通过项、通过完整 Linux/House/输出契约测试，并以新的官方反馈决定是否提交。排队时不重传、不因等待长度更换 incumbent。

### P2 — T3：检验现有速度候选，避免从头再做微优化

当前四 worker 官方增益已实测。现有本地 `scalar-latency` 镜像 `sha256:a12731…` 通过 71/71 语义门槛、426/426 配对运行和稳定 Parquet/event 对照；相对 **unlocked-queue** 本地候选的 Docker start-to-exit 速率约增 **5.50%**，但测试中 Docker 中断、共驻容器状态变化，且比较对象并非官方提交的四 worker 镜像。[候选说明](simulation-agent/README.md)。这不是新的官方分数。

**下一实验 T3-SCALAR-DIRECT-v1：**固定 [950514 所用镜像](simulation-agent/README.md) `sha256:df5e1b9d…` 为 control，直接与现有 scalar 镜像比较完整 65 单市场 + 6 batch roster。预先锁定随机化运行顺序、重复次数、CPU/内存限制、宿主机指纹和计时公式；控制/候选交错运行，记录 Docker start-to-exit、逐单元中位速率、总均值、资源与完整语义/哈希。任何宿主机中断或其他重负载污染整个预登记区块，重跑该区块，不只重跑不利单元。T3 计时期间不运行 T1/T4 的 Docker 压测。

**预登记晋级门槛：**71/71 语义、事件数和输出哈希继续完全一致，零参与者失败；完整 roster 的成对 Docker start-to-exit 速率改进，其 bootstrap 95% 下界 **>0**，总体验证中位改进 **≥3%**；65 个单市场和六个 batch 的组别均值退化均 **≤2%**；结论不依赖剔除任何有效但不利的计时区块。这些百分比是本地发布筛选规则，并非官方门槛。将统计区间、幅度、资源和工程风险一起提交发布复核；若证据不足，继续使用已评分的 950514。只有这个既有候选判定完毕，才考虑新的 profiling/实现方向。

### P3 — T2：只优化真正评分的日频预测路径

新月度分支在 950513 的 71 个评分单元上没有触发；不能把同分解释为月度方法无效。[评分对照](project-evidence/t23-scored-feedback-20260928.md)。现有 online-ensemble 本地 proposal 的独立第三折为 9 胜、11 负、51 平，均值差虽有利，但 bootstrap 区间跨零，未达到发布依据。[模型说明](forecast-agent/README.md)。

**下一实验 T2-D1：**冻结 71 个已知单元的输入形状和当前生产实现，在每个任务 cutoff 前的资料中构造严格按时间排序的内层选择折与未触碰验证折。先校准本地评分器：分别计算 marginal CRPS、variogram、1/5/95/99% 尾部 pinball，并标明官方 M0 归一化尺度未公开，因此本地结果只是代理指标。只预登记少数通用的日频候选，例如资产类别限定的边际波动更新和固定依赖结构的对照；不要按 task ID 分支，也不要用 Development 结果当历史标签。分别报告 rates level、FX level、factor log-return、单/多资产及 horizon 的成对结果。

**晋级门槛：**在预先冻结的未触碰时间区块中，归一化代理均值至少改善本地既定 2% 门槛，成对时间区块证据为正，任一必需组别退化不超过 2%，且 71/71 接口与运行验收通过。区块太少时标为探索性，扩充合规的历史版本后再决定；维持当前 950513 行为，不把代理胜利写成官方成绩。

## 发布与停止规则

1. 每个新的**代码改动任务**开始前，重新咨询工作区 [AGENTS.md](AGENTS.md) 指定的五个当前官方仓库：[共享工具包](https://github.com/Agenthon-2026/Agenthon2026-public)、[T1](https://github.com/Agenthon-2026/track1-coding-public)、[T2](https://github.com/Agenthon-2026/track2-forecasting-public)、[T3](https://github.com/Agenthon-2026/track3-simulation-public)、[T4](https://github.com/Agenthon-2026/track4-analysis-public)，包括 README、适用 AGENTS/CONTRIBUTING/SUBMISSION_CLI 及相关规则。记录版本与适用 scorer。
2. 每次比较先冻结精确源、镜像 digest、工具包版本、任务 roster、资源、随机种子和评价方法；本地数据、代理指标、Development 与 Final 分开标注。保留失败运行和未通过的候选。
3. 发布前提供具体镜像差异、许可/依赖、完整验证、包哈希及预期消耗的官方提交次数；真实 Team Key 只走官方隐藏输入。此计划不授权任何新的公开镜像、打包或平台上传。
4. T1 终态或组织方通知、T4 可评分单元数、T2 日频损失、T3 完整 roster 容器计时是下一轮四个决定性反馈。若证据门槛未过，保留各自 incumbent，不以截止日期为由把未验收的候选直接当作 Final 版本。

本计划经 [Codex with ChatGPT 工作区对话](https://chatgpt.com/g/g-p-6ab1634500348191967c38123bc3c2dc-agenthon-2026/c/6abc2a24-7864-83ea-a379-1a60aefe710e) 的 `c2c_7a91` 独立阅读与两轮 PLAN 修订。Codex 对官方回复、现有镜像和本地候选的事实边界做了复核；本轮没有运行参赛测试。
