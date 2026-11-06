---
name: zsspec-init
description: 核实项目事实并初始化知识；已有知识的修订登记为交付提案。
metadata:
  version: "2.4"
---

先查询 status，读取与需求相关的真实布局、代码和数据样例。记录依据、当前事实和仍需复核的假设；不要把拟新增目录写成已有代码。事实与用户需求冲突时说明影响，普通技术选择自主确定。
把 files 对象写到当前 draft_directory 的 knowledge.json，validate/submit kind=knowledge。首次初始化写入 MAP/modules；已有知识后登记为 knowledge_proposal，不直接覆盖知识库。后续工作结合提案与源码核实，提案纳入最终独立验收并在用户接受后归档。
MAP 最多30行，模块最多200行，陷阱合并去重最多5条。初始化和提案均不增加人工门禁。

工具字段与原生角色协议见安装后的 `.zscode/extensions/aidevflow/flow.md`。
