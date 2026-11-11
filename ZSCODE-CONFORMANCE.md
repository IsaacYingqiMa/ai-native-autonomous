# zscode 原生协议先期测试记录

本文记录 2026-11-09 正式适配前的测试状态。2026-11-10 已完成正式入口适配，当前安装和使用以 [zscode 使用教程](ZSCODE-USAGE.md) 为准。

2026-11-09 在隔离临时项目中完成 22 项检查，全部通过。测试目标为本机 zscode/0.1.0，可执行文件 SHA-256：

    6d4332d503954e30a2c7d00ebf4066971702a047357d8417d6bd17a8e9f64c15

这确认当前构建具备 AIDevFlow 接入所需的基础原生协议能力。正式 zscode 适配器仍未实现和启用；不能把协议通过解释为现有套件已经支持 install --runtime zscode。

## 已验证的行为

| 范围 | 实际验证 |
|---|---|
| 项目资源发现 | 从 .zscode 加载测试扩展、公共流程技能和当前五个 zsspec 技能；忽略测试项目中的 .omp 技能标记 |
| 原生运行上下文 | 主代理、子代理身份和原生工具清单可见；子代理扩展正确绑定到独立身份 |
| UI 交互协议 | 保存并暂停、取消、延迟 35 秒回复；无界面明确报告 hasUI=false，测试检查点保持暂停 |
| 角色派发 | 真实原生 task 执行单个项目角色；调用与结果的 ID、输入一致 |
| 角色权限 | 原生角色实际工具集为 read、grep、glob、yield，不含写入、shell 或再次派发 |
| 结构化结果 | yield 的 data 对象产生可验证结果；错误 schema 不产生有效结构化完成 |
| 工具拦截 | 扩展在工具执行前拒绝写入，目标文件未生成 |
| 取消执行 | RPC abort 中断真实原生角色；取消未转为成功 |
| 会话恢复 | 退出并启动新进程后打开同一会话；恢复原调用 ID、代理 ID 和完整结构化结果 |
| 接入保护 | AIDevFlow 继续拒绝未启用的 zscode runtime |

本次测试约 40 秒，包含 35 秒的主动延迟。现有 OMP 扩展另有 39 项回归通过。

## 测试方式与边界

- Python 驱动已安装的真实 zscode 可执行文件，经原生 JSONL RPC 发送请求。
- 测试扩展注册本地脚本模型，固定生成工具调用和结果；没有调用外部付费模型。
- 角色派发、原生工具执行、schema 验证、取消和会话持久化由真实 zscode 运行时执行。
- UI 选择及检查点是明确的测试样本，不是对真实业务 Intent/PRD 的人工批准。
- 只验证了 zsspec 资源发现，没有让模型执行五个技能的工程流程。
- 尚未验证 AIDevFlow 的正式 zscode 安装、审批绑定、受控方法读取、Gate 回执接入和业务全程交付；这些需要完成适配器后再验收。
- 项目、会话、测试配置和测试账号目录均位于 .test-tmp；未改动原始 zsspec、业务项目或用户全局配置。

## 接入时需要保留的协议细节

1. getSessionFile() 在首次模型回合前即可返回路径，但文件可能尚未创建。恢复应依赖原生会话接口和实际持久化历史，不能把早期文件不存在直接判作恢复失败。
2. 结构化结果位于 details.results[].structuredOutput.data，必须同时验证原生执行状态和 structuredOutput.status，并绑定原始调用输入。
3. 测试脚本的自定义 provider 在扩展加载后才可用，因此探针在 ready 后先确认模型切换成功，再发送脚本模型回合。该探针启动策略不改变业务项目的模型配置。
4. 测试结论绑定当前可执行文件摘要；相同的 0.1.0 版本文字不能替代对后续构建的协议回归。

## 复跑

需要 Python 3.11+、Git、PATH 上的 zscode，以及本地完整的当前 zsspec 包。

    python -B tests/zscode_native_smoke.py --delay-seconds 35 --output .test-tmp/zscode-conformance-final.json
    bun test tests/omp-extension.test.ts

脚本：[tests/zscode_native_smoke.py](tests/zscode_native_smoke.py)；原生扩展样本：[tests/fixtures/zscode-conformance.ts](tests/fixtures/zscode-conformance.ts)；本次详细结果：[JSON 报告](.test-tmp/zscode-conformance-final.json)。
