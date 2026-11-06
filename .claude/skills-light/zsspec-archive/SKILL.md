---
name: zsspec-archive
description: 在最终用户接受后应用已验收知识提案，并恢复或完成归档事务。
metadata:
  version: "2.4"
---

前置：当前候选的真实独立验收通过，实际用户接受，候选未变化。调用 archive，应用被验收绑定的知识提案，更新 MAP/modules/INDEX，移动 active 变更，最后释放活动指针。
事务中断重复 archive 恢复，不替换已验收提案，不覆盖他人修改。MAP最多30行，模块最多200行，陷阱去重最多5条。保留原生执行、修订和验收历史；Git 提交不作为门禁。
用户主动放弃由 flow 原生交互处理，保留代码和证据，不发布未完成知识。

工具字段与原生角色协议见安装后的 `.zscode/extensions/aidevflow/flow.md`。
