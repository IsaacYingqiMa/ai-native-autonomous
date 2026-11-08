# 新文件夹安装与首次任务测试（Windows / OMP）

适用当前工作区的 AIDevFlow 和含 `zsspec-plan` 的方法包。OMP 的适配验证基线为 18.13.2。本教程创建独立测试项目，启用需求人工确认，完成一个 Python 标准库加法任务。

`python`、`git` 等命令在 PowerShell 执行；`/aidevflow-*` 命令在 OMP 对话框输入。代码框中的路径按实际修改。不要将业务项目建立在框架源码内部。

## 1. 检查环境

```powershell
python --version
git --version
omp --version
```

需要 Python 3.11+、Git、已配置可用模型的 OMP。真实小任务会调用模型，需确保额度可用。框架本身无需第三方 Python 包；本次任务也不需要 Node、数据库、Playwright 或 Bun。

已有 OMP 配置可直接使用。若尚未验证模型，先在普通 OMP 会话发送一句简单对话，确认模型可响应，再退出。

## 2. 创建全新的业务项目

在同一个 PowerShell 窗口按顺序执行。下面的业务目录应尚不存在；如果已经存在，请换一个新名称。

```powershell
$AidevflowSource = 'D:\inspur\code\spec-driven\ai-native-autonomous'
$AidevflowProject = 'D:\Work\aidevflow-first-run'

if (-not (Test-Path -LiteralPath (Join-Path $AidevflowSource 'scripts\aidevflow.py'))) {
  throw '框架源码路径不正确'
}
if (Test-Path -LiteralPath $AidevflowProject) {
  throw '目标目录已存在，请换一个新的业务目录'
}

New-Item -ItemType Directory -Path $AidevflowProject -Force
Set-Location $AidevflowProject
Set-Content -LiteralPath README.md -Value '# AIDevFlow First Run' -Encoding utf8
New-Item -ItemType Directory -Path specs -Force
@'
test:
  e2e: disabled
  manual: disabled
'@ | Set-Content -LiteralPath specs/zsspec.yml -Encoding utf8

git init
git add README.md specs/zsspec.yml
git commit -m "Initialize AIDevFlow test project"
if ($LASTEXITCODE -ne 0) {
  throw '初始提交失败，请处理 Git 错误后重试提交，再继续下一步'
}
```

本次是无界面的模块测试，所以在任务开始前关闭 e2e/manual。静态审查、真实自动化测试和独立验收仍然需要执行，Gate 仍要求 strict。

若 Git 提示缺少身份，只在这个项目设置自己的真实信息，然后重试提交：

```powershell
git config user.name '你的实际姓名'
git config user.email '你的实际邮箱'
git commit -m "Initialize AIDevFlow test project"
```

确认初始提交成功：

```powershell
git log -1 --oneline
```

Git 只需本地使用，无需创建远程仓库。

## 3. 复制完整新版 zsspec

当前本机框架的 `.claude/skills` 中有方法包。安装器不会自动复制它；如果源码来自重新克隆的仓库，请另行取得完整方法包，将 `$MethodSource` 改成实际来源。

```powershell
$MethodSource = Join-Path $AidevflowSource '.claude\skills'
$MethodTarget = Join-Path $AidevflowProject '.omp\skills'
$MethodNames = @(
  'README.md',
  'zsspec-req', 'zsspec-plan', 'zsspec-test',
  'zsspec-apply', 'zsspec-done', 'zsspec-change',
  'zsspec-init', 'zsspec-standard', 'zsspec-feedback'
)
# 若分发包提供额外隐藏资源，一并保留；当前核心适配不要求 .skillhub 存在。
if (Test-Path -LiteralPath (Join-Path $MethodSource '.skillhub')) {
  $MethodNames += '.skillhub'
}

foreach ($MethodName in $MethodNames) {
  if (-not (Test-Path -LiteralPath (Join-Path $MethodSource $MethodName))) {
    throw "方法源缺少：$MethodName"
  }
  if (Test-Path -LiteralPath (Join-Path $MethodTarget $MethodName)) {
    throw "目标已存在，请核对后再继续：$MethodName"
  }
}
New-Item -ItemType Directory -Path $MethodTarget -Force
foreach ($MethodName in $MethodNames) {
  Copy-Item -LiteralPath (Join-Path $MethodSource $MethodName) -Destination $MethodTarget -Recurse -Force
}
```

