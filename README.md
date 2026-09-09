# Code Cdoe Skill

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
| 文件检查 | 提供 exists/header/full 分级检查；按声明检查文件、表头、唯一键、行数和数值，拒绝低等级静默跳过强规则。 |
| 安全运行 | 临时目录生成和检查，按阶段成组替换结果及凭据；替换异常回退，中断后可显式恢复。 |
| 运行凭据 | 记录 sha256/stat/external 实际指纹、参数、命令、版本和已执行/未执行检查，明确外部摘要是否重新验证。 |
| 依赖重跑 | 比较输入、代码、参数、环境和产物，阶段内复用哈希、执行后重新检查；可选择目标阶段及上游。 |

入口为 `scripts/analysis_workflow.py`，需要 Python 3.11+，无第三方依赖。命令用法、配置字段与保护边界见 [工具说明](references/tools.md)；可复制 [合成数据示例](assets/minimal/README.md) 独立试运行。

工具要求被调脚本将本次产物写入指定临时目录，并正确传播错误；不隔离任意脚本写入、不保证整组文件在替换过程中始终原子可见，也不推断未声明的依赖或科学口径。已有工作流不必迁移。

默认保持严格指纹和原有表格验证。大文件可显式选择低成本模式；恢复备份仍执行严格 SHA-256。新版凭据为 version=2，旧凭据会触发重跑，旧恢复日志继续兼容。具体升级与成本边界见工具说明。

## 适用场景

- 创建新的科研分析模块。
- 整理结构混乱的生物信息学或统计分析目录。
- 修改 Bash、R、Python 分析脚本并重新生成结果。
- 修复表格或多面板图件，并验证全部受影响产物。
- 核对 README、代码、参数和实际结果之间的一致性。
- 整理服务器或集群已有分析：输入归入 `input/` 或保留有来源的外部引用，结果与对应日志一起放入 `output/`，依据已有脚本或日志恢复 `code.txt` 和简洁 README；只整理时不重跑，不补猜缺失参数。

单纯解释统计概念、阅读论文或回答局部代码问题时，不需要创建该 skill 规定的项目结构。

## 安装与调用

同一份目录适用于 Claude Code、Codex 和 NousResearch Hermes Agent。按使用的客户端复制整个 `code-cdoe-skill` 文件夹；不要只复制 `SKILL.md`，也不要再嵌套一层同名目录。

| 客户端 | 默认个人安装目录 | 显式调用 |
|---|---|---|
| Claude Code | `~/.claude/skills/code-cdoe-skill/` | `/code-cdoe-skill 请整理并验证这个科研分析项目。` |
| Codex | `~/.agents/skills/code-cdoe-skill/` | `$code-cdoe-skill 请整理并验证这个科研分析项目。` |
| Hermes Agent | `~/.hermes/skills/code-cdoe-skill/` | `/code-cdoe-skill 请整理并验证这个科研分析项目。` |

Claude Code 项目级目录为 `.claude/skills/`，Codex 为 `.agents/skills/`，均在其中放置整个 skill 文件夹。Hermes 使用自定义 `HERMES_HOME` 或 profile 时，安装到该配置实际使用的 skills 目录。`~` 表示用户主目录；Windows 实际路径以客户端配置为准。目录与调用依据：[Claude Code 官方说明](https://code.claude.com/docs/en/skills)、[Codex 官方说明](https://learn.chatgpt.com/docs/build-skills)、[Hermes 官方说明](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)。

已有 Codex 环境若已从 `~/.codex/skills/` 加载此 skill，可沿用当前已验证的目录；不要为同一客户端保留多个不同版本的同名副本。复制后新建会话并检查技能列表及实际加载路径；能否自动触发还取决于客户端配置和模型选择。

安装后应保留以下结构：

```text
code-cdoe-skill/
├── SKILL.md
├── agents/openai.yaml
├── README.md
├── LICENSE
├── scripts/analysis_workflow.py
├── references/
│   ├── tools.md
│   └── agents.md
├── assets/minimal/
└── tests/
```

`agents/openai.yaml` 仅提供 Codex 的可选界面元数据；Claude Code 和 Hermes 的执行不依赖它。共用指令只使用标准的 `name`、`description` 元数据。无须安装 OpenAI、Anthropic 或 Hermes SDK；运行器仅依赖 Python 标准库。

详细环境处理、命令引用和验收步骤见 [宿主适配说明](references/agents.md)。

## 使用示例

以下任务正文可接在对应客户端的显式调用后，也可直接用自然语言提出。

```text
使用 code-cdoe-skill，为这批 GWAS 输入建立一个可重复运行的分析模块，生成关联结果和 Manhattan 图。
```

```text
使用 code-cdoe-skill，修复当前 R 分析的所有分组标签问题，重跑受影响的表格和图，并确认结果来自本次运行。
```

```text
使用 code-cdoe-skill，检查 README 与代码中的 FDR、协变量和 PC 数是否一致；能确认的直接修正并重跑，影响科学含义但无法判断的冲突请指出。
```

例如整理服务器下载的 `result.assoc` 与 `result.log`：

```text
使用 code-cdoe-skill，依据现有日志和脚本整理这个服务器分析目录。结果和日志一起放到 output/，恢复可确认的 code.txt；大文件优先链接，未下载输入保留有依据的服务器路径。只整理，不重跑；缺失步骤和参数明确标注。
```

这一能力由 Agent 按技能规范读取记录完成，运行器不会自动解析任意软件日志。整理完成不代表历史分析已经复现；日志不完整时也不承诺生成完整可执行入口。

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

## 许可证

本项目采用 [MIT License](LICENSE)。
