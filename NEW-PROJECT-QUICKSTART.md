# 新项目首次任务

当前安装和统一入口以 [README](README.md) 为准，完整职责与恢复见 [架构说明](ARCHITECTURE.md)。

1. 准备独立业务 Git 仓库及初始提交。
2. 提供完整当前五技能包到 `.omp/skills`：README.md、zsspec-init/spec/plan/apply/archive，保留模板。不要复制旧版 req/test/done/standard 清单。
3. 从框架源码运行 install --runtime omp --skills-root .omp/skills。新项目默认启用需求人工确认。
4. doctor 与 method-inspect 通过后，重启 OMP，在实际会话中执行 /aidevflow-doctor。
5. /aidevflow-flow 提出小型需求，讨论并确认 Intent/PRD/验收场景。
6. 观察系统自主执行规格审查、计划审查、开发、验证、修复、独立验收与归档。
7. 核对完整 status 的 delivery_ready、verdict_current、真实功能和归档内容；没有证据不能计为通过。

建议首例使用已有技术栈的一个可观察行为，并包含一次失败修复和一次会话重启。zscode 使用同一入口，步骤见 [使用教程](ZSCODE-USAGE.md)。并行 worker 和真实生产部署不是本版默认能力。
