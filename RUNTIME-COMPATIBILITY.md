# Observer B2：运行兼容性修复与重新验证

本次针对 ai-native-dashboard 的 Claude Code 2.1.311 会话排查修改框架源码，不修改该业务项目，不删除其记录，不修改 zsspec 完整技能包 v1.4。README 的文档版本 v1.18、方法规则标识 zsspec-managed-v1 与框架观察器版本互相独立。

## 为什么改

真实会话出现：主模型 end_turn 提前收尾；Analyst 8 轮、Planner 10 轮上限导致尚未交付；子代理日志明确记录 PostToolUse:SubagentHandback 被取消；Skill 实际展开但没有 PostToolUse 观察；手工探测污染了观察目录；Contract 接受 fixtures/** 却在冻结时按具体文件使用；共享 .gitignore 正常修改被 doctor 视为运行时损坏。本次不推定这些现象都由某个升级引入，不要求降级 Claude Code。

## 新任务的三层边界

1. **业务 Gate 保持严格**：需求与 Contract 不可变；Design 审查、测试冻结、有效 Red、工作项完成、当前候选检查和独立验收仍必需。方法仍按 req → des → task → chk → verify → test / apply / test → done 执行。
2. **方法观察适配可变化**：start-intake 固定 execution_policy.method_evidence=artifact_gated，method_observations_required=false。PreToolUse:Skill 记录 requested，PostToolUse:Skill 记录 observed；缺失不会代替产物审查，也不会单独阻断推进。observed 不表示执行完成。旧运行不自动迁移；旧 profile 的测试冻结规则也不改。
3. **独立角色执行证据仍必需**：首先用 Hook；丢失时由本地适配器恢复原生结构化会话记录。未知必要字段、输入/身份冲突、未结束的执行、无结果、旧 evidence epoch / bundle 仍不能通过。未知附加字段允许。

## 角色结果恢复

Gate 在登记角色结果而缺少回执时自动尝试 recover-runtime。也可单独执行：

```powershell
python scripts/aidevflow.py recover-runtime
python scripts/aidevflow.py recover-runtime --session-id <Claude会话ID>
python scripts/aidevflow.py doctor --session-id <Claude会话ID>
```

doctor 检查只读，不导入任何观察。适配器只读取 CLAUDE_CONFIG_DIR（缺省 ~/.claude）下 projects 的匹配会话，不把完整聊天/思考链交给模型。每文件最多 32 MiB、最多最近 64 个 Agent 调用、每次最多 8 个已知会话。末行未写完可以等待，完整损坏行报错。未知会话格式明确诊断，不猜 ID。

恢复要求：原生 Agent tool_use + 按 tool_use_id 关联的结构化 toolUseResult；角色输入与当时 Gate 状态一致；后台子代理成功 Handback 的 tool_result。只有带 toolEndsTurn=true 的原生成功返回，或已经捕获的真实停止/完成事件，才证明执行结束。孤立消息、async_launched、模型声称完成都不够。保留源文件、记录 UUID/摘要、原始上下文和 claude-transcript-v1 来源；不伪造 Start/Stop，不将历史输入补绑成当前输入。

这是本机执行来源校验，不抵御能够修改本地文件的同权限恶意用户。不要手工复制聊天输出来拼造观察。

## 续跑由一个机制负责

新 start-intake 默认 continuation_owner=bounded_stop。主模型提前结束时，Stop 可请求继续普通已授权动作；相同持久状态最多补两轮，累计最多 30 次。达到上限只在 status.continuation 标记 attention_required，不改变业务阶段、不判定通过、不消耗业务修复预算。真实 awaiting_user_input、needs_human、归档/交付、子代理 Stop 或保护写入拦截不触发自动续跑。

```powershell
# 已使用 Claude 原生 /goal 时，让原生机制独占续跑
python scripts/aidevflow.py continuation --owner native

# 普通交互会话使用有限次数的提前停止恢复
python scripts/aidevflow.py continuation --owner bounded_stop

# 主动人工控制每次继续
python scripts/aidevflow.py continuation --owner manual
```

不要同时叠加 /goal、/loop 与 Stop 重试。Stop 是补救措施，不能保证第三方模型一直执行。角色达到轮次上限时，协调者只续跑同一原生 Agent 一次，要求复用已分析内容交付；持续失败再报告原因，不重复开三个 Analyst。角色预算调整为 Analyst 16、Planner/Verifier 24，并要求预留输出轮次。

## 诊断与契约

- doctor 区分共享 .gitignore 的正常追加与核心运行时改动：保留必要 AIDevFlow 忽略规则时只报告 configuration_drift；核心文件 hash 失配仍失败。无需为增加 node_modules 忽略行重装设置。
- trace-method 缺少原生 session/tool/transcript 身份时拒绝写正式观察。旧 manual-probe 记录不计为观察；禁止在真实运行中注入模拟载荷。探测请使用只读 doctor 或隔离测试项目。
- design_artifacts/test_files/test_support_files 必须是具体计划文件，登记时拒绝 glob。路径与文件存在性仍在 Design 审查/冻结检查，不能把目录冒充文件。
- Hooks 关键路径不再启动 claude --version；版本检查由 doctor 负责。会话版本以实际日志为依据，安装版本不等于运行版本。
- status 出错不再向业务事件日志追加 command_rejected，便于只读监控；其他 Gate 写命令的拒绝仍可审计。

## 在空业务项目重新验证

仅在你自己确认项目内容不需要保留后清理；本次修复不执行清理。保留/重新初始化 Git，并先有初始提交。从源码安装：

```powershell
python -B D:\InspurCode\ai-native-autonomous\scripts\aidevflow.py --root D:\InspurCode\ai-native-dashboard install
```

手工安装完整 zsspec v1.4 方法资产，退出并重启 Claude Code。先完成前后端空应用、依赖/浏览器和测试命令准备，再输入 `/aidevflow-flow` 与原始看板需求。无需自己编写 Contract 或注入测试观察。

检查顺序：

1. doctor：核心文件无失配；能力信息为 configured/unverified 时不要解释成“真实调用已经验证”。
2. Intent：一次 Analyst 交付，必要时一次续跑；若 Handback 被取消，recover-runtime 应恢复真实结果，无需重做分析。
3. Contract：测试及辅助文件列出具体文件名；没有 fixtures/**；范围和验收覆盖合理。
4. Design：真实 REQ/DES/TASK/CHK、测试、Work Plan、AI 审查与冻结、有效 Red。Skill telemetry 缺失显示警告，不能伪造 observed。
5. Build/Verify：工作项证据、当前候选的全部检查、真实 API/浏览器证据、独立 Verifier，最后 Gate 到 delivery_ready。
6. 人为停止/失败用隔离用例验证，不修改真实 Gate 状态或冻结测试。记录人工催促次数、重复分析、真正业务缺陷和基础设施阻塞。

## 验证口径

2026-10-27 实际验证：完整回归 126 项全部通过（333.400 秒，包含本机 HTTP 检查）；增加完整无 Skill telemetry 流程用例后，兼容性组 26 项全部通过（36.964 秒），合计 127 个不同测试用例。完整流程用例确认缺少独立 Verifier 回执仍被拒绝，补齐真实协议夹具回执后才能交付。初次沙箱内执行的 HTTP 用例受 WinError 10013 限制，随后允许本机网络的完整回归通过。

真实 2.1.311 历史会话只读解析并隔离回放：3 份 Analyst 与 1 份 Planner 回执都可恢复。原业务项目控制文件前后摘要一致。新安装包 doctor 通过，正常追加 .gitignore 后仍通过。37 份供应方 Skill/README 文件摘要保持不变。

详细记录：[验证 JSON](.aidevflow/maintenance/b2-validation-20261027.json)。这些结果不等于已经完成一次新的自主看板业务开发。

新增回归覆盖原生 Handback 恢复、只读检查、旧输入/身份冲突拒绝、无结束证据拒绝、隔离探测、通配符提前拒绝、续跑上限与旧运行保留。另用真实历史会话在隔离副本回放，不能把回放称为一次新的自主业务交付。真实看板项目重跑结果需单独记录。
