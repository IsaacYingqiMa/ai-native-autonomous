# OMP 适配与恢复

协议基线为 omp 18.13.2。OMP 为主要执行器，Claude 兼容适配独立保留。设计目标不等于已验证能力。

## 任务与回执

CLI 新安装默认 omp；Python install(source, target) 保留历史 Claude 默认。新 intake 固定执行绑定和方法根，历史无绑定任务继续按 Claude 规则读取。旧回执不改写，omp 写 invocation-v3；Gate 检查 runtime、角色、输入上下文、输出摘要、bundle 和证据轮次。

关键角色默认 blocking、只读、结构化返回，一次一个角色，支持单元素 tasks 数组或扁平 schema。多个关键角色同批调用明确拒绝，保持当前串行业务策略。异步 launch 保持 dispatched；不要把角色改为后台后将输出展示当成正式完成。

活动任务中原生 task 派发仅开放上述三个受管角色；普通实现和修复由协调者使用受控工具完成。任意自定义角色、后台业务 worker 和并行任务尚未建立独立策略，不能绕过这个入口使用。

## 工具覆盖与边界

普通业务只读工具不启动 Python。受管方法资源读取经过 bridge，路由到固定的物理来源并核对摘要，每次附带执行约束；原生用户 `/skill:` 命令和自动预载不等同于这条受控读取路径，同名副本由 doctor 提示。关键角色读取进入策略检查，Verifier 只能读取当前固定 bundle。路径检查覆盖 write、edit 的 hashline/replace/patch、hashline 全部文件及 MV 目标、标准 apply_patch 文件/移动头和 notebook。虚拟 write URL 不当成本地文件放行。

Shell 沿用共享 best-effort 检查及 Gate 的完整性校验，不是 OS sandbox，不能保证分析任意脚本的全部间接副作用。支持的 CLI 是状态写入入口；不能换工具绕过拒绝。

已分类 LSP 只读动作、code_actions 列表和显式 apply=false 的重命名预览可以使用。活动任务中，LSP/AST 直接修改、eval、未知 MCP 写入及未验证副作用明确阻止，通过受控 edit/write 或 Gate 检查命令完成业务工作。本版不声称覆盖全部 omp 原生写入功能。

原生 browser/computer/eval 仍不开放。已提供本地 Playwright 声明式 runner：只连接声明的本机 HTTP(S) origin、独立浏览器上下文、不使用用户配置档、阻止跨 origin HTTP 请求、WebSocket、service worker 和下载；支持导航、点击、填表、按键、可见性和文本断言，逐步截图。活动任务检查冻结计划与证据目录，Gate 捕获实际退出结果、报告和截图。static/manual 用 record-check 如实登记，manual 仍需实际查看图片。截图或脚本通过不代表视觉验收通过。详细说明见流程 references/omp-capabilities.md。

方法根支持 install --skills-root 和无活动任务时的单候选识别。配置路径不存在而原生目录有包时，doctor 给出重装参数；多候选不猜。项目配置和供应方原文不会被模型静默改写。新方法快照覆盖根 README 和辅助脚本，旧快照继续使用原指纹格式，不原地升级。

用户原生权限、审批及全局配置保持有效，框架不自动设置 yolo。

## 进程、并发与性能

扩展按绑定创建按需 stdio Python worker；可变状态保存在 factory closure，避免父子会话串数据。主会话首次受控操作申请项目协调租约；另一个协调会话不能同时写入，只读查看不占租约。

CLI 写命令使用独立 writer 锁串行化，doctor/status 保持可读。当前锁覆盖完整写命令，包括 run-tests；另一写命令有限等待后报忙。分段事务和并行业务流水线留待后续，不隐含宣称已实现。

轻量 summary 不计算候选哈希；完整 status 复用同次摘要。omp 按 invocation 增量更新，不每条事件重放全部历史。测试前后、阶段推进、bundle 和交付保持真实内容校验。设计稿的毫秒预算不是实测保证。

## 恢复

dispatch 在执行前落盘，包含原始输入与业务上下文。结果和 reducer 快照保存在 omp-observations，同事件幂等。身份冲突、不同输出、非法 JSON、原生 schema invalid、取消和失败均不能计通过。

recover-runtime 恢复适配器的持久来源。session_start/switch 时，扩展从 SessionManager 当前选定分支恢复已派发调用的 typed toolResult。没有派发锚点时不能事后补造派发。

本版不扫描整个 ~/.omp 会话树，不从时间猜当前分支。压缩后原生结果不可见、源会话缺失或后台结果未进入支持路径时，保持待诊断；先恢复原生会话并确认旧执行结束，再决定是否新派。

bridge 协议版本 1，单帧上限 8 MiB，具有 request ID 与超时。管道失败不盲重试未知状态操作，先 status/recover。worker 退出后由 OS 释放锁，不清理用户已有服务。

## 验证记录

自动测试、真实 RPC 发现、真实角色调用、完整业务演练和浏览器验收分别计量。未完成演练不能作为完整业务交付。

