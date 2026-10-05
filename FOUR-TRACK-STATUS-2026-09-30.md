# 四赛道 Development 状态与下一步

状态表核对于 **2026-09-30 23:10 UTC**，T4 诊断更新至 **22:40 UTC**，T1 排队状态已按[组织方新回复](https://github.com/Agenthon-2026/track1-coding-public/issues/27#issuecomment-5920920816)修正。以下是本队提交的 Development 阶段结果，不代表 Final 排名；分数和状态以链接中的 CodaBench 官方记录为准。

| Track | 最新已核对提交 | 当前结果 | 下一步 |
| --- | --- | --- | --- |
| T1 Coding | [952541](https://www.codabench.org/api/submissions/952541/) | 仍为 `Submitting`，`task: null`，没有新分数；组织方确认正常排队、尚未 intake。上一次有分数的 [948333](https://www.codabench.org/api/submissions/948333/) 为 **7/86**，primary **0.0813953488**。 | 不重传；等待终态并冻结 86 项反馈，再决定是否启用本地默认关闭的修复候选。等待期间审计输出文件跨用户可读性。 |
| T2 Forecasting | [950513](https://www.codabench.org/api/submissions/950513/) | **71/71** 可评分；primary normalized score **1.0135349826**（越低越好；榜单显示 **−1.0135349826**），与 [947121](https://www.codabench.org/api/submissions/947121/) 持平。新月度趋势分支没有覆盖本次 71 个评分单元。 | 针对实际评分的日频任务改进通用预测路径，先做时间顺序验证和逐单元对比，再考虑提交。 |
| T3 Simulation | [950514](https://www.codabench.org/api/submissions/950514/) | **71/71** 可评分；primary **29,447.2373 events/s**，高于 [948335](https://www.codabench.org/api/submissions/948335/) 的 **26,124.5133 events/s**，提升 **12.7188%**。批量单元贡献主要增益，单市场单元均值略降。 | 保留当前四 worker 批量方案，复核单市场下降及资源开销；取得符合 Final 环境的重复计时证据后再判断泛化收益。 |
| T4 Analysis | [953762](https://www.codabench.org/api/submissions/953762/) | `Finished`，但 **0/10** 可评分；7 个 `container_crashed`、3 个 `no_output`。primary 0.0 和榜单占位值 −1e9 均不能当作预测质量分数。[组织方已确认](https://github.com/Agenthon-2026/track4-analysis-public/issues/2#issuecomment-5920737584)使用 scorer 5.2.2：三个退出码 0 的单元因输出树被拒，另七个由参赛程序自行以退出码 2 结束。 | 已在旧镜像复现 0600 输出文件无法跨用户读取；本地权限修复通过跨用户及 11 个公开单元的检查，尚未发布。继续定位七个退出码 2，再决定是否提交新镜像。 |

T2 和 T3 的旧提交 947121、948335 是比较基线，最新已评分提交分别是 **950513**、**950514**。T3 的 12.7188% 是 Development 实测差异，不能直接推断 Final 的速度提升。T4 的权限问题有本地因果复现；七个退出码 2 的根因仍未知，House 请求返回 HTTP 200 并不证明模型输出通过了参赛程序的解析与证据校验。

下一轮分赛道实验、晋级门槛与提交时机见[四赛道优化计划](FOUR-TRACK-NEXT-ROUND-PLAN-2026-09-30.md)。

代码变更前须按 [AGENTS.md](AGENTS.md) 重新核对五个官方仓库的当前说明：[共享工具与比赛规则](https://github.com/Agenthon-2026/Agenthon2026-public)、[T1](https://github.com/Agenthon-2026/track1-coding-public)、[T2](https://github.com/Agenthon-2026/track2-forecasting-public)、[T3](https://github.com/Agenthon-2026/track3-simulation-public)、[T4](https://github.com/Agenthon-2026/track4-analysis-public)。
