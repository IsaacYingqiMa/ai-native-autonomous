# zscode 安装版 + zsspec-light：两个入口，两个审查点

公开命令只有 `/aidevflow-discover` 和 `/aidevflow-flow`。诊断、进度、恢复、批准和验收都由入口统一处理。正常流程中，用户只审查Intent/PRD与最终产物。

## 安装与开始

需要Python 3.11+、Git、已配置可用模型的zscode。业务项目与框架分开并有初始Git提交。安装器不代替用户提交。

```powershell
python D:\InspurCode\ai-native-autonomous\scripts\aidevflow.py --root D:\Work\my-project install --runtime zscode
Set-Location D:\Work\my-project
zscode
```

安装完整light技能、需求分析、基线审查与独立验收角色、产物schema、精简本地运行时和项目扩展，保留用户模型、权限和全局配置。安装后从业务项目根重新启动zscode，避免旧扩展命令仍留在会话中。

在zscode输入一条需求即可开始，入口自动检查安装和环境：

```text
/aidevflow-flow 修复add(a,b)始终返回0的问题，正确计算两个整数之和并补测试。
```

也可用 `/aidevflow-discover <需求>` 进入需求讨论。两者均先恢复当前检查点，不新建第二个活动任务。

## 实际操作顺序

1. 用户描述需求，与AI讨论必要业务问题。
2. AI生成 `specs/active/<slug>/intent.md` 与 `prd.md`，在本轮结束后自动展示原生审查选项。
3. 用户选择“确认需求并开始自主开发”，或在同一个交互中提出修改意见。关闭窗口不批准；存在阻塞问题时继续讨论。
4. AI自动生成light规格与计划，执行TDD、测试、诊断修复和全量回归。
5. AI自动固定证据bundle，通过真实原生task派发只读独立验收角色。不通过则自主修复并重新验收。
6. 当前候选独立验收通过后，自动展示最终产物审查。用户选择“验收通过”“提出修改意见”或“暂不验收”。
7. 接受后AI自动应用已验收知识提案、归档并释放活动指针。

不需要单独输入doctor、approve、accept或confirm。Git提交不是继续条件，用户在交付后另行决定提交、合并和发布。

## 在flow里表达意图

```text
/aidevflow-flow
/aidevflow-flow 到哪了
/aidevflow-flow 暂停
/aidevflow-flow 继续
/aidevflow-flow 放弃当前任务
/aidevflow-flow 检查环境
```

- 无参数：继续当前检查点，或重新打开尚未完成的原生审查。
- 查看状态、检查环境：只显示结果，不改变需求和批准。
- 暂停、继续：保存或恢复当前执行；未知在途执行需要核实旧进程已结束，不盲目重复。
- 放弃：在原生交互中明确确认，保留代码与证据，不发布未完成知识。
- 普通修改意见：需求入口阶段更新Intent/PRD并重新审查；最终交付阶段回到批准范围内的AI修复及独立验收。

即使输入“好的”“验收通过”，系统也会呈现当前版本的原生审查，不把文本直接写成批准。业务描述中的“暂停按钮”等不会被当作生命周期操作。

## 状态与异常

正常流程：discovery → spec → plan → applying（必要时repairing→applying） → verify → delivery_ready → done → finalizing → archived。

首次知识初始化、规格、计划、测试和回归没有额外人工门禁。常见失败由AI自主诊断修复。2.4.2默认在同一工作段内允许读取256次、主Agent模型请求160次或活跃时间10800秒（3小时）。材料实际登记、工作段首次有效原生结果和任务/基线推进可开始新进展预算段；等待原生角色返回不计活跃时间。重复打包、诊断文字、revision、角色调用ID、重复approve和无效结果不重置预算，同任务反复生成候选也不能无限刷新额度。状态中的autonomy_budget列出上限、用量和具体触发项。
操作拒绝第3次触发 operation_correction 纠错指引；同类拒绝12次或同段累计30次、测试连续失败20次、基线审查未通过16次、独立验收格式/执行连续失败16次、产品回修超过20轮才达到对应硬上限。操作失败预算只在真实任务/基线推进后结清，角色派发不清空它。缺少真正业务决定时仍保留检查点。

在 `.aidevflow/project.json` 的 `progress_limits` 配置 providers/reads/seconds，在 `failure_limits` 配置 operation_warning/operation_repeats/operation_total/execution_failures/baseline_reviews/verification_failures/repair_rounds。均为正整数，operation_warning < operation_repeats <= operation_total。普通升级保留自定义值；需要将已有项目提高到新默认额度时，关闭旧 zscode 后执行：

```powershell
python D:\InspurCode\ai-native-autonomous\scripts\aidevflow.py --root D:\InspurCode\ai-native-zscode install --runtime zscode --upgrade-active --extend-budgets
```

安装器备份活动检查点和旧安装，保留高于新默认的自定义额度，不自动恢复已停止任务。重启后使用 `/aidevflow-flow 继续`。

