# Mini Harness

一个**最小但完整**的 agent harness，约 700 行（含注释）。只依赖标准库；
`tiktoken` 是**可选、默认关闭**的依赖（开启后用于精确 token 计数，否则用字符估算）。
默认连 DeepSeek 云端，也可以直接指向本机 `llama-server` 或其他 OpenAI 兼容服务。

> harness 不是模型，也不是 prompt。它是把「只会生成 token 的模型」变成
> 「能观察、能动手、能自己纠错地完成任务」的那一层程序。

## 目录

| 路径 | 层 | 职责 |
| --- | --- | --- |
| `harness.py` | 入口层 | CLI / REPL、参数解析、终端打印 + JSONL 轨迹日志 |
| `loop.py` | 循环层 | Agent 状态机：拼上下文、调模型、执行工具、回填结果、停止条件；`fit_context()` 按 token 预算裁剪 |
| `tools.py` | 工具层 | 工具注册表 + 4 个内置工具；路径围栏、超时、危险命令拦截、输出截断 |
| `world.py` | 世界层 | 客服世界（SQLite）：客户 / 订单 / 优惠券 / 工单；建表、种子数据、只读查询 |
| `llm.py` | 模型层 | OpenAI 兼容客户端；重试、响应归一化成 `Completion` / `ToolCall` |
| `playground/` | 示例 | 供 agent 练手的小脚本（`buggy.py`） |
| `runs/` | 产物 | 每次运行的 `run-YYYYmmdd-HHMMSS.jsonl` 轨迹 |
| `requirements.txt` | 依赖 | 唯一可选依赖 `tiktoken` |
| `.gitignore` | 忽略 | `runs/`、`__pycache__/`、本地备份 |

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
│   每轮前置: fit_context() 按 token 预算丢最旧的整轮            │
├───────────────────────────────┬───────────────────────────────┤
│ tools.py  Tool Registry       │ llm.py  ChatClient            │
│ 路径围栏/超时/权限/截断        │ 重试/解析/归一化               │
└───────────────────────────────┴───────────────────────────────┘
                                 │ HTTP POST {base_url}/chat/completions
                                 ▼
         ┌───────────────────────┴───────────────────────┐
         ▼                                               ▼
   api.deepseek.com（默认）                    本机 llama-server :8080/v1
```

一次任务就是一次**消息列表的增量构造**：

1. `system`（行为约束 + 工作区路径）+ `user`（任务）
2. POST 请求，带上 `tools`（JSON Schema）和 `tool_choice: auto`
3. 模型返回 `tool_calls` → 逐个执行 → 追加 `role=tool` + `tool_call_id`
4. 回到第 2 步；直到模型**不再调用工具**，它的文字就是最终答复

## 快速开始

```bash
# 依赖：Python 3.10+，一个 OpenAI 兼容服务；tiktoken 可选且默认关闭
pip install -r requirements.txt      # 可跳过，不装则用字符估算 token
# 想要精确 token 计数：export MINI_HARNESS_TOKENIZER=on

# 默认连 DeepSeek：Key 只从环境变量读取（不写进代码）
export DEEPSEEK_API_KEY=sk-...
python3 harness.py "统计当前目录有多少个 .py 文件,并说明各自作用" --workspace playground

# 初始化客服世界（生成 world.db：3 个客户、5 个订单、2 张券）
python3 harness.py --reset-world

# 交互模式
python3 harness.py --workspace playground

# 换成自建推理服务：base_url 要带上 /v1
python3 harness.py "跑一下 buggy.py 看报什么错" \
    --base-url http://127.0.0.1:8080/v1 --model qwen-local --workspace playground

# 更安全的用法：每条 shell 命令都人工确认 + 禁用 shell
python3 harness.py --ask --workspace ~/projects/demo
python3 harness.py --no-shell --workspace ~/projects/demo
```

内置工具：`list_dir`、`read_file`、`write_file`、`run_bash`。
环境变量 `LLAMA_BASE_URL` / `LLAMA_MODEL` 可覆盖默认连接（名字沿用自 llama-server 时期，
对任何后端都生效）。

## 命令行参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `task` | 空 | 一次性任务；留空进入 REPL（`/exit` 退出） |
| `--version` | — | 打印版本号 |
| `--base-url` | `$LLAMA_BASE_URL` 或 `https://api.deepseek.com` | API 根地址，自动拼 `/chat/completions` |
| `--model` | `$LLAMA_MODEL` 或 `deepseek-flash` | 模型名 |
| `--workspace` | `.` | 工具可访问的根目录，必须存在 |
| `--max-turns` | `20` | 最多几轮工具调用（每轮都重发上下文，轮数越多越贵） |
| `--context-tokens` | `64000` | 上下文裁剪预算（token） |
| `--temperature` | `0.0` | 采样温度；工具调用建议 0，写作可调高 |
| `--max-tokens` | `8192` | 单轮输出上限（deepseek-flash 最大 384K） |
| `--ask` | 关 | 每次执行 shell 前人工确认 |
| `--no-shell` | 关 | 禁用 `run_bash` |
| `--allow-dangerous` | 关 | 放行危险命令正则（慎用） |
| `--trace-dir` | `runs` | JSONL 轨迹目录 |

