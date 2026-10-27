# Claude observer B：变更说明与完整使用教程

日期：2026-10-26。观察器版本：`claude-observer-b1`。业务可靠性协议保持 v3。

本次更新只加固 Claude Code 的派遣观测、结果关联、诊断与安装迁移。
Intent → Contract → Design → Build → Verify → delivery_ready、zsspec 方法资产、测试冻结、独立验收和业务修复预算不变。
这是 Claude 适配加固，不表示第二个 Coding Agent 已经实现。

## 1. 这次解决什么

原实现依赖先收到匹配的 SubagentStart，才能接纳后续结果。现在支持两条证明路径：

- 派遣路径：PreToolUse:Agent 固定执行身份和输入 → PostToolUse:Agent 关联实际执行 → 收到报告及完成证明。
- 原生生命周期路径：真实 SubagentStart → 报告/Stop。保留历史行为；存在待关联的同角色派遣时，必须等待准确关联，不能用生命周期绕过派遣约束。

普通前台和后台子代理均可使用。缺少 Start 时，如果派遣、结果和完成证明齐备，仍能形成回执。
前台 `completed` 可以提供完成证明；后台 `async_launched` 只表示启动；后台报告仍须结束证明。
Handback 报告优先于最终收尾文字。相同来源的冲突结果不会静默选择最新一份。

执行记录在派遣时固定 task、role、输入摘要、stage、evidence epoch，以及适用的 request/intent/contract/bundle/candidate 摘要。
Verifier 不能在结束时补绑“当前包”。重复事件去重，乱序事件持久保存后关联；不会根据角色名称猜测哪个执行返回了结果。
进程锁仅串行化观察器和回执校验；业务 Gate 仍是单任务、单协调者，不支持多个协调者并发修改状态。

未知新增字段不会单独造成拒绝；无法识别的必要身份或返回结构会留下诊断。
不会从任意文本提取 Agent ID、从聊天记录伪造回执，或收到合法 JSON 就推断执行完成。
观察失败不阻断原生工具，但 Gate 仍要求完整执行证明。写入护栏继续按原规则拒绝非法操作。

## 2. 当前支持边界

三个关键角色应使用新建的普通子代理，指定准确的 `subagent_type`、`prompt`、`description`，不设置 `name`、`resume`、`fork`、`isolation`。

| 运行方式 | 本版行为 |
|---|---|
| 普通前台 subagent | 支持 |
| 普通后台 subagent | 支持，启动与完成分开 |
| Start 缺失但父派遣、结果、完成都可关联 | 支持 |
| 事件重复或结果先于父调用返回 | 去重/暂存，等待准确关联 |
| teammate / Teams 消息 | 检测为 unsupported_mode，不能作为关键角色完成证明 |
| resume / fork / isolation 请求 | 本版关键角色不接纳，使用新的普通子代理 |
| 只有孤立 Stop/Handback | 保留诊断，不补造开始记录 |
| 旧 v1 回执 | 继续读取，不补造新协议字段 |

不修改全局 Teams 设置。即使全局开启 Teams，也应通过普通派遣执行三个关键角色。
若 Claude 实际仍选择不受支持的方式，先确认旧执行已结束或取消，再重新派遣；不要同时启动多个不确定状态的尝试。

Hook 和哈希是本机流程完整性措施，不构成操作系统沙箱或抵御同权限恶意进程的身份认证。

## 3. 在上游验证修改

打开 PowerShell：

```powershell
Set-Location "D:\InspurCode\ai-native-autonomous"
python --version
git --version
claude --version
python -B -m unittest discover -s tests -v
```

需要 Python 3.11+、Git；AIDevFlow 无第三方 Python 依赖。
完整测试包含真实本地 HTTP 请求，需要允许 Python 访问本地端口。WinError 10013 表示运行环境权限问题，应在允许本地网络的终端重跑，不跳过或模拟该检查。

```powershell
python -B scripts/aidevflow.py doctor
```

