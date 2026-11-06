---
name: zsspec-spec
description: 将批准 Intent/PRD 派生为可执行规格和最小模块设计，保持完整验收范围。
metadata:
  version: "2.4"
---

先读取 status 给出的批准需求与知识，核实有关联代码。spec.json 使用 content、acceptance_map，可增加 design。按安装后的 spec.schema.json 填写。
content 包含需求、规格、已剔除、知识缺口、knowledge 修正项、冲突声明。每个唯一 Sx 写明 WHEN/THE SYSTEM SHALL、失败路径、可执行验收及预期、代码依据和覆盖需求。完整覆盖所有批准 AC。
design 将只读 source_refs 与 modules 分开。模块明确 id、implementation_files、test_files、depends_on、contracts（输入、输出、错误及边界）；完整路径必须与计划一致。新增工程使用结构化 design；旧任务可保留原格式后通过修订补充。
validate 后 submit kind=spec，自动进入计划。执行期纠错不重新直接提交 spec：使用 zsspec-apply 的派生修订提案。业务含义与批准需求冲突时报告确切冲突，不能仅写“无”或自行删除范围。

工具字段与原生角色协议见安装后的 `.zscode/extensions/aidevflow/flow.md`。
