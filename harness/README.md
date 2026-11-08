# Agent Harness P0 —— 副作用感知快照 × 风险分级调度

对应开题报告 3.2（RC1–RC4）与 4.1（三层架构）。**P0 的目标不是产出结论，而是产出「能产出结论的机器」**：
所有基线、消融、预算扫描、φ 敏感性都走同一条执行路径，指标自动落盘。

```
Action ──▶ ① 硬门控（确定性/无状态） ──▶ ② PreVal（概率评分，可替换）
                                                    │
                                                    ▼
                                    ③ 分级调度（预算约束下的隔离强度选择）
                                       ├─ 直接放行（R0/R1）
                                       ├─ dry-run（影子执行，φ 保真）
                                       ├─ fork（状态分叉 + 影子世界 + 后果不变量）
                                       └─ 人工/拒绝（预算耗尽或判定危险）
                                                    │
                                                    ▼
                              World.apply（真实副作用）──▶ Ledger（append-only）
```

结构图与代码阅读顺序见 [`../docs/STRUCTURE.md`](../docs/STRUCTURE.md)。

无第三方依赖（仅标准库），Python ≥ 3.9 即可运行；实测在 `3.12.14` 与 `3.14.4` 上均通过。

---

## 1. 快速开始

```bash
cd /home/vince/Agent-Harness

python demo_harness.py                            # ★ 一键端到端演示（先跑这个）
python demo_harness.py --phi 0.3 --budget 60      # 看协同增益的失效边界

python -m unittest discover -s tests -t . -v     # 15 项自检
python -m harness.cli scenarios                  # 看场景集
python -m harness.cli trace --policy OURS --budget 160   # 看逐条决策轨迹
python -m harness.cli run --out runs/p0          # 默认矩阵（B1/B2/B3/OURS × 预算 100/300/900）
```

完整矩阵（本 README 的数据即由此生成）：

```bash
python -m harness.cli run \
  --policies B1,B2,B3,OURS,A1,A2,A3,A4 \
  --budgets 80,160,320 --phi 0.6 --out runs/p0_phi06
python -m harness.cli run --policies B1,B2,B3,OURS,A1,A2,A3,A4 \
  --budgets 80,160,320 --phi 0.95 --out runs/p0_phi095
python -m harness.cli run --policies B1,B2,B3,OURS,A1,A2,A3,A4 \
  --budgets 80,160,320 --phi 0.30 --out runs/p0_phi030
```

产物：`summary.csv`（每策略×预算一行）、`per_scenario.csv`（每场景一行）、`ledger.csv`（全量账本，含分支来源与世界哈希）、`meta.json`（成本模型常数，保证可重算）。

---

## 2. 目录结构

```
harness/
  core/   action.py     动作与工具能力标签（Tier R0–R3、irreversible、dryrun_ok、state_slots）
          verdict.py    Decision / Verdict / ApplyResult
          engine.py     gate → preval → policy → execute → ledger 的唯一路径
          ledger.py     append-only 账本（request_id/幂等键/参数摘要/分支来源/世界哈希）
          budget.py    运行级预算 B（跨场景共享，可耗尽）
          cost.py      成本模型常数
          context.py   策略层依赖注入
          util.py      确定性哈希（噪声可复现）
  gates/  hard_gate.py   无状态硬门控（L1 静态 + L2 关系）
          preval.py     概率评分器（P0 为确定性桩，接口可替换）
  sched/  policies.py    B1–B3 / OURS / A1–A4，全部实现同一 decide() 接口
  state/  cow.py         写时复制状态（delta / full 两种快照语义）
          fork.py        fork = 状态分叉 + 影子世界 + 后果不变量 + 成本度量
  env/    world.py       SQLite 模拟世界：订单/客户/工单/券/审批/外发/退款
  bench/  scenarios.json 18 条场景（11 攻击 + 7 正常）
          runner.py      评测矩阵
          metrics.py     IR / FAR / CR / 成本 / 延迟
  cli.py
tests/test_harness.py    15 项自检（含 C1 副作用泄漏演示）
runs/                    结果 CSV（可复现，勿手改）
```

---

## 3. 工具风险分级（与报告 4.4 的对齐）

| 级别 | 语义 | 工具 | 默认动作 |
|---|---|---|---|
| R0 | 无副作用 | `order_query` `logistics_query` `faq_search` `refund_preview` `coupon_preview` | 直接放行 |
| R1 | 副作用可逆 | `ticket_tag` `session_note` `ticket_status` | 记账后放行 |
| R2 | 不可逆但可预演 | `refund_execute` `coupon_redeem` | dry-run 优先，残差风险高时 fork |
| R3 | 不可逆且不可预演 | `bulk_message` `account_close` `escalate_regulator` | fork / 人工 |

