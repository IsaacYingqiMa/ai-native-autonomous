# ZSSpec 技能包

基于 Spec-Driven Development (SDD) 规范驱动开发的 Claude Code 技能包。规格链路由校验脚本机器化保障：编号、覆盖闭合、越界、快照一致、状态迁移全部脚本断言，模型零手编状态。

## 技能列表

**随包分发（9 个技能）**

| 技能 | 职责 | 触发语 |
|------|------|--------|
| `zsspec-init` | 项目初始化（协作入口 / zsspec.yml / 公共设计） | "初始化项目""创建CLAUDE.md" |
| `zsspec-req` | 需求讨论收敛与 REQ 生成（`--docs` 读 reference/ 产品文档）；确认后经脚本 `baseline` 定稿 | "这个功能怎么做""帮我梳理需求""写需求""落REQ" |
| `zsspec-plan` | **规格设计三层**：无参自动流水线，`--des` / `--task` / `--chk` 单阶段，`--update` 增量重编；每阶段 verify 断言 + baseline 定稿 | "写设计文档""拆任务""写检查清单""生成测试用例""生成规格文档" |
| `zsspec-apply` | 开发执行：`gate` 开工门禁；TASK 唯一执行依据（AC 快照内联）；状态经脚本 `set` 回写 | "开始写代码""开搞""实现了" |
| `zsspec-test` | CHK 统一测试执行：`--static` 读代码 / `--auto` e2e / `--manual` 看界面；无参自动流水线 | "帮我review代码""跑测试""运行 e2e""执行检查项""测试报告" |
| `zsspec-done` | 验收收口：脚本 `report` 对账 → 用户确认 → `close` 置功能点已完成 | "做完了""验一下""确认完成" |
| `zsspec-change` | 变更处理（含 Bug 修复）：修订上游后脚本 `stale` 置下游待同步 → `plan --update` 重编 | "要改一下""有问题""修复" |
| `zsspec-feedback` | 技能包问题/建议反馈上报（可选附会话记录） | "反馈问题""上报问题""提建议" |
| `zsspec-standard` | 模板规范 + 校验脚本（不直接执行） | - |

**维护侧（不随包分发）**：`zsspec-doctor` —— 技能包评审诊断（"技能包有问题""评审这些反馈""出改造方案"）。

## 流程图

```
init → req → plan(des→task→chk) → apply → test → done
 ↑      ↑            ↑              ↑       ↑      ↑
 └change┴────────────┴──────────────┴───────┴──────┘
（每层经脚本 verify 断言 + baseline 定稿；apply/test 经脚本 set 回写；done 经 report/close）
```

规格链核心机制（详见 `zsspec-standard/spec-rules.md` 第 9/10 节）：**接力棒模型**（每层只读一份上游：DES 要点表内联 AC 快照承接 REQ，TASK/CHK 只读 DES/TASK，不回看 REQ）+ **两条不变量**（覆盖闭合、无越界，脚本断言）+ **状态唯一落点**（文档状态/条目状态全部脚本回写）。

## 校验脚本

`{skills_root}/zsspec-standard/scripts/zsspec_check.py`，纯标准库 Python 3.8+。**调用者是模型**（各技能按 SKILL.md 固定步骤调用），人不直接调用；stdout 结论先行 + 失败项定位 + 下一步建议，写命令全支持 `--dry-run`：