目录内的阶段说明、模板、references 和支持脚本应完整保留。清单有意排除维护技能 `zsspec-doctor`。

## 4. 安装 AIDevFlow

保持当前目录为业务项目根目录：

```powershell
python "$AidevflowSource\scripts\aidevflow.py" install --workflow-profile reviewed-discovery-v1
```

这里只显式传一个参数，便于本次测试原生需求审查。其余默认值是：

| 项目 | 行为 |
| --- | --- |
| 安装目标 | 当前目录 |
| 执行器 | omp |
| 方法目录 | 自动选中唯一候选 `.omp/skills` |
| Python | 固定本次安装所使用的解释器 |
| 新任务方法 | 当前包对应 `zsspec-managed-v2`，由生成的 Contract 和 Gate 校验确定 |

如果只想按默认需求流程安装，命令是 `python "$AidevflowSource\scripts\aidevflow.py" install`。但它使用 `legacy`，不会出现本教程后续的独立 Intent/PRD 人工审查窗口。不要在创建任务后再切换流程配置。

安装只准备套件，不开始业务任务。检查：

```powershell
Get-Content .aidevflow/project.json
python scripts/aidevflow.py doctor
python scripts/aidevflow.py method-inspect
```

配置应包含 `runtime: omp`、`workflow_profile: reviewed-discovery-v1`、`skills_root: .omp/skills`。doctor 应无缺失角色、方法或安装摘要错误，`readiness.initial_commit` 应为 true。首次没有活动任务正常；本次未安装浏览器也正常。method-inspect 应能识别当前 zsspec 适配，且不会创建业务任务。

## 5. 启动 OMP，提交简单任务

从业务项目根目录启动：

```powershell
omp
```

在 OMP 对话框先输入：

```text
/aidevflow-doctor
```

确认项目扩展、需求分析角色和 read/bash/task 可用。再输入下面的完整需求：

```text
/aidevflow-flow 请为当前空项目开发一个 Python 标准库整数加法模块。

业务需求：
1. zsspec 模块名固定为 adder，代码文件为 src/adder.py。
2. 提供 add(a, b)，合法整数输入返回精确的相加结果。
3. 两个参数均不接受 bool、float、str、None 等非整数类型；非法输入抛出 TypeError。
4. 验收覆盖正数、负数、零、大整数，以及任意一个参数非法的情况。
5. 使用 unittest，测试放在 tests 下，通过 python -m unittest discover -s tests -p "test_*.py" -v 运行。
6. README 给出调用方式和实际测试命令。不使用第三方依赖、网页、数据库或外部接口。

先生成 Intent、PRD 和验收场景，等我在原生审查交互中明确确认后再开发。
新任务使用 zsspec-managed-v2，串行执行，遵守现有 specs/zsspec.yml。
确认后完成 Contract、REQ/DES/TASK/CHK、AI 审查、测试冻结、有效 Red、实现、Green/回归与独立 Verifier 验收。
新模块尚不存在时，可以先以明确的入口存在性断言产生 Red；模块存在后正常导入并验证行为，不吞掉导入或环境异常。
普通设计和测试代码由 AI 审查并留证，重大范围或验收变化向我确认。
不要自动提交、合并、部署，不要修改套件、方法包或清除历史。
最终请提供完成内容、实际测试结果、Gate 状态及用户验收方法。
```

直接在对话中回答必要业务问题。不要改用 `/skill:aidevflow-flow`；后者只加载说明，不能代替原生审查入口。首次无需额外开启 `/goal`。

## 6. 验证暂停与需求确认

草稿就绪后，阅读 Intent、PRD、验收场景；预览截断时打开列出的完整文件。

首次测试建议选择“保存并暂停”，验证未开始实现。随后输入：

```text
/aidevflow-flow
```

