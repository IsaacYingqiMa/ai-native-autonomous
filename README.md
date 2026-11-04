# AIDevFlow

当前含 `zsspec-plan` 的方法包使用 `zsspec-managed-v2`，完整流程、版本隔离与恢复见 [当前 zsspec 适配说明](ZSSPEC-MANAGED.md)。旧任务保持原规则。

AIDevFlow 是面向 AI 自主研发交付的本地控制套件。用户提供需求并回答关键业务问题，Coding Agent 完成分析、规格、实现和修复；Gate 管理任务状态、冻结测试、真实检查证据和独立验收。

**新安装默认使用 omp（oh-my-pi）**。Claude Code 保留独立适配；运行 omp 不要求安装 Claude Code。方法包支持项目级 `.omp/skills` 和 `.claude/skills`，保留供应方原文。

新项目可按下文的最简命令安装，默认使用 omp 和 `legacy` 需求流程。需要先人工确认 Intent/PRD 再自主开发时，可显式启用 `reviewed-discovery-v1`，详见后面的可选参数和 [AI 自主套件完整使用教程](AI-AUTONOMY-GUIDE.md)。

```text
需求 → Intent → Design → Build → Verify → delivery_ready
                              ↑         │
                              └─ 路由修复 ─┘
```

`delivery_ready` 不代表已经提交、合并、部署或完成生产验收。当前为单项目、单活动任务、单协调者、串行工作项；不自动迁移旧任务到另一执行器。

## 准备环境

- Python 3.11+、Git；框架运行时不依赖第三方 Python 包。
- 已配置可用模型的 omp。适配基线为 `18.13.2`，详见 [OMP 兼容性指南](OMP-COMPATIBILITY.md)。
- 目标是独立 Git 项目根目录，开始任务前已有初始提交。
- 完整项目级 zsspec v1.4 方法包，由供应方另行提供。
- 按业务准备语言运行时、数据库、服务和浏览器测试环境。

```powershell
python --version
git --version
omp --version
```

使用已安装的 omp 二进制不要求额外安装 Bun/npm 包；维护 TypeScript 适配代码时可用 Bun 运行测试。

## 新项目快速开始

从空文件夹初始化到真实模型小任务验收，可直接按 [新文件夹安装与首次任务测试](NEW-PROJECT-QUICKSTART.md) 操作，包含可复制的完整命令、暂停恢复和通过标准。

### 1. 准备业务项目和方法包

框架源码与业务项目必须分开。取得完整框架源码，不能只复制 Python 包。以下示例假设源码在 `D:\Tools\ai-native-autonomous`，业务项目在 `D:\Work\my-project`，请替换为实际路径。

业务项目须为独立 Git 仓库，开始任务前已有初始提交。已有项目保留现有工作；新空项目先按团队规范初始化并提交。

从维护者取得完整的当前 zsspec 方法包，放入业务项目的 `.omp/skills/`，包括根 README.md、references、模板、脚本与隐藏资源。保留供应方原文：

```text
zsspec-req / zsspec-plan
zsspec-test / zsspec-apply / zsspec-done / zsspec-change
zsspec-init / zsspec-standard / zsspec-feedback / .skillhub
```

已有方法包在 `.claude/skills/` 时可继续使用，无需搬迁；只有一个候选方法目录时安装器会自动识别。方法包需要另行准备，安装器不会下载、复制或升级 zsspec；本地源码中的 `zsspec-*` 目录受 Git 忽略，克隆框架不保证包含方法包。

`zsspec-doctor` 是维护技能，业务复制时显式排除，不能用 `zsspec-*` 无差别复制。框架不删除已有维护技能；doctor 报告其存在，受管任务拒绝加载它。反馈与使用上报不能作为验收证据，按实际授权配置。

### 2. 在业务项目根目录执行最简安装

以下命令在 PowerShell 执行：

```powershell
Set-Location D:\Work\my-project
python D:\Tools\ai-native-autonomous\scripts\aidevflow.py install
```

新项目不传参数时，默认安装到当前目录、使用 **omp**、自动选择项目方法目录，并使用 **`legacy` 需求流程**。`legacy` 仍会澄清阻塞问题、生成 Intent 并执行 Gate 交付流程，但不会启用独立的 Intent/PRD 原生人工审查窗口。需要该窗口时，按后文显式启用 `reviewed-discovery-v1`。

安装器不创建业务任务、不安装业务依赖、不配置 Git 身份、不修改全局 omp 配置，也不覆盖项目 `.omp/config.yml`。

### 3. 检查安装并启动 omp

```powershell
python scripts/aidevflow.py doctor
omp
```

检查 `ok`、`missing_hooks`、`missing_roles`、`manifest_mismatches`、`method_errors`。doctor 不调用模型、不派发角色；配置通过不等于真实流程通过。新项目没有 active run 正常。

