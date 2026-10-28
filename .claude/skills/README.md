# ZSSpec 技能包

基于 Spec-Driven Development (SDD) 规范驱动开发的 Claude Code 技能包。

## 技能列表

| 技能 | 职责 | 触发语 |
|------|------|--------|
| `zsspec-init` | 项目初始化 | "初始化项目""创建CLAUDE.md" |
| `zsspec-req` | 需求讨论收敛与 REQ 生成（`--docs` 读取 reference/ 产品文档） | "这个功能怎么做""帮我梳理需求""写需求""落REQ" |
| `zsspec-des` | 基于已定稿 REQ 生成 DES-前端/后端设计文档 | "写设计文档""设计接口""定数据模型" |
| `zsspec-task` | 基于 REQ+DES 生成 TASK，并输出需求×任务覆盖对照（漏覆盖拦截） | "拆任务""写任务清单""补TASK" |
| `zsspec-chk` | 基于 REQ/DES/TASK 生成 CHK 检查清单（测试用例）文档（automation=static/auto/manual） | "写检查清单""生成 CHK""生成测试用例" |
| `zsspec-verify` | 开发前一致性校验、门禁判断与全局治理检查（默认聚焦小模块/子功能，支持 module/global scope） | "检查spec是否能开工""先查REQ/TASK/CHK/DES是否一致""看看当前这小块够不够写""检查整个specs体系是否健康" |
| `zsspec-apply` | 开发准入与执行（按 TASK 驱动开发并即时回写 TASK 状态） | "开始写代码""开搞""实现了" |
| `zsspec-test` | CHK 检查项统一测试执行：--static 静态审查 / --auto e2e 闭环 / --manual chrome-devtools-mcp 辅助检查；无参数自动流水线并回写状态 | "帮我review代码""做代码审查""合并前看一下""生成测试代码""跑测试""运行 e2e""执行检查项""测试报告" |
| `zsspec-done` | 判定 REQ 是否可更新为已完成；若前序 TASK/CHK 未收口则打回对应阶段 | "做完了""验一下""确认完成" |
| `zsspec-change` | 变更处理（含Bug修复） | "要改一下""有问题""修复" |
| `zsspec-feedback` | 技能包问题/建议反馈上报（可选附会话记录） | "反馈问题""上报问题""提建议" |
| `zsspec-standard` | 模板规范（不直接执行） | - |
| `zsspec-doctor` | 技能包评审诊断（**仅维护侧，不随包分发**）：反馈 → 证据 → 处置建议报告（立即改/缓改/不动/观察） | "技能包有问题""评审这些反馈""出改造方案" |

## 测试域怎么选

> 为什么有这一节：chk / verify / test / done 的触发语都带"检查/验证/测试"语义，是套件里最易混淆的一组（真实反馈："chk/test/verify/done 都像测试，不知道用哪个"）。按"你要回答的问题"定位，30 秒选对入口；技能增删时随技能表同步维护本表。

| 你要回答的问题 | 用谁 | 一句话区分 |
|---|---|---|
| 测试用例清单还没生成？ | `zsspec-chk` | **造单**：写 CHK 检查项文档 |
| specs 文档一致吗、能开工吗？ | `zsspec-verify` | **审单**：spec 门禁，不碰代码 |
| 代码/系统/界面真的对吗？ | `zsspec-test` | **执行**：static 读代码 / auto 跑系统 / manual 看界面 |
| 全部做完、可以收尾了吗？ | `zsspec-done` | **收口**：验收判定与状态同步 |

顺序记忆：chk 造单 → verify 审单 → test 执行 → done 收口。

## 流程图

```
init → req → des → task → chk → verify → apply → test → done
 ↑      ↑      ↑      ↑        ↑         ↑       ↑      ↑
 └change┴──────┴────────┴────────┴─────────┴───────┴──────┘
（test 内部：static 读代码 → auto 跑系统 → manual 看界面）
```

## 使用方式

**安装（项目级或用户级/全局任选其一）**：

- **项目级**：将本目录下所有子文件夹复制到项目的 `.claude/skills/`（CodeBuddy 为 `.codebuddy/skills/`）
- **全局级**：复制到用户主目录 `~/.claude/skills/`（CodeBuddy 为 `~/.codebuddy/skills/`），所有项目共享

> 注 1：`zsspec-doctor` 为维护侧评审技能，仅保留在技能包维护仓库，**分发/安装时排除该目录**。
> 注 2：`.skillhub/` 为使用量监测共享脚本目录（隐藏目录，见下「使用量监测」），**分发/安装时必须一并复制**。

