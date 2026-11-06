# light 2.4.2 循环修订修复交付

日期：2026-11-06。范围：框架、安装资源、light 执行技能、本地验证，以及原测试项目的受管安装升级。

## 已完成

1. 修订内部按实际回执和候选绑定区分等待审查、在途、通过、修改、纠正和已消费状态。上下文、扩展派发与实际动作使用同一判定；同一已通过候选直接执行 run/repair_apply，不再反复派发。
2. 候选摘要不包含 reason、草稿文件名、角色调用ID或耗时。相同候选复用审查；相同失败预检返回已有证据；新内容经预检后才生成新审查包。
3. 隔离 RED 预检先于修订审查。成功、失败、超时和无法启动都保存证据；应用时仍检查当前绑定并重放。重放失败和候选过期进入可纠正分支，允许修改后重提；事务中断继续恢复原事务。
4. 基线审查的动态严格输出契约由宿主内部 baseline-assignment 注入，绑定 run、bundle、验收ID和路径。公开模型上下文保持最小参数；无效输出得到具体格式错误，实际通过回执固定哈希。
5. 扩展阻止不应派发的任务，也跳过这些被阻止调用的伪结果回执，避免“重复审查被拦截后又报缺少派发锚点”。
6. 角色调用ID、重复approve、无效输出、修订子阶段切换和同任务后续候选不再无限刷新预算；有效原生进展按工作段记录一次。原生等待仍排除活跃计时。
7. status 优先显示最新修订失败证据和可执行动作。尚未取得有效 RED 的准备错误可以受控 retry，只开放当前组测试，保留先前工作组的冻结基线。
8. 旧循环停点可以根据不可变历史中的真实 RED 重放失败迁移到 correction_required。迁移前检查文档、运行证据和冻结文件完整性，不覆盖越过 Gate 的修改，不伪造审批或结束未知在途执行。

保留原有 160 次主模型请求、256 次读取、3 小时及各失败预算；没有继续放大预算。Intent/PRD 与最终产物的人工审查保留。

## 验证

| 检查 | 结果 |
| --- | --- |
| 新增核心恢复测试在旧实现上运行 | RED：7 项运行，按预期失败 |
| 后续边界用例 | 已分别复现并修正过期候选、严格契约、受控重试、新候选刷新预算、迁移完整性和多工作组恢复指引 |
| 完整 Python light 回归，7 个模块分三组隔离运行 | 116 项全部通过：47 + 33 + 36 |
| 最后一处多工作组恢复指引调整后的生命周期复验 | 14 项全部通过，含新增用例 |
| 相关初始审查、派生修订及恢复指引复验 | 3 项全部通过 |
| TypeScript 扩展，含被拦截调用的结果处理 | 38 项通过，0 失败 |
| 实际安装后的 Python Bridge 协议 | 严格宿主契约、在途等待、重复派发拦截及真实 RED 推进均通过 |
| 本机 zscode 命令/技能发现 | 通过；没有发送模型提示 |
| zsspec-apply 的 skill quick_validate | 通过 |
| 真实 server 历史停点隔离升级 | 迁移到 correction_required，新候选得到 14 项目标断言失败的 RED 预检，生成新审查包 |

完整本地恢复测试包含：审查通过 → 注入真实子进程重放故障 → 保存证据 → 修正重提 → 新审查 → RED/GREEN → 全量回归 → 独立验收 → 用户接受 → 归档。测试中的角色回执和用户交互均为明确的测试夹具；检查子进程真实执行。

隔离 server 回放仅准备新审查任务，没有为新候选伪造 approve，也不表示原业务 server 已实现。预检的 14 项失败是预期的 RED，仍需实际角色检查测试语义。

主要记录在框架 `.test-tmp`：

- `lifecycle-release-a.log`、`lifecycle-release-b.log`、`lifecycle-release-c.log`
- `lifecycle-final-recovery.log`、`lifecycle-final-guidance.log`
- `lifecycle-typescript-verified.log`、`lifecycle-native-discovery-verified.log`
- `lifecycle-server-result.json`、`lifecycle-transport-result.json`
- `lifecycle-original-upgrade.json`

## 原测试项目已升级

`D:\InspurCode\ai-native-zscode` 已安装 2.4.2，原 run 不变。原 revision 210 迁移到 211，仍为 repairing，但恢复阶段已变为 correction_required，允许 repair_propose 或当前未接受 RED 的受控 retry；不再返回旧包的审查派发任务。

升级检查确认：Intent/PRD 和批准不变、任务结果和冻结基线不变、16 个业务文件哈希不变。最终源码与原项目安装的六个关键后端模块、扩展 index.ts 字节一致。旧安装、配置和检查点已备份；路径保存在 `lifecycle-original-upgrade.json`，首次升级备份为 `.aidevflow/light-runs/LIGHT-9afc5c8df34ddce85b03b476/upgrades/5a46121b0a4adc3e73196f94`。

没有自动启动模型、代写新独立审查或恢复未知执行。真实模型端到端演练仍待继续原会话验证。

## 现在如何继续

1. 完全退出旧 zscode 实例，再打开 `D:\InspurCode\ai-native-zscode` 和原会话，确保加载新扩展。
2. 执行 `/aidevflow-flow 继续`。当前应先根据最新失败证据修正/重提 server 测试候选，然后审查、应用，进入 server GREEN 和后续回归。
3. 不需要新建项目、删除状态或重新确认同一 Intent/PRD。真实审查若提出修改，应改候选后再审，而非反复批准同一包。

可选检查命令：

```powershell
python D:\InspurCode\ai-native-zscode\scripts\aidevflow.py --root D:\InspurCode\ai-native-zscode status
```

后续复现完整本地回归：

```powershell
Set-Location D:\InspurCode\ai-native-autonomous
python -X utf8 -B -m unittest discover -s tests -p 'test_light_*.py' -v
bun test tests/zscode-light-extension.test.ts
```

当前测试集合较完整回归时新增了一项多工作组恢复指引用例，该用例与相关生命周期/指引用例已在最终修改后复验。HTTP 用例需允许连接本机 127.0.0.1。