| 命令 | 作用 | 调用者 |
|---|---|---|
| `status <模块>` | 链路状态 + 条目进度 + 下一步建议 | plan / apply 入口 |
| `verify <模块> [--global]` | 六断言：A1 编号 / A2 覆盖闭合 / A3 无越界 / A4 快照一致（自动置下游待同步）/ A5 状态合法 / A6 齐套 | plan 链尾、随时 |
| `gate <模块>` | 开工门禁：全定稿 + 无待同步 + 覆盖闭合 | apply 开工 |
| `baseline <模块> <范围>` | 草稿/待同步 → 已定稿（逐层前置，先全验后全写） | req / plan 确认后 |
| `stale <模块> <范围>` | 修订上游后置下游待同步（范围=被修订的源层 req/des/task） | change / req --update 后 |
| `set <条目ID> <状态> [--note]` | 条目回写：ID 全局唯一定位 + 迁移校验 + 写后复读 | apply（TASK）/ test（CHK） |
| `report <模块>` | AC×TASK×CHK 对账表 + specs/index.md | done 入口 |
| `close <模块>` | 全链校验通过 → 功能点原子置已完成（strict 判定机器化） | done 用户确认后 |
| `discard <模块>` | 模块整链下线：全部文档置已废弃（终态，唯一写入通道），verify/gate 跳过 | change 判定模块废弃 |
| `migrate <模块> [--apply]` | 存量旧格式文档迁移（默认 dry-run 展示计划） | 存量项目升级 |
| `selftest` | fixtures 驱动自测（分发前必绿） | doctor / 分发校验 |

## 使用方式

**安装（项目级或用户级/全局任选其一）**：

- **项目级**：将本目录下所有子文件夹复制到项目的 `.claude/skills/`（CodeBuddy 为 `.codebuddy/skills/`）
- **全局级**：复制到用户主目录 `~/.claude/skills/`（CodeBuddy 为 `~/.codebuddy/skills/`），所有项目共享

> 注 1：`zsspec-doctor` 为维护侧评审技能，仅保留在技能包维护仓库，**分发/安装时排除该目录**。

技能包根定位（skills_root）由 `specs/zsspec.yml` 的 `paths.skills_root` 控制（缺省 `auto`，详见 `zsspec-standard/spec-rules.md` 第 6 节）：

```
{skills_root}/
├── zsspec-init/SKILL.md
├── zsspec-req/SKILL.md
├── zsspec-plan/
│   ├── SKILL.md
│   └── stage/（des.md / task.md / chk.md 阶段细则）
├── zsspec-apply/SKILL.md
├── zsspec-test/
│   ├── SKILL.md
│   └── reference/（static.md / auto.md / manual.md / playwright-code.md）
├── zsspec-done/SKILL.md
├── zsspec-change/SKILL.md
├── zsspec-feedback/
│   ├── SKILL.md
│   └── scripts/feedback_send.py
└── zsspec-standard/
    ├── SKILL.md
    ├── spec-rules.md
    ├── scripts/（zsspec_check.py + skillhub_log.py + README.md + fixtures/）
    ├── requirement/  design/  task/  checklist/
```

### 在其他编程工具中使用（oh-my-pi、opencode、CodeBuddy 等）

- **oh-my-pi**：官方原生直读项目与用户级 `.claude/skills/`，复制安装后直接可用，零额外配置。
- **CodeBuddy**：官方支持 SKILL.md 格式（frontmatter 字段兼容），但技能目录为 `.codebuddy/skills/`，需一次性复制或软链；项目协作入口复制为 `CODEBUDDY.md`（或由 `/zsspec-init` 检测 `.codebuddy/` 自动生成）。
- **项目协作入口多入口**：`/zsspec-init` 默认双写 `CLAUDE.md` + `AGENTS.md`（内容同源），检测到 CodeBuddy 时生成 `CODEBUDDY.md`——SDD 流程约束在各工具会话启动时均被自动加载。
- **WorkBuddy**：技能包格式兼容 Agent Skills 标准，但其定位为办公向 Agent，工程执行链路（bash/node/git/Playwright）未验证，暂不承诺支持。

### 使用量监测（随包注入，零阻塞）

每个技能的 SKILL.md 首部注入 3 行引导块：技能被使用时先运行 `zsspec-standard/scripts/skillhub_log.py --skill "@zsspec/<技能名>" --data \'{<模块>,<模式参数>,<一句话描述>}\'`，向本机 `%LOCALAPPDATA%\SkillHub\usage.log` 追加一行 JSON 日志（使用人/技能/时间/主机 + 本次使用上下文：模块名、模式参数、一句话描述），并由后台进程批量上报至企业合规服务器（SkillHub 平台，失败自动补传）。机制移植自 SkillHub，与企业内其他 SkillHub 技能共享本机身份登记。