应恢复同一个需求讨论并重新展示审查。选择“确认并开始自主开发”后，协调者应继续工程流程。需要修改需求时选择“提出修改意见”；修订版本需要重新审查。

普通阶段无需手动输入内部 Gate 命令。常规失败在预算内修复；出现权限、环境、重大业务决策或完整性阻断时按具体原因处理。

## 7. 查看进度和恢复任务

可打开另一个 PowerShell；新窗口不共享前面定义的变量，直接进入业务目录：

```powershell
Set-Location D:\Work\aidevflow-first-run
python scripts/aidevflow.py status
```

| 项目 | 应观察到的结果 |
| --- | --- |
| 方法 | 当前 Contract 为 zsspec-managed-v2 |
| Design | 有规格、实际 AI 审查、冻结测试和有效 Red |
| Build | 工作项完成，真实 Green/回归及必需检查满足 |
| Verify | 独立角色读取固定 bundle，登记当前有效回执 |
| 交付 | current_stage 为 delivery_ready，verdict_current 为 true |

`active` 不证明仍有模型执行；也要检查 `continuation.attention_required`、`agent_invocations`、`blocker`、待完成检查和 `next_actions`。`status --summary` 不验证候选新鲜度，不能作为最终验收依据。

退出或中断后，先确认旧执行结束，再从同一项目启动 OMP 并输入 `/aidevflow-flow`。它应恢复原任务。若为 needs_human，先让协调者解释 blocker 和具体恢复方案；不能仅凭普通“继续”清除。不要删除活动指针、改摘要或重建任务来绕过错误。

## 8. 亲自运行测试与业务验收

到达 delivery_ready 后，在业务项目 PowerShell 运行：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python -c "from src.adder import add; print(add(2, 3)); print(add(-2, 1)); print(add(0, 0))"
```

第二条命令应依次输出 5、-1、0。测试应真正执行用例并通过；`Ran 0 tests` 不算成功。检查大整数和非法输入用例确实存在。

以下额外调用应抛出 TypeError，命令返回非零是此项验证的预期：

```powershell
python -c "from src.adder import add; add(True, 1)"
```

这些调用用于用户复核；Gate 的正式执行证据仍应由协调者通过 run-tests 收集。如果复核发现问题，反馈给协调者修复并重新验收，暂不执行 accept。

满意后，在 OMP 对话框输入：

```text
/aidevflow-accept adder
```

该命令在技术交付和独立验证之后登记实际业务验收，并通过原生 close 收口。它不提交代码或发布应用。

## 9. 归档并开始下一任务

保存交付记录，停止写入会话，再在业务项目 PowerShell 执行：

```powershell
python scripts/aidevflow.py archive
```

归档保留历史并关闭活动指针。下次从同一个业务项目启动 OMP，使用 `/aidevflow-flow <下一项需求>`。

## 10. 本次测试的通过标准

- 安装与原生 doctor 无阻断错误，新任务正确选择 v2。
- 需求未经批准不开发；暂停后恢复同一讨论，无重复任务。
- 批准后生成规格与工作计划，完成冻结、有效 Red、真实 Green/回归。
- 独立 Verifier 通过，完整 status 的 verdict_current 为 true。
- 用户实际运行的测试与调用符合需求，之后 accept 成功。
- 归档后历史仍在，可开始下一任务。

这是一次真实模型的小型模块试点，不覆盖前端浏览器、数据库或复杂企业集成。可选的无模型框架测试在源码目录执行：

```powershell
Set-Location D:\inspur\code\spec-driven\ai-native-autonomous
python -B -m unittest discover -s tests -p test_method_v2.py -v
python -B tests/omp_native_smoke.py
python -B tests/omp_review_smoke.py
```

原生探针使用临时项目，不代表业务验收。维护 TypeScript 扩展时另可执行 `bun test tests/omp-extension.test.ts`；业务使用无需安装 Bun。

更多边界见 [当前 zsspec 托管说明](ZSSPEC-MANAGED.md)、[完整使用指南](AI-AUTONOMY-GUIDE.md)、[恢复指南](.claude/skills/aidevflow-flow/references/recovery.md)。
