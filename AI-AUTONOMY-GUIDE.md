# AI 自主套件完整使用教程（Windows / omp）

当前含 `zsspec-plan` 的方法包使用 `zsspec-managed-v2`，完整流程、版本隔离与恢复见 [当前 zsspec 适配说明](ZSSPEC-MANAGED.md)。旧任务保持原规则。

适用：当前工作区的 AIDevFlow、omp 18.13.2、`reviewed-discovery-v1`。核对日期：2026-11-01。

这份教程把一次性安装和每天使用分开。PowerShell 是执行安装命令的终端；omp 是你与 AI 对话的界面。`python ...`、`git ...` 在 PowerShell 输入；`/aidevflow-flow` 等斜杠命令在 omp 输入。复制代码框里的命令，不复制代码框标记。

## 1. 先理解现在能做什么

```text
你提出想法 → AI 探究需求、提出问题 → Intent / PRD / 验收场景
                                      ↓
                             你明确确认具体版本
                                      ↓
Contract → Design → 冻结测试与规格 → Build → 真实检查 → 独立 Verify
                                      ↑                 ↓
                                      └── 有限修复 ─────┘
                                               ↓
                                    delivery_ready → 你验收
```

- 你负责决定业务目标、关键取舍、需求是否正确，以及最后是否发布。
- AI 负责分析、计划、实现、测试、普通修复和交付说明；Gate 管理状态、证据和阶段门禁。
- 有预算与权限边界的自主执行已经实现。它会在需求决策、保护阻断、缺少环境、修复预算耗尽等情况下停下。
- 当前为单项目、单活动任务、单协调者、串行工作项；续跑有上限，不能承诺无限运行。
- 讨论记录和审批版本会保留；跨任务持续学习的纠错记忆库、通用自动诊断恢复、产品前端尚未完成。
- 本地保护用于减少误操作，不能替代企业身份认证或操作系统权限隔离。真实模型的新需求确认全流程与复杂生产业务仍需试点验收，不能把自动化测试数量当成业务完成度。

## 2. 一次性准备环境

打开 Windows 的“终端”或 PowerShell，逐条执行：

```powershell
python --version
git --version
omp --version
```

需要 Python 3.11 或更高、Git，以及配置好模型的 omp。本机本次检查为 Python 3.13.5、Git 2.43.0、omp 18.13.2。框架本身不需要额外的 Python 包；使用 omp 二进制也不需要为本套件安装 Bun。

如果提示找不到某条命令，先完成该软件安装，再重新打开终端：

