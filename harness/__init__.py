"""Agent Harness —— 副作用感知快照与风险分级调度（P0 骨架）。

模块地图：
  core/    动作模型、决策/裁决、账本、预算、成本模型、统一引擎
  gates/   硬门控（确定性）与 PreVal（概率性评分，可替换）
  sched/   风险分级调度策略（B1–B4 基线、A1–A4 消融、Ours 完整方案）
  state/   写时复制（COW）状态、增量快照度量、fork 管理器
  env/     SQLite 模拟世界（订单/退款/工单/外发），含 preview 与后果不变量
  bench/   场景集、运行器、指标
"""

__version__ = "0.1.0-p0"
