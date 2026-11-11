# AIDevFlow：当前 zsspec 2 / OMP 与 zscode 实现

当前方法协议是 `zsspec-docs-v2`，与供应方技能版本、旧 `zsspec-managed-v2` 名称不同。新任务只发现当前 init/spec/plan/apply/archive 包，不补造旧技能或调用已删除的脚本。供应方五个技能和 README 均保持原样。

## 职责与源码

- `aidevflow/services/`：批准需求到交付的公共服务。design 管方法准备、独立规格/计划审查与执行绑定；execution 管任务级测试和受控进度；revisions 管范围内工程纠错；delivery 管暂存归档和提交；checkpoints 恢复已授权事务。
- `aidevflow/gate.py`：沿用的状态、证据与阶段门禁入口，调用上述服务。保留原来的运行数据格式，避免把目录重排变成历史审批迁移。
- `aidevflow/method_adapters/zsspec_docs.py`：当前规格/任务卡解析、文档结构检查、语义摘要和进度投影。
- `aidevflow/runtimes/omp/`：OMP 安装渲染、预检、工具策略、原生观察与恢复；`runtimes/claude_code/`：Claude 专属安装、预检、观察与会话恢复。旧的 omp.py/runtime.py/agents.py/diagnostics.py 只作导入兼容入口，不复制实现；installer.py 通过 runtime 注册表派发安装。
- `aidevflow/runtimes/registry.py`：显式 runtime 身份与能力。OMP 与 zscode 分别注册 runtime 身份，公共协议位于 runtimes/shared/omp_protocol；回执、目录与安装清单按宿主独立绑定。
- `assets/roles/`、`assets/flows/`、`assets/schemas/`：公共资产维护源。OMP 不再从 .claude 读取或用字符串替换生成角色/流程。
- `integrations/shared/client.ts`：公共 JSONL bridge 客户端；`integrations/omp/`：OMP 原生命令、UI 和事件接入。

首版使用一个活动变更、一个协调者和安全串行任务顺序。供应方并发建议由托管调度策略替代，不能绕过工具保护派发未注册 worker。未来并发能力必须通过对应能力测试；zscode 已接入相同串行流程。

## 业务项目安装布局

```text
.aidevflow/
  project.json                     项目配置
  local.json                       本机 Python，不进入 Git
  engine/
    python/aidevflow/               公共引擎与分目录 runtime 实现
    assets/                        公共角色/流程/示例
    host/shared/                   公共 bridge 客户端
  schemas/                         对 Agent 公开的数据协议
  install/common.manifest.json
  install/runtimes/omp.manifest.json
  runs/ discoveries/ bundles/       保留的持久化协议路径
  active-run.json                   活动交付任务
  active-discovery.json             活动需求讨论
  runtime-manifest.json             历史读取端兼容的汇总清单
.omp/
  extensions/aidevflow/             OMP 原生接入
  agents/aidevflow-*.md             公共正文 + OMP frontmatter/传输说明
  skills/aidevflow-flow/SKILL.md
scripts/aidevflow*.py               薄入口，加载 engine/python
specs/
  MAP.md modules/
  active/<slug>/spec.md plan.md
  archive/INDEX.md <slug>_<date>/
```

方法来源由 skills_root 单独指定，既有 .omp/skills 或 .claude/skills 不自动搬迁；也支持中立的 .aidevflow/methods/zsspec。每个任务在首次方法调用前固定完整原文快照。安装器不会下载、升级或覆盖供应方包。

公共代码只安装一份。不同 runtime 的原生产物分别归属各自清单。OMP 安装先渲染完整文件计划，再校验活动上下文，禁止在活动任务/需求讨论中更新已固定代码。相同内容重装允许；历史数据不覆盖，活动任务不迁移 runtime。升级需要结束/受控归档当前上下文并重启宿主。

## 当前自动流程