技能包根定位（skills_root）由 `specs/zsspec.yml` 的 `paths.skills_root` 控制（缺省 `auto`，按解析链自适应项目级/全局级安装，详见 `zsspec-standard/spec-rules.md` 第 6 节）：

```
{skills_root}/
├── .skillhub/
│   └── skillhub_log.py（使用量监测共享脚本）
├── zsspec-init/SKILL.md
├── zsspec-req/SKILL.md
├── zsspec-des/SKILL.md
├── zsspec-task/SKILL.md
├── zsspec-verify/SKILL.md
├── zsspec-apply/SKILL.md
├── zsspec-chk/SKILL.md
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
    ├── requirement/
    ├── design/
    ├── task/
    └── checklist/
```

### 在其他编程工具中使用（oh-my-pi、opencode、CodeBuddy 等）

- **oh-my-pi**：官方原生直读项目与用户级 `.claude/skills/`，复制安装后直接可用，零额外配置。
- **CodeBuddy**：官方支持 SKILL.md 格式（frontmatter 字段兼容），但技能目录为 `.codebuddy/skills/`，需一次性复制或软链；项目协作入口复制为 `CODEBUDDY.md`（或由 `/zsspec-init` 检测 `.codebuddy/` 自动生成）。
- **项目协作入口多入口**：`/zsspec-init` 默认双写 `CLAUDE.md` + `AGENTS.md`（内容同源），检测到 CodeBuddy 时生成 `CODEBUDDY.md`——SDD 流程约束在各工具会话启动时均被自动加载。
- **WorkBuddy**：技能包格式兼容 Agent Skills 标准，但其定位为办公向 Agent，工程执行链路（bash/node/git/Playwright）未验证，暂不承诺支持。

### 使用量监测（随包注入，零阻塞）

每个技能的 SKILL.md 首部注入 3 行引导块：技能被使用时先运行 `.skillhub/skillhub_log.py --skill "@zsspec/<技能名>"`，向本机 `%LOCALAPPDATA%\SkillHub\usage.log` 追加一行 JSON 日志（使用人/技能/时间/主机/项目），并由后台进程批量上报至企业合规服务器（172.22.4.49，SkillHub 平台，失败自动补传）。机制移植自 SkillHub，与企业内其他 SkillHub 技能共享本机身份登记。

**协议集中定义，技能内不重复**：身份登记协议（NEED_PROFILE / --set-profile / --skip）由脚本 stdout 指引自描述，机制与数据链路见 `.skillhub/README.md`——改协议只动脚本一处，13 个技能的引导块零改动。

**反馈同通道**：zsspec-feedback 的反馈数据（含可选会话记录）走同一 SkillHub 接口上报（`skill=@zsspec/zsspec-feedback`，内容在 data 字段，与使用数据 clientId 同人关联；会话记录须用户显式确认才附带，见该技能 HARD GATES）。

| 要点 | 说明 |
|---|---|
| 身份登记 | 每台电脑仅一次：首次使用时技能会询问姓名+部门（登记后永不再问；可拒绝，拒绝后以空身份记录） |
| 零阻塞 | 上报在分离的后台子进程进行；脚本任何失败（python 缺失/路径错误等）静默跳过，绝不影响技能正常流程 |
| 隐私边界 | 使用监测仅记录使用人、技能名、时间、主机名与项目路径，不采集会话内容与代码内容；例外是 feedback 附带会话记录（用户显式确认 + 敏感提示 + 5MB 上限） |
| 禁用方式 | 环境变量 `SKILLHUB_NO_UPLOAD=1` 停止上报（仅本地记录）；删除 SKILL.md 中 `ZSSPEC USAGE LOGGER` 注释块可完全移除监测 |

项目 specs 目录结构：

```
specs/
├── zsspec.yml                          # 项目配置（测试策略与验收门禁）
├── requirement/
│   └ [模块]/REQ-[模块].md
├── design/
│   └ [模块]/DES-[模块]-前端.md
│   └ [模块]/DES-[模块]-后端.md
├── task/
│   └ [模块]/TASK-[模块].md
└── checklist/
    └ [模块]/CHK-[模块].md
```

## 典型使用流程

### 1. 新项目初始化

```bash
/zsspec-init
```