`method_sources` 显示配置目录、候选目录与同名副本；`next_actions` 给出缺失路径的修复方向。`readiness.initial_commit` 必须为 true 才能创建任务。Python Playwright 包和 Chromium 分别检查：包存在不代表浏览器已安装或服务可用。在 omp 内运行 `/aidevflow-doctor` 可查看该会话实际启用的工具。

安装或重装后，从业务项目根重新启动 omp，在对话框输入：

```text
/aidevflow-doctor
```

核对扩展和角色的来源、启用状态。不要关闭扩展保护或用全局同名角色替换项目角色。

### 4. 用同一个入口开始和恢复任务

先准备可运行环境和真实测试命令；有活动任务时恢复，不创建第二个任务。新项目可以先用普通对话准备最小技术栈和空应用，此时不要启动业务 intake。

在 omp 对话框直接描述需求：

```text
/aidevflow-flow 为我的项目实现一个功能：说明业务目标、约束和验收期望。
```

也可先准备项目内 UTF-8 需求文件，再输入：

```text
/aidevflow-flow docs/request.md
```

协调者按当前配置执行需求澄清、Contract、Design、Build、Verify，普通步骤无需逐条输入内部命令。当前方法包的新任务使用 `zsspec-managed-v2`。用户不手写 Intent、Contract 或 Work Plan JSON。

暂停或重启后，在同一项目的 omp 中输入以下命令，继续已有检查点；有活动任务时不会创建第二个任务：

```text
/aidevflow-flow
```

使用 `/aidevflow-flow` 原生入口。`/skill:aidevflow-flow` 只加载方法说明，不能替代启用需求确认流程后的原生审查交互。交付后由用户实际业务验收；通过时使用 `/aidevflow-accept <实际模块名>` 完成方法收口，提交、合并与部署仍需另外授权。

## 安装默认值与可选参数

参数只在需要改变默认行为时传入。项目配置保存在 `.aidevflow/project.json`，日常调用无需重复传安装参数。

| 参数 | 新项目省略时 | 已有项目重装时 |
| --- | --- | --- |
| `--root` | 当前工作目录，必须为业务 Git 项目根目录 | 同样使用当前工作目录，不会自动查找上次安装目标 |
| `--runtime` | `omp` | 仍默认 `omp`，不会根据旧配置自动选择 Claude |
| `--skills-root` | 在 `.omp/skills` 和 `.claude/skills` 中发现含 `zsspec-*/SKILL.md` 的目录；只有一个候选时选用，没有候选时默认 `.omp/skills` | 优先保留可用的已有配置；活动任务始终使用其固定来源 |
| `--workflow-profile` | 有效行为为 `legacy` | 保留已有配置；未配置时仍按 `legacy` 解释 |

没有可保留配置且发现多个方法目录时，安装器要求显式选择 `--skills-root`。目录被选中不代表方法包完整，仍须运行 doctor。活动任务固定执行器和方法来源；安装器不会自动迁移它们。

### 先人工确认 Intent/PRD，再自主开发

在没有活动任务、需求讨论或待恢复交接的新项目中，增加一个参数即可：

```powershell
python D:\Tools\ai-native-autonomous\scripts\aidevflow.py install --workflow-profile reviewed-discovery-v1
```

重新启动 omp，仍使用 `/aidevflow-flow <需求>`。讨论完善后会展示原生审查交互：确认即进入自主开发，修改则继续讨论，暂停后从同一入口恢复。原生确认与文件摘要由系统处理，用户无需手动复制版本参数。此配置目前仅支持 omp；完整批准边界见 [需求确认流程](REVIEWED-DISCOVERY.md)。

### 指定目标、执行器或方法目录

不切换工作目录时，用 `--root` 指定业务项目；它放在 `install` 前。其余参数按需选择，例如：

```powershell
python D:\Tools\ai-native-autonomous\scripts\aidevflow.py --root D:\Work\my-project install --runtime omp --skills-root .omp/skills --workflow-profile reviewed-discovery-v1
```

- `--skills-root .claude/skills`：使用项目已有的 Claude 方法目录，无需搬迁文件。
- `--skills-root auto`：没有活动任务时显式重新发现方法目录，不优先沿用现有配置。
- `--workflow-profile legacy`：显式选择默认需求流程；有活动上下文时不能切换流程。
- `--runtime claude-code`：显式安装 Claude Code 后端。两个后端可以同时安装，活动任务仍使用原执行器；历史无绑定任务继续按 Claude 规则读取。[Claude 使用指南](CLAUDE-QUICKSTART.md)中的历史安装示例需增加该参数。

Python 固定为安装时使用的解释器，环境迁移后需从正确 Python 环境重装。示例可在 `python` 后增加 `-B`，仅用于禁止生成字节码缓存，不影响安装配置。原生审批、模型和共享配置不会被自动改成宽松模式。