`doctor` 只读检查安装、必要 Hook、角色定义、清单哈希、Teams 提示及已有活动任务，不调用模型。
`ok: true` 表示配置检查通过，不代表真实派遣或业务任务已通过。
`installed_version` 是当前磁盘 CLI 版本；Hook 未提供运行中版本时，`running_version` 保留 null，不冒充已经升级的旧会话版本。

## 4. 更新 zdata-agent-starter 的安装副本

本轮上游修改不会自动安装到业务项目，也不会继续旧业务任务。
先结束/退出该项目正在运行的 Claude，确认没有仍在实现或验收的子代理，再更新。避免会话使用旧配置、磁盘却已替换新运行时。

可先备份控制文件。以下仅复制，不清除任何状态：

```powershell
$adfTarget = "D:\InspurCode\zdata-agent-starter"
$adfBackup = Join-Path $env:TEMP ("aidevflow-backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Path $adfBackup | Out-Null
foreach ($adfPart in @('.aidevflow', '.claude', 'scripts', '.gitignore')) {
    $adfSource = Join-Path $adfTarget $adfPart
    if (Test-Path -LiteralPath $adfSource) {
        Copy-Item -LiteralPath $adfSource -Destination $adfBackup -Recurse
    }
}
Write-Host "备份位置：$adfBackup"
```

执行安装：

```powershell
python -B "D:\InspurCode\ai-native-autonomous\scripts\aidevflow.py" `
  --root "D:\InspurCode\zdata-agent-starter" install
if ($LASTEXITCODE -ne 0) { throw "安装失败，先处理诊断，不继续业务任务。" }

Set-Location "D:\InspurCode\zdata-agent-starter"
python -B scripts/aidevflow.py doctor
if ($LASTEXITCODE -ne 0) { throw "预检未通过，请检查输出。" }
git -c core.fsmonitor=false status --short
```

安装更新 AIDevFlow runtime、角色、flow Skill、wrapper、Schema、安装清单及其管理的 Hook。
生成的旧 Hook 会被替换；同组用户 Hook 和其他设置保留，重复安装不会累积相同观察器。
手动改写过的 AIDevFlow 命令不一定能被识别为旧生成项，安装后应检查重复调用。
历史 runs、原始需求、Contract、测试证据、事件和 zsspec 固定资产不会被安装器重写。
安装会改变候选运行时身份；已有检查可能 stale，需要重新取证，不能重算旧结果摘要掩盖变化。

本项目已有 12 个 zsspec Skills，通常无需再次复制。首次安装到其他项目时，安装器仍不自动安装 zsspec。
只复制缺少的完整目录，已有方法目录应先核对，不覆盖用户定制：

```powershell
$adfMethods = "D:\InspurCode\ai-native-autonomous\.claude\skills"
Get-ChildItem -LiteralPath $adfMethods -Directory |
  Where-Object { $_.Name -like 'zsspec-*' } |
  ForEach-Object {
      $adfDestination = Join-Path '.claude\skills' $_.Name
      if (Test-Path -LiteralPath $adfDestination) {
          Write-Host "保留已有方法：$($_.Name)"
      } else {
          Copy-Item -LiteralPath $_.FullName -Destination '.claude\skills' -Recurse
      }
  }
```

按项目规则审查安装 diff 和版本管理范围，不把用户已有暂存修改混入安装提交。
重新启动 Claude Code，使 Hook、Agent 定义和 flow 说明全部生效：

```powershell
Set-Location "D:\InspurCode\zdata-agent-starter"
claude
```

## 5. 继续当前流程图沉淀任务

现有任务 ID：`ADF-20261023074901-eecd16`，更新前为 `intent / intake_started`。
先在 PowerShell 查看真实状态：

```powershell
python -B scripts/aidevflow.py status
```

在重新启动的 Claude 会话中输入：

```text
/aidevflow-flow

继续当前活动任务 ADF-20261023074901-eecd16。先执行 doctor 和 status，
依据 Gate 当前状态继续，不新建、不归档、不覆盖历史任务。

这是流程图能力沉淀任务，只为本次 flowchart 模块新增规格，其余模块先不补。
原始需求、既有用户选择和项目约定继续有效；请先核对已有记录。
聊天中已回答但尚未登记的选择，核对问题和选项后通过 Gate answer 登记；
只有解释发生实质变化时才重新询问，不把旧聊天中的说法当成已登记状态。