1. 原生 /aidevflow-flow 讨论 Intent、PRD、规则与验收场景，用户确认准确版本并授权自主开发。新 OMP 安装默认启用 reviewed-discovery-v1。
2. prepare-method 固定 slug 和方法快照；zsspec-init 补齐真实知识底座后 record-initialization。
3. spec 仅落实已批准内容。需求价值质疑、范围剔除与已解决问题不重复执行；普通工程选择自主记录。代码中正待改变的旧行为不视作业务冲突。
4. prepare-artifact-review --kind spec → 只读 artifact_reviewer → record-artifact-review。超过五条规格先自动准备 blind 请求；其读取范围只含批准材料与模块知识，不含规格草稿。再进行实际规格审查。
5. plan 生成后同样独立审查。可修复缺陷 changes_required 自动返修，不改批准材料；真实新业务决策 requires_decision 保存具体问题并停止，不伪造用户答案。审查返修有持久上限。
6. Contract Planner 从真实已审查产物生成执行绑定。register-contract → advance design → compile-work-plan → advance build。固定 canonical acceptance → Sx → task/check 关系；多实现卡必须声明 task_checks，防止首个局部任务被整项验收卡住。
7. apply 按工作项开发。测试卡生成真实测试，审查报告通过 record-design-review 留档，freeze-tests 固定本任务测试/支持文件，再取有效 Red、实现到 Green。无测试卡使用真实构建/运行/指标检查。static/manual 保留明确的 AI 声明来源和证据要求。
8. 完成工作项需要具体 observation 和当前有效检查。Gate 回写勾选、验收、组状态与完成块，只排除这些明确执行槽位的语义摘要。R1 开始后必须真实重放命令。全部测试/支持文件冻结、所有工作完成且当前检查通过才可进 Verify。
9. prepare-delivery-archive 创建可写临时 specs 工作区；archive 在其中更新知识和 INDEX，移动当前变更。seal-delivery-archive 只接受允许的知识文件及原样 spec/plan 迁移，保存前后内容对象。
10. prepare-verifier 把批准材料、spec/plan 独立审查、代码、执行结果和暂存归档清单打成固定包。最终 Verifier 只能读取该包。验收失败回到实现修复并重开工作；不把“缺证据”自动当成人工业务决策。
11. 通过后 commit-delivery-archive → advance delivery_ready。归档对象仍逐个核对真实字节；代码候选与被验收的归档元数据分别绑定。任何未审查修改不能借归档获得许可。Git 提交、合并、发布不自动执行。

## 修订与恢复

范围不变的工程缺陷：begin-engineering-revision --reason → 只改返回的草稿路径 → 可在返回的 execution-contract-draft.json 中提出有界技术文件/检查声明更新 → 重新独立审查 spec/plan 及该执行草稿 → commit-engineering-revision。canonical acceptance ID、业务验收含义与审批不变；经审查可细化 S-ID 映射和技术文件/命令范围；工作项重开，测试需重新审查冻结，旧证据 epoch 失效。修订会保存历史，事务中断用 recover-engineering-revision。

普通修复无需追加人类授权，包括 AI 写错的冻结断言。当前文档协议按未变化的候选/审查对象识别重复无进展，不再将工程纠错与验收缺陷累加到一个小额度审批预算。无进展时使用 automation_stalled 技术检查点并保留证据；真实业务决定仍使用 needs_human。旧共享预算造成的普通停止可由 recover-routine 恢复，不伪造用户批准。验收退回只重开受影响工作项及后续依赖。

新任务在 prepare-method 固定实际起始工作树，包含尚未提交的上一版实现；当前冻结断言经修订后必须重新 Red 回放，旧测试哈希的 Red 不能替代。计划解析保留 Sx-n/Pn/Tn/Rn，未知复选框直接报错。Gate 同步完成状态与实际完成时间，归档必须携带已完成计划，重复保存不会改变封存内容。

