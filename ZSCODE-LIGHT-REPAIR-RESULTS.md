# zscode + zsspec-light 2.4 本地修复记录

日期：2026-11-05。范围：框架源码、安装资源与本地验证。用户已选择本次先交付本地验证结果，不执行真实模型端到端演练。

## 已实现

1. 规格路径保持完整目录；新增结构化 design，区分只读依据、实现/测试文件、模块依赖和输入输出契约。源 plan schema 直接包含 prepare。
2. initial revise 返回未冻结测试分支；派生规格/计划使用既有 repair_propose/repair_apply 进行只读审查、影响计算与事务应用。
3. 已接受 RED 的基线不会因重提规格清空。部分工作完成后的修订保留历史，受影响基线隔离重放 RED；未来工作项的修订测试不能替代当前组的 RED。
4. 全量回归失败重新打开实现任务，保留失败执行和历史；修复后重新取得 GREEN、回归与独立证据。修复预算耗尽时保留可编辑游标，实际用户恢复后保留旧预算历史并开启有限新一轮。
5. 真实环境失败的 prepare/未接受 RED 可受审查纠正环境命令；同运行器迁移只允许修改为环境实际检测到的路径，其他参数保持不变。审查包包含触发例外的真实执行内容。
6. Gate 统一失败预算；按错误码及材料类型区分同故障，保留总体停止上限。扩展移除重复三次失败阈值；状态查询不消耗研究读取次数。
7. knowledge 初始化后提交为提案，不直接覆盖现有知识；最终固定进验收包，用户接受后归档。
8. 需求分析输入提供历史问题及真实回答上下文；宿主批准判定允许有实际问答来源的已决问题，不接受单纯伪造 decisions 解除阻塞。
9. 新增 report_conflict(reason)，立即保存业务冲突停止点，不等待空转预算。无冻结基线时真实用户反馈可返回需求审查；已有冻结基线时保守停止，或经实际用户明确选择继续按原批准范围修复。
10. 最终回归支持 http/browser/static checks 和每轮重新生成的证据文件。Gate 固定物理副本及哈希进不可变 bundle，缺少新证据、过大证据或后续篡改不能通过。
11. 同步五个 light skill、角色提示、工具协议、安装分发、快速入门；方法版本为 2.4，沿用 light-run-v2 和既有扁平传输协议。

## 验证方式

新增测试先在旧行为上取得 RED，再实现修复并取得 GREEN。隔离项目中使用真实本地检查子进程；单元测试里的需求/审查角色回执为显式模拟。独立技能前向检查使用真实案例材料，不运行模型，不修改业务项目。

| 验证 | 结果 |
|---|---|
| Python light 完整回归（含20项新修订测试） | 90 项通过，402.615 秒；日志 .test-tmp/light-repair-release.log |
| TypeScript 扩展 | 37 项通过 |
| 真实本机 HTTP 请求、结果校验与证据封存 | 通过；沙箱限制回环连接，使用获准的沙箱外测试运行 |
| 五个 SKILL 的 quick_validate | 全部通过 |
| 原生 zscode 安装与命令/技能发现 | 通过；未发送模型请求 |
| 原生 Intent/PRD 审查及提交后的提前交接 | 通过；合成业务状态，未发送模型请求 |
| 原生最终产物审查 | 通过；合成业务状态，未发送模型请求 |
| 原案例隔离升级、批准保留与派生审查准备 | 通过；未伪造新的独立审查结果 |

可复现的命令：

```powershell
python -X utf8 -B -m unittest discover -s tests -p 'test_light_*.py' -v
bun test tests/zscode-light-extension.test.ts
python -X utf8 -B tests/zscode_light_smoke.py
python -X utf8 -B tests/zscode_light_checkpoint_replay.py --project D:\InspurCode\ai-native-zscode
```

完整 Python 回归包含本机 HTTP 测试，需要允许连接 127.0.0.1；封闭沙箱的网络拒绝不应被记录为该检查通过。

## 原案例结果

原项目仍为 `LIGHT-9afc5c8df34ddce85b03b476`、revision 49、needs_attention；未修改批准、事件、测试、正式规格或安装副本。

隔离副本的升级保留原 run 和批准，恢复后可以通过正确 spec 草稿加已登记计划生成 derived 审查任务。当前磁盘上的 plan 草稿仍是另一套 dashboard/* 布局；它与修正后的 spec 草稿同时提交会被新校验正确拒绝，不能盲目复用。

最终隔离探针结果保存在 `.test-tmp/checkpoint-replay-hic4gh0g/.aidevflow/checkpoint-replay-result.json`。它证明升级与恢复入口可执行，不代表新原生角色已经审查或看板已经交付。

## 保留边界

- 本次未执行真实模型端到端验收，也未对业务看板进行浏览器产品验收；未模拟通过来替代它们。
- 实际 PRD 的“无结束记录则结束时间为空”与派生规格的“取最后记录时间”存在业务含义差异。真实恢复时需要遵守原批准语义，或取得用户对新语义的审查；不能通过路径修复顺便覆盖该决定。
- 已接受 RED 后的业务范围变更暂时保守停止，不提供自动清空基线重开需求的捷径。普通技术修订已有专用通道。
- 修订保持任务身份、顺序与验收关联；不自动删除/增加执行中的任务，也不在未声明文件外任意编辑。
- shell/runner 约束属于流程保护，不是操作系统沙箱。物理截图存在并不独自证明界面符合需求，仍需独立审查与实际产品验收。

## 后续升级原项目

本次只交付源码和本地验证结果。后续先关闭旧 zscode，确认无在途执行，再执行：

```powershell
python D:\InspurCode\ai-native-autonomous\scripts\aidevflow.py --root D:\InspurCode\ai-native-zscode install --runtime zscode --upgrade-active
```

安装器备份受管文件和检查点；重启 zscode 后使用 `/aidevflow-flow 继续`。当前业务冲突由新的停止分支处理；工程修订通过真实原生角色后才能执行。未提交、合并或发布 Git。
