# ZSSpec 技能包

基于 Spec-Driven Development (SDD) 规范驱动开发的 Claude Code 技能包。

## 技能列表

| 技能 | 职责 | 触发语 |
|------|------|--------|
| `zsspec-init` | 项目初始化 | "初始化项目""创建CLAUDE.md" |
| `zsspec-brain` | 需求讨论收敛 | "这个功能怎么做""帮我梳理需求" |
| `zsspec-spec` | 生成规格文档（REQ/DES/TASK）并为 CHK 生成提供完整输入 | "生成spec""写需求文档""写设计文档""补任务清单" |
| `zsspec-test-gen` | 基于 REQ/DES/TASK 生成 CHK 测试用例文档（automation=static/auto/manual） | "写检查清单""生成测试用例""根据 spec 生成测试" |
| `zsspec-verify` | 开发前一致性校验、门禁判断与全局治理检查（默认聚焦小模块/子功能，支持 module/global scope） | "检查spec是否能开工""先查REQ/TASK/CHK/DES是否一致""看看当前这小块够不够写""检查整个specs体系是否健康" |
| `zsspec-apply` | 开发准入与执行（按 TASK 驱动开发并即时回写 TASK 状态） | "开始写代码""开搞""实现了" |
| `zsspec-code-review` | 基于 CHK 中 automation=static 检查项的结构化静态代码审查 | "帮我review代码""做代码审查""检查本次改动有没有问题""合并前看一下" |
| `zsspec-e2e-gen` | 基于 CHK 中 automation=auto 检查项生成 Playwright 测试代码（只生成不执行） | "生成测试代码""生成 e2e 代码""写 Playwright 测试" |
| `zsspec-e2e-run` | 执行已有 Playwright e2e 测试并回写 automation=auto 检查项状态 | "运行测试""跑 e2e 测试""测试结果" |
| `zsspec-done` | 判定 REQ 是否可更新为已完成；若前序 TASK/CHK 未收口则打回对应阶段 | "做完了""验一下""确认完成" |
| `zsspec-change` | 变更处理（含Bug修复） | "要改一下""有问题""修复" |
| `zsspec-standard` | 模板规范（不直接执行） | - |

## 流程图

```
init → brain → spec → test-gen → verify → apply → code-review
         ↑      ↑        ↑         ↑         ↑            ↑
         └────── change ─┴─────────┴─────────┴────────────┘
                                          └→ e2e-gen ─→ e2e-run ─→ done
```

## 使用方式

将本目录（`zsspec-skills`）下的所有子文件夹复制到项目的 `.claude/skills/` 目录下：

```
.claude/skills/
├── zsspec-init/SKILL.md
├── zsspec-brain/SKILL.md
├── zsspec-spec/SKILL.md
├── zsspec-verify/SKILL.md
├── zsspec-apply/SKILL.md
├── zsspec-test-gen/SKILL.md
├── zsspec-code-review/SKILL.md
├── zsspec-e2e-gen/SKILL.md
├── zsspec-e2e-run/SKILL.md
├── zsspec-done/SKILL.md
├── zsspec-change/SKILL.md
└── zsspec-standard/
    ├── SKILL.md
    ├── rules/
    │   ├── 01-spec-rules.md
    │   └── 02-search-rules.md
    ├── requirement/
    ├── design/
    └──task/
```

项目 specs 目录结构：

```
specs/
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
- `CLAUDE.md` - 项目协作入口
- `specs/design/` - 公共设计文档

### 2. 需求讨论

```bash
/zsspec-brain 用户管理模块
```

输出：结构化讨论结论（对话中呈现）

### 3. 生成规格文档

```bash
/zsspec-spec 用户管理
```

生成：
- `specs/requirement/用户管理/REQ-用户管理.md`
- `specs/design/用户管理/DES-用户管理-前端.md`
- `specs/design/用户管理/DES-用户管理-后端.md`
- `specs/task/用户管理/TASK-用户管理.md`

如需生成 CHK 测试用例文档，继续执行：
- `/zsspec-test-gen 用户管理`

### 4. 生成测试用例文档

```bash
/zsspec-test-gen 用户管理
```

生成：
- `specs/checklist/用户管理/CHK-用户管理.md`

### 5. 开发前规格审查

```bash
/zsspec-verify 用户管理
```

执行：默认以当前模块或子功能为范围，先做 REQ / DES / TASK / CHK 一致性校验，再做开发前门禁判断；也支持 module/global scope 做模块级或全局 specs 健康治理检查

### 6. 开始开发

```bash
/zsspec-apply 用户管理
```

执行：开发准入检查 + 按 TASK 锁定当前开发任务 + 状态更新 + 编码实现 + TASK 状态即时回写

### 7. 静态代码审查

```bash
/zsspec-code-review 用户管理
```

执行：基于 REQ / DES / CHK 中 `automation=static` 检查项 / NFR 与当前代码改动，做低噪声、可追溯的结构化静态代码审查，并回写 static 项状态

### 8. 生成 E2E 测试代码

```bash
/zsspec-e2e-gen 用户管理
```

执行：读取 CHK 中 `automation=auto` 的检查项，生成 Playwright 测试代码，生成后停止并等待用户 review

### 9. 执行 E2E 测试

```bash
/zsspec-e2e-run 用户管理
```

执行：运行由 `zsspec-e2e-gen` 生成的 Playwright e2e 测试代码，回写 `automation=auto` 检查项状态并生成执行报告

### 10. 完成验收

```bash
/zsspec-done 用户管理
```

执行：核对 TASK 与 CHK（static/auto/manual）状态，处理 manual 项，并判定是否可将 REQ 更新为已完成；若前序未收口则打回对应阶段

### 11. 变更处理

```bash
/zsspec-change 用户管理 需要调整登录逻辑
```

根据影响程度自动判断处理方式。

## 文档规范

详细命名规范见: `zsspec-standard/rules/01-spec-rules.md`

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

- 技能包版本: v1.0
- 适用工具: Claude Code