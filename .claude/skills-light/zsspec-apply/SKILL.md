---
name: zsspec-apply
description: 执行真实 TDD、分层修订、自动回归修复和原生独立验收。
metadata:
  version: "2.4.2"
---

先 status，使用 current_task、failure_summary、recovery 和批准需求。仅编辑当前任务声明的未冻结文件；检查使用 run。依赖由 prepare 准备，runner 不自动安装依赖或强设 registry。
RED 测试准备后 prepare_baseline。仅在返回 task_call 时派发原生只读角色；reviewing 等待真实结果，approved 直接按 next_action 执行，不重复审查。只有 initial approve 才 run：目标断言失败才是有效 RED；普通 Error、测试初始化崩溃、导入、环境、无测试和跳过均不算。缺少计划中的实现时，用明确的能力存在性或行为断言表达缺失，不用普通抛错或复杂假服务代替被测实现。通过 RED 后冻结测试。GREEN/重构不得弱化已有断言。

按事实选择修复分支：
- initial revise，测试未冻结：修正 recovery.unfrozen_files，再 prepare_baseline 重审。无需先制造一次失败执行。
- 派生规格/计划路径或设计错误：在 draft_directory 写 repair.json={reason,group,changes:{测试原路径:修订草稿路径},spec_path?:规格JSON草稿,plan_path?:计划JSON草稿}。changes 可为空，但至少有实际派生变更。repair_propose → 原生审查 → approve 后 repair_apply。
- 冻结测试错误：repair.json 的 changes 指向修订草稿，可用 implementation_files 纠正同组实现文件清单。保留用例和批准行为；repair_propose → 原生审查 → repair_apply 隔离重放原始 RED、原子换版，再 GREEN/回归。
- 修订预检或应用失败：按 status.recovery.phase=correction_required 和最新 repair_failure 证据修正草稿，再 repair_propose。Gate 先预检再提供新审查任务；同一失败候选不反复执行，同一已批准候选不再次派发。变更草稿不等于旧审查包发生变化，新候选需要新的实际回执。事务中断或未知在途结果按恢复指引处理，不删除事务或伪造完成。
- GREEN/重构失败：修改当前 files 再 run。全量回归失败由 Gate 重新打开实现任务，按新的 current_task 修复；旧失败证据留在 repair_evidence/history。
- 未通过 RED 的准备错误：retry(reason) 后重准备。retry 不适用于 GREEN 或冻结成功基线。
- 环境错误：按失败摘要定位依赖、导入或 runner；涉及支持文件/路径时用受管提案修订，不能把基础设施异常当作 RED。

所有修订保留用户批准摘要、验收 ID 和任务顺序。验证命令通常保持不变；真实环境失败且未接受 RED 的工作项/prepare 可经审查纠正命令，同运行器在新环境中的确切路径也可受审查迁移；已接受任务的业务检查不得移除或弱化。spec_path/plan_path 修订不得清空已有成功 RED。未受影响完成事实保留。提案过期或在途执行未知时先恢复真实结果，不重复派发。
知识新发现可用 submit kind=knowledge 登记提案。全量回归通过进入 verify，prepare_verifier 获取固定 bundle 和 task_call，原生 task 派发受管独立验收；协调者只传 agent/task，不覆盖策略。可用 verification.json 的 knowledge_files 固定最终知识提案。
独立 revise 后自动修复并取得新回归、新 bundle；纯格式错误使用 verifier_context 在同一 bundle 纠错，保留 failed/revise。只有 delivery_ready 才报告交付并结束本轮，宿主打开最终用户审查。用户接受后自动进入 archive；无需 Git 提交。
预算耗尽报告具体 code/原因/证据与恢复点。真实业务范围冲突调用 report_conflict(reason)，保存停止点并向用户说明，不虚构决定，不用状态编辑或放弃重建解锁。

工具字段与原生角色协议见安装后的 `.zscode/extensions/aidevflow/flow.md`。