**上报地址可配置**（服务器会迁移，如 4.49 → 4.48；**项目实际开发以 `specs/zsspec.yml` 的 `skillhub.report_url` 为准**）：优先级为环境变量 `SKILLHUB_REPORT_URL` > `specs/zsspec.yml` 的 `skillhub.report_url` > 脚本内建默认（兜底，指向当前服务器）；配置项支持裸地址（自动补端点路径）或完整 URL，缺失/非法一律回落默认不报错。机制见 `zsspec-standard/spec-rules.md` 第 6.3 节。

**协议集中定义，技能内不重复**：身份登记协议（NEED_PROFILE / --set-profile / --skip）由脚本 stdout 指引自描述，机制与数据链路见 `zsspec-standard/scripts/README.md`——改协议只动脚本一处，全部技能的引导块零改动。

**反馈同通道**：zsspec-feedback 的反馈数据（含可选会话记录）走同一 SkillHub 接口上报（`skill=@zsspec/zsspec-feedback`，内容在 data 字段，与使用数据 clientId 同人关联；会话记录须用户显式确认才附带，见该技能 HARD GATES）。

| 要点 | 说明 |
|---|---|
| 权限预授权 | 由 `/zsspec-init` 按本机解析到的 skills_root 合并写入项目 `.claude/settings.json`（skillhub_log / zsspec_check / feedback_send 三脚本，只追加缺失项）；**技能包自身不固化授权**（各机安装位置不同），使用者可随时自行增删。未跑 init 的环境首次执行按正常权限确认，批准即可 |
| 身份登记 | 每台电脑仅一次：首次使用时技能会询问姓名+部门（登记后永不再问；可拒绝，拒绝后以空身份记录） |
| 零阻塞 | 上报在分离的后台子进程进行；脚本任何失败（python 缺失/路径错误等）静默跳过，绝不影响技能正常流程 |
| 隐私边界 | 使用监测记录使用人、技能名、时间、主机名及本次使用上下文（模块名、模式参数、一句话使用描述），不采集会话内容与代码内容；例外是 feedback 附带会话记录（用户显式确认 + 敏感提示 + 5MB 上限） |
| 上报目标 | `specs/zsspec.yml` 的 `skillhub.report_url` 配置（裸地址或完整 URL），环境变量 `SKILLHUB_REPORT_URL` 优先覆盖；均未提供时按脚本内建默认，服务器迁移改配置即可 |
| 禁用方式 | 环境变量 `SKILLHUB_NO_UPLOAD=1` 停止上报（仅本地记录）；删除 SKILL.md 中 `ZSSPEC USAGE LOGGER` 注释块可完全移除监测 |

## 典型使用流程

```bash
/zsspec-init                          # 1 初始化（协作入口 + zsspec.yml + 公共设计）
/zsspec-req 用户管理                   # 2 需求收敛 → 草稿 → 确认后 baseline 定稿
/zsspec-plan 用户管理                  # 3 规格三层流水线（DES 人工确认；TASK/CHK 断言即质检）
/zsspec-apply 用户管理                 # 4 gate 开工 → 按 TASK 开发 → set 回写
/zsspec-test 用户管理                  # 5 三类检查项执行 → set 回写
/zsspec-done 用户管理                  # 6 report 对账 → 确认 → close 收口
/zsspec-change 用户管理 调整登录逻辑    # 变更：stale 置下游 → plan --update 重编
```

单阶段与增量：`/zsspec-plan 用户管理 --des|--task|--chk|--update`；单端设计 `--des --only=front`。所有模块必经设计（DES 强制）。

### 存量项目迁移

存量项目旧格式文档统一走 migrate 通道，进度零丢失：

| 场景 | 做法 |
|---|---|
| 新项目 | 直接用最新版，无需迁移 |
| 存量项目 | `zsspec_check.py migrate <模块>`（默认 dry-run 展示 11 类改造计划）→ 人工复核 → `--apply`（存量进度零丢失，状态/编号/锚点自动补齐）→ `verify` 全绿 → REQ 降级锚提示下 `plan --update` 重编固化 |

