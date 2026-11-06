# light 2.4.1 预算扩展与误停修复交付

日期：2026-11-05。源码已修改并完成本地回归；业务项目 `D:\InspurCode\ai-native-zscode` 尚未升级，仍为 2.4 / revision 64。未运行真实模型端到端演练，未修改原批准或冻结测试。

## 本次完成

- 无进展段默认预算：主模型请求 160 次、研究读取 256 次、活跃时间 10800 秒（3 小时）。
- 同类操作拒绝第三次返回必须调整策略的纠错指引；硬上限为同类连续 12 次、同段累计 30 次。
- 单任务执行失败上限 20 次，基线审查未通过 16 次，独立验收格式/执行失败 16 次，产品回修允许 20 轮。
- 操作错误与持久化任务/基线进展绑定，在同一检查点结清旧计数并保存历史，修复跨组误停。提案接受只结清该 action 的连续拒绝，累计预算保留到实际推进。
- 计划冲突返回 PLAN_CONFLICT、task_id、field、current/proposed 和当前权威计划，帮助修正最小草稿。没有新增绕过原生审查的通道，也未实现新的 plan_patch 协议。
- 统一支持 progress_limits / failure_limits；新增安装参数 --extend-budgets。普通升级保留自定义额度，显式扩展只提高额度，保留更高配置。
- Node 默认 reporter 的测试通过/失败数量可正确解析；同步工具流程、安装教程和说明。

配置和不变量详见 `ZSCODE-LIGHT-BUDGET-POLICY.md`。单条验证子进程的超时仍为原执行策略；本次扩展的是自主推进和纠错预算。

## 本地验证

| 检查 | 结果 |
| --- | --- |
| 新增预算测试先运行旧实现 | 13 项运行，按预期失败，记录 budgets-red.log |
| 实现后的预算定向测试 | 13 项全部通过，49.742 秒 |
| Python light 完整回归 | 103 项全部通过，277.812 秒 |
| TypeScript 扩展回归 | 37 项通过、0 失败 |
| 原 revision 64 隔离复制、升级及纠错提案 | 通过；不派发/伪造原生审查 |
| 安装 CLI --extend-budgets 及格式检查 | 通过 |

日志保存在框架 `.test-tmp/budgets-red.log`、`budgets-green.log`、`budgets-release.log`、`budgets-typescript.log`。真实停点回放结果为 `.test-tmp/budget64-replay-result.json`。

回放确认：升级不改变 run/revision/approval/tasks/baselines；原草稿精确报告任务 1.1/1.2 的 specs 差异；只给 2.2 增补 collector 的候选可进入 repairing，返回审查任务，连续拒绝降为 0、累计拒绝仍为 2，等待真实审查和应用。被检查的原项目文件哈希没有变化。

## 在原项目测试

1. 关闭该项目旧 zscode，确保没有在途测试或原生角色。安装器有锁和在途检查，不能通过删除锁文件绕过。
2. 在 PowerShell 执行升级。可继续使用原项目，不需要新建项目或删除 session：

```powershell
python D:\InspurCode\ai-native-autonomous\scripts\aidevflow.py --root D:\InspurCode\ai-native-zscode install --runtime zscode --upgrade-active --extend-budgets
```

升级会备份旧安装、配置和活动检查点。输出应含 providers=160、reads=256、seconds=10800 及 failure_limits；更高的自定义额度保留。现有停止状态保留，不在安装中自动恢复或批准。

3. 核对实际安装和额度：

```powershell
Get-Content D:\InspurCode\ai-native-zscode\.aidevflow\project.json
python D:\InspurCode\ai-native-zscode\scripts\aidevflow.py --root D:\InspurCode\ai-native-zscode status
```

配置中的 method_version 应为 2.4.1，status.autonomy_budget 应显示新额度。此时仍可能显示原 needs_attention，这是等待明确恢复的旧停止点，不代表新预算再次耗尽。

4. 重启 zscode，打开同一个项目和原会话，执行：

```text
/aidevflow-flow 继续
```

让协调者按新 flow 和 PLAN_CONFLICT 诊断处理当前旧草稿。无需手改状态、冻结测试或验收关联，也无需重新审查同一 Intent/PRD。

5. 观察实际行为：保留原运行和已完成 collector 证据；当前计划修正进入真实原生审查，通过后应用并执行 metrics 的 RED/GREEN 和回归。可恢复工程错误应继续纠错；同类第三次只给策略提示，真实阶段推进后旧操作失败不会带入新组。最终仍保留正常产品验收。

## 可选的自动回放

原项目仍停在本案例 metrics 2.1 时，可运行不会修改原项目的回放：

```powershell
Set-Location D:\InspurCode\ai-native-autonomous
python -X utf8 -B tests/zscode_light_budget_replay.py --project D:\InspurCode\ai-native-zscode
```

原项目继续后该专用案例的前置条件不再满足，应以正常运行事实验证，不强制把项目退回 revision 64。

本地完整回归命令：

```powershell
Set-Location D:\InspurCode\ai-native-autonomous
python -X utf8 -B -m unittest discover -s tests -p 'test_light_*.py' -v
bun test tests/zscode-light-extension.test.ts
```

Python 完整回归包含本机 HTTP 测试，需允许连接 127.0.0.1。测试夹具中的角色回执为明确模拟；真正产品端到端效果需在上述原项目恢复过程中验证。
