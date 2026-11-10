# Agent-Harness

面向不可逆动作风险的 Agent Harness：副作用感知快照 + 风险分级调度。

- **只跑 OURS 一条链路（接本地大模型）**：`conda activate langgraph-fork && python ours_harness_demo.py`
  —— ChatOpenAI 客户端让模型提议工具调用 → 硬门控/PreVal/dry-run/fork 判定 → 输出 IR/CR
  —— 需先启动 llama-server（命令见文件头注释）；换地址用 `--llm-base`
- **想读全部机制？**：`python harness_full_demo.py`
  —— 单文件版（13 节，`--map` 打印行号导航），把整套架构压进一个可从上往下读的脚本
- **一键看效果**：`python demo_harness.py`
  —— 一条客服会话 8 个动作走完三层门控，再对比四种策略、跑 18 条场景集，终端直接出结论
- **逐行讲解（新手向）**：[`docs/LINE_BY_LINE.md`](docs/LINE_BY_LINE.md)（把 ours_harness_demo.py 拆成 10 段，逐行注解）
- **工作流程与函数作用域**：[`docs/FLOW.md`](docs/FLOW.md)（4 张 Mermaid 图 + 25 个函数的作用/读写/纯度表）
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
