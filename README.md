# AIDevFlow

AIDevFlow 是面向 AI 自主研发交付的本地控制套件。你提供需求、回答关键业务问题，Coding Agent 完成分析、规格、实现和修复；套件管理任务状态、阶段门槛、测试证据和独立验收。

当前运行环境是 **Claude Code + 项目级 zsspec 完整技能包 v1.4**，采用单项目、单活动任务、单协调 Agent 的运行方式，适合先在独立 Git 项目中试点。

```text
原始需求 → Intent → Design → Build → Verify → delivery_ready
                              ↑         │
                              └─ 实现缺陷按路由修复 ─┘
```

`delivery_ready` 表示满足本次任务的交付条件，不代表已提交 Git、合并 PR、部署或完成生产验收。套件不保证一次实现正确，也不能凭测试通过证明全部业务场景已经覆盖。

## 先了解当前边界

| 能力 | 当前行为 |
|---|---|
| 自然语言入口 | Agent 从原始请求生成 Intent 和 Contract，用户不用手写 JSON |
| 方法与状态 | zsspec 提供工程方法，Gate 管理执行状态和验收 |
| 自主推进 | 普通产物由协调 Agent 作 AI 审查；重大范围、风险、验收变化仍需用户决定 |
| 测试与修复 | 运行真实检查、保存结果；实现缺陷可修复，冻结测试不能自动改写 |
| 独立验收 | 新上下文的 Verifier 审查固定证据包；不能自行启动服务或补跑浏览器 |
| 多项目 | 可以分别安装，各项目独立运行；不是跨项目调度服务 |
| 尚未实现 | 独立 Intent 头脑风暴入口、自动问题知识库、其他 Coding Agent 适配、完整发布运维闭环 |

当前业务入口是 `/aidevflow-flow`。`/aidevflow-intent` 尚不存在。想法未明确时，先与 Agent 普通对话整理需求，再进入自主流程。

## 一、准备环境

本文命令以 **Windows PowerShell** 为例。

| 依赖 | 要求 |
|---|---|
| Python | 3.11 或以上；框架运行时无额外第三方 Python 依赖 |
| Git | 安装目标必须是 Git 仓库根目录；创建任务前需要初始提交 |
| Claude Code | 本机已安装，模型服务及工具可用；从业务项目根启动 |
| zsspec | 维护者另行提供完整 v1.4 方法包，安装到项目 `.claude/skills/` |
| 业务环境 | 根据项目准备，如 Node.js、数据库、测试工具、Playwright 浏览器 |

```powershell
python --version
git --version
claude --version
```

Hook 默认使用 `python`。使用 Conda/虚拟环境时，从正确环境启动 Claude，并确认 Claude 的 Bash/PowerShell 工具能找到该 Python。

已记录的真实会话与回放涉及 Claude Code 2.1.310 / 2.1.311。它们是历史验证版本，不是要求降级的版本，也不代表所有后续版本已验证。安装检查之后仍需真实小任务验证。

## 二、获取源码并安装

### 1. 获取维护者已发布的源码

```powershell
$AidevflowSource = 'D:\Tools\ai-native-autonomous'
git clone https://github.com/IsaacYingqiMa/ai-native-autonomous.git $AidevflowSource
```

也可以使用维护者提供的完整源码包，并把变量改为实际解压路径。必须保留 `aidevflow/`、`scripts/`、`.aidevflow/` 模板、`.claude/agents/`、`.claude/skills/aidevflow-flow/`，不能只复制 Python 包。

后续 PowerShell 示例沿用这些变量，重新打开终端后请重新设置。变量不是项目配置。

> 以维护者通知的已发布提交或分发包为准。本地修改不一定已经推送；历史测试数字也不代表当前下载内容已经验证。

### 2. 准备业务项目

源码仓库和业务项目必须分开。示例：

```powershell
$AidevflowProject = 'D:\Work\aidevflow-demo'
```

已有项目：确认路径是 Git 仓库根目录且已有提交，保存现有工作。不要重新初始化或创建无关提交。

新建空项目可执行下列命令；目录已存在时跳过第一条：

