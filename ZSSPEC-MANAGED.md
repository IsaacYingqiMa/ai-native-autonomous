# 当前 zsspec 托管协议

本版以供应方 v2.0 的 init → spec → plan → apply → archive 为唯一新任务方法。AIDevFlow 协议名 `zsspec-docs-v2`，不是旧 `zsspec-managed-v2` 脚本协议的延续。原始技能和模板不作修改。

完整架构和检查点见 [ARCHITECTURE](ARCHITECTURE.md)，安装与用户入口见 [README](README.md)。

托管规则：批准材料是业务依据；spec 跳过重复价值质疑、范围剔除和已解决问题，保留工程质量与真正冲突检查；各阶段报告不暂停流程，由 Gate 驱动。规格/计划分别独立审查，缺陷自动返修。测试按活动任务审查冻结并执行有效 Red/Green。进度由 Gate 回写明确槽位，不能借状态更新修改任务描述或预期。当前调度为串行，供应方并发建议由托管策略替代。

独立验收前暂存归档，检查通过后提交精确审查过的知识更新与规格迁移。不调用旧 zsspec_check.py，不要求旧 close 或额外用户验收指令来完成技术交付。业务验收、提交与发布仍与技术状态分开。
