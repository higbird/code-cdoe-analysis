# 合成数据最小示例

用于演示检查、安全运行、运行凭据和按依赖重跑，不用于推断科学结论。

- Input：input/data.tsv，4 个合成样本，id 为唯一键，value 为非负数。
- Method：analysis 阶段按 group 计算 n 和均值，先乘以 params.scale；plot 阶段生成均值条形图。
- Run：需要 Python 3.11+，无第三方依赖。复制整个 minimal 文件夹到工作目录；不要直接在安装的 skill 内积累分析状态。
- Output：output/summary.tsv 和 output/means.svg；本例输入要求至少一行，因此不产生空分组表。

使用项目的 Python 和 skill 实际路径：

```text
python <skill>/scripts/analysis_workflow.py plan workflow.json
python <skill>/scripts/analysis_workflow.py run workflow.json
python <skill>/scripts/analysis_workflow.py plan workflow.json
```

首次运行应执行 analysis 和 plot；完成后计划应均为 skip。
修改 workflow.json 的 plot.params.title 时只重跑 plot；修改 analysis.params.scale 或 input/data.tsv 时重跑 analysis 及 plot。
共享参数不要在两个阶段重复维护。

运行器是本例唯一调度入口，analysis.py 和 plot.py 由它调用，无需手动按顺序运行。安全协议与限制见 skill 的 references/tools.md。
