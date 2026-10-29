# AIDevFlow

AIDevFlow 是面向 AI 自主研发交付的本地控制套件。用户提供需求并回答关键业务问题，Coding Agent 完成分析、规格、实现和修复；Gate 管理任务状态、冻结测试、真实检查证据和独立验收。

**新安装默认使用 omp（oh-my-pi）**。Claude Code 保留独立适配；运行 omp 不要求安装 Claude Code。方法包支持项目级 `.omp/skills` 和 `.claude/skills`，保留供应方原文。

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

## 安装到业务项目

框架源码与业务项目必须分开。取得完整源码，不能只复制 Python 包。

```powershell
$AidevflowSource = 'D:\Tools\ai-native-autonomous'
$AidevflowProject = 'D:\Work\my-project'
python -B "$AidevflowSource\scripts\aidevflow.py" --root "$AidevflowProject" install --runtime omp --skills-root .omp/skills
```

已有项目保留现有工作，不重新初始化。新空项目先按团队规范建立初始提交。安装器不创建业务任务、不安装业务依赖、不配置 Git 身份、不修改全局 omp 配置，也不覆盖项目 `.omp/config.yml`。

省略 `--skills-root` 时保留可用的现有配置；没有活动任务且仅有一个候选方法目录时自动识别。`--skills-root auto` 可显式重新发现；多个候选需要明确选择。活动任务固定方法来源，不能重装切换。已有包放在 `.claude/skills` 可继续显式选择该目录，无需搬迁。

```text
.aidevflow/
  project.json                  新任务默认执行器、Python 和方法根
  runtime/aidevflow/             公共运行时
  schemas/                      产物与执行协议
  runtime-manifest.json         受管文件摘要
.omp/
  extensions/aidevflow/          原生扩展与 bridge 客户端
  agents/aidevflow-*.md          Analyst、Planner、Verifier
  skills/aidevflow-flow/         流程与 references
scripts/
  aidevflow.py
  aidevflow_bridge.py
  aidevflow_browser.py           本地 Playwright 检查与逐步截图
```

Python 固定为安装时的解释器。环境迁移后从正确 Python 环境重装。原生审批、模型和共享配置不会被自动改成宽松模式。

Claude 项目显式选择后端：

```powershell
python -B "$AidevflowSource\scripts\aidevflow.py" --root "$AidevflowProject" install --runtime claude-code
```

两个后端可以同时安装，默认值仅影响新任务。活动任务固定执行器；历史无绑定任务继续按 Claude 规则读取。[Claude 使用指南](CLAUDE-QUICKSTART.md)中的历史安装示例需增加 `--runtime claude-code`。

## 安装方法包并检查

从维护者取得完整方法包，将以下目录完整放入选定的方法根（上述示例为 `.omp/skills/`），包括根 README.md、references、模板与支持脚本。保留供应方原文：

```text
zsspec-req / zsspec-des / zsspec-task / zsspec-chk / zsspec-verify
zsspec-test / zsspec-apply / zsspec-done / zsspec-change
zsspec-init / zsspec-standard / zsspec-feedback / .skillhub
```

`zsspec-doctor` 是维护技能，业务复制时显式排除，不能用 `zsspec-*` 无差别复制。框架不安装或升级方法包，也不删除已有维护技能；doctor 报告其存在，受管任务拒绝加载它。反馈与使用上报不能作为验收证据，按实际授权配置。

```powershell
Set-Location $AidevflowProject
python -B scripts/aidevflow.py doctor
```

检查 `ok`、`missing_hooks`、`missing_roles`、`manifest_mismatches`、`method_errors`。doctor 不调用模型、不派发角色；配置通过不等于真实流程通过。新项目没有 active run 正常。

`method_sources` 显示配置目录、候选目录与同名副本；`next_actions` 给出缺失路径的修复方向。`readiness.initial_commit` 必须为 true 才能创建任务。Python Playwright 包和 Chromium 分别检查：包存在不代表浏览器已安装或服务可用。在 omp 内运行 `/aidevflow-doctor` 可查看该会话实际启用的工具。

安装后重启 omp，从业务项目根启动；在扩展和角色列表核对来源、启用状态。不要关闭扩展保护或用全局同名角色替换项目角色。

## 启动完整流程

先准备可运行环境和真实测试命令；有活动任务时恢复，不创建第二个任务。新项目可以先用普通对话准备最小技术栈和空应用，此时不要启动业务 intake。

将需求写入 UTF-8 文档，如 `docs/request.md`，启动 `omp` 后输入：

```text
/aidevflow-flow 请执行 docs/request.md 中的需求。
先检查 doctor/status，有活动任务则恢复。
新任务使用 zsspec-managed-v1，串行执行。
普通产物由协调 Agent 如实记录为 AI 审查；重大范围、风险和验收变化向我确认。
Design 生成并冻结测试、收集有效 Red；Build 修改实现，运行真实 Green/回归。
按任务运行真实 API/浏览器验证，经独立 Verifier 和 Gate 到 delivery_ready。
不要提交、合并、部署、修改框架或清除历史。
```

也可使用 `/skill:aidevflow-flow` 后发送需求；不要重复启动相同任务。协调者生成 Intent、Contract 和 Work Plan，用户不手写 JSON。

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