```powershell
New-Item -ItemType Directory -Path $AidevflowProject
Set-Location $AidevflowProject
git init
git commit --allow-empty -m 'chore: initialize project'
```

提交若提示缺少身份，请按公司规范配置自己的 `user.name` 和 `user.email` 后重试。安装器不会设置 Git 身份。

### 3. 执行安装

在普通终端中运行：

```powershell
python -B "$AidevflowSource\scripts\aidevflow.py" --root "$AidevflowProject" install
```

`--root` 是全局参数，放在 `install` 前。不要把目标指向框架源码自身。

安装器会复制框架运行时、schema、模板、三个角色和流程 Skill，生成 CLI/Hook 入口，合并项目 `.claude/settings.json`，追加必要忽略规则并生成安装清单。

它不会安装 zsspec、业务依赖或浏览器，不会生成应用、创建任务。无关设置会保留，框架管理的文件和 Hook 会更新。已有项目应查看安装 diff，确认与已有约定兼容。

```text
业务项目/
├── .aidevflow/
│   ├── runtime/aidevflow/          运行时
│   ├── schemas/                   结构化产物格式
│   ├── contract.example.json
│   ├── work-plan.example.json
│   └── runtime-manifest.json      安装文件摘要
├── .claude/
│   ├── agents/aidevflow-*.md      Analyst、Planner、Verifier
│   ├── skills/aidevflow-flow/     流程入口
│   └── settings.json             项目 Hook/权限
└── scripts/
    ├── aidevflow.py
    └── aidevflow_hook.py
```

## 三、安装项目级 zsspec 方法包

AIDevFlow 安装器与 zsspec 分开。供应方方法目录不属于本仓库默认跟踪的分发资产，不能假设 clone 后已有完整方法包。

从维护者取得 **zsspec 完整技能包 v1.4**，安装为：

```text
.claude/skills/
├── aidevflow-flow/               保留，不覆盖
├── zsspec-req/SKILL.md
├── zsspec-des/SKILL.md
├── zsspec-task/SKILL.md
├── zsspec-chk/SKILL.md
├── zsspec-verify/SKILL.md
├── zsspec-test/SKILL.md          包含 reference/ 等支持文件
├── zsspec-apply/SKILL.md
├── zsspec-done/SKILL.md
├── zsspec-change/SKILL.md
├── zsspec-init/SKILL.md
├── zsspec-standard/SKILL.md      包含模板、spec-rules.md 等
├── zsspec-feedback/SKILL.md
└── .skillhub/                   按供应方完整分发规则保留
```

完整复制各目录，不只复制 `SKILL.md`；不要多套一层 `.claude/skills/zsspec/.claude/skills/`。已有方法包需要更新时，先核对版本及活动任务，不混合覆盖。

维护专用 `zsspec-doctor` 不是业务依赖。供应方自带的使用上报/feedback 按供应方和公司授权配置，不是 AIDevFlow 验收证据；安装检查不要求执行上报脚本。

| 版本标识 | 含义 |
|---|---|
| `v1.4` | zsspec 完整技能包版本 |
| `v1.18` | 供应方 README 文档版本，不是完整包版本 |
| `zsspec-managed-v1` | 托管规则标识，不是另一个 Skill 或命令 |
| `claude-observer-b2` | 当前框架观察器标识 |

安装后检查：

```powershell
Set-Location $AidevflowProject
python -B scripts/aidevflow.py doctor
```

应看到 `ok: true`，`missing_hooks`、`missing_roles`、`manifest_mismatches`、`method_errors` 为空。新项目 `active_run: null` 正常。Skill 数量单独不能证明兼容。

`live_skill_hook_support: unverified` 表示尚未实测，`running_version: null` 也不等于未安装 Claude。doctor 不派遣 Agent、不执行业务测试。

创建任务前，`status` 返回 `No active AIDevFlow run` 属于正常现象。

## 四、准备业务运行与测试环境

安装或升级后重启 Claude Code；从业务项目根启动：

```powershell
Set-Location $AidevflowProject
claude
```

已有可运行项目应沿用实际架构和命令。空项目先准备环境，暂不创建业务任务。可发送：

