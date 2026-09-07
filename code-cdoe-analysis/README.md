# code-cdoe-skill

一套面向科研数据分析 Agent 的轻量工作规范，附带可选 Python 执行工具，用于组织、修改、运行和验证以 Bash、R、Python 或混合工具完成的分析项目。

*A lightweight Agent Skill for organizing, running, and validating reproducible scientific data-analysis projects.*

## 功能

- 将具有独立科研意义的任务组织为清晰模块。
- 保护原始数据，使用明确的输入、输出和路径约定。
- 维持一个清晰的运行入口，减少重复脚本和隐藏步骤。
- 修改已有项目时控制范围，保留用户文件和无关改动。
- 实际运行受影响的分析，并检查退出状态、产物及关键数据内容。
- 避免生成无用途的总结、审查、日志和 Agent 工作记录。

本 skill 管理分析工作流，不代替统计方法选择、实验设计、领域解释或人工科研判断。

## 可选程序功能

| 功能 | 已提供的实现 |
|---|---|
| 文件检查 | 检查缺失、零字节、表头、必需列、联合唯一键、行数、有限数值和范围；合理表头空表可通过。 |
| 安全运行 | 临时目录生成和检查，按阶段成组替换结果及凭据；替换异常回退，中断后可显式恢复。 |
| 运行凭据 | 记录输入、源码和产物 SHA-256，参数、实际命令、版本查询结果、退出码与检查结果。 |
| 依赖重跑 | 比较声明的输入、代码、参数、环境和产物，按依赖安排重跑；可选择目标阶段及上游。 |

入口为 `scripts/analysis_workflow.py`，需要 Python 3.11+，无第三方依赖。命令用法、配置字段与保护边界见 [工具说明](references/tools.md)；可复制 [合成数据示例](assets/minimal/README.md) 独立试运行。

工具要求被调脚本将本次产物写入指定临时目录，并正确传播错误；不隔离任意脚本写入、不保证整组文件在替换过程中始终原子可见，也不推断未声明的依赖或科学口径。已有工作流不必迁移。

## 适用场景

- 创建新的科研分析模块。
- 整理结构混乱的生物信息学或统计分析目录。
- 修改 Bash、R、Python 分析脚本并重新生成结果。
- 修复表格或多面板图件，并验证全部受影响产物。
- 核对 README、代码、参数和实际结果之间的一致性。

单纯解释统计概念、阅读论文或回答局部代码问题时，不需要创建该 skill 规定的项目结构。

## 安装

将仓库目录复制到 Codex 的个人 skills 目录：

```text
~/.codex/skills/code-cdoe-analysis/
```

安装后应保留以下路径：

```text
code-cdoe-analysis/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── README.md
├── LICENSE
├── scripts/analysis_workflow.py
├── references/tools.md
├── assets/minimal/
└── tests/test_workflow.py
```

重新启动或新建 Codex 任务后，可以显式调用：

```text
$code-cdoe-analysis 请整理并验证这个科研分析项目。
```

skill 也允许根据任务内容自动触发。

## 使用示例

```text
$code-cdoe-analysis 为这批 GWAS 输入建立一个可重复运行的分析模块，生成关联结果和 Manhattan 图。
```

```text
$code-cdoe-analysis 修复当前 R 分析的所有分组标签问题，重跑受影响的表格和图，并确认结果来自本次运行。
```

```text
$code-cdoe-analysis 检查 README 与代码中的 FDR、协变量和 PC 数是否一致；能确认的直接修正并重跑，影响科学含义但无法判断的冲突请指出。
```

## 默认模块形式

新项目在适用时采用：

```text
NN_name/
├── input/
├── output/
├── code.txt
└── README.md
```

`code.txt` 是默认入口约定。已有项目若使用有效的 `run.py`、`analysis.R`、Makefile 或工作流管理器，应保留其现有入口。

OpenAI Skills API 接受 skill 目录或 ZIP 文件上传。相关接口见 [OpenAI Skills API](https://developers.openai.com/api/reference/python/resources/skills/methods/create)。

## 许可证

本项目采用 [MIT License](LICENSE)。