如仍处于 intake_started，请创建新的普通 intent analyst，
指定准确 subagent_type，不带 name/resume/fork/isolation。
使用新执行返回的原始 JSON；不要给旧草稿补造回执，也不要复用 probe-1。
登记 Intent 后继续 Planner、Design、Build、检查和独立验收。

派遣未完成时等待真实结果；遇到 unsupported_mode 或未知执行状态，
确认前次执行已结束/取消后再重新派遣，避免重复执行。
除范围、风险、验收选择和 Gate 明确要求外，按已批准流程继续。
保留最终现场，暂不 archive，不自动提交代码。
```

`intake_started` 不需要也不能机械使用 `resume-run`，直接继续 Intake。
旧探针记录保留作诊断；它不等于新 Analyst 的完成证明，无需删它才能继续。
新 Analyst 输出可能与旧草稿不同，不能用一次执行的回执登记另一份 JSON。

## 6. 新任务怎么使用

同一项目一次只运行一个活动任务。已有任务应先完成或按其合法结束条件处理，不能为测试覆盖它。
建议首次完整演练在独立 Git 项目中进行，安装同一套 runtime 和方法。

在没有活动任务时，在 Claude 中输入例如：

```text
/aidevflow-flow

按当前项目约定新增一个无外部依赖的小功能，完整验证自主研发流程。
如果不存在 workspace/tools/text_stats.py，请提供 analyze_text(text)：
char_count=len(text)，word_count=len(text.split())，line_count=len(text.splitlines())；
非字符串抛 TypeError。空串三项为 0；"a b\nc" 应返回 5、3、2。
若目标已存在，先报告现状，不覆盖。

使用标准库 unittest；业务范围限于该工具及对应测试，framework 保持只读。
默认一个工作项，按实际需求选择检查，不引入外部服务或新依赖。
真实派遣 Analyst、Planner、Verifier，关键角色使用普通新建 subagent，
不带 name/resume/fork/isolation。
按 Gate 完成合约、规格、测试冻结、有效 Red、实现、Green、回归、独立验收。
执行合约声明的方法，保留每一步证据，最终报告 task_id 和 Gate 状态。
不要修改套件、冻结基线或角色输出来伪造流程通过，不自动提交或 archive。
```

若已有需求 Markdown，可直接指明该文件，不需要手工写 Intent 或 Contract。
这个例子仅验证无外部依赖功能。真实 HTTP、数据库、容器字体或 OSS 能力要按其实际验收要求取证。

## 7. 查看执行与定位阻塞

```powershell
python -B scripts/aidevflow.py doctor
$adfState = python -B scripts/aidevflow.py status | ConvertFrom-Json
$adfState.agent_invocations |
  Select-Object role,status,provenance,agent_id,start_observed,execution_finished,output_source