```text
先做项目准备，不调用 start-intake，不实现业务功能。
检查当前目录、Git 初始提交、AIDevFlow doctor 和已有活动任务。
有活动任务先报告，不重新初始化。

空项目先与我确认最小技术栈，再准备可运行的空应用、依赖和测试工具。
已有项目沿用架构和命令，不覆盖应用。
按需执行 zsspec-init；保留已有 CLAUDE.md、AGENTS.md 和配置。
把实际启动、构建、测试命令和环境缺口写入项目说明。
不修改 AIDevFlow 运行时、角色、Hook 或供应方 Skill 原文。
```

前端任务需要实际浏览器验证能力。采用 Playwright 时提前准备依赖和浏览器。数据库、账号或外部接口不可用时如实报告，不能用模拟结果声称真实验证通过。

`specs/zsspec.yml` 缺失时有默认策略，**不要求手工写演练配置**。若由 zsspec-init 生成或确需调整，应在 Contract 固定前完成。当前有效策略为项目级 Skills、strict、串行；配置 `parallel:auto` 也不会开启并行。详见 [托管说明](ZSSPEC-MANAGED.md)。

准备完成后，查看并按项目规范提交需要版本管理的准备产物；依赖目录、凭据和本地运行日志不纳入提交。

## 五、启动第一个自主任务

### 1. 写清楚需求

第一项任务选小而明确的需求。可以先普通讨论，再整理成项目内 UTF-8 Markdown，例如 `docs/request.md`，说明目标用户、预期行为、本次范围、不做什么、成功例子和约束。

例如在已确认的 Python 空应用中实现本地任务清单查询：

```text
读取用户指定的 JSON 任务文件，展示任务 ID、标题和状态。
支持 todo / doing / done 筛选；不筛选时展示全部任务。
无匹配项明确提示；文件不存在、JSON 损坏、状态参数非法时清晰报错。
查询不得修改原始数据文件。
不做图形界面、网络服务、数据库、登录或任务编辑。
用真实命令和自动测试验证正常、空结果和错误路径。
```

这个例子无需浏览器。前后端看板等场景作为后续演练，明确要求真实 API/浏览器验证。

### 2. 调用流程入口

在 Claude 中输入 `/aidevflow-flow`，然后发送：

```text
请使用 AIDevFlow 执行 docs/request.md 中的需求。
先检查 doctor 和 status。有活动任务时按原有规则恢复，不创建第二个任务。

新任务采用 zsspec-managed-v1，串行执行。
普通规格和测试代码由协调 Agent 审查，如实记录为 AI 审查。
验收测试在 Design 生成、审查并冻结；Build 修复实现，不改冻结测试。
范围、风险、验收变化或关键业务歧义向我确认，其余按批准边界继续。
执行真实命令检查，适用时运行真实 API 和浏览器验证。
经过独立 Verifier 和 Gate 到 delivery_ready 后汇报。
不要自行提交、合并、部署或清除记录。
```

替换为真实需求文件路径。也可以直接在提示中写需求，由协调 Agent 保存请求快照。

不需要手工 `init` 或填写示例 Contract。协调者应执行 `start-intake`、角色调用、登记及推进。`init` 是已有手写 Contract 时的底层入口，不是普通用户第一步。

### 3. 用户参与什么

回答影响业务目标、范围、风险和验收的关键问题。当前 `clarify_then_auto` 在阻塞问题解决后可以自动定稿 Intent；记录的是自主策略，不是假称用户逐份批准了文档。

重要需求应检查 Intent 的目标、范围、成功标准和假设，不能只看是否进入下一阶段。普通产物 AI 审查并不能替代业务负责人作出的必要判断。

## 六、观察状态和阶段产物

在另一个终端只读查看：

```powershell
Set-Location $AidevflowProject
python -B scripts/aidevflow.py status
```

有活动任务且命令成功输出 JSON 后，可以提取字段。每次观察重新运行，避免读取旧变量：

```powershell
$AidevflowStatus = python -B scripts/aidevflow.py status | ConvertFrom-Json
$AidevflowStatus | Select-Object task_id, current_stage, status, verdict_current
$AidevflowStatus.work_items | Select-Object id, title, status
$AidevflowStatus.checks | Select-Object id, kind, status
$AidevflowStatus.pending_checks
$AidevflowStatus.pending_attestations
$AidevflowStatus.continuation
```

