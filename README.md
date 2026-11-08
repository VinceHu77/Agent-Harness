# Agent-Harness

面向不可逆动作风险的 Agent Harness：副作用感知快照 + 风险分级调度。

- **一键看效果（推荐先跑这个）**：`python demo_harness.py`
  —— 一条客服会话 8 个动作走完三层门控，再对比四种策略、跑 18 条场景集，终端直接出结论
- **看不懂代码？先看结构图**：[`docs/STRUCTURE.md`](docs/STRUCTURE.md)（9 张 Mermaid 图 + 阅读顺序）
- **想一步步学会**：[`learn/README.md`](learn/README.md)（8 站带读 + 6 个可运行探针，约 4 小时）
- **P0 原型与实验结果**：[`harness/README.md`](harness/README.md)
- 结果 CSV：[`runs/p0_phi06`](runs/p0_phi06)、[`runs/p0_phi095`](runs/p0_phi095)、[`runs/p0_phi030`](runs/p0_phi030)
- 快速验证：`python -m unittest discover -s tests -t .`
- 演示脚本参数：`python demo_harness.py --phi 0.3 --budget 60 --save`
- 跑矩阵：`python -m harness.cli run --policies B1,B2,B3,OURS,A1,A2,A3,A4 --budgets 80,160,320 --phi 0.6 --out runs/p0_phi06`

> 根目录其余脚本（`runtime_full_arch.py`、`KG_demo.py`、`custom_service_demo.py`、
> `agent_brain_demo.py`、`fork_demo.py` 等）是阶段一的**动机演示**，保留作为论文 §1 的现象证据，
> 不再作为运行时。新实现全部在 `harness/` 下，无第三方依赖。
