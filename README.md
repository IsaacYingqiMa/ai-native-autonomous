# AIDevFlow

AIDevFlow 是面向 OMP 和 zscode 的 AI 自主开发套件。你提出需求并确认 Intent（业务意图）、PRD 和验收场景，AI 随后基于 zsspec 完成规格、计划、开发、验证、独立验收与归档。

## 快速开始

准备 Python 3.11+、Git，以及已配置可用模型的 zscode 或 OMP。已安装当前版本的项目可以直接跳到[启动并提交需求](#4-启动并提交需求)。

### 1. 准备源码和业务仓库

把 AIDevFlow 源码和业务仓库放在同一个工作目录下。以 zscode 为例，安装后的目录如下。

```text
workspace/
├── ai-native-autonomous/        # AIDevFlow 源码仓库
│   └── scripts/aidevflow.py     # 用来安装 AIDevFlow
└── my-project/                  # 业务仓库，在这里开发和启动 zscode
    ├── .git/                    # git init 创建
    ├── .zscode/skills/          # 放入 zsspec 方法包
    ├── .aidevflow/              # 安装生成的引擎和配置
    └── scripts/aidevflow.py     # 安装生成，用来查看状态和诊断
```

在你选好的上级目录执行以下命令。最后两条命令在新建的 `my-project` 中初始化 Git 并创建初始空提交。

```sh
mkdir workspace
cd workspace
git clone https://github.com/IsaacYingqiMa/ai-native-autonomous.git ai-native-autonomous
mkdir my-project
cd my-project
git init
git commit --allow-empty -m "Initial commit"
```

如果 Git 提示缺少作者信息，在业务仓库中配置姓名和邮箱，再重试空提交。

```sh
git config user.name "Your Name"
git config user.email "you@example.com"
git commit --allow-empty -m "Initial commit"
```

已有业务仓库时，将 AIDevFlow 源码克隆到业务仓库旁，进入业务仓库根目录并运行 `git rev-parse --verify HEAD`。输出提交哈希后，直接进入第 2 步。

### 2. 放入 zsspec 方法包

另行获取 **zsspec 2 五技能包**，将包内的 `README.md`、`zsspec-init`、`zsspec-spec`、`zsspec-plan`、`zsspec-apply`、`zsspec-archive` 直接放进 `my-project/.zscode/skills/`。使用 OMP 时放进 `my-project/.omp/skills/`。保留各技能的模板和引用文件。项目已有完整方法包时，安装命令的 `--skills-root` 可以指向该目录在业务仓库内的相对路径。详见[方法包目录和复制示例](ZSCODE-USAGE.md#2-提供方法包)。

### 3. 安装并检查

在 `workspace/my-project/` 执行下列命令。`../ai-native-autonomous/scripts/aidevflow.py` 指向相邻源码仓库的安装脚本，`--root .` 表示安装到当前的业务仓库。系统使用 `python3` 时，将 `python` 替换为 `python3`。

**zscode**

```sh
python -B ../ai-native-autonomous/scripts/aidevflow.py --root . install --runtime zscode --skills-root .zscode/skills --workflow-profile reviewed-discovery-v1
```

**OMP**

```sh
python -B ../ai-native-autonomous/scripts/aidevflow.py --root . install --runtime omp --skills-root .omp/skills --workflow-profile reviewed-discovery-v1
```

安装后继续在业务仓库运行检查，确认输出中的 `ok` 为 `true`。

```sh
python -B scripts/aidevflow.py doctor
```

### 4. 启动并提交需求

在业务仓库运行 `zscode` 或 `omp`，然后在会话中输入一条需求。也可以先输入 `/aidevflow-doctor` 检查会话工具。

```text
/aidevflow-flow 做一个简单的待办清单。用户可以添加待办、标记完成，重新打开后还能看到保存的待办和完成状态。
```

审阅 AI 整理的业务意图、PRD 和验收场景，选择 **确认并开始自主开发**。需要续跑时，在同一个业务仓库重开宿主，输入不带参数的 `/aidevflow-flow`。完成后，用同一命令提交下一项需求。

## 工作流程

| 阶段 | 产物与检查 |
| --- | --- |
| 需求讨论 | AI 整理业务意图、PRD 和验收场景，由用户审阅确认 |
| 规格与计划 | 基于已批准的需求生成 zsspec 规格和开发计划，两份产物分别进行独立审查 |
| 开发与验证 | 按工作项实现，记录测试、构建或运行检查的结果，发现普通工程缺陷时自主修复并复验 |
| 独立验收与归档 | 根据批准的验收场景检查实现与证据，通过后完成归档，进入 `delivery_ready` |

涉及需求范围、验收目标或关键业务规则的实质变化时，流程保存当前进度并请用户决定。

`delivery_ready` 表示技术交付就绪。Git 提交、合并与部署由项目团队决定。

## 查看进度与升级

在业务仓库运行 `python -B scripts/aidevflow.py status` 查看当前阶段、下一步和验收结果。要检查已配置的方法包，运行 `python -B scripts/aidevflow.py method-inspect`，当前协议为 `zsspec-docs-v2`。

已有项目可以原地升级。先结束当前需求讨论或开发任务，再退出宿主。已交付任务可用项目中的 `python -B scripts/aidevflow.py archive` 关闭活动运行并保留历史。更新 AIDevFlow 源码后，从业务仓库重跑同一宿主的安装命令，完成 `doctor` 检查后重开宿主。

## 文档

| 文档 | 内容 |
| --- | --- |
| [zscode 使用指南](ZSCODE-USAGE.md) | 安装、需求审阅、恢复及交付检查 |
| [新项目首次任务](NEW-PROJECT-QUICKSTART.md) | 从准备项目到检查首次交付 |
| [zsspec 托管协议](ZSSPEC-MANAGED.md) | 方法包约定、需求批准与规格审查规则 |
| [架构与验证](ARCHITECTURE.md) | 共享引擎、宿主适配、安装布局和验证方式 |