生成：
- `CLAUDE.md` + `AGENTS.md`（双写，内容同源）- 项目协作入口；CodeBuddy 场景为 `CODEBUDDY.md`
- `specs/zsspec.yml` - 项目配置（skills_root、测试策略与验收门禁）
- `specs/design/` - 公共设计文档

### 2. 需求收敛与 REQ 生成

```bash
/zsspec-req 用户管理模块          # 讨论模式收敛需求
/zsspec-req 用户管理模块 --docs   # 从 reference/ 产品文档解析需求
```

输出：需求结论表（表格化审查）→ 确认后落盘 `specs/requirement/用户管理/REQ-用户管理.md`

### 3. 生成设计文档

```bash
/zsspec-des 用户管理
```

生成（输入：已定稿的 REQ），输出设计要点表供审查后落盘：
- `specs/design/用户管理/DES-用户管理-前端.md`
- `specs/design/用户管理/DES-用户管理-后端.md`

### 4. 生成任务清单

```bash
/zsspec-task 用户管理
```

生成（输入：REQ + DES），输出任务清单与需求×任务覆盖对照，漏覆盖拦截：
- `specs/task/用户管理/TASK-用户管理.md`

### 5. 生成测试用例文档

```bash
/zsspec-chk 用户管理
```

生成：
- `specs/checklist/用户管理/CHK-用户管理.md`

### 6. 开发前规格审查

```bash
/zsspec-verify 用户管理
```

执行：默认以当前模块或子功能为范围，先做 REQ / DES / TASK / CHK 一致性校验，再做开发前门禁判断；也支持 module/global scope 做模块级或全局 specs 健康治理检查

### 7. 开始开发

```bash
/zsspec-apply 用户管理
```

执行：开发准入检查 + 按 TASK 锁定当前开发任务 + 状态更新 + 编码实现 + TASK 状态即时回写

### 8. 统一测试执行（static / auto / manual）

```bash
/zsspec-test 用户管理                # 自动流水线：static → auto → manual，全量回写状态
/zsspec-test 用户管理 --static       # 仅静态代码审查（--focus/--scope/--mode 等可选）
/zsspec-test 用户管理 --auto         # 仅 e2e：生成 Playwright 代码 → review → 执行 → 回写
/zsspec-test 用户管理 --auto --gen-only # 只生成测试代码，停等 review
/zsspec-test 用户管理 --manual       # 仅 manual 项：chrome-devtools-mcp 辅助检查 + 截图留证
```

执行：读取 CHK 检查项，static 做结构化代码审查、auto 做 Playwright 闭环（三层注释溯源、报告落盘 `specs/checklist/reports/<时间戳>/`）、manual 用 chrome-devtools-mcp 操作浏览器截图留证（`通过 (AI辅助)`），统一回写检查项状态

### 9. 完成验收

```bash
/zsspec-done 用户管理
```

执行：核对 TASK 与 CHK（static/auto/manual）状态与证据，判定是否可将 REQ 更新为已完成；若前序未收口则打回对应阶段

### 10. 变更处理

```bash
/zsspec-change 用户管理 需要调整登录逻辑
```

根据影响程度自动判断处理方式。

## 项目配置（specs/zsspec.yml）

`/zsspec-init` 自动生成到 **`specs/zsspec.yml`**（已存在则不覆盖）；可随时手工修改，改完即时生效，无需重启或同步。

```yaml
paths:
  skills_root: auto  # auto（缺省）：按解析链探测技能包根；或显式路径（项目相对/绝对）
test:
  e2e: enabled      # enabled / disabled
  manual: enabled   # enabled / disabled
parallel:
  mode: auto          # auto（缺省）/ off：多单元并行开关（spec-rules.md 第 8 节）
  max: 3              # 同时执行 PWU 上限
gate:
  mode: strict      # strict / quick
```

| 字段 | 取值 | 影响 | 消费技能 |
|---|---|---|---|
| `paths.skills_root` | `auto`（缺省）/ 显式路径 | 技能包根定位（模板引用前缀）；auto 按 ① 项目 `.claude/skills` → ② 项目 `.codebuddy/skills` → ③ `~/.claude/skills` → ④ `~/.codebuddy/skills` 探测，详见 spec-rules.md 第 6 节 | 全部技能 |
| `test.e2e` | `enabled`（缺省）/ `disabled` | UI 可操作类验收标准 → 生成 `auto` 检查项 / **不生成**（CHK 备注区留痕，不转 manual） | zsspec-chk、zsspec-test |
| `test.manual` | `enabled`（缺省）/ `disabled` | 纯视觉/外部系统类 → 生成 `manual` 检查项 / **不生成**（备注区留痕） | zsspec-chk、zsspec-test |
| `gate.mode` | `strict`（缺省）/ `quick` | strict：CHK 全部收口才能验收；quick：仅强制 static 审查 + TASK 完成 + 用户确认 | zsspec-done |
| `parallel.mode` | `auto`（缺省）/ `off` | auto：多独立单元且宿主支持子代理时按协议并行；off：一律串行 | 全部执行类技能（des/task/chk/apply/test/verify） |
| `parallel.max` | `3`（缺省）/ 正整数 | 同时执行的并行工作单元（PWU）上限 | 同上 |

