---
name: zsspec-plan
description: 按规格与模块依赖生成串行 TDD 任务、检查和验收覆盖。
metadata:
  version: "2.4"
---

读取批准规格、设计及 environment 的确切工具路径。生成最多20项原子任务：可选 prepare → 每组 RED/GREEN/可选 REFACTOR → 最后 regression。任务定义使用安装后的 plan.schema.json。
每项有 id、title、kind、specs、精确 files、argv；RED/GREEN/REFACTOR 有 group，后两者有局部 regression argv。同组命令相同；每组独立测试文件；prepare 在 RED 前准备支持文件与依赖；最终 regression 的 files 为空。
按模块依赖串行安排，核对 AC→Sx→任务→测试/检查的覆盖。结构化 design 的 group 和完整文件路径必须逐项对应；已有只读代码引用不代表必须修改。接口层测试使用明确的上下游契约，避免后续实现依赖被冻结测试中的隐含数据格式。
最终 regression 可增加 checks 数组，每项包含 id、kind(http/browser/static)、specs、argv、evidence_paths。证据路径限定本 run 的 specs/drafts/<run_id>/evidence/；runner 每次生成新报告/截图/trace，Gate 固定不可变副本纳入 bundle，历史文件不能充当本轮通过。
Web 验收应有真实 HTTP 和浏览器行为测试，由计划中的 runner 启动/清理服务并检查交互；依赖在 prepare 中准备，缺少环境不能报通过。视觉/行为证据不足时报告具体缺项。
将 tasks 保存到 plan.json，validate/submit kind=plan 后自主开发。执行期计划纠错使用派生提案，不放弃任务重建。

工具字段与原生角色协议见安装后的 `.zscode/extensions/aidevflow/flow.md`。