$adfState.agent_observer | ConvertTo-Json -Depth 8
```

| 状态/诊断 | 含义和下一步 |
|---|---|
| dispatched | 派遣前已登记，等待实际返回/关联 |
| started | 观察到实际开始或后台启动，仍需结果与结束 |
| output_received | 有报告但没有完成证明；不能登记为完成 |
| completed | 执行完成且有 JSON；业务 Schema、摘要与版本仍须 Gate 校验 |
| missing_output | 有结束证明，没有报告；检查 Handback，必要时由原角色返回 |
| invalid_output | 返回内容不是 JSON 报告；也可能是平台在角色轮数耗尽后仅返回说明文字，先区分预算问题再决定重派，不由协调者编辑 |
| identity_conflict | 身份、输入或多份报告冲突；检查诊断，不猜测归属 |
| execution_failed | 平台报告失败/取消；确认结束后按原因重新尝试 |
| unsupported_mode | teammate、resume、fork、isolation 不受本版关键角色协议支持 |
| superseded | 早期生命周期投影已被父派遣准确关联替代；保留审计，不计为新完成 |
| unmatched_event | 事件暂未关联；父调用仍在运行时可能只是正常乱序，等待桥接 |
| dispatch_pre_missing | 没有派遣前记录；检查安装和 Hook，重启后新派遣，不追认旧输出 |

没有当前活动任务时，`status` 提示 No active；`doctor` 仍可执行。
旧 v1 回执不具备全部新字段，字段空白不表示历史从未执行。
`stale_v2_receipts` 表示当前输入已改变的历史记录，历史执行保留但不能用于当前登记。

必要时只读事件日志：

```powershell
Get-Content -LiteralPath '.aidevflow\events.jsonl' -Tail 30
```

观察器异常会记录安全诊断，不保存完整派遣 prompt。实际角色 JSON 作为结果工件保存。
不要手动编辑 `.aidevflow`、运行合成 Hook、删除失败历史或关闭回执要求。

## 8. 中断、修复与基础设施恢复

普通会话中断后重新进入同一项目，运行 `/aidevflow-flow 请先读取 status，继续当前任务`。
不要重开任务，不重复仍有效的检查。对活动执行先确认是否完成，再决定是否新派遣。

只有 Gate 已处于 needs_human，并且用户确实授权继续时，协调者才使用恢复命令：

```powershell
# 已记录的基础设施阻塞：保留业务阶段和产品修复预算
python -B scripts/aidevflow.py resume-run --mode retry --by user --utterance "实际的用户继续指令"

# 产品验收失败：增加明确的修复额度并要求新证据
python -B scripts/aidevflow.py resume-run --mode repair --by user --utterance "实际的用户继续指令" --additional-attempts 1
```

上面是命令格式，不能把占位文字当真实授权。观察器缺失不会自行把任务改成 needs_human。
Verify 中若更新运行时导致检查 stale，按 status.pending_checks 重新执行，再 prepare-verifier、重新派遣独立 Verifier。
Stop Hook 仅提示，不负责续跑、推进阶段或接受交付。

## 9. 验证记录与保证范围

- 框架及观察器最终回归：2026-10-26，70 项全部通过，完整运行 115.878 秒；包括真实本地 HTTP 检查。新增 29 项涵盖缺少 Start、乱序、重复、并发进程、默认字段展开、身份冲突、旧包、输出冲突、旧回执、只读诊断及安装迁移。
- 单测中生命周期使用可控事件夹具；它们不能替代真实 Claude 派遣。
- 本机真实 Claude Code **2.1.310** 隔离演练已完成：任务 `ADF-20261026041008-35ad1d`，Analyst（42.40 秒）、Planner（61.23 秒）、Verifier（110.10 秒）均由真实 Agent 工具派遣并返回有效回执；实际采集了父派遣、Start、Handback、Stop 和父调用返回。Handback/Stop 在父调用 PostToolUse 之前到达，之后准确合并。
- 该隔离任务完成 Intent/Contract 登记、有效 Red、Green、证据包、独立 Verifier 判决与 `delivery_ready`。代码和测试由演练脚本准备，未运行 zsspec，因此它证明角色/观测/Gate 链路，不代表 Claude 自主实现、zsspec 全流程或流程图业务验收已通过。真实 Start 缺失的恢复由夹具回归验证，未宣称真实 CLI 本次恰好漏发 Start。
- 实际记录：[演练报告](.test-tmp/claude-live-20261026-121007/rehearsal-report.json)，原始记录保留在同目录及对应本机 Claude 会话中；该临时目录不纳入 Git。此前失败尝试也保留：一次测试父工具集限制导致子代理零工具；一次 Analyst 耗尽 8 轮未交付报告。两次都没有形成业务通过，未删除后冒充首次成功。最终尝试限制必要读取范围后完成；未提高生产角色 maxTurns 或修改其职责。
- 未安装到 zdata-agent-starter，未恢复其旧任务，未执行其业务验收，未提交/推送 Git。

平台参考：[Hooks](https://code.claude.com/docs/en/hooks)、[Teams 派遣规则](https://code.claude.com/docs/en/agent-teams#how-claude-starts-agent-teams)。
后续任意破坏性更新仍可能需要适配；本次通过多来源关联和诊断减少单一事件缺失造成的误阻塞，不承诺自动兼容所有未来版本。