规则：文件不存在 / 字段缺失 / 值非法 → 一律按缺省值执行，不报错；某类型改为 disabled 后，存量对应项在下次 `/zsspec-chk <模块> --update` 时标"跳过"，恢复 enabled 后重置"待执行"，不做类型间转换。

## 文档规范

命名、变更记录、检索与文档地图规范见: `zsspec-standard/spec-rules.md`

### 编号格式

| 类型 | 格式 | 示例 |
|------|------|------|
| 功能需求 | REQ-{模块}-{功能} | REQ-用户管理-用户新增 |
| 设计文档 | DES-{模块}-{端} | DES-用户管理-前端 |
| 任务 | TASK-{模块}-{功能}-{中文标识} | TASK-用户管理-用户新增-后端接口 |
| 检查项 | CHK-{模块}-{分类}-{功能} | CHK-用户管理-FUNC-用户新增 |

### 状态流转

**TASK 状态**

```
未开始 → 进行中 → 已完成
              ↓
           已废弃
```

**CHK 状态**

```
待执行 → 通过
      ↘ 失败
      ↘ 阻塞
      ↘ 跳过
      ↘ 废弃
```

## 版本信息

- 技能包版本: v1.18
- 适用工具: Claude Code 及其他兼容 SKILL.md 格式的编程工具（oh-my-pi、opencode、CodeBuddy 等；WorkBuddy 未验证不承诺）

### 变更记录

