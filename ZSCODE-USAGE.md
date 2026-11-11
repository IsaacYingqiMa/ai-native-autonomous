# 在 zscode 中使用 AIDevFlow

当前正式入口与 OMP 保持一致：安装到业务项目，打开 zscode，用 /aidevflow-flow 提交需求；讨论并审阅 Intent、PRD 和验收场景后，选择“确认并开始自主开发”，由同一流程推进规格、计划、开发、验证、返修、独立验收与归档。

用户不需要运行 PROBE 指令、选择脚本模型、填写 Contract JSON 或搬运角色结果。本地脚本模型只存在于框架测试中，正常使用采用你已经配置的真实模型。

## 1. 准备

- Python 3.11+、Git、已能正常使用的 zscode。当前验证构建为 zscode/0.1.0。
- 一个独立业务 Git 仓库，已有初始提交。
- 业务技术栈的依赖和测试环境。页面验收还需要相应的浏览器和可启动服务。
- 完整当前 zsspec 包：README.md，以及 init/spec/plan/apply/archive 的目录、SKILL.md、模板和引用文件。

按[准备源码和业务仓库](README.md#1-准备源码和业务仓库)将源码克隆到业务项目旁，并为新项目创建初始空提交。然后从业务仓库根目录运行以下 PowerShell 命令。

    $suiteRoot = (Resolve-Path -LiteralPath '../ai-native-autonomous').Path
    $projectRoot = (Get-Location).Path

    python --version
    git --version
    zscode --version
    git -C "$projectRoot" status --short
    git -C "$projectRoot" rev-parse --verify HEAD

`git rev-parse --verify HEAD` 输出提交哈希后，可以继续准备方法包。

## 2. 提供方法包

新项目可以将原始包放在业务项目的 .zscode/skills：

    .zscode/skills/
      README.md
      zsspec-init/
      zsspec-spec/
      zsspec-plan/
      zsspec-apply/
      zsspec-archive/

如果该项目还没有方法包，将完整方法包解压到业务项目旁的 `zsspec-package` 目录，再复制到空目标目录。若解压后还有一层目录，将 `$methodPackage` 指向直接包含 `README.md` 和五个技能目录的路径。

    $methodPackage = (Resolve-Path -LiteralPath '../zsspec-package').Path
    $skillsRoot = Join-Path $projectRoot '.zscode/skills'
    New-Item -ItemType Directory -Path $skillsRoot -Force | Out-Null
    foreach ($name in @('zsspec-init', 'zsspec-spec', 'zsspec-plan', 'zsspec-apply', 'zsspec-archive')) {
        Copy-Item -LiteralPath (Join-Path $methodPackage $name) -Destination $skillsRoot -Recurse
    }
    Copy-Item -LiteralPath (Join-Path $methodPackage 'README.md') -Destination $skillsRoot

已有完整包时直接复用其目录。安装时通过 `--skills-root` 指定项目内的路径，例如 `.claude/skills` 或 `.aidevflow/methods/zsspec`。方法包由使用方单独提供，安装器保留供应方原文。

## 3. 安装

使用后续项目运行时所需的 Python 环境执行：

    python -B "$suiteRoot\scripts\aidevflow.py" --root "$projectRoot" install --runtime zscode --skills-root .zscode/skills --workflow-profile reviewed-discovery-v1

如果方法包已经在业务项目的 .claude/skills，将上面的 .zscode/skills 改为 .claude/skills。该参数只控制方法来源；zscode 入口与角色仍安装到 .zscode。

安装后主要布局：

    .aidevflow/
      project.json                         runtime=zscode
      local.json                           本机 Python
      engine/python/aidevflow/             共享引擎与分目录适配
      engine/host/shared/                  公共客户端和流程控制
      schemas/                             公开数据协议
      install/common.manifest.json
      install/runtimes/zscode.manifest.json
    .zscode/
      extensions/aidevflow/                 原生薄入口
      agents/aidevflow-*.md                 独立角色
      skills/aidevflow-flow/SKILL.md        公共流程
    scripts/aidevflow*.py                   项目薄入口

不会修改你的 zscode 模型、登录信息或全局配置。公共代码只有一份，OMP/zscode 原生文件由各自清单管理。活动需求/任务禁止跨 runtime 切换或升级固定代码。

## 4. 检查并启动

    $flowCli = Join-Path $projectRoot 'scripts\aidevflow.py'
    python -B $flowCli --root "$projectRoot" doctor
    python -B $flowCli --root "$projectRoot" method-inspect

核对 doctor.ok=true、workflow_profile=reviewed-discovery-v1、readiness.initial_commit=true，方法协议为 zsspec-docs-v2，来源与模板完整。没有活动任务时 status 报 No active AIDevFlow run 是正常状态。

安装或升级后打开新的 zscode 会话：

    Set-Location $projectRoot
    zscode

以下命令在 zscode 输入框中输入：

    /aidevflow-doctor

它检查当前会话的真实工具。配置检查通过不代表页面、服务或模型输出已通过业务验收。

## 5. 提出需求并确认

在 zscode 输入：

    /aidevflow-flow 为当前项目已有列表接口增加分页校验：page 默认 1 且为正整数；page_size 默认 20，范围 1 到 100；非法输入返回明确参数错误；保持原权限、排序和返回结构。请先讨论 Intent、PRD 和验收场景，等我审阅后自主开发。

也可以传入业务项目内 UTF-8 需求文件：

    /aidevflow-flow requirements/pagination.md

模型先调查和讨论业务问题，产出 Intent、PRD、需求及正常/异常/权限验收场景。审查界面提供：

| 选项 | 行为 |
|---|---|
| 保存并暂停 | 保存当前材料，等待你再次进入 |
| 提出修改意见 | 根据真实反馈重新整理并审阅新版本 |
| 确认并开始自主开发 | 绑定准确版本的需求批准，自动进入开发 |

只讨论暂不开发时，使用 /aidevflow-discover。其审阅选项为“确认，仅保存需求”；以后 /aidevflow-flow 可启动开发。

批准后跳过 zsspec 的重复需求质疑、范围裁剪和已解决问题。规格与计划仍需独立审查，普通缺陷自行返修；真正改变业务范围的决定不会由模型伪造。测试、文档进度与证据按 Gate 管理，用户无需逐阶段批准。

普通修复包括测试命令错误、计划错误、AI 写错的冻结断言和验收缺陷。它们由 AIDevFlow 经独立审查自行修正，不要求用户追加授权。需求、验收目标或关键业务语义的实质变化仍需确认。反复对同一未变化的候选进行无效修复时，系统保留现场并报告技术阻塞，不会以验收通过结束。

## 6. 查看进度与恢复

PowerShell 中：

    python -B $flowCli --root "$projectRoot" status --summary
    python -B $flowCli --root "$projectRoot" status

summary 适合查看下一步；完整 status 用于判断检查和验收有效性。关键字段包括 current_stage、status、next_actions、checks、evidence_health、verdict_current、delivery_archive。

OMP/zscode 共用业务流程，但一个活动任务始终绑定创建它的 runtime。会话退出后，从同一业务目录重新打开 zscode，再输入：

    /aidevflow-flow

源码仓库的修改不会自动覆盖已安装到业务项目的引擎。没有活动任务时，按安装步骤更新同一 runtime 的项目副本，再重开 zscode。安装器会保护活动上下文，禁止直接覆盖正在使用的引擎；不要删除活动指针或伪造批准来绕过。已使用新引擎的旧预算检查点，可通过原入口或 recover-routine 恢复普通修复；真实需求决策、用户暂停和完整性异常保持原状。

它从持久检查点继续，先恢复已授权且字节一致的本地事务。原生角色状态未知时先 recover-runtime；存在工程或归档事务中断时按提示使用对应 recover 命令。普通用户不需要手工管理角色 ID。

默认续跑有停滞和总次数边界，用户暂停、预算耗尽和实际权限缺口会保留状态。不要同时启用两套续跑循环。status 的 needs_human 需要处理具体 blocker，不能仅修改状态来跳过。

## 7. 交付与下一项需求

正常技术交付应核对：

    current_stage = delivery_ready
    verdict_current = true
    delivery_archive.committed = true

并确认所有必需检查和实际功能。交付后，无参数 /aidevflow-flow 显示完成状态；通过同一入口提交明确的下一项需求，会保留并关闭旧运行，再创建新的需求讨论。原任务的批准和证据不会被改写。

Git 提交、合并、发布另行授权。下一次任务前整理并审阅提交本次业务变更，形成清楚的 Git 基线。

运行数据主要在 .aidevflow/discoveries、runs、bundles；工程知识在 specs/MAP.md、modules 和 archive。运行数据默认不进 Git，换机器恢复活动任务要保留这些本地记录。

## 验证范围

本版修复了工程修订复审的原生派发门禁，并统一当前 S-ID 的 Contract Schema。框架测试覆盖正式 /aidevflow-flow 入口、实际需求审查交互、原生角色回执、工程复审、真实行为断言失败到通过、独立验收及归档。

这些集成测试使用本地脚本模型隔离模型质量变量；正常产品使用无需它。尚未将真实模型在任意业务项目上的开发质量视作已证明。首个真实模型任务建议选择现有技术栈中边界明确的小改动。