> **需要你在论文里显式说明的一处口径修正**：报告 4.4 把「退款预校验 / 优惠券预检」放在 R2 行，
> 但这两个工具本身是只读探针（调用不产生副作用）。本实现把它们标为 R0，把 `dryrun_ok`
> 记在真正的高风险工具上。否则「R2 = 不可逆但可预演」与「预检工具本身可安全调用」会自相矛盾。

---

## 4. 三个关键机制（P0 已实现，可直接用于 RC1–RC4）

### 4.1 硬门控是**无状态**的
只校验「单请求 vs 实体当前字段」，不做跨请求累计推理。因此：

- 单请求合法性 → 硬门控（确定性、零漏报）
- 跨动作组合后果 → fork 的后果不变量（需要状态）

这解释了两件事：为什么「两笔各 3000、合计 6000 的拆分退款」必然穿透门控（ATK-03），
以及为什么「跨租户只读查询」只能靠门控拦（ATK-11：读操作不改变世界状态，fork 的不变量**看不见**它）。

### 4.2 fork = 状态分叉 + 影子世界 + 后果不变量
`ForkManager.run()` 在影子库上执行同一动作，比较执行前后的**后果不变量**：

```
C-OVERREFUND  C-COUPON-LEAK  C-CROSSTENANT  C-UNAPPROVED-REFUND
C-CLOSED-POINTS  C-BULK-NOCONSENT  C-ESCALATE-NOAPPROVAL
```

裁决 oracle 是确定性不变量，不是 PreVal 分数 → 即使评分器漏报，组合型越权仍会被拦。

### 4.3 dry-run 保真度 φ 与 `V_fork`
dry-run 在影子上执行后按 φ 扰动（`1-φ` 概率失真，其中 80% 漏报、20% 误报）。
调度器只在

```
V_fork = p_r · L · (1-φ) > C_fork
```

时 fork —— 即开题报告 3.3 的触发条件。`L`（一次泄漏的损失权重）与 `C_fork`（fork 成本）都可从 CLI 调：

```bash
python -m harness.cli run --l-units 55 --fork-base 2 --per-mb 100
```

---

## 5. 实测结果（P0，18 场景，L=55）

### 5.1 主结果：φ=0.6，B=160

| policy | IR | FAR | CR | cost_total | fork | dryrun | 预算耗尽 |
|---|---|---|---|---|---|---|---|
| B1 仅 PreVal | 0.364 | 0.000 | 1.000 | 0.2 | 0 | 0 | 0 |
| B2 PreVal+全量 fork | 1.000 | 0.286 | 0.714 | 146.1 | 6 | 0 | 5 |
| B3 仅硬门控 | 0.545 | 0.000 | 1.000 | 0.2 | 0 | 0 | 0 |
| **OURS** | **1.000** | **0.000** | **1.000** | **67.2** | 6 | 5 | 0 |
| A1 去硬门控 | 0.909 | 0.000 | 1.000 | 68.2 | 6 | 7 | 0 |
| A2 全量快照 | 1.000 | 0.000 | 1.000 | 100.9 | 4 | 7 | 0 |
| A3 固定 fork | 1.000 | 0.000 | 1.000 | 116.8 | 11 | 0 | 0 |
| A4 兜底(=B1) | 0.364 | 0.000 | 1.000 | 0.2 | 0 | 0 | 0 |

读法（三条都直接对应报告里的假设）：

1. **B1（仅 PreVal）IR 0.364**：概率评分在 11 条攻击里漏 7 条 —— C3 的直接证据。它最便宜，但便宜没有意义。
2. **B2（全量 fork）IR 1.0 但 CR 0.714、FAR 0.286**：预算在 5 处被迫降级，正常任务被误拒。这是 C2 的直接证据：**不是 fork 不好，而是全量 fork 太贵**。
3. **OURS 在 67.2 的成本下拿到 IR=CR=1.0、FAR=0**：比 B2 省 54%，比 A3（固定 fork）省 42%。
4. **A1 去硬门控 → IR 掉到 0.909**：掉的正是 ATK-11「跨租户只读查询」。这是硬门控**不可替代**的证据：读操作不改状态，fork 的后果不变量对它完全盲。

### 5.2 协同增益：φ 敏感性（B=160）