| 版本 | 日期 | 更新人 | 说明 |
|------|------|--------|------|
| v1.18 | 2026-10-23 | xuzhitu | feedback 上报对接 SkillHub（服务端零改动）：发送脚本 python 重写（feedback-send.js 退役），反馈作为 skill=@zsspec/zsspec-feedback 的记录走 usage-logs/batch 同接口，内容装 data 字段、clientId 与使用监测同人关联；身份自取本机 SkillHub 登记替代 git user.name；暂存收敛到 %LOCALAPPDATA%\SkillHub\feedback-outbox\；v1.11 monitor.url 通道约定退役（yml 模板与配置表删除该字段，环境变量 ZSSPEC_MONITOR_URL/TOKEN 停用），doctor 三处措辞同步 |
| v1.17 | 2026-10-23 | xuzhitu | 集成使用量监测（移植 SkillHub 机制）：新增共享脚本 .skillhub/skillhub_log.py（本地 JSONL 落盘 + 身份登记协议 + 后台断点续传上报 172.22.4.49），13 个 SKILL.md 注入 3 行引导块（frontmatter 预授权 + 注释块；登记协议由脚本 stdout 自描述，集中定义于 .skillhub/，技能内不重复）；上报复用企业 SkillHub 平台，skill 标识 @zsspec/ 命名空间；README 补监测说明与分发树 |
| v1.16 | 2026-10-23 | xuzhitu | 新增并行执行协议（spec-rules.md 第 8 节）：PWU 四级（模块/文档/任务/检查类）、读写集封闭、变更清单合并、波次调度、门禁集中、宿主降级链与护栏映射；des/task/chk/apply/test/verify/req/init 八技能挂并行映射；zsspec.yml 增 parallel.mode/max |
| v1.15 | 2026-10-23 | xuzhitu | doctor 报告形态重构（处置导向）：处置枚举收敛为立即改/缓改/不动/观察（不默认全改，不动/观察为正常结论）；结构简化为处置一览 → 逐条方案（每条≤3行）→ 最终决策回填区 → 附录存档（一行一条）；"待拍板决策"改为"仅 ⚠️ 条目需人表态"，逐条决策由人/执行 AI 回填 |
| v1.14 | 2026-10-23 | xuzhitu | doctor 输入体系完善：评审来源文件约定 docs/reviews/inputs/（YYYY-MM-DD-描述命名）；新增 --ref 参考输入（背景文档不拆条目仅作诊断上下文）；阶段③前病历库检索（同主题历史结论防翻烧饼） |
| v1.13 | 2026-10-23 | xuzhitu | 模板双源统一（doctor 首跑报告 D1/D2 落地）：根 spec-standard/（底座）与 zsspec-standard/ 全镜像同步、互不引用，规范文件统一 spec-rules.md（根 README 引用与 CHK 模板引用对齐）；分发树补 zsspec-feedback；新增"测试域怎么选"导航；doctor 体检清单沉淀"双源镜像一致"检查项 |
| v1.12 | 2026-10-22 | xuzhitu | 新增 zsspec-doctor 维护侧评审技能（反馈 → 证据 → 分级方案 → 5+1 轮自验证 → 待拍板报告，落盘 docs/reviews/；仅维护侧，不随包分发；机械体检 LLM 清单制 reference/checklist.md，报告 3 层 7 节模板） |
| v1.11 | 2026-10-22 | xuzhitu | 新增 zsspec-feedback 反馈技能：结构化收集缺陷/建议/疑问，可选附会话记录（默认不带、显式确认门禁，精简/原始两模式，原始限 5MB）；随包发送脚本支持本地暂存与自动重发；上报走统一监测通道（zsspec.yml `monitor.url`，环境变量 ZSSPEC_MONITOR_URL 优先，init 模板已含），未配置时落 ~/.zsspec/feedback-outbox/ 暂存 |
| v1.10 | 2026-10-22 | xuzhitu | 移除技能使用统计：上报脚本、hook、settings.json、usage-server 及全部引用整体删除（SKILL.md 上报节、README 使用统计章节等）；skills_root 解析链与项目记忆多入口（v1.9）保留 |
| v1.9 | 2026-10-22 | xuzhitu | 跨工具/跨安装方式适配：新增 skills_root 解析机制（spec-rules.md 第 6 节，zsspec.yml `paths.skills_root` 缺省 auto，项目级/全局级/CodeBuddy 安装自适应）；项目协作入口多入口（默认双写 CLAUDE.md + AGENTS.md，CodeBuddy 场景生成 CODEBUDDY.md）；技能内模板路径全部改 `{skills_root}` 前缀，去除 `.claude` 硬编码 |
| v1.8 | 2026-10-22 | xuzhitu | rules 三文件（命名/检索/文档地图）合并为 zsspec-standard/spec-rules.md；版本递增改为惯例（非硬约束），变更记录保持强制 |
| v1.7 | 2026-10-22 | xuzhitu | 项目配置 zsspec.yml 落地：init 自动生成；test.e2e 与 test.manual 独立开关（disabled 即该类型整体不做、不转为其他类型，缺省均 enabled）；gate.mode 实现 done 门禁策略化（strict 全量收口 / quick 仅强制 static，缺省 strict） |
| v1.6 | 2026-10-22 | xuzhitu | zsspec-code-review + zsspec-e2e-test 合并为 zsspec-test：--static/--auto/--manual 三类验证一个入口（-- 前缀避免与自然语言冲突），manual 新增 chrome-devtools-mcp 辅助检查（截图留证、通过 (AI辅助) 标注），无参数自动流水线 static → auto → manual 并回写 CHK 状态 |
| v1.5 | 2026-10-22 | xuzhitu | zsspec-e2e-gen + zsspec-e2e-run 合并为 zsspec-e2e-test（--gen-only/--run-only，生成后 review 门禁保留，自动修复限白名单并展示 diff，报告统一落盘 specs/checklist/reports/） |
| v1.4 | 2026-10-22 | xuzhitu | zsspec-test-gen 更名为 zsspec-chk；CHK 格式协议上收 zsspec-standard/checklist 模板（automation 收敛为 auto/manual/static）；chk 支持测试策略联动（specs/zsspec.yml） |
| v1.3 | 2026-10-21 | xuzhitu | zsspec-spec 拆分为 zsspec-des（设计要点表审查）与 zsspec-task（需求×任务覆盖对照，漏覆盖拦截）并删除 |
| v1.2 | 2026-10-21 | xuzhitu | 新增 zsspec-req（合并需求讨论与 REQ 生成，支持 --docs 读文档模式）；退役 zsspec-brain；zsspec-spec 瘦身为基于 REQ 生成 DES/TASK |
| v1.1 | 2026-10-09 | xuzhitu | 新增技能使用监测（SKILL.md 内嵌上报 + Claude Code hook 双路，跨工具兼容） |