## 客服世界（`world.py`）

`world.db` 是一个 SQLite 文件（已 gitignore），四张表：

| 表 | 关键字段 |
| --- | --- |
| `customers` | `id / name / tier / points / status / tenant_id` |
| `orders` | `id / customer_id / status / paid_cents / refunded_cents / items_json` |
| `coupons` | `code / customer_id / status / value_cents / expires_at` |
| `tickets` | `id / customer_id / subject / status / created_at` |

种子数据故意留了几种边界：正常订单、部分退款订单（`ORD-1004` 已退 5000）、
已关闭订单（`ORD-1005`）、已注销客户（`CUST-03`）——后面的门控规则直接拿它们做用例。

```bash
python3 harness.py --reset-world          # 清空重建 + 写入种子
```

```python
import world
world.query("SELECT * FROM orders WHERE id = ?", ("ORD-1001",))
```

## DeepSeek 配置建议

当前代码针对 `deepseek-flash`（1M 上下文 / 384K 最大输出）做了三件事：

1. **关闭 thinking 模式。** DeepSeek 默认开启 thinking（effort=high），而带 `tools`
   时官方要求把每轮的 `reasoning_content` 原样回传，否则返回 400。本 harness 不保存
   它，所以在请求里显式加了 `"thinking": {"type": "disabled"}`（只对 deepseek.com 生效）。
2. **默认 `temperature=0`。** 工具调用要可复现；thinking 模式下该参数本就会被忽略。
3. **默认 `--max-turns 20`、`--context-tokens 64000`。** 每轮都会重发上下文，
   轮数和上下文大小直接决定成本。

| 场景 | 建议参数 |
| --- | --- |
| 日常工具调用 | `--max-turns 20 --context-tokens 64000 --max-tokens 4096` |
| 长文/整仓库分析 | `--max-turns 30 --context-tokens 256000 --max-tokens 16384` |
| 省钱/快速验证 | `--max-turns 10 --context-tokens 32000 --max-tokens 2048` |

> 计费参考（off-peak）：输入缓存命中 $0.003/1M、未命中 $0.15/1M、输出 $0.6/1M。
> 只要前面几轮的消息不变，后续请求会命中前缀缓存，成本远低于按未命中估算。

## 环境变量

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 调用 deepseek.com 时的 Key | 空（请求会 401） |
| `LLAMA_BASE_URL` / `LLAMA_MODEL` | 覆盖默认端点/模型 | deepseek |
| `MINI_HARNESS_TOKENIZER` | 设为 `on` 时用 tiktoken 精确计数；否则按 2 字符≈1 token 估算 | `off` |

`MINI_HARNESS_TOKENIZER` 只影响**上下文裁剪的估算**，不影响模型计费。
开启后首次会加载 tiktoken 词表（几秒，可能联网），之后走本地缓存。

## 内置工具

| 工具 | 关键行为 |
| --- | --- |
| `list_dir(path=".")` | 目录列表，目录在前；最多 200 项，超出提示剩余数量 |
| `read_file(path, offset=1, limit=400)` | 带行号返回；未读完时提示 `offset` 续读位置 |
| `write_file(path, content)` | 整文件覆盖，自动建父目录 |
| `run_bash(command, timeout=30)` | 在 workspace 下 `shell=True` 执行；返回 `[exit N]` + stdout/stderr；超时 1–120s |

## 关键设计（这些才是 harness 的「完整」所在）

1. **错误也是上下文。** 工具抛异常、参数 JSON 坏了、路径越界、未知工具，一律转成
   `[error] ...` 字符串回填给模型。模型看到错误才能自纠；静默失败则循环卡死。
2. **上下文预算按 token 算，分三步裁剪。** `count_tokens()` 按「每条消息 +4、
   末尾 +2」的官方口径统计（默认字符估算，开 `MINI_HARNESS_TOKENIZER=on` 用 tiktoken）。
   装得下就原样返回；装不下先把**单条超过预算一半**的消息截断（否则一条 50KB 的
   `read_file` 结果会让后面全丢光也装不下）；仍超预算才从最旧的**整轮**
   （assistant + 其后的 tool 消息）开始丢，绝不留孤立的 `tool_call_id`，
   并始终保留 system 和第一条任务描述。被裁剪时会发一条 `context` trace 事件。