报错时先读原始错误，不要把 JSON 解析失败当作空任务。

| 阶段 | 主要产物 | 观察重点 |
|---|---|---|
| Intent | 请求快照、intent-draft.json、intent.md、角色回执、contract.json、方法策略 | 目标不偏离原始需求，关键选择与假设清楚 |
| Design | REQ/DES/TASK/CHK、Work Plan、测试、AI 审查、冻结清单、Red | 测试有真实断言；Red 不是依赖/服务错误 |
| Build | 代码、工作项证据、Green/回归、static/manual 证据 | 检查对应当前代码，失败不靠删除测试解决 |
| Verify | 固定证据包、BUNDLE.json、独立报告 | 各验收项有实质依据，缺口和风险被报告 |
| delivery_ready | 当前有效验收结论、交付说明 | 检查 verdict_current，用户再验收业务并决定提交/上线 |

运行数据在 `.aidevflow/runs/<TASK_ID>/`，活动指针为 `.aidevflow/active-run.json`，事件日志为 `.aidevflow/events.jsonl`。规格路径以本次 Contract 为准。

- `awaiting_user_input`：需要回答关键问题。
- `active`：Gate 允许工作，不表示 Claude 此刻一定正在运行。
- `retrying`：存在需要按路由处理的验收失败。
- `needs_human`：需要明确处理，不能静默重置。
- 检查为 `missing / failed / stale / invalid / blocked`：不能作为当前通过结果。

文件存在、Skill 被调用、截图存在，都不能单独证明功能通过。新任务的 Skill 观察是辅助信息，角色结果、测试和业务验收仍有必要门槛。

## 七、失败与恢复

### 普通中断或会话重启

重新从业务项目根启动 Claude，发送：

```text
/aidevflow-flow
读取 doctor 和 status，恢复当前活动任务，不创建新任务，不删除历史。
按当前阶段继续。缺角色回执先尝试 recover-runtime，再判断是否需要重派。
若 needs_human、权限不足、冻结基线缺陷或重大范围变化，解释具体原因。
不要手工修改状态、伪造观察或绕过保护规则。
```

不要为了从头开始删除 active-run.json、事件日志或冻结清单。恢复以 Gate 状态为准。

### 续跑选择

新任务默认 `bounded_stop`：没有持久进展最多补两轮，累计最多 30 次自动续跑。到达上限在 `status.continuation.attention_required` 提示，不自动接受交付，不消耗业务修复预算。

如果已由 Claude 原生 `/goal` 负责持续执行，在创建任务后选择原生机制独占续跑：

```powershell
python -B scripts/aidevflow.py continuation --owner native
```

普通会话的有限恢复使用 `--owner bounded_stop`；人工控制使用 `--owner manual`。不叠加多套 Stop 重试、`/goal` 和 `/loop`。这些选择不放宽业务验收。

### 结果已返回但缺回执

```powershell
python -B scripts/aidevflow.py recover-runtime
```

正式恢复只导入能关联的原生结构化记录，不通过模型文字推断完成。必要时取得实际 Claude 会话 ID，使用 `doctor --session-id` 只读诊断，或 `recover-runtime --session-id` 指定来源。普通 doctor 不执行恢复。

### 常见问题

| 现象 | 处理 |
|---|---|
| 缺方法 Skill | 核对完整 v1.4 包、目录层级及 reference/template 文件 |
| 新项目没有 active run | 正常，通过流程入口提交需求 |
| Hook 找不到 Python | 检查启动环境和工具 PATH，修正后重启 |
| Skill 为 requested / not_observed | 新任务保留警告并依靠真实产物审查；旧任务规则不自动放宽 |
| 子代理达到轮次上限 | 有界续跑原角色，复用已有分析，不重复开多个相同角色 |
| missing_output / 缺回执 | 先恢复；身份冲突、未知结构或执行未完成时不能伪造通过 |
| 测试失败 | 实现问题修代码，环境问题修环境，不改断言凑绿 |
| 冻结测试或规格缺陷 | 暂停，明确处理基线，不手动重算 hash |
| 检查 stale | 当前代码变化，重新执行必要检查 |
| .gitignore configuration_drift | 必要忽略规则仍在时通常只是提示，无需重装 |
| 核心文件 manifest 失配 | 明确是否有授权升级或误改，按安装/恢复流程处理 |
| Hook/权限拒绝控制目录写入 | 使用合法 Gate 操作，不换工具绕过 |
| needs_human | 了解原因并明确决定后，按支持的 resume-run 方式处理 |

