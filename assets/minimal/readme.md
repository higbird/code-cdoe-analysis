# 合成数据最小示例

用于演示检查、安全运行、运行凭据和按依赖重跑，不用于推断科学结论。

- Input：input/data.tsv，4 个合成样本，id 为唯一键，value 为非负数。
- Method：analysis 阶段按 group 计算 n 和均值，先乘以 params.scale；plot 阶段生成均值条形图。
- Run：需要 Python 3.11+，无第三方依赖。复制整个 minimal 文件夹到工作目录，并保留空的 output/ 文件夹；不要直接在安装的 skill 内积累分析状态。
- Output：output/summary.tsv 和 output/means.svg；本例输入要求至少一行，因此不产生空分组表。

模块顶层始终只有 input/、output/、code.txt、readme.md。配置位于 input/workflow.json，辅助源码位于 input/src/，运行日志、事务和凭据位于 output/.analysis-state/。

code.txt 是 Python 代码，也是唯一调度入口。使用项目的 Python，将下列 `<skill实际目录>` 替换为包含 SKILL.md 和 scripts/ 的 skill 绝对目录：

```text
python code.txt --skill-root "<skill实际目录>" plan
python code.txt --skill-root "<skill实际目录>" run
python code.txt --skill-root "<skill实际目录>" plan
```

省略动作时默认 run。也可设置环境变量 CODE_CDOE_SKILL_ROOT 后直接运行 `python code.txt`。从其他目录启动时，为 code.txt 提供绝对路径；入口会从自身位置定位模块根，不依赖终端当前目录。恢复中断前先核对工作流及子进程均已停止，再使用 `python code.txt --skill-root "<skill实际目录>" recover --confirm-stopped`。

首次运行应执行 analysis 和 plot；完成后计划应均为 skip。
修改 input/workflow.json 的 plot.params.title 时只重跑 plot；修改 analysis.params.scale 或 input/data.tsv 时重跑 analysis 及 plot。
共享参数不要在两个阶段重复维护。

本例 input/data.tsv 为随包提供的合成数据，未引用外部真实输入。实际分析若引用外部文件，将其绝对路径、用途和存在性检查结果记在 input/ 内的清单，并在本文件展示；不要将来源清单或日志放到模块顶层。安全协议与限制见 skill 的 references/tools.md。