2026-10-28 在 Windows、omp 18.13.2 上已实测原生 RPC 能发现流程命令和 Skill；真实模型通过原生 task 执行 Analyst、返回结构化结果，正常退出，结果通过 Gate 的 Intent 格式校验。原生输出 schema 使用完整角色结构；结果 Hook 提供不可变文件路径，避免抄写展示树造成类型损失。

完整业务演练任务 `ADF-20261028084822-1a2b3e` 最终达到 `delivery_ready`，完整 status 的 `verdict_current=true`。真实 omp 原生 task 分别完成 Analyst、Planner、Verifier；业务采用 zsspec-managed-v1，完成需求/设计/任务/检查单、工作项、设计审查、测试冻结、有效 Red、实现、真实 Green/回归、静态审查和 bundle-only 独立验收。

该演练是小型 Python 整数加法任务，Green 和回归各实际执行 8 项 unittest，e2e/manual 在任务开始前的配置中关闭。演练经历预算中断、角色输出不合规及适配修复后续跑，并非一次无中断成功；旧失败回执保持原样。首轮耗时促成只读 role-context 和直接结果路径交接；原生参数过滤、yield 封装和精确证据路径问题均在演练中修复，未放宽 Gate 校验。最终状态保存在本机 `.test-tmp/omp-full-final/verify-result.json`，原生记录及 Gate 历史亦保留。没有运行真实前端浏览器验收，不将此任务外推为所有技术栈或所有原生工具均已验证。

原生 yield 必须使用 `{"data": <完整角色输出>}` 传输封装，不能把 schema/task_id 等结果字段直接放在 yield 参数顶层。完整 schema、角色定义和 role-context 均明确这一点；框架消费的是原生 structuredOutput.data，不把封装写入业务产物。

原生任务参数以当前 schema 实际传输的字段为准，不额外注入可能被运行时剥离的 schemaMode。Verifier 的 evidence 必须逐字等于 BUNDLE.json 的 files[].path，使用 bundle 相对路径；路径后附加解释、行号或前置 bundle 目录均不接受。解释放在 reason。

首轮适配回归：Python 全量 158 项通过，随后角色说明调整后的 omp 专项 31 项通过；Bun 扩展测试 9 项通过。原生 RPC 发现通过，确认扩展、流程命令和 Skill 能被本机 omp 加载。原生发现探针不发送模型请求。

本轮对齐修复：Python 全量 170 项通过，Bun 扩展测试 12 项通过，新增来源/浏览器专项 12 项通过。实际 Chromium 完成导航、点击、断言和逐步截图；错误断言返回失败。冻结计划通过 Gate run-tests 执行，报告和图片被捕获，随后单独登记为 AI coordinator attestation。该测试使用合成页面，不是看板业务验收或人工批准。

真实 omp 来源冲突探针在隔离项目同时放置两份同名技能，刻意读取 `skill://claude/zsspec-req`；扩展实际返回固定的 `.omp/skills` 文件，没有读到影子内容，原生 read 无错误退出。记录在本机 `.test-tmp/omp-alignment-native/result.json`。测试项目 ai-native-omp-test 已重装并选择 `.omp/skills`，方法错误及安装摘要错误清零，供应方文件摘要和原 Python 路径保持不变；它仍缺少初始 Git 提交，未启动业务任务。未修改 omp 全局描述数据库。`git diff --check` 通过。

小型 Intent 项目中的 200 次连续 bridge 写入策略检查：worker 冷启动 158.55 ms，策略检查 p50 2.607 ms、p95 3.866 ms。该数据不包含实际写文件或大型候选哈希，不代表大型仓库的验收性能。

另以 1,000 个新增业务文件的候选测量 10 次：完整 status 每次只计算一次候选，p50 805.858 ms、p95 906.361 ms；summary p50 1.682 ms、p95 3.485 ms。summary 不检查证据新鲜度，不能代替完整验收。测量为本机单进程结果，不能外推到网络盘或其他规模。


## 2026-10-29：证据与基线恢复验证

安装清单的 reliability_version 更新为 4。omp 与 Claude 共用写锁、不可覆盖的执行证据、可审查的证据隔离/真实重跑、版本化基线修订和中断事务恢复。pytest 跳过不能作为普通通过；批准例外必须经过明确的 Contract 策略、具体用例豁免和独立验收，最终标记 delivery_ready_with_exceptions。

本轮验证：188 个 Python 测试全部通过（无跳过），包含真实本机 HTTP 与 Chromium、跨线程/进程写入、五条损坏记录恢复、基线完成/回滚/冲突保留、原始提交 Red 重放与例外交付；12 个 Bun 扩展测试通过。安装后的原生 omp RPC 命令发现通过，未发送模型请求。另验证了旧版无基线元数据任务与耗尽修复预算的恢复边界。它们不替代真实模型的业务验收。

本轮仅修改套件源码，未给旧业务项目重装或改写任务。重新安装保留旧执行器绑定和历史；已有损坏必须按 [恢复指南](.claude/skills/aidevflow-flow/references/recovery.md) 留档处理，安装本身不会自动接受旧任务或清除冻结。