不要把任何暂停都归因于 CC 升级，也不要把降级当作首选。详见 [运行兼容性指南](RUNTIME-COMPATIBILITY.md)。

## 八、完成和开始下一项

确认 `delivery_ready`、当前验收有效，并检查实际业务结果，再按公司流程提交/评审/发布。

开始下一项前，让协调者归档本次任务，也可在业务根运行：

```powershell
python -B scripts/aidevflow.py archive
```

归档保留历史并关闭活动指针。只有 delivery_ready 或 needs_human 的任务支持归档，不能把它当作任意取消 active 任务的命令。新任务使用新 ID。

运行目录默认不进入 Git。团队共享需要另行保存正式产物、结论和证据引用；本地 JSON 不等于团队共享归档。

## 九、升级已有项目

不必先删除旧任务，但运行时更新可能改变候选摘要，旧检查需要重验。旧 Contract/验收规则不会自动迁移。

1. 记录任务 ID、阶段和阻塞原因，结束正在写入的 Claude 会话与子代理。
2. 保存业务修改及重要运行数据。运行数据被 Git 忽略，Git 提交不会替你备份，应按需要另行备份。
3. 更新框架源码到维护者发布的版本；本地有修改先处理，不用 reset --hard 强行覆盖。
4. 从新版源码重新安装：

```powershell
python -B "$AidevflowSource\scripts\aidevflow.py" --root "$AidevflowProject" install
```

5. 查看 diff，重启 Claude，运行 doctor/status，再恢复任务。

安装器不升级 zsspec；活动任务固定的方法资产和配置不能随意覆盖。先在独立项目验证兼容性，再更新已有任务环境。

## 十、维护者发布检查

- 新增模块、schema、角色、流程 Skill/references、安装脚本和指南已纳入 Git 或完整源码包，不只发布 README。
- 用 `git status --short`、`git diff --check` 检查待发布内容与私人文件。
- 在源码根运行 `python -B -m unittest discover -s tests -v`；本机 HTTP 用例需要允许测试所需的本机连接。
- 在临时 Git 项目安装，另装 zsspec，检查 doctor 并运行真实小任务，不把配置检查称为业务演练。
- 提供明确的提交 ID/发布标识、zsspec v1.4 获取方式和已验证环境。
- 按团队流程提交、评审和推送，确认远端包含提交后再通知使用者。本文不自动发布代码。

zsspec 和 `.skillhub` 在框架仓库中被忽略，应按供应方规则另行分发；不能依赖维护者本机恰好安装了这些目录。

### 已记录的验证范围

Observer B2：126 项完整回归通过，随后 26 项兼容性测试通过，合计 127 个不同用例；真实历史会话隔离回放恢复了三份 Analyst 和一份 Planner 回执；安装包预检通过。

这证明相关机制和恢复路径经过验证，不是新的完整自主业务交付，也不证明新用户环境或其他模型一定兼容。模型路由、供应方 Skill、业务依赖及真实验收条件仍需分别验证。

## 进一步阅读

- [Observer B2 运行兼容性、恢复与重跑指南](RUNTIME-COMPATIBILITY.md)：当前兼容性行为优先参考。
- [zsspec v1.4 串行托管说明](ZSSPEC-MANAGED.md)：字段、测试冻结与证据登记。
- [Claude observer B 历史教程](CLAUDE-COMPATIBILITY.md)：含 b1 历史记录，冲突以 B2 为准。

`docs/`、`examples/` 部分属于本地维护材料或历史演练，不是新用户安装必需的分发文件；旧流程描述不能覆盖当前指南。
