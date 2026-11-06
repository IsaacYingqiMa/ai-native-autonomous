# AIDevFlow + zsspec-light 2.4.2

入口讨论 → Intent/PRD 用户审查 → 自主规格/设计/计划/TDD/修订 → 原生独立验收 → 最终用户审查 → 自动归档。公开命令仅 /aidevflow-flow、/aidevflow-discover。Git 提交不作为完成条件。

先 status，按 skill_path 读取当前阶段方法。工程规则归属五个 zsspec-light skill；Gate 的状态与证据为权威。已有任务继续当前检查点。

## 模型工具
只接受扁平字符串字段，不传 data、value 或内联对象。
- status、environment、requirements_context：只有 action。status 返回 current_task、skill_path、failure_summary、recovery、operation_correction、autonomy_budget；environment 返回工具确切路径，不联网安装。
- validate/submit：action、kind、path。kind=requirements/spec/plan/knowledge/verification，path 指向当前 run 草稿；requirements 正式提交例外，必须是原生角色不可变 result_path。validate 不登记正式材料。
- prepare_baseline、baseline_context、run、repair_apply、prepare_verifier、verifier_context、archive：只有 action。
- repair_propose：action、path，提案格式见 .zscode/extensions/aidevflow/repair.schema.json 与 status.recovery。
- retry：action、reason，只重准备尚未通过的 RED。
- report_conflict：action、reason，记录确切业务矛盾并持久化停止；不修改原批准。未建立冻结基线时，用户在 flow 提供真实需求反馈可重新讨论并审查；已有冻结基线则保持检查点，不能直接清空重启。
草稿目录和完整 schema 由 status 给出；禁止直接编辑状态、投影或冻结文件。

## 需求角色与用户边界
flow 宿主固定原始需求。requirements_context 返回权威 task_call，原生 task 单次派发一个 aidevflow-requirements-analyst。batch 形式使用单元素 tasks。
角色返回完整 requirements-v1；每条 source 为 request 或真实 feedback:<从0开始的索引>。结合 SOURCE 的 prior_questions/recorded_answers、项目 MAP 和相关源码核实事实，保留已决问题 ID 与选项，不能自造回答。
原生 ask 只询问真实 open_questions 的问题 ID。宿主固定问题、记录真实回答；超时默认项不算回答。完成后重新分析并 submit requirements result_path。展示 Intent/PRD 后结束本轮，由宿主打开用户审查。模型不提供批准或最终接受操作。

## 原生审查
需求、基线与最终独立角色只用返回的 task_call，禁止覆盖 tools/model/outputSchema/schemaMode/sourceSession/isolated。宿主绑定实际执行身份和严格输出契约。角色启动不等于完成，未知在途调用先恢复原生结果。
基线上下文返回 phase/next_action。只有 review_required 且有 task_call 才派发；reviewing 等待或恢复真实结果；approved 直接 run（initial）或 repair_apply（repair/derived），不能反复审查同包。重复准备同候选复用当前审查结果。
revise 先查 recovery：未冻结测试直接纠正并重审；派生设计用 spec_path/plan_path 提案；冻结测试用草稿基线修订。修订先隔离预检，失败证据在 repair_failure；correction_required 修正草稿后直接 repair_propose，不重新派发旧包。真实应用失败也返回此分支；未知在途和换版事务中断不能当作候选失败重开。
最终独立角色仅读固定 bundle。每项 Sx 一次，证据精确引用 objects/<sha>。格式失败在同 bundle 内纠错，保留 failed/revise；业务失败走自主回修，取得新证据后再验收。
只有 delivery_ready 才报告交付并结束本轮。宿主打开最终用户审查，接受绑定当前候选/bundle，随后 archive；旧交互不能接受新候选。

## 停止与恢复
Gate 返回稳定 code、材料类型、合法恢复分支及预算；不要从报错文字猜格式或重复提交同一错误。默认工作段上限为160次主模型请求、256次实际读取、10800秒（3小时）活跃时间；状态查询不消耗读取次数，也不重置预算。真实角色等待不计活跃时间。
同类操作拒绝第3次时按 operation_correction 改变诊断/修复策略，自主继续，不要求用户确认。PLAN_CONFLICT 的 diagnostic 列出 task_id、field、current、proposed 和 current_plan；以当前已登记计划为准生成最小修订，不复用夹带其他验收变化的旧草稿。有效提案被接受只结清该 action 的连续拒绝；累计预算保留到真实任务或基线推进。角色调用ID、重复派发/approve、无效输出、改 reason、查询状态及重开会话不刷新进展预算；有效审查结果按同一语义主体只记录一次。原生等待仍不计活跃时间。
默认同类拒绝12次或同段累计拒绝30次、单任务连续执行失败20次、基线审查未通过16次、独立验收格式/执行失败16次、产品回修超过20轮才达到对应硬上限。项目 progress_limits / failure_limits 可分别配置，以 autonomy_budget 实际返回为准；不能换 action 绕过累计上限。扩展遵从 Gate 的停止结论。
暂停/恢复/放弃由 flow 原生宿主处理。运行事实使用 status，业务恢复不扫描 runtime、events、state 或 progress。真正缺少业务决定时说明冲突并保持检查点，不自造批准、不直接改状态。
