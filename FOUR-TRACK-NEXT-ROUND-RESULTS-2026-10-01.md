# 四赛道下一轮执行结果

执行依据：[2026-09-30 计划](FOUR-TRACK-NEXT-ROUND-PLAN-2026-09-30.md)。本文件随本轮验证更新；尚未完成的项目明确标出，不视为验收通过。

| 赛道 | 本轮结论 | 主要边界 |
| --- | --- | --- |
| T1 | 952541 官方 8/86，较旧提交净增 1 题；本地权限修正版通过验证 | 旧 7 题有 3 题退化为崩溃，60 个崩溃缺逐题诊断，未达晋级门槛 |
| T2 | 日频候选族停止，不晋级 | 本地代理改善不足 2%，时间区块区间跨零 |
| T3 | scalar 候选通过预登记的完整本地晋级门槛 | 426/426 本地通过；非官方排名，未上传 |
| T4 | E2 本地可评分恢复候选通过公开形状与故障矩阵 | 七个官方退出 2 的真实根因及 House 实测仍未知 |

## 范围与复核

- 保留启动时已有的 T1/T4 未提交工作；实验分别冻结其源文件和差异。未提交代码不等于本轮新增代码。
- 按 [AGENTS.md](AGENTS.md) 在各代码改动任务前重新查阅五个官方仓库的当前 README、适用 AGENTS/CONTRIBUTING/SUBMISSION_CLI 和相关规则：[共享](https://github.com/Agenthon-2026/Agenthon2026-public)、[T1](https://github.com/Agenthon-2026/track1-coding-public)、[T2](https://github.com/Agenthon-2026/track2-forecasting-public)、[T3](https://github.com/Agenthon-2026/track3-simulation-public)、[T4](https://github.com/Agenthon-2026/track4-analysis-public)。各实验记录对应时间、SHA、源和输入哈希。
- 已在既有 ChatGPT Project 为本 Codex chat 建立[规划/复核对话](https://chatgpt.com/g/g-p-6ab1634500348191967c38123bc3c2dc-agenthon-2026/c/6abdb060-20b4-83e9-a25f-8661e5b6a32e)，任务号 `c2c_b731`。第一轮独立复核认可 T4 本地恢复证据、T2 停止结论和 T3 v3 登记；第二轮要求的 T1 修正与 T3 完整计时现已在本地完成。经用户授权，第二轮的[过滤后失败输出](project-evidence/next-round-20261001/c2c-iteration2-filtered-failures.txt)和[通过输出](project-evidence/next-round-20261001/c2c-iteration2-filtered-successes.txt)已记录供复核读取。内置浏览器复核通道目前不可用，第二轮结果尚未送交 ChatGPT 作最终审查，不将其记为验收完成。
- 本轮授权限本地实施和验证。没有公开镜像发布、提交打包或平台上传。用户另明确允许在 T3 计时期间暂时停止 `claude-app-1`，并在结束或失败后恢复。

## T4 — T4-E2-v1

本地验证及 ChatGPT 独立复核完成，可作为本地恢复候选。已在冻结权限候选与 E2 候选间复现、修复完整 CRLF JSON 围栏拒绝和部分完整历史回退下的请求容量预检问题。后者仅在预算紧张时优先补齐缺行。实体归属、cutoff、引用和 grounding 检查保持严格；无依据时继续退出 2。

- Linux 完整测试 191 项通过；Windows 189 项通过、2 项跳过。保留修复前的失败和旧 scorer 测试断言调整记录。
- 控制镜像的 43 个故障矩阵案例与候选的 44 个案例均符合预期；两个有完整、可依据输出的恢复路径由退出 2 变为退出 0。
- 控制与候选各完成 11/11 公开形状、78 行输出，并通过当前 scorer 5.2.2 smoke。`score=None`，不代表预测质量得分。
- 候选在 `umask 077` 下由 UID 65534 写出的 11 份输出，均可由 UID 65533 读取且语义相同；文件 0644、目录 0755，无符号链接或所有人可写条目。
- 本地 E2 镜像：`sha256:0caea21f7c34364741b11c1613377866c44ca830ec7f5425963f9ae1a94c4b69`。没有发布。

[完整证据](project-evidence/t14-experiments/T4-E2-v1/README.md)。新增诊断只记录阶段、分类和计数，不记录原始模型正文、提示词或凭证。

官方七个退出 2 没有保存响应正文或参赛日志，不能据此证明它们由上述机制导致。本机没有可用 House 配置，本轮真实 House 探针为 0；不把合成 HTTP 200 覆盖写成线上因果证明。

## T1 — T1-PERM-AUDIT-v1

本地权限修正版验收完成。952541 已在官方平台 **`Finished`**，显示分数 **0.0930**（948333 为 0.0814）：86 题中 8 题得分、60 题容器崩溃、18 题未通过领域规则检查。旧提交得分的 7 题中，4 题保持得分、3 题变为崩溃；另有 4 题新得分。因此虽然净增 1 题，仍未达到计划中的旧 7 题保持门槛。[终态冻结](project-evidence/next-round-20261001/t1-952541-terminal-freeze.json)及[86 题逐项结果](project-evidence/next-round-20261001/t1-952541-unit-outcomes.csv)已保存。没有重传。报告给出的 403 错误路由和默认模型 thinking 破坏纯 JSON 解析是通用崩溃排查提示，没有逐题运行日志，不能将 60 次崩溃逐一归因。86 题反馈现已暴露，不再把它们当作未见过的确认数据。

[只读崩溃审计](project-evidence/next-round-20261001/t1-952541-readonly-diagnostic-20261001.md)核对了精确提交镜像：它已经使用正确的 `/v1/chat/completions`、Bearer 认证和关闭 thinking 的请求字段，故未据通用提示改动传输代码。平台渲染的逐题表及[已登录的 `get_details` 核查](project-evidence/next-round-20261001/t1-952541-get-details-sanitized-20261001.json)都没有逐题阶段、耗时或 traceback；签名日志链接被内置浏览器阻止，需取得实际诊断后再选定单一修复实验。

精确冻结镜像在相同真实入口下，`umask 022` 的 14 个条目均可跨 UID 读取，`umask 077` 虽退出 0，却有 13 个条目不可读。加入可执行文件的后续夹具得到相同结论：旧镜像 15 项中有 14 项不可读。此压力实验不证明原提交遇到了该故障。

初版候选虽通过正常权限检查，独立审查发现其 `finally` 也可能改变被拒绝的既存目录权限，因此没有采用该版本。修正版只在子进程成功接受新输出树并发送确认后，核对该目录身份并完成权限调整。

- 最终本地镜像：`sha256:b56a7671cb6a270826cbb0a81c9b00c4871c8ea7a9bb1782440bb53980e5f4be`。与冻结 952541 源相比只改 `cli.py` / `workspace.py`，C3 求解和 review 工作保持原样，未加入候选。
- 精确镜像核心 57/57、完整工作树容器回归 82/82；非空、同输入、输入内、输入祖先及根 symlink 五种真实 CLI 拒绝路径的字节、权限和所有权保持不变。
- 候选 `umask 077`、`022`、显式 private 输出三例均 15/15 跨 UID 可读，格式及可执行权限保留。异常退出 1 的诊断树 5/5、watchdog 退出 124 的诊断树 7/7 均可读。
- 额外 WSL 工作树测试曾有 5 failures / 1 error，完整日志保留；不把它写成通过，也不在缺少独立证据时断言其根因。容器回归与镜像实测单列报告。

独立代码及运行证据复核通过。[完整证据](project-evidence/t13-experiments/T1-PERM-AUDIT-v1/README.md)。没有发布或替换排队提交。

## T2 — T2-D1：不晋级

生产源和 950513 保持原样。冻结比较了基线、63 日波动半混合、126 日半混合，以及仅用较早内折选择候选的规则。69/71 卡提供 205 个外折，共 3,096 条分量记录；两卡缺少足够新历史折。

| 方法 | 等卡权重代理改善 | 等日历年区块的改善 95% 区间 | 结论 |
| --- | ---: | --- | --- |
| 63 日波动半混合 | +0.4092% | [-0.020458, +0.011899] | 低于 2%，区间跨零 |
| 126 日波动半混合 | +0.5962% | [-0.020484, +0.013321] | 低于 2%，区间跨零 |
| 内折选择规则 | -0.1285% | [-0.019726, +0.003072] | 未改善，区间跨零 |

区间单位是代理损失，且按年份加权，与等卡百分比估计不同的量。所有必需组别的退化均未超过 2%，不能补偿总体门槛失败。历史版本缺失和先前数据曝光也独立阻止晋级。

7 项校准/时间隔离测试通过；三种生成方案各通过 71/71 宿主 CLI 与当前官方 g0–g3 检查，共 213/213。该结果不构成 Docker 资源或官方预测分数证明。按停止规则结束该候选族。[完整证据](project-evidence/t2-d1-20261001/README.md)、[机器摘要](project-evidence/t2-d1-20261001/summary.json)。

## T3 — T3-SCALAR-DIRECT-v5：本地晋级门槛通过

以官方已评分四 worker 镜像 `sha256:df5e1b9d…` 为对照、既有 scalar 镜像 `sha256:a12731a1…` 为候选，重新预登记完整 65 单市场加 6 batch 的比较。v5 的一整块预热与两整块测量各有 142/142 次运行通过，当前 toolkit 2.5.1 验证器各验收 142/142，Docker 事件各有且仅有 142 个预期启动；426 次运行零参与者失败，三块均为 `complete_clean`。[冻结计划](project-evidence/t13-experiments/T3-SCALAR-DIRECT-v5/plan-v5.json) SHA256 为 `393fb72823fa49557abbc1dfbb349b22377ea9669854dfd299dac52cd427eef5`。

只用两块测量的 284 条记录计算，71 单元平均速率提高 **8.9676%**，逐单元相对改善中位数 **8.2893%**；按 scenario family 成对 bootstrap 的绝对速率差 95% 区间为 **[443.05, 823.98] events/s**。65 个单市场的组别均值提高 **9.3048%**，6 个 batch 提高 **7.0215%**，71 个单元的中位增益均为正。[独立审计](project-evidence/t13-experiments/T3-SCALAR-DIRECT-v5/independent-final-audit.json)复算与[机器摘要](project-evidence/t13-experiments/T3-SCALAR-DIRECT-v5/summary.json)一致，预登记本地晋级门槛全部通过。六个 batch 同属一个 scenario family，其家族聚类区间退化为单点，不提供独立家族层面的不确定性估计。本结果只证明公开 roster 上的本地表现，不能换算为官方 Final 分数。

旧实验保留：[v3 首个测量块的宿主中断](project-evidence/t13-experiments/T3-SCALAR-DIRECT-v1/blocks/1/attempt-01/interruption-audit.json)使 138/142 的整块作废；通用 Docker 代理无法映射 WSL 输入的 [v3 重试基础设施失败](project-evidence/t13-experiments/T3-SCALAR-DIRECT-v1/blocks/1/attempt-02/infrastructure-audit.json)也未纳入统计。[v4](project-evidence/t13-experiments/T3-SCALAR-DIRECT-v4/README.md) 的预热在 12/142 后被 Windows WSL 更新中断，内核和 socket 指纹变化，因此另立 v5；旧记录和失败证据均未混入 v5。

每块结束后原 `claude-app-1` 均以同一 ID 和 `on-failure` 策略恢复；它仍按原有行为周期性重启。完整计时后已将 Ubuntu 专用 socket 恢复为 `root:root 660`，并从原始备份恢复 Docker Desktop 的 `EnableDockerAI=true` 设置。Desktop 重启后再次核验同一 daemon、原容器及新建 socket 的原组，详见[权限租约](project-evidence/t13-experiments/T3-SCALAR-DIRECT-v5/socket-permission-lease.json)。[本地发布复核](project-evidence/t13-experiments/T3-SCALAR-DIRECT-v5/RELEASE-REVIEW.md)记录镜像差异、资源与许可风险。没有构建新的发行包、公开镜像、上传 Development 或提交 Final；正式发布前仍需核验镜像内许可清单和发行物哈希。