`zsspec-managed-v2` 是新任务 Contract 的方法配置，不是安装参数，也不会把旧任务升级为 v2。安装器根据实际方法包选择 Contract 示例，协调者生成新任务配置并由 Gate 校验。因此应先准备方法包，再安装框架；旧任务保留原规则。

安装后主要生成：

```text
.aidevflow/
  project.json                  新任务默认执行器、Python、方法根与可选流程配置
  runtime/aidevflow/             公共运行时
  schemas/                      产物与执行协议
  runtime-manifest.json         受管文件摘要
.omp/
  extensions/aidevflow/          原生扩展与 bridge 客户端
  agents/aidevflow-*.md          分析、规划与独立验收角色
  skills/aidevflow-flow/         流程与 references
scripts/
  aidevflow.py
  aidevflow_bridge.py
  aidevflow_browser.py           本地 Playwright 检查与逐步截图
```

## 执行与验收规则

关键角色原生 task 调用一次一个；schema 要求时使用单元素 tasks 数组。安装定义默认 blocking、只读、结构化输出；不要覆盖角色工具、输出 schema 或隔离模式。后台 launch 不能计为完成。Verifier 只能读取当前固定 bundle。

协调者可用 `role-context --role intent_analyst`、`contract_planner` 或 `verifier` 取得 Gate 根据当前任务准备的只读派发参数。原生结果 Hook 提供不可变 `result_path`，直接用于 record-intent、register-contract 或 record-verdict，避免抄写展示树及反复阅读框架代码。

用户可用 `/skill:<name>` 交互加载；协调者通过 `read skill://<name>` 按需读取。活动任务的方法读取会路由到固定的物理来源、核对摘要，并附带托管执行约束。静态审查按维度串行完成，不派发供应方示例中的任意 reviewer 名称。

浏览器自动检查及 AI 辅助检查使用受控 Playwright 通道。将 JSON 操作计划声明为测试/辅助文件并在 Design 冻结，经 Gate 运行 `scripts/aidevflow_browser.py`，捕获报告与截图，再实际查看图片、如实登记 manual 检查。无需 chrome-devtools-mcp；原生 computer/eval 仍不开放。计划格式、命令和能力边界见 [OMP 能力映射](.claude/skills/aidevflow-flow/references/omp-capabilities.md)。

## 状态、恢复与续跑

```powershell
python -B scripts/aidevflow.py status
python -B scripts/aidevflow.py status --summary
python -B scripts/aidevflow.py recover-runtime
python -B scripts/aidevflow.py doctor --session-id <实际会话ID>
```

完整 status 检查候选与证据；summary 的 `candidate_freshness: not_checked` 不能证明验收有效。关注阶段、状态、pending_checks、pending_attestations、agent_invocations 和 verdict_current。

重启后重新使用流程入口，扩展按 Gate 和当前原生分支恢复。CLI 恢复适配器保存的真实观察；扩展还会从当前分支补回已派发角色的原生结果。缺失身份、原生输出或完成证据时保持待诊断，不从文字猜测、不制造事件。先确认旧执行结束再重派。

续跑只选一个所有者：

```powershell
python -B scripts/aidevflow.py continuation --owner bounded_stop
# 已由用户选择 omp /goal 时：
python -B scripts/aidevflow.py continuation --owner native
# 人工控制：
python -B scripts/aidevflow.py continuation --owner manual
```

框架不覆盖已有 Goal。bounded_stop 同时服从框架和 omp 原生次数限制。暂停、预算耗尽、待回答不等于交付。needs_human 按实际原因处理，不删除活动指针或改写冻结摘要。详见 [托管规则](ZSSPEC-MANAGED.md)。

冻结测试/规格需要纠正时，使用可审查的版本化基线修订；执行证据损坏时，使用诊断、留档隔离和真实重跑。修订中断可完成或回滚。跳过测试默认不能通过，批准例外交付会明确显示 `delivery_ready_with_exceptions`。完整命令和边界见 [恢复指南](.claude/skills/aidevflow-flow/references/recovery.md)。这些能力同时适用于 omp 和 Claude；重新安装不会自动修改或修复旧任务。

## 升级与维护

升级前停止写入会话，保存业务修改并备份 Git 忽略的运行数据；重装同一 runtime，查看 diff，重启并 doctor/status。运行时更新可能使旧检查失效，必须重验；活动任务中不覆盖固定方法包。

交付后由用户验收业务并决定提交/发布。通过 archive 保留历史、关闭活动指针，再创建下一任务。

```powershell
python -B -m unittest discover -s tests -v
bun test tests/omp-extension.test.ts
python -B tests/omp_native_smoke.py
git diff --check
```

HTTP 回归需要允许本机连接。自动测试、原生扩展发现、真实模型演练和浏览器验收分别记录，不能互相替代。支持边界与验证记录见 [OMP-COMPATIBILITY.md](OMP-COMPATIBILITY.md)。
