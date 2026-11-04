# 当前 zsspec 的 AIDevFlow 托管适配

本版本适配含 `zsspec-plan` 和 `zsspec-standard/scripts/zsspec_check.py` 的当前方法包。新任务使用 **zsspec-managed-v2**。zsspec 原始技能、脚本和 README 不作修改；旧的 zsspec-managed-v1 任务保留原规则，见 [旧版说明](ZSSPEC-MANAGED-LEGACY.md)。不按 README 的版本字符串猜测兼容性。

## 责任与流程

用户讨论并批准 Intent/PRD → AI 审查设计与测试 → 自主实现和修复 → 独立验证 → delivery_ready → 用户业务验收。

- AIDevFlow 管理需求批准、执行状态、冻结基线、工作项、证据、修复预算与交付门禁。
- zsspec 提供 req → plan（DES/TASK/CHK）→ apply → test → done 的工程方法。
- 普通设计定稿和测试代码审查由 AI 承担，必须留存实际审查证据。不得称为“人工已确认”。
- 重大需求、接口、验收、权限与风险变化，以及最终业务验收、发布授权由人决定。
- OMP /goal 或 bounded_stop 只能选一个续跑所有者；它们都不能替代 Gate 的业务门禁。

## 新任务如何使用

先在独立业务项目准备完整的当前方法包、Python/Git、OMP 模型和业务依赖。不要删除旧任务依赖的方法包。安装器只更新 AIDevFlow，不安装或改写 zsspec。

```powershell
python -B D:\Tools\ai-native-autonomous\scripts\aidevflow.py --root D:\Work\my-project install --runtime omp --skills-root .omp/skills --workflow-profile reviewed-discovery-v1
```

在业务项目根检查：

```powershell
python scripts/aidevflow.py doctor
python scripts/aidevflow.py method-inspect
```

重新启动 OMP，然后使用同一个入口：

```text
/aidevflow-flow 说明目标、使用者、现有文档、约束和验收期望
```

在原生确认界面审阅 Intent、PRD 和场景，选择确认并开始自主开发。仅保存需求后，直接 `/aidevflow-flow` 即可继续。已批准后携带长文字调用 flow，会让你选择“继续已批准需求”或“修改需求并重新审阅”，避免一句续跑指令被当作需求修改。明确要修改需求时用 `/aidevflow-discover 修改意见`。

后续常规操作由协调 Agent 执行。用户无需逐条手动运行下文内部命令。

## 新 Contract 与映射

方法阶段固定为 Design=req/plan/test，Build=apply，Review=test/done。额外声明：

```json
"method": {
  "name": "zsspec",
  "profile": "zsspec-managed-v2",
  "modules": ["feature"],
  "acceptance_map": {"ACCEPT-1": ["AC-feature-value-one"]},
  "stages": {
    "design": ["zsspec-req", "zsspec-plan", "zsspec-test"],
    "build": ["zsspec-apply"],
    "review": ["zsspec-test", "zsspec-done"]
  }
}
```

已批准的验收 ID（如 ACCEPT-1）保持不变，acceptance_map 映射到 zsspec 的原生 AC ID。全体原生 AC 必须被准确覆盖，不能靠数量匹配。REQ 一行承接多个 AC 不需要重复表格行。

协调者从真实 TASK 派生 Work Plan；所有 TASK/CHK 行分别映射到 Gate 的 work_item/required_check。REQ 总览每行登记一次 acceptance 投影，其 source_id 选择该行包含的一项规范验收 ID；全体验收覆盖由 acceptance_map 判断，不要求每项验收独占一行 REQ。参照 `.aidevflow/contract.example.json`、`work-plan.example.json` 和公开 schema。

## 自主工程链路