- Python：[官方 Windows 下载](https://www.python.org/downloads/windows/)，安装时启用 PATH 选项。
- Git：[官方 Windows 下载](https://git-scm.com/download/win)。
- omp：[官方安装说明](https://github.com/can1357/oh-my-pi/blob/v18.13.2/README.md#install)。本套件验证基线是 18.13.2，新版本应先验证适配。已有可用基线时无需重装。

先单独运行 `omp`，按你所用供应商完成模型登录或 API 配置，并测试一句普通对话。支持登录的供应商可使用 `/login`，选择模型可使用 `/model`；供应商的账号、费用、配额由其配置决定。不要把密钥写进需求文档。具体机制见 [omp 模型与认证说明](https://github.com/can1357/oh-my-pi/blob/v18.13.2/README.md)。

## 3. 准备业务项目，和套件源码分开

套件源码目录是 `D:\inspur\code\spec-driven\ai-native-autonomous`。业务代码放在另一个目录，例如 `D:\Work\aidevflow-pilot`。

以后每次打开新的 PowerShell 窗口，先设置这两个变量；业务项目路径按实际修改：

```powershell
$AidevflowSource = 'D:\inspur\code\spec-driven\ai-native-autonomous'
$AidevflowProject = 'D:\Work\aidevflow-pilot'
```

已有 Git 项目：直接把第二个变量改成项目根目录，保留已有文件与提交，跳过下面的新建步骤。先保存好现有工作，避免把历史改动混进本轮交付。

只有新项目才执行：

```powershell
New-Item -ItemType Directory -Path $AidevflowProject -Force
Set-Location $AidevflowProject
git init
Set-Content -LiteralPath README.md -Value '# AIDevFlow Pilot' -Encoding utf8
git add README.md
git commit -m "Initialize pilot project"
```

如果提交提示缺少姓名和邮箱，在这个项目设置你自己的真实信息，再重试提交：

```powershell
git config user.name '你的实际姓名'
git config user.email '你的实际邮箱'
git commit -m "Initialize pilot project"
```

然后检查：

```powershell
git rev-parse --show-toplevel
git log -1 --oneline
```

应显示业务项目根目录和至少一个提交。没有初始提交，不能开始任务。

## 4. 安装套件，并明确启用需求确认

先关闭正在这个业务项目中执行的 omp 会话。在 PowerShell 运行：

```powershell
python -B "$AidevflowSource\scripts\aidevflow.py" --root "$AidevflowProject" install --runtime omp --skills-root .omp/skills --workflow-profile reviewed-discovery-v1
```

这条命令把运行时、项目级扩展、角色、流程 Skill 和管理脚本安装到业务项目。它不启动模型、不创建业务任务、不自动安装业务依赖，也不修改你的全局 omp 配置。

`--workflow-profile reviewed-discovery-v1` 是关键：省略它的新项目使用旧流程。旧项目已有活动任务或需求讨论时，不能直接切换 profile；应先按原流程完成或受控关闭当前上下文。

核对安装配置：

```powershell
Set-Location $AidevflowProject
Get-Content .aidevflow/project.json
```

应包含 `runtime: omp`、`workflow_profile: reviewed-discovery-v1` 和 `skills_root: .omp/skills`。配置是 JSON 展示，不需要手工修改。

## 5. 准备完整的方法包

套件负责流程控制；zsspec 方法包提供规格、设计、测试与实现方法。只安装套件还不够。

如果业务项目已使用完整的 `.omp/skills` 方法包，保留原包并跳过复制。已有包在 `.claude/skills` 时，可在安装命令选择 `--skills-root .claude/skills`，不要随意移动活动任务的方法来源。

对于全新的试点目录，当前源码的 `.claude/skills` 中已有完整方法包，可用下面的明确清单复制。它不会覆盖已有同名目标；遇到冲突请先核对已有安装。

```powershell
$MethodSource = Join-Path $AidevflowSource '.claude\skills'
$MethodTarget = Join-Path $AidevflowProject '.omp\skills'
$MethodNames = @(
  'README.md',
  'zsspec-req', 'zsspec-plan', 'zsspec-test', 'zsspec-apply', 'zsspec-done',
  'zsspec-change', 'zsspec-init', 'zsspec-standard', 'zsspec-feedback'
)
if (Test-Path -LiteralPath (Join-Path $MethodSource '.skillhub')) {
  $MethodNames += '.skillhub'
}
foreach ($MethodName in $MethodNames) {
  if (-not (Test-Path -LiteralPath (Join-Path $MethodSource $MethodName))) {
    throw "方法源缺少：$MethodName"
  }
  if (Test-Path -LiteralPath (Join-Path $MethodTarget $MethodName)) {
    throw "目标已存在，请先核对：$MethodName"
  }
}
New-Item -ItemType Directory -Path $MethodTarget -Force
foreach ($MethodName in $MethodNames) {
  Copy-Item -LiteralPath (Join-Path $MethodSource $MethodName) -Destination $MethodTarget -Recurse -Force
}
```

保留目录内的 references、模板、脚本和隐藏资源。不要使用通配符把 `zsspec-doctor` 一起复制进业务项目；它是维护技能，受管任务不允许加载它。

## 6. 做安装检查

在业务项目的 PowerShell 运行：

```powershell
Set-Location $AidevflowProject
python -B scripts/aidevflow.py doctor
```

重点看 `ok`、`missing_hooks`、`missing_roles`、`manifest_mismatches`、`method_errors`、`readiness.initial_commit` 和 `next_actions`。缺失角色、摘要错误、方法包缺失等问题，先按提示处理。首次没有活动任务是正常情况。

再从这个目录启动 omp：

```powershell
omp
```

在 omp 里输入：

```text
/aidevflow-doctor
```

确认当前会话真正启用了 `read`、`bash`、`task`、项目扩展和需求角色。扩展未加载时，关闭后从业务项目根重新启动。

doctor 通过只代表配置检查通过。网页项目仍需准备 Node、依赖、数据库、服务、浏览器等实际运行环境；这些不会由套件安装器一并准备。第一次可以选 Python 标准库小任务，先验证流程。

## 7. 从统一入口提出需求

下面开始的命令都在 omp 对话框输入。首次建议复制这个小型业务示例，再按自己的业务修改：

```text
/aidevflow-flow 为企业做一个费用报销规则校验模块，先讨论清楚需求并生成 Intent、PRD、验收场景，等我在审查交互中确认后再开发。
这是 Python 标准库试点，不接真实财务系统，不做网页、登录或数据库。
员工提交报销金额和是否有发票；金额必须大于零且最多两位小数。
有发票且金额不超过 5000 元时允许提交，否则返回明确原因。
金额 5000 元可以提交，5000.01 元不可以；非法金额不能崩溃。
实现可导入的模块，提供使用示例和能实际运行的 unittest。
请确认金额输入形式、返回结果和异常处理等剩余业务问题，再让我审查。
确认后执行规划、设计、测试、实现、修复和独立验收，遵守 Gate。
普通技术细节由你决定，重大业务取舍和需求变更交给我。
不要自动提交、合并或部署；不要清除历史或修改套件保护。
```

规则只是演示，不代表你公司的财务制度。你也可以提供项目内的 UTF-8 需求文件：

```text
/aidevflow-flow docs/request.md
```

用文件时直接给路径即可。新流程会把需求保存为受管快照；普通用户不必写 JSON、手工建 Contract、复制内部任务 ID 或文件摘要。

不要用 `/skill:aidevflow-flow` 代替此入口：那只是加载方法说明，不能代替扩展创建需求讨论和人工审查。

## 8. 回答问题，然后审查需求

AI 会探究需求并提问。你可以一次回答多个问题，例如：

```text
金额输入统一是十进制字符串；最多两位小数，不接受科学计数法。
是否有发票只接受布尔值。错误输入返回结构化失败结果，不抛给调用者。
不要做自动取整，也不要增加审批流。
请记录这些回答，更新需求后再让我确认。
```

普通回答可直接在当前对话发送。若已经退出或暂停，用统一入口附带新意见：

```text
/aidevflow-flow 补充：没有发票时必须返回“缺少发票”，请更新 PRD 后重新让我确认。
```

草稿就绪后会出现审查交互，包含 Intent、PRD、验收场景和完整文件路径。逐项检查：目标用户、范围、不做什么、角色权限、正常和失败路径、边界值、完成标准。预览较长时会截断，要打开给出的完整文件检查，不要只看摘要。

这里的交互是 omp 内的原生选择框，不是浏览器页面。材料显示在对话记录中，选择框使用短标题；自动审查在停止回调结束后独立等待，人工阅读超过 30 秒不会因停止回调超时而取消。升级扩展后应重启 omp，再从原入口恢复审查。

| 选择 | 系统行为 |
| --- | --- |
| 确认并开始自主开发 | 批准当前版本并自动进入开发，无需再发第二条启动命令 |
| 提出修改意见 | 保存反馈并继续需求分析，新版本必须重新确认 |
| 保存并暂停 / 取消窗口 | 保存检查点并停止，稍后从统一入口恢复 |

推荐选项或一句普通“继续”不等于需求批准。只有明确的批准动作才允许进入交付。

如果只想形成需求、暂时不开发，可以从 `/aidevflow-discover <想法>` 开始。其确认选项是“确认，仅保存需求”；将来真正准备开发时，再输入 `/aidevflow-flow`。已有交付任务时，讨论专用入口不会恢复开发。

## 9. 确认后，让 AI 连续执行

确认后 AI 应继续形成 Contract、设计、测试和工作计划，完成实现与真实检查，并交给只读 Verifier 验收。普通步骤无需你逐条输入内部命令。

默认使用框架的 `bounded_stop` 续跑；它有防空转和次数限制，且受 omp 原生限制。首次使用无需额外开启 `/goal`。等待你回答、权限不足、环境不可用、状态损坏、保护阻断或预算用尽时，停止是正确行为。

如果 AI 在工作没完成时结束，并且没有正在运行的角色任务：先查看状态，再用 `/aidevflow-flow` 续跑；不要反复同时发送“继续”，避免打断原生任务。

高级用法：用户明确选择由原生 Goal 续跑时，在已有交付任务中将 Gate 设置为 `continuation --owner native`，并按 omp 的实际 Goal 机制操作。这个设置本身不会创建或恢复 Goal。原生 Goal 与框架循环只选一个所有者；暂停的原生 Goal 不会被本套件擅自恢复。

## 10. 暂停、关闭和恢复

需求阶段：在审查交互选择“保存并暂停”。开发阶段：使用 omp 的中断操作停止当前执行；具体按键以当前界面提示为准。普通消息“暂停”可能排队，不应把它当成已经停止工具执行的确认。

重新打开 PowerShell，回到同一个项目：

```powershell
Set-Location 'D:\Work\aidevflow-pilot'
python -B scripts/aidevflow.py status
omp
```

在 omp 内输入：

```text
/aidevflow-flow
```

系统读取已有检查点。暂停在需求审查处时重新展示审查；已有交付任务时继续同一任务，不重新建任务。

| 看到的状态 | 你的处理 |
| --- | --- |
| collecting / awaiting_answers | 继续讨论或回答问题 |
| review_ready / paused | 审查当前版本，或用统一入口恢复 |
| approved | 已确认；仅保存的需求需由你用统一入口授权开始 |
| handoff_pending | 用统一入口恢复交接；不要删除事务标记 |
| intent_approved / active / retrying | 根据当前规划、设计、实现、验收检查点继续 |
| needs_human | 先看具体 blocker，明确处理决定后才能恢复 |
| continuation.attention_required | 续跑次数或无进展限制触发，检查原因再决定下一步 |
| delivery_ready | 进入业务验收；不是已经上线 |

`needs_human` 不会被一条普通“继续”自动清除。可在 omp 明确要求“先解释 blocker，给出恢复方案；经我确认后按方案继续”。基础设施故障和产品修复使用不同恢复方式；冻结基线或证据完整性问题需要具体修复方案，不要盲目运行通用重试。

需要原始状态时，在另一个 PowerShell 查看：

```powershell
python -B scripts/aidevflow.py status
```

`status --summary` 可看简要进度，但不验证候选新鲜度，不能替代完整验收。恢复缺失的原生结果由协调者结合 `recover-runtime` 和当前原生分支处理；先确认旧执行已结束，再决定是否重派。跨新会话的模型记忆不保证完整，已持久化的 Gate 状态与证据才是依据。

## 11. 完成验收并开始下一任务

让 AI 提供：完成内容、启动/使用方式、实际测试结果、未完成或例外项、关键文件和验收证据。你按已批准场景操作一遍。

在 PowerShell 查看完整状态：

```powershell
python -B scripts/aidevflow.py status
```

重点核对 `current_stage: delivery_ready`、`verdict_current: true`、检查是否全部满足，以及是否存在例外。`status` 字段可能展示 `delivery_ready_with_exceptions`，有例外不代表无条件通过。证据发生变化时不能只凭旧完成消息验收。

满意后，由你决定是否提交、合并、部署；套件到达 `delivery_ready` 不会自动完成这些操作。

确认交付记录已保存后，关闭写入会话，在业务项目 PowerShell 归档：

```powershell
python -B scripts/aidevflow.py archive
```

归档保留任务历史并关闭活动指针。`needs_human` 也可由操作者明确归档，但属于停止并保留现场，不等于交付成功。随后重新启动 omp，用 `/aidevflow-flow <下一项需求>` 创建下一任务。

## 12. 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 找不到 `/aidevflow-flow` | 确认安装到业务项目，在该目录重启 omp，再运行 `/aidevflow-doctor` |
| 直接走旧 Intent 流程，没有新审查 | 检查 project.json 的 profile；不要在活动任务中强行切换 |
| method_errors / 缺少 zsspec | 核对实际 skills_root、根 README、隐藏资源和完整支持文件 |
| 角色报输出或回执错误 | 让协调者按实际错误纠正并重新派发；不要手改角色输出或伪造结果 |
| 没有 UI 不能确认 | 在支持原生确认交互的 omp 会话继续；不能默认同意 |
| 预算耗尽或环境安装失败 | 说明具体原因，准备环境或明确授权后再恢复；不要无限重试 |
| 提示已有协调者 | 确认另一个会话是否仍在运行；结束旧会话后再恢复，不删锁文件 |
| 批准后想增加一个新模块 | 这是范围变化；当前 M1 不支持交付中随意重写批准基线，先停下制定受审查的后续方案 |
| 希望以后永远记住这次纠正 | 当前可保留本任务的回答与反馈，但跨任务自动纠错库尚未实现 |

不要删除活动指针、交接事务或历史日志来“解锁”；不要修改冻结测试让检查变绿，也不要用空 Skill 文件骗过 doctor。

## 13. 数据、升级和团队试点

需求版本、反馈、批准保存在 `.aidevflow/discoveries/`；交付状态与证据在 `.aidevflow/runs/` 等目录。这些运行数据多数被 Git 忽略：只推送代码不能完整迁移运行现场，换电脑或升级前需单独备份。

升级前停止所有写入会话，保存业务改动并备份运行数据；重装相同后端与 profile，重启后 doctor/status。v2 任务使用独立方法源快照，更新外部技能不切换该任务的方法；旧 v1 任务仍禁止替换其固定方法包。运行时变化可能要求重新收集检查证据。

首次团队试点记录：总耗时、模型费用、人工决策次数、人工介入分钟数、需求返工次数、验收通过率、恢复是否成功。先用小任务验证，再逐步增加项目复杂度。是否达到“80% 完成度”，应按事先约定的业务验收清单统计，不能按文件数量或 AI 自报比例统计。

当前验证范围和限制见 [需求确认流程与复查记录](REVIEWED-DISCOVERY.md)、[OMP 兼容性记录](OMP-COMPATIBILITY.md)。旧适配曾完成小型真实模型演练，不能代替新增需求审查流程的真实复杂业务验收。

## 14. 本教程的实际核对记录（2026-11-01）

- 重新检查统一入口、人工确认、交接、续跑上限、人工阻断、归档及恢复的代码路径；完整设计中的纠错记忆库与通用自动恢复仍未实现。
- 需求专项 33 项通过；omp 扩展 33 项通过；真实 omp RPC 审查窗口与持久暂停探针通过，未调用模型。
- 在新临时 Git 项目实际安装 `reviewed-discovery-v1`，复制教程清单中的完整真实方法包：doctor.ok=true，method_errors、missing_roles、manifest_mismatches 均为空。
- 本次未重新运行全部 Python 回归，也未运行真实模型的新流程完整业务验收。安装验证不计为业务交付验证。
- 本地核对材料：`.test-tmp/usage-audit-discovery.log`、`.test-tmp/guide-install-result.json`。临时安装仅用于教程核对，未替你安装或迁移正式业务项目。