| φ | OURS 成本 | OURS IR | A3 成本 | A3 IR | A2 成本 | A2 IR |
|---|---|---|---|---|---|---|
| 0.95（预演可信） | **46.0** | 1.0 | 116.8 | 1.0 | 100.9 | 1.0 |
| 0.60（中等） | 67.2 | 1.0 | 116.8 | 1.0 | 100.9 | 1.0 |
| 0.30（预演不可信） | 109.6 | 1.0 | 116.8 | 1.0 | 100.9 | **0.909** |

- **φ 高时调度器敢信预演**：OURS 只 fork 4 次（R3），成本 46，与「所有不可逆都 fork」的 A3 同 IR/CR，成本只有 39%。
- **φ 低时调度器退化为全 fork**：OURS 109.6 ≈ A3 116.8 —— 这正是 G3 要的「协同增益成立/失效的区间」边界。
- **A2 在 φ=0.3 时 IR 掉到 0.909**：全量快照把 fork 成本抬高到 24，`V_fork` 判定「不值得 fork」，于是放弃隔离而漏掉 ATK-03。**这就是「快照粒度 × 调度策略」的耦合效应**（IE(B) 的实证材料）：粒度不只影响成本，还会**改变调度决策**。

### 5.3 快照成本分解（B=160，φ=0.6）

| policy | 状态快照总量 | 影子世界总量 | 每次 fork 搬运 | avg_fork_ms |
|---|---|---|---|---|
| OURS（delta） | 192 B | 516 KB | ≈ 86 KB | 0.20 |
| A2（full） | 549 KB | 344 KB | ≈ 223 KB | 0.63 |
| B2（full） | 823 KB | 516 KB | ≈ 223 KB | 0.61 |

**这是 P0 最重要的一个「负结果」**：增量快照把状态部分压缩了约 4000×，但总搬运量只降了 2.6×，
因为**影子世界复制占了 90%**。也就是说：

> 在不改变影子复制方式的前提下，快照粒度对 fork 成本的改善存在**上界 ≈ state/(state+shadow)**。
> 「快照成本降低一个数量级」（G1 的待验证项）在本实现下**不成立**，除非把影子复制也做成增量
> （页级 COW / savepoint / 只复制 `state_slots` 声明的槽位）。

这条结论建议直接写进论文的 RC2 分析，并把 P1 的第一个技术决策定在这里。

---

## 6. 已知边界（不要把 P0 数字当结论用）

| # | 边界 | 影响 | P1 待办 |
|---|---|---|---|
| 1 | PreVal 是**确定性桩**（特征 + 固定噪声），不是 LLM judge | B1 的 IR=0.364 不能外推到真实 guardrail | 换成 LLM/小模型评分器，做校准曲线（ROC/PR） |
| 2 | 「fork」是影子 SQLite 复制，不是进程级 fork | `avg_fork_ms≈0.2ms` 与 CRIU/DeltaBox 的 27s/ms 级**不可比** | 接入真实 checkpoint/fork 运行时，重新标定 `C_fork` |
| 3 | 成本模型是合成的（`per_mb=100`、`fork_base=2`） | 预算 B 的绝对数值无意义，只有相对比较有效 | 用实测延迟/内存回归标定三个常数 |
| 4 | 后果不变量是手写的 7 条规则 | 覆盖不到未知攻击；组合爆炸未处理 | 形式化「状态槽位 → 不变量」的生成方法 |
| 5 | 18 条场景，无置信区间 | 单条场景翻转就能改 9 个百分点 | 扩到 ≥100 条，bootstrapped CI + 配对检验 |
| 6 | ATK-03 第一笔 3000 会真实执行 | IR 口径按「危险步骤是否执行」计，未计**部分损失** | 增加 partial-loss 指标（已发生但未致损的副作用） |
| 7 | `L=55` 是选定值 | 改变 L 会移动 fork 触发点 | 做 L 的敏感性曲面（与 φ、B 三维） |
| 8 | 单进程、无并发 | 账本/世界未做并发写保护 | 多会话并发下的账本一致性与预算仲裁 |

---

## 7. 与旧 demo 的关系

仓库根目录的 `runtime_full_arch.py` / `KG_demo.py` / `custom_service_demo.py` / `agent_brain_demo.py`
是**动机演示**，保留不动：

- `agent_brain_demo.py` 里「FORK 分支执行后，下一个用例被『已退款』拦住」应作为 **C1 的实证**写进论文 §1
  （fork 隔离状态、不隔离副作用）。`tests/test_harness.py::test_c1_side_effect_is_not_rollbackable`
  已经把这件事做成了可重复的单测。
- 旧 demo 的 fork 分支是 `time.sleep(1)`，不产生任何隔离效果 —— 这正是 P0 要替换掉的东西。