1. req 消费已批准需求，不重新发明范围。plan 按 des/task/chk 阶段生成四层文档；实际 AI 审查后，通过 `method-run baseline --module feature --scope req --evidence <review.md>` 逐层定稿，scope 依次 req、des、task、chk。
2. Design 内准备所有验收测试和测试支持文件。适用时使用 test --auto --gen-only。注册 Work Plan 后，对每个模块执行 `method-run verify --module feature` 与 `method-run gate --module feature`。缺失断言的旧格式不能冒充开工放行。
3. 留下最终设计/测试 AI 审查报告并 record-design-review，freeze-tests，取得有效 Red，advance --to build。服务没启动、依赖缺失不算有效 Red。
4. apply 实现；通过 Gate 开始、完成或重开工作项。工作项/检查变化后执行 sync-projections。只允许适配器调用原生 set，同步不会改写设计或验收语义。
5. 通过 run-tests 执行真实检查。静态和人工辅助检查使用实际报告、截图和当前候选摘要 record-check。缺失、失败、过期或无效证据不能通过。失败按 Gate 规则修复并重新收集证据。
6. done 用于检查收尾问题，此时不执行其直接 close。prepare-verifier 生成包含方法策略、固定源文件、原生执行记录和审查证据的包，交给独立 Verifier；record-verdict 后才允许 advance --to delivery_ready。
7. 用户实际业务验收后输入 `/aidevflow-accept feature`。也可在操作者终端执行 `method-accept --module feature --by <姓名> --utterance <实际验收指令>`。适配器通过原生 close 更新 REQ；AI shell 不能代替用户执行该授权。原生生成的索引保存在操作证据中，不覆盖共享的实时 specs/index.md。发布仍需另外授权。

原生 TASK 状态只能单向完成。重开已完成的修复工作时，Gate 仍保持工作项进行中，并记录原生状态无法回退的原因；原生文档的“已完成”绝不能让 Gate 跳过修复。

## 隔离、版本与恢复

方法发现只在准备阶段生成缓存，不授权执行所有 zsspec-*。执行适配器是 AIDevFlow 自带代码，不加载供应方任意插件。Contract 固定方法描述、适配器 API、配置和完整使用源文件。OMP 的受管读取会重定向到任务源快照，外部方法包更新不会切换其运行流程。Claude 原生 Skill 加载器尚无相同的读取重定向，因此保留保守策略：安装源改变就触发完整性阻断；它不承诺 OMP 的运行中更新隔离。未知协议停止适配评估，不让 AI 临时发明新的执行规则。

原生脚本在复制的 specs 上执行，固定操作、模块、输入摘要、脚本摘要和审查证据。提交前再次验证状态版本与文件；冲突时不覆盖新编辑。脚本执行期间释放 Gate 写锁，另设工作进程锁阻止运行未结束时被恢复命令抢占。这是可信本地脚本的暂存机制，不是操作系统沙箱。

中断后不要编辑状态文件。先确认原工作进程已退出，再：

```powershell
python scripts/aidevflow.py method-recover --mode finish
# 若只有未完成的暂存，或决定取消尚未提交的操作：
python scripts/aidevflow.py method-recover --mode rollback
```

finish 只完成已验证事务；rollback 拒绝覆盖后来的编辑。已经写入 Gate 的事务只能完成收尾，不能无声回滚。冻结规格/测试的实质变化走原有人工授权的 baseline revision，不能借原生 baseline 命令重新批准。

## 验证边界

自动化集成测试使用本仓库的真实 zsspec 脚本验证定稿、门禁、状态同步、技术交付、操作者验收、版本隔离和事务恢复；集成样例使用真实 unittest 断言产生 Red/Green，Verifier 回执与 AI 审查报告使用明确标识的协议测试数据。真实 OMP RPC 检查覆盖命令加载和审查界面往返，不调用模型。它们不能证明复杂企业项目能自动完成某个百分比；推广前仍需真实模型与代表性业务项目的完整试点。

## 2026-11-03 源码验证记录

- Python 全量回归：235 项，233 通过、2 跳过。跳过项需要准备 Python Playwright/浏览器解释器；不计为浏览器验收通过。逐项日志保存在本机 `.test-tmp/zsspec-v2-regression.log`。
- 随后的快照读取与候选隔离收尾改动，重新执行最新方法适配专项：14 项全部通过；包括 Git 跟踪的方法文件/README 更新隔离、Claude 保守阻断、真实行为 Red/Green、独立验证协议到用户收口、部分提交恢复、并发冲突与冻结修订。
- OMP 扩展：39 项通过；真实原生命令发现通过，真实审查窗口往返与持久暂停通过。
- Stop 后延迟 35 秒再选择的真实 RPC 审查探针通过；Stop 在人工响应前返回，没有触发模型调用。
- Python 源码语法与 Git diff 空白检查通过。本轮没有调用真实模型做完整业务演练，也没有替业务项目安装或迁移。
- 原生脚本的仓库自测依赖分发中未包含的 fixtures，因此未将供应方 selftest 列为通过项；集成测试使用本仓库生成的明确样例执行实际原生脚本。

后续兼容策略：保持协议的模板/说明变化可重新发现并用于新任务；协议、状态机或原生解析结构变化仍需更新内置适配器。不能把“发现了 zsspec-*”当作任意未来版本都已兼容。