运行检查使用 check-context 和 record-check --observations，逐条保留批准场景、真实观察与对应证据；未验证场景不能记录通过。该记录仍明确标为 AI attestation，独立验收需要检查其内容和佐证。验收包增加 candidate-binding.json、verification-summary.json 与短 E 编号，分别说明规范化身份、真实测试计数和证据引用。仅引用错误可交给独立角色纠正原结果，不能由协调者转录或改判。

真正需求变化不得走该通道：记录具体问题和变更建议，等待实际业务决定；不能直接改已批准 Intent/PRD。当前不会自动迁移批准范围。

归档提交中断用 recover-delivery-archive；新用户 /aidevflow-flow 入口会先自动完成已存在、已授权且字节一致的工程/归档事务，再做只读预检。冲突保留现场，绝不覆盖后来编辑。尚未提交的归档可 reset-delivery-archive 后重新生成。

角色派发前保存精确输入引用，结果必须来自匹配的实际原生执行。未结束的角色存在派发围栏，继续回合不能重复创建角色。丢回调先 recover-runtime；纯文本声称完成和后台 launch 都不算完成。

续跑只有一个所有者：用户已有原生 Goal 时选 native，否则 bounded_stop/manual。文档草稿变化也计入持久进展。预算、用户暂停、未知执行和真实权限缺口不被无限循环掩盖。宿主进程退出后重新从同一入口恢复，不声称可以让进程永不退出。

## 验证口径

在 AIDevFlow 源码根目录运行下面的命令。当前 zsspec 集成测试从 `.claude/skills` 读取完整五技能包，原生入口测试需要本机安装对应宿主。

```sh
python -B -m unittest discover -s tests -v
bun test tests/omp-extension.test.ts
python -B tests/native_workflow_smoke.py --runtime omp
python -B tests/native_workflow_smoke.py --runtime zscode
```

test_zsspec_docs 使用当前原始技能包和真实 Python 断言验证 Red/Green、自动返修、原生回执协议、工程修订、归档及中断恢复；测试中的审查结论和最终 verdict 明确为协议样本，不是模型质量证明。OMP 扩展测试验证原生交互边界；RPC 探针不发送模型请求。真实模型业务试点需要单独记录，不能由这些测试替代。

zscode 已接入正式 runtime，原生薄入口位于 integrations/zscode，共享流程控制位于 integrations/shared/omp-flow.ts。安装与日常使用见 [zscode 使用教程](ZSCODE-USAGE.md)。正式入口集成测试使用脚本模型驱动真实宿主、Gate、角色、行为检查、工程复审与归档；原生协议先期记录见 [测试记录](ZSCODE-CONFORMANCE.md)。

2026-11-10 本机验证记录：

| 检查 | 结果 |
|---|---|
| Python 按模块回归 | 293 项，291 通过，2 项浏览器相关检查跳过 |
| 公共扩展行为在 OMP / zscode 入口执行 | 各 39 项通过 |
| zscode 原生协议探针 | 22 项通过，正式适配已启用 |
| OMP / zscode 正式入口集成 | 均从 /aidevflow-flow 到 delivery_ready，覆盖原生需求审阅、独立角色、工程修订复审、真实行为检查失败到通过、独立验收、归档提交及下一需求衔接 |

集成测试运行实际宿主与 Gate，模型响应为脚本样本。回归中曾有一次候选捕获保护拒绝。加入前后摘要诊断后，证据组 92 项重跑及该项连续三次独立复核通过。

普通修复适配的后续验证中，316 项 Python 回归通过，OMP 与 zscode 扩展各 39 项通过。zscode 原生入口集成到达 `delivery_ready`，验收有效、归档已提交，并可从同一入口提交下一项需求。原生集成使用受控脚本模型，真实业务验收需使用项目配置的模型和环境执行。

本轮只更新套件源码，没有覆盖正在使用的业务项目安装副本，也没有修改原始 zsspec 包。活动上下文的安装保护仍保留，不能直接覆盖旧引擎或删除活动指针来绕过。
