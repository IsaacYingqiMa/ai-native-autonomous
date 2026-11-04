# 新版 zsspec 与 AIDevFlow 的串行托管使用说明

2026-10-27 Observer B2 补充：新建自主任务以真实产物审查作为方法门槛，Skill 观察缺失只作诊断；五阶段、方法调用顺序、测试冻结和独立验收仍需执行。具体执行恢复、续跑限制和重新安装方式见 [运行兼容性指南](RUNTIME-COMPATIBILITY.md)。旧任务保留原门槛。

本次适配针对当前 zsspec 完整技能包 v1.4。采用用户确认的两项策略：普通文档定稿和测试代码由协调 Agent 审查后继续，明确记录为 AI 审查；验收测试仍在 Design 准备和冻结，Build 不自动修改冻结测试。五阶段保持 Intent → Design → Build → Verify → delivery_ready。

版本口径按技能包维护者确认统一：

- **v1.4**：zsspec 完整技能包版本。
- **v1.18**：技能包自带 README 的文档版本；其中原有版本字样保留，不作为完整技能包版本依据。
- **zsspec-managed-v1**：AIDevFlow 托管执行规则的第一版，与上述两个版本独立。运行时按实际方法资产和配置校验，不根据 README 版本号放行。

本说明不修改 zsspec 方法资产、其 README 或内部反馈示例的版本字段。

## 适配范围

- 新任务 Contract 选择 `method.profile: zsspec-managed-v1`。不自动升级旧 Contract、旧任务或旧验收规则。
- Design：req → des → task → chk → verify → test（适用的 `--auto --gen-only`）。Build：apply。Review：test → done。
- 由 TASK 的真实工作分解生成 Work Plan，Gate 管理进度并投影到表格。原始需求、Intent 与 Contract 是边界；REQ 不得重新发明需求。
- 配置采用 strict、项目级 `.claude/skills`、串行。zsspec.yml 原有 parallel:auto 不会被重写，但本次有效策略为 off；Gate 同时只允许一个工作项进行中。文档/审查子代理串行由协调指令约束，本版不是通用并行调度器。
- 执行测试仍由 Gate 捕获。static/manual 新增独立证据登记，明确标为 AI 协调者声明，不冒充命令执行或人工批准。
- 新版 zsspec 的使用上报、feedback、维护侧 doctor 不属于业务验收链路。本次没有执行这些上报脚本。

## 升级与准备

先结束项目中仍在执行的 Claude 会话与子代理，再更新框架。不要清除历史任务。安装方式沿用 [兼容性教程](CLAUDE-COMPATIBILITY.md)。本次代码只修改框架源项目，没有安装到其他业务项目。

业务项目需要完整的新版 zsspec 方法资产；AIDevFlow 安装器不会替你覆盖或迁移这些资产。按供应方分发规则包含 `.skillhub` 与 `zsspec-test/reference`、`zsspec-standard` 模板，排除维护专用 `zsspec-doctor`。旧任务仍依赖旧入口时，应先合法结束旧任务，或在独立测试项目验证新版，不直接删除旧方法后强行继续旧 Contract。

在业务项目根运行：

```powershell
python scripts/aidevflow.py doctor
python scripts/aidevflow.py status
```

doctor 新增 method_errors 与 method_policy。它检查实际入口和配置，不能证明浏览器 MCP、服务或业务检查可用。未安装方法包时会报告缺失，不再用“Skill 数量大于零”代表兼容。

配置示例：

```yaml
paths:
  skills_root: auto
test:
  e2e: enabled
  manual: enabled
parallel:
  mode: off
  max: 1
gate:
  mode: strict
```

为保持 Python 无第三方依赖，本版只解析上面这种两层标量映射（支持注释和简单引号）；不支持 YAML 锚点、数组、花括号映射、多行值。遇到不支持的语法会报错，绝不悄悄忽略。quick、全局/自定义方法根暂不支持。禁用某种测试不能消除 Contract 中的验收义务；有冲突应在登记 Contract 前解决。