3. **停止条件。** 模型不再调工具 / 达到 `--max-turns` / 模型服务异常（`LLMError`）。
   没有它们，弱模型会无限调用工具。另外 `finish_reason == "length"` 时会在答复后
   追加一句「可能被截断」的提示。
4. **工具输出必须截断。** 单个结果上限 8000 字符。一条 `find /` 就能撑爆 32k 上下文。
5. **权限在工具层，不在 prompt 里。** 路径 `resolve()` 后必须落在 workspace 内；
   危险命令正则拦截；`--ask` 人工确认；shell 超时上限 120s。
6. **可观测。** 每次请求/响应/工具调用写入 `runs/run-*.jsonl`，出问题可回放。
7. **流式输出（SSE，始终开启）。** `llm.chat(stream=True, on_text=...)` 边收边打印；
   工具调用按 `index` 分片累积后还原成完整的 `ToolCall`。若服务端忽略 `stream`
   返回普通 JSON，会自动退回非流式解析。流式中途断开不重试（避免重复输出）。

## 轨迹格式（`runs/run-*.jsonl`）

每行一个事件，三种 `kind`，都带 `t`（时间戳）：

```jsonc
{"t":"10:23:56","kind":"request","turn":1,"messages":[...]}          // 本轮真实发给模型的完整上下文
{"t":"10:23:58","kind":"response","turn":1,"content":"...","usage":{...},
 "tool_calls":[{"id":"call_0","name":"read_file","arguments":"{\"path\":\"buggy.py\"}"}]}
{"t":"10:23:58","kind":"tool","turn":1,"name":"read_file","arguments":"...","result":"[buggy.py 第 1-4 行...]"}
```

`request` 事件里存的是**裁剪后**的消息，所以「模型到底看到了什么」是可复查的。

## 实测记录

2026-11-08，后端为 `llama-server` + qwen2.5-7b-instruct Q4_K_M（本机约 70 tok/s）。
任务：「运行 buggy.py，定位并修复报错，然后重新运行确认输出正确。」
当时的 `buggy.py` 里 `average()` 写的是 `len(num)`：

```
第 1 轮  → read_file(buggy.py)         ← 发现 len(num)
第 2 轮  → write_file(buggy.py, ...)   ← 改为 len(nums)
          → run_bash("python3 buggy.py")← [exit 0] 平均分: 86.33
第 3 轮  最终答复（不再调用工具）
```

3 轮完成，全程无需人工干预。对应轨迹：`runs/run-20261108-151510.jsonl`。

注意：**`playground/buggy.py` 现在是已修好的版本**，想复现上面这条记录，需要先把
`len(nums)` 改回 `len(num)`。

## 已知限制

- **Key 只从环境变量读。** `llm._api_key_for()` 只在 `base_url` 含 `deepseek.com`
  时附加 `Authorization`（本地 llama-server 不带 key）。历史版本曾把 key 写死在
  代码里：如果那次提交进过 git 历史，请到控制台**轮换**该 key。
- **不是沙箱。** `run_bash` 的拦截只是**安全带**，能拦明显手滑（`rm -rf /`、`mkfs`、
  `dd of=/dev/*`、关机、fork bomb），拦不住有意绕过。不要在无隔离环境里用
  `--allow-dangerous` 跑不可信任务。
- 没有跨任务记忆、没有子 agent / MCP / 审批 UI / 评测集。
- token 计数默认是**字符估算**（2 字符 ≈ 1 token），不是目标模型的真实分词；
  需要精确值时开 `MINI_HARNESS_TOKENIZER=on`（并 `pip install tiktoken`）。
  这么做是为了避免 tiktoken 首次加载词表时联网卡住启动（实测可超过 60 秒）。
- trace 会持续增长；`runs/` 已加入 `.gitignore`，只保留想作为样例的几条即可。

## 扩展路线

| 方向 | 落点 |
| --- | --- |
| 上下文压缩 | `loop.py` 的 `fit_context`：超预算时先让模型摘要旧轮次 |
| 精确分词 | `_size()` 换成目标模型的 tokenizer（llama-server 可查 `/tokenize`） |
| 更多工具 | `tools.py` 的 `add()`；MCP server 可适配成一批 `Tool` |
| 子 agent | `Agent` 本身封装成 `spawn_agent(task)` 工具 |
| 审批/审计 | `ToolRegistry._confirm` + trace 里记录批准链 |
| 评测 | 回放 `runs/*.jsonl`，对比工具调用序列与最终文件状态 |
