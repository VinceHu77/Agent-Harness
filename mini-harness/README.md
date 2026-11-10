# Mini Harness

一个**最小但完整**的 agent harness，约 600 行（含注释）、零第三方依赖，直接跑在本机
`llama-server`（OpenAI 兼容接口）上。

> harness 不是模型，也不是 prompt。它是把「只会生成 token 的模型」变成
> 「能观察、能动手、能自己纠错地完成任务」的那一层程序。

## 目录

| 文件 | 层 | 职责 |
| --- | --- | --- |
| `harness.py` | 入口层 | CLI / REPL、参数、JSONL 轨迹日志 |
| `loop.py` | 循环层 | Agent 状态机：拼上下文、调模型、执行工具、回填结果、停止条件 |
| `tools.py` | 工具层 | 工具注册表 + 4 个内置工具；路径围栏、超时、危险命令拦截、输出截断 |
| `llm.py` | 模型层 | OpenAI 兼容客户端；重试、响应归一化成 `Completion` / `ToolCall` |

## 架构

```
┌──────────────────────────────────────────────────────────────┐
│ harness.py     CLI / REPL / trace 日志                        │
├──────────────────────────────────────────────────────────────┤
│ loop.py        Agent Loop                                     │
│                                                               │
│   messages ──► llm.chat() ──► 有 tool_calls? ──否──► 最终答复   │
│      ▲                            │是                         │
│      └──── tool 消息 ◄── tools.call() ◄───────────────────────┤
│                                                               │
│   每轮前置: fit_context() 按预算丢最旧的整轮                   │
├───────────────────────────────┬───────────────────────────────┤
│ tools.py  Tool Registry       │ llm.py  ChatClient            │
│ 路径围栏/超时/权限/截断        │ 重试/解析/归一化               │
└───────────────────────────────┴───────────────────────────────┘
                                │ HTTP
                                ▼
              llama-server :8080  POST /v1/chat/completions
```

一次任务就是一次**消息列表的增量构造**：

1. `system`（行为约束 + 工作区路径）+ `user`（任务）
2. POST 请求，带上 `tools`（JSON Schema）
3. 模型返回 `tool_calls` → 逐个执行 → 追加 `role=tool` + `tool_call_id`
4. 回到第 2 步；直到模型**不再调用工具**，它的文字就是最终答复

## 快速开始

```bash
# 前提：llama-server 已在 127.0.0.1:8080 运行（model alias = qwen-local）
cd mini-harness

# 一次性任务
python3 harness.py "统计当前目录有多少个 .py 文件,并说明各自作用" --workspace playground

# 交互模式
python3 harness.py --workspace playground

# 更安全的用法：每条 shell 命令都人工确认
python3 harness.py --ask --workspace ~/projects/demo
```

内置工具：`list_dir`、`read_file`、`write_file`、`run_bash`。
环境变量 `LLAMA_BASE_URL` / `LLAMA_MODEL` 可覆盖默认连接。

## 关键设计（这些才是 harness 的「完整」所在）

1. **错误也是上下文。** 工具抛异常、参数 JSON 坏了、路径越界，一律转成
   `[error] ...` 字符串回填给模型。模型看到错误才能自纠；静默失败则循环卡死。
2. **上下文预算。** 按字符估算（`--context-chars`，默认 20000）。超预算时从最旧的
   **整轮**（assistant + 其后的 tool 消息）开始丢，绝不留孤立的 `tool_call_id`，
   并始终保留 system 和第一条任务描述。
3. **三个停止条件。** 模型不再调工具 / 达到 `--max-turns` / 模型服务异常。
   没有它们，弱模型会无限调用工具。
4. **工具输出必须截断。** 单个结果上限 8000 字符。一条 `find /` 就能撑爆 32k 上下文。
5. **权限在工具层，不在 prompt 里。** 路径 `resolve()` 后必须落在 workspace 内；
   危险命令正则拦截；`--ask` 人工确认；shell 超时上限 120s。
6. **可观测。** 每次请求/响应/工具调用写入 `runs/run-*.jsonl`，出问题可回放。

## 实测记录

`playground/buggy.py` 里 `len(num)` 是错的。任务：
「运行 buggy.py，定位并修复报错，然后重新运行确认输出正确。」

```
第 1 轮  → read_file(buggy.py)         ← 发现 len(num)
第 2 轮  → write_file(buggy.py, ...)   ← 改为 len(nums)
         → run_bash("python3 buggy.py")← [exit 0] 平均分: 86.33
第 3 轮  最终答复（不再调用工具）
```

qwen2.5-7b-instruct Q4_K_M 在本机（约 70 tok/s）3 轮完成，全程无需人工干预。

## 这不是什么

- 没有流式输出（SSE）、没有 token 级精确计数（字符估算）、没有跨任务记忆
- 没有子 agent、MCP、沙箱容器、审批 UI、评测集
- `run_bash` 的拦截只是**安全带**，不是安全边界。不要在没有隔离的环境里
  用 `--allow-dangerous` 跑不可信任务

## 扩展路线

| 方向 | 落点 |
| --- | --- |
| 流式输出 | `llm.py` 增加 `stream=True`，逐块拼接 `tool_calls` delta |
| 上下文压缩 | `loop.py` 的 `fit_context`：超预算时先让模型摘要旧轮次 |
| 更多工具 | `tools.py` 的 `add()`；MCP server 可适配成一批 Tool |
| 子 agent | `Agent` 本身封装成 `spawn_agent(task)` 工具 |
| 审批/审计 | `ToolRegistry._confirm` + trace 里记录批准链 |
| 评测 | 回放 `runs/*.jsonl`，对比工具调用序列与最终文件状态 |