## 正常使用

在已配置的 Claude 项目中输入：

```text
/aidevflow-flow

使用新版 zsspec-managed-v1 完成本次任务。先查看 doctor 和 status。
普通产物由协调 Agent 审查，如实登记为 AI 审查，不重复要求人工确认。
有活动任务时先按其原有 Contract 继续，不自动升级或新建。
新任务串行执行，验收测试在 Design 准备、审查并冻结。
范围、风险、验收变化和冻结测试缺陷返回我处理。
需求如下：……
```

多数命令应由协调 Agent 执行。用户主要提供真实需求和关键选择，不需手写 Contract 或手工修改状态。

### Contract

参考 `.aidevflow/contract.example.json`，重点是：

```json
{
  "method": {
    "name": "zsspec",
    "profile": "zsspec-managed-v1",
    "stages": {
      "design": ["zsspec-req", "zsspec-des", "zsspec-task", "zsspec-chk", "zsspec-verify", "zsspec-test"],
      "build": ["zsspec-apply"],
      "review": ["zsspec-test", "zsspec-done"]
    }
  },
  "evidence_paths": ["specs/checklist/reports", "test-results", "playwright-report"]
}
```

这是字段示例，不是可直接登记的完整 Contract。完整示例仍需按项目填写命令、代码路径、设计产物、测试和验收标准。

检查项分两类：

```json
[
  {"id":"CHECK-e2e","suite":"e2e","mode":"auto","acceptance_items":["REQ-功能"]},
  {"id":"CHECK-static","kind":"static","acceptance_items":["REQ-功能"]},
  {"id":"CHECK-ui","kind":"manual","acceptance_items":["REQ-功能"]}
]
```

命令项在 test_commands 定义实际 argv。static/manual 不填写 suite/target，必须通过 record-check 登记证据。每个验收项应有充分覆盖；至少声明一个 static 检查。不要用 static 代替必须运行的 UI/HTTP 行为。

evidence_paths 是专用运行输出目录，不是额外业务写权限。不能与代码、规格或冻结测试重叠。其内容不参与候选代码摘要，只有登记时捕获的不可变快照进入证据链，避免测试生成报告导致自己失效。不能把实现或依赖放在这些目录。

### Design 审查与冻结

完成四类规格、必要测试代码、TASK 到 Work Plan 的登记后，协调者先检查覆盖、断链和测试断言，把实际检查发现与处理写入报告，然后：

```powershell
python scripts/aidevflow.py record-design-review --reviewer "Claude coordinator" --reason "已检查规格覆盖与可执行测试断言，报告记录结论" --evidence specs/checklist/reports/design/review.md
python scripts/aidevflow.py freeze-tests
```

Gate 记录审查时的规格、测试和计划摘要。审查后又改了内容，必须重新审查。没有审查记录不能冻结。记录写明 ai_coordinator_attestation，不表示人工确认，也不能自动证明模型审查质量。

随后按 Contract 收集有效 Red，并进入 Build。可执行测试列入 test_files；POM、fixture、配置等辅助文件列入 test_support_files。两者一同冻结，辅助文件不会被伪装成单独的 Green 测试目标。red_test_files 仅列需要证明新增行为缺失的测试，不能强迫所有回归和辅助文件失败。目标服务不可达不等于有效 Red。

### Build 的运行与证据

命令检查：

```powershell
python scripts/aidevflow.py run-tests --suite e2e --evidence test-results/result.json test-results/screenshot.png
```

报告/截图须在命令结束后存在且非空，路径须属于 evidence_paths；建议预先约定本轮输出目录。Gate 保存实际退出码和 stdout/stderr，并复制文件快照。文件存在不代表功能通过；命令结果、报告含义和独立验收共同决定。