## 项目配置（specs/zsspec.yml）

`/zsspec-init` 自动生成（已存在则不覆盖）；改完即时生效。

```yaml
paths:
  skills_root: auto  # auto（缺省）：按解析链探测；或显式路径
test:
  e2e: enabled      # enabled / disabled
  manual: enabled   # enabled / disabled
parallel:
  mode: auto          # auto（缺省）/ off
  max: 3              # 同时执行 PWU 上限
gate:
  mode: strict      # strict / quick
skillhub:
  report_url: 172.22.4.48   # 本项目上报目标（裸地址自动补端点路径，或完整 URL）
```

| 字段 | 取值 | 影响 | 消费方 |
|---|---|---|---|
| `paths.skills_root` | `auto`（缺省）/ 显式路径 | 技能包根定位 | 全部技能与脚本 |
| `test.e2e` | `enabled`（缺省）/ `disabled` | UI 可操作类 AC → 生成 `auto` 检查项 / 不生成（CHK 备注留痕，不转 manual） | plan --chk、zsspec-test |
| `test.manual` | `enabled`（缺省）/ `disabled` | 纯视觉/外部系统类 → `manual` 检查项 / 不生成 | plan --chk、zsspec-test |
| `gate.mode` | `strict`（缺省）/ `quick` | strict：脚本 close 强制全收口；quick：TASK 完成 + static 过 + 用户确认即收口（经 set 显式回写） | zsspec-done |
| `parallel.mode` | `auto`（缺省）/ `off` | 多独立单元且宿主支持子代理时按协议并行 | plan / apply / test |
| `parallel.max` | `3`（缺省）/ 正整数 | 并行工作单元上限 | 同上 |
| `skillhub.report_url` | 裸地址 / 完整 URL（缺省时脚本内建默认兜底） | 监测与反馈上报目标地址（**项目实际上报以此为准**；环境变量 `SKILLHUB_REPORT_URL` 优先） | skillhub_log / feedback_send |

规则：文件不存在 / 字段缺失 / 值非法 → 一律按缺省值执行，不报错；某类型 disabled 后存量未收口项（待执行/失败/阻塞）在下次 `plan --chk --update` 时标"跳过"（已通过项保持不动），恢复后重置"待执行"，不做类型间转换。

## 文档规范

命名、变更记录、检索、文档地图、数据链与状态协议见 `zsspec-standard/spec-rules.md`（第 9 节数据链、第 10 节状态协议）。

### 编号格式

| 类型 | 格式 | 示例 |
|------|------|------|
| 功能需求 | REQ-{模块}-{功能} | REQ-用户管理-用户新增 |
| 验收标准 | AC-{模块}-{功能}-{语义名} | AC-用户管理-用户新增-手机号必填 |
| 处理规则 | R-{模块}-{功能}-{语义名} | R-用户管理-用户新增-唯一性 |
| 设计实体 | API/TBL/RULE/FSM/PAGE-{模块}-{语义名} | API-用户管理-用户创建 |
| 任务 | TASK-{模块}-{功能}-{中文标识} | TASK-用户管理-用户新增-后端接口 |
| 工程任务 | TASK-ENG-{语义名} | TASK-ENG-项目初始化 |
| 检查项 | CHK-{模块}-{分类}-{功能} | CHK-用户管理-FUNC-用户新增 |

### 状态流转

**文档状态**（脚本维护，唯一落点在头部状态块）：`草稿 → 已定稿 →（上游变更时脚本置）待同步 → 已定稿 / 已废弃`

**TASK 状态**：`未开始 → 进行中 → 已完成 / 已废弃`（两跳完成，非法迁移脚本拦截）

**CHK 状态**：`待执行 → 通过（可带标注）/ 失败 / 阻塞 / 跳过 / 废弃`（通过可回归失败，废弃终态）

## 版本信息

- 技能包版本: v1.4
- 适用工具: Claude Code 及其他兼容 SKILL.md 格式的编程工具（oh-my-pi、opencode、CodeBuddy 等；WorkBuddy 未验证不承诺）