2.4.2 的基线上下文返回 phase/next_action，仅 review_required 且返回 task_call 才派发。reviewing 等待实际结果；approved 直接 run 或 repair_apply；correction_required 按最新 repair_failure 证据修正草稿后重提。未取得有效 RED 的准备故障可按 allowed_actions 受控 retry；已接受 RED 的冻结测试保持受审查换版。旧 server 循环停点升级后自动识别历史重放失败；安装前必须结束并恢复未知在途角色，不能删锁或伪造结果。

这里的预算是本地防空转计数，不是模型服务的token、费用或账户额度。默认值位于`aidevflow/backends/zscode/progress.py`的`LIMITS`；安装项目的`.aidevflow/project.json`可用`progress_limits`覆盖`providers`、`reads`和`seconds`，均只接受正整数。正常使用无需调整上限。

会话重启后运行flow恢复。独立角色结果从当前原生分支恢复，仅接受已有派发锚点的真实结果。未知旧执行经实际用户核实后可以恢复，原记录标记为未知终止，不能算独立验收通过，必须重新派发验证。

产物变化会使旧验收失效，自动重新回归和独立验收。最终接受绑定用户看到的候选和bundle，过期交互不能接受新版本。

## 更新与边界

方法源为本项目 `.claude/skills-light/`，更新后在业务项目无活动任务时重装并重启zscode。活动任务默认禁止重装；已安装的light任务可先关闭zscode并确认无在途执行，再显式用 `install --runtime zscode --upgrade-active` 备份原安装与检查点，保留run_id、Intent/PRD批准和执行证据；运行中的协调者持锁时拒绝升级。同名冲突或受管文件被修改时先备份审查，不静默覆盖。旧版缺少Intent/PRD或独立验收的任务不能自动迁移成新版本已批准任务。

一项目一个活动任务、一个协调者；计划最多20个原子任务；按工作项使用独立测试文件，通过RED后冻结。开发编辑仅允许当前任务声明文件；入口与设计阶段允许编辑当前任务 specs/drafts/<run_id>/ 内的草稿；单条检查超时120秒；task只允许受管只读需求分析、基线审查与独立验收角色。

框架不修改zscode自身权限审批配置。shell检查不是OS隔离；任意浏览器验收需要专门适配。冻结测试错误使用草稿修订→原生AI基线审查→隔离重放RED→原子更新基线→GREEN与全量回归，不增加正常人工门禁。

## 开发维护验证

```powershell
python -B -m unittest discover -s tests -p test_light_workflow.py -v
bun test tests/zscode-light-extension.test.ts
python -B tests/zscode_light_smoke.py
```

终端内部诊断仍保留给维护人员，不是用户流程步骤。自动测试、真实命令发现和真实模型业务演练分别记录，不能相互替代。

## 2.3 产物提交与升级

用户调用方式不变。内部需求通过完整 schema 的原生分析角色生成，由宿主保存结果文件，再由 Gate 按原始结果路径登记。规格、计划和知识采用 validate/submit 的 kind/path 参数；不再传通用 data 对象。校验调试草稿不会写入正式 Intent/PRD。

状态默认返回摘要和文件引用，完整文档按需读取。方法约定只保存在扩展 flow.md，各 skill 不再重复整份规则。空闲项目重装时仅移除清单登记、哈希未改变的旧运行时文件，保留其他用户文件。

原测试项目已有light活动任务时，关闭zscode后可在原安装命令末尾增加 `--upgrade-active`。备份位于安装返回的 `upgrade_backup` 路径（该run的 `upgrades/` 下）。升级不会重置失败计数或直接标记通过；重启后用 `/aidevflow-flow 继续`，让AI诊断并走基线修订或实现修复。不要直接编辑状态JSON清除活动指针。

## 2.4 受控修订与恢复

status 返回阶段 skill_path、冻结/未冻结修复分支和错误上下文。初始 AI 审查 revise 且测试未冻结时，直接修正测试再审查；规格/计划错误通过 repair_propose 的 spec_path/plan_path 草稿审查，不能通过重提规格清空已有 RED。原始范围、验收和历史证据保留。

全量回归失败自动返回实现任务，保留 repair_evidence，修复后重新 GREEN/回归/独立验收。knowledge 在初始化之后提交为提案，验收前固定、接受后归档。最终回归支持 checks（http/browser/static）与每轮新生成的 evidence_paths，物理证据固定进 bundle。

真实环境失败的未接受工作项和 prepare 可以经审查纠正工具命令；同运行器在新环境中的确切路径可迁移，不能借此删除业务检查。独立验收仍须真实派发及完整回执。

业务矛盾通过 report_conflict 保存 needs_attention/business_decision 检查点。尚无冻结基线时，用户在 flow 输入实际业务意见后重新生成 Intent/PRD 并审查；已有冻结基线时不直接清空重开，保持停止，或由用户明确选择按原批准范围继续修复。普通技术纠错不增加人工审批。