static/manual：先取 status 中的 candidate_sha256，针对这个版本执行审查或浏览器检查，再登记：

```powershell
python scripts/aidevflow.py record-check --id CHECK-static --result passed --candidate <检查前取得的SHA> --reviewer "Claude coordinator" --reason "报告中无阻断发现" --evidence specs/checklist/reports/static/review.md
python scripts/aidevflow.py record-check --id CHECK-ui --result passed --candidate <检查前取得的SHA> --reviewer "Claude coordinator" --reason "按步骤完成界面检查，截图支持结论" --evidence specs/checklist/reports/ui/review.md specs/checklist/reports/ui/screen.png
```

尖括号是占位符，不直接执行。失败用 failed，缺条件用 blocked，非 passed 返回非零。manual 的 passed 至少要有截图；主观审美或真实外部系统缺条件仍应阻塞。Gate 校验检查项、候选版本、文件完整性和时效，不凭截图扩展名证明操作真实发生；独立 Verifier 仍需审核证据实质。

status.pending_checks 只列需要运行的命令；pending_attestations 列 static/manual 的缺失、失败、过期或损坏记录。Skill 加载成功不等于模式执行完成。代码变化后即使 CHK 文本没变，旧证据仍过期。不要把“已通过且清单未变就跳过”的原生规则直接套在托管任务上。

TASK/CHK 状态由 Gate 投影。manual 通过显示 AI辅助；报告里保留来源、日期与截图。done 只做方法层收口，不能提前标记最终 REQ 完成，也不要求一个尚未生成的最终 Verifier verdict。通过独立验收后 Gate 再投影 REQ 完成状态。

## 失败与恢复

- 实现缺陷：走 Gate route，重开受影响工作项，修复并重新取证。
- 冻结测试缺陷：暂停处理基线，不自动更改选择器、断言或重算哈希。
- 规格/计划变化：不能通过 zsspec-change 直接修改冻结内容。本版仍需明确的新基线处理。
- 配置或方法资源变化：固定策略与方法包摘要会检测到，包括 test/reference 和 standard 模板；不静默切换到新策略。
- 报告源文件后来覆盖：已捕获的快照保留原内容；要登记新结果必须重新检查。历史失败不会被删除，最新有效结果用于当前检查状态。
- 证据快照被修改：标为 invalid，不能进入独立验收。
- 旧合同：不添加 profile、不制造旧方法观测、不强行套新审查门禁。

## 验证边界

框架回归与托管流程测试验证配置、阶段门禁、时效、证据保存和打包。它们不代表完成了一次真实 zsspec 自主业务开发，也不证明浏览器实测或首次交付合格率已提高。正式使用前，建议在隔离业务副本跑一个真实小功能，检查 Gate delivery_ready、有效 verdict 与真实功能结果。

### 2026-10-27 实际验证记录

- 最终完整回归：`python -B -m unittest discover -s tests -v`，101 项全部通过，313.067 秒；含原有 70 项与本轮新增 31 项。
- 包含新配置校验、Intake 角色回执衔接、AI 设计审查、测试辅助文件冻结、串行工作项、命令/声明隔离、过期与篡改拦截、缺失/旧报告拒绝、截图打包和原有真实本地 HTTP 检查。
- 隔离安装使用真实当前方法包（12 个业务 Skill，不包含维护 doctor）；安装后的 CLI doctor 通过，method_errors 为空，耗时约 3.24 秒。记录保留在 `.test-tmp/managed-install-64ub1ll6/smoke-result.json`。
- 验证中修复了 Windows 短暂文件占用导致的原子替换失败（有上限重试），以及 cmd 版本探测遗留子进程可能拖住输出管道的问题（原生探测、文件输出、有限等待）。永久错误继续报错。
- 未调用模型或上报脚本，未安装到 zdata-agent-starter，未修改其任务记录。没有运行一次真实的新版 zsspec 自主业务开发或浏览器验收。
