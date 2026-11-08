# AIDevFlow · zscode + zsspec-light

当前主线是 **zscode 安装版 + zsspec-light 2.4**。正常人工审查只有两处：入口 Intent/PRD 和最终产物。中间的规格、计划、TDD、修复、回归与原生独立验收由 AI 自主推进，最终接受后自动归档。

## 安装和使用

业务项目需要 Git 初始提交、Python 3.11+、已配置可用模型的 zscode。

```powershell
python D:\inspur\code\spec-driven\ai-native-autonomous\scripts\aidevflow.py --root D:\Work\my-project install --runtime zscode
Set-Location D:\Work\my-project
zscode
```

在 zscode 中输入 `/aidevflow-flow <需求>`；`/aidevflow-discover <需求>` 也可进入需求讨论。进度、诊断、暂停、恢复和审查都通过 flow 处理，无需其他用户命令。

- [安装与使用教程](ZSCODE-LIGHT-QUICKSTART.md)
- [设计、边界与验证要求](ZSCODE-LIGHT-ADAPTATION-PLAN.md)
- [light 方法源码](.claude/skills-light/README.md)：未来更新 skill 放在 `.claude/skills-light/`，随安装器分发。
- [模型工具协议](integrations/zscode/flow.md)：原生结构化需求结果及复杂草稿通过文件路径登记。

## 稳定性约定

Gate 维护唯一状态和不可变证据，zscode 扩展负责原生交互与实际角色回执。需求分析与独立验收采用明确输出 schema；模型工具使用扁平字符串参数，不传通用 data 对象。草稿校验不登记正式材料；同故障第三次触发纠错指引，默认连续12次或同一无进展段累计30次才停止。真实任务/基线推进结清旧错误，扩展遵从 Gate 的单一停止结论。

基线审查与独立验收的输出契约由宿主绑定当前材料；格式错误在同一bundle内纠正，保留失败结论。防空转预算默认160次主Agent模型请求、256次读取、10800秒（3小时）活跃时间。实际材料登记、工作段首次有效原生结果与任务/基线推进可开启新预算段；重复派发、重复approve、无效输出和单纯替换候选不无限刷新预算，原生等待时间不计入。这是本地保护，不是模型账户额度，配置见安装教程和 [预算契约](ZSCODE-LIGHT-BUDGET-POLICY.md)。

安装包只分发 light 运行所需模块，不携带旧 Contract/Design 引擎。安装器保留全局模型、权限和认证配置；默认只升级空闲项目；已有 light 任务可在关闭 zscode 且无在途执行后，用 `--upgrade-active` 备份安装并保留原检查点，升级后重启 zscode。Git 提交、合并和发布由用户另行决定。

zscode 的 Python 运行时代码位于 `aidevflow/backends/zscode/`，原生 TypeScript 扩展位于 `integrations/zscode/`；共享文件仅保留存储、锁和需求校验。旧 runtime 的目录暂不调整。

## 开发验证

```powershell
python -B -m unittest discover -s tests -p 'test_light_*.py' -v
bun test tests/zscode-light-extension.test.ts
python -B tests/zscode_light_smoke.py
```

真实模型演练由 `tests/zscode_light_live.py` 显式启动，需要联网与凭据授权；自动测试中的角色回执和人工选择模拟不能替代真实模型验证。

旧 OMP/Claude 与完整版方法只保留历史基线，见 [历史使用说明](docs/legacy/README-omp-cc.md)，不再作为新安装主线。

企业经验沉淀与持续改进的规划见 [企业经验与流程改进闭环设计](ENTERPRISE-EXPERIENCE-DESIGN.md)；该能力当前处于设计阶段。

2.4 增加受审查的派生规格/计划修订、全量回归自动回修、知识提案与真实检查附件；未冻结测试可直接纠正重审，已有 RED 基线保留。业务矛盾使用 report_conflict 持久化停止，不等待预算耗尽。实现与验证状态见 [修复记录](ZSCODE-LIGHT-REPAIR-RESULTS.md)。

2.4.2 补齐修订预检/应用失败后的恢复，按候选复用审查并阻止重复派发，保存失败重放证据，支持旧循环停点迁移。状态返回实际可执行的 phase/next_action；已批准候选直接应用，失败候选修改后重提，详见 [恢复契约](ZSCODE-LIGHT-REPAIR-LIFECYCLE.md)。

2.4.3 适配 zsspec v2.0 方法包：Gate 同时接受 v2.0（需求/边界/规格/设计/记录，冲突声明为「记录」子段且必须为"无"）与 legacy light 2.4 规格结构；新增 `build` 任务类型承接非测试任务卡（构建/运行核验/达标，跑通即完成、不得触碰冻结测试），测试卡仍走 RED/GREEN 配对。映射规则见 [flow 集成](integrations/zscode/flow.md)。
