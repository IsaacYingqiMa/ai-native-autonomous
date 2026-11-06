# zsspec-light 2.4 · AIDevFlow 托管版

唯一正常人工审查：入口 Intent/PRD 批准、独立验收通过后的最终产物验收。
light负责批准之后的规格、计划、TDD与知识方法；AIDevFlow负责状态、证据与独立验收闭环。

流程：需求讨论 → Intent/PRD → 人工批准 → spec → plan → apply/自动修复 → 独立验收 → 最终人工验收 → archive。
Git提交不阻塞研发完成；知识初始化与测试结果自动校验。使用 `/aidevflow-discover <需求>` 或 `/aidevflow-flow <需求>` 开始，入口会自动展示Intent/PRD与最终产物的原生审查选项；无需额外批准或验收命令。`/aidevflow-flow` 可随时继续、查看进度或重新打开审查。

方法源位于本目录，随安装器分发到业务项目 `.zscode/skills`。执行参数统一见安装后的 `.zscode/extensions/aidevflow/flow.md`。
