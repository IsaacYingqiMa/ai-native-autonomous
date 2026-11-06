# zscode + zsspec-light runtime

本目录维护 zscode 安装版的 light 流程。原生 TypeScript 扩展在 `integrations/zscode/`，方法源在 `.claude/skills-light/`。旧 OMP/Claude 引擎暂不迁移。

| 文件 | 职责 |
|---|---|
| `engine.py` | LightGate、状态投影、规格/计划约束、真实任务执行、文件边界、归档事务 |
| `artifacts.py` | 文件产物提交、schema 校验、原生需求分析回执、简短状态 |
| `review.py` | Intent/PRD 批准、独立验收证据包与回执、最终接受 |
| `revision.py` | 完整设计路径、派生修订、环境命令例外、影响范围与原子应用 |
| `verdict.py` | 按本次规格/证据绑定的严格输出契约，与Gate共用的字段校验 |
| `repair.py` | 初始测试审查、冻结基线修订、原始实现 RED 重放、修订事务恢复 |
| `progress.py` | 持久化进展预算、原生业务问答留档与会话恢复 |
| `environment.py` | 本地 Python/Node/npm/Git 环境事实，不安装、不联网 |
| `bridge.py` | zscode 宿主与模型操作边界、单协调者租约、JSONL 本地协议 |
| `installer.py` | 安装白名单、角色/技能分发、无在途执行时的检查点保留升级 |
| `cli.py` | 安装后供维护使用的 status/doctor；用户入口在原生扩展 |

共享依赖只有 `aidevflow/storage.py`、`locking.py`、`requirements.py` 和包入口。安装后的业务项目不导入旧 `gate.py` 或 `discovery.py`；源码总安装入口仅委派到对应 runtime。

正常人工审查只有 Intent/PRD 与最终产物。基线审查和独立验收均为受管只读 AI 角色，不能由协调者自签通过。

升级使用原安装命令；保留已有 light 任务时追加 `--upgrade-active`，先关闭 zscode。安装器拒绝在途执行和已最终接受但尚未完成归档的任务。备份放在该 run 的 `upgrades/` 下；冻结的 `.gitignore`、批准需求和执行证据保持原样。重启后通过 `/aidevflow-flow 继续` 恢复，原失败不会直接标为通过。

本地验证：`python -B -m unittest discover -s tests -p 'test_light_*.py'` 与 `bun test tests/zscode-light-extension.test.ts`。`tests/zscode_light_case_replay.py --project <已有测试项目>` 在隔离副本复现指定看板故障，只使用已有本地依赖；角色结果为测试模拟，不代表真实模型验证。
