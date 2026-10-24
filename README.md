# testing-llm-endpoint-authenticity

一个 Claude Code skill，用来黑盒检测 OpenAI 兼容的大模型接口（含 new-api / one-api 中转站、第三方转售）是否真的提供了所声称的模型，并识别中转站背后的真实上游。

## 功能

1. **真伪评分（`scripts/model_audit.py`）**：把多个独立信号加权合成 0-100 分。
   | 检查项 | 权重 |
   |---|---|
   | 厂商响应头 | 15 |
   | 响应格式与流式 | 15 |
   | 身份自述与事实题 | 15 |
   | 长上下文（大海捞针） | 15 |
   | 推理 | 10 |
   | 工具调用与 JSON | 10 |
   | 指令遵循 | 5 |
   | 错误格式 | 5 |

   得分按适用项重新归一化，上限 98（黑盒无法证明真实）。90 分以上高度可信，75-89 基本可信，60-74 存疑，60 以下疑似冒充。
2. **错误指纹探针（`scripts/error_probe.py`）**：向接口发送 12 种畸形请求（空 messages、非法 role、超大 max_tokens 等），原样输出错误响应。很多中转站会把上游的错误体原样透传，从而泄露上游产品名、错误码和硬性约束，再拿错误串去搜索交叉验证。
3. **隐藏 system prompt 注入检测**：写在 `SKILL.md` 中，由 agent 手动探测（token 开销差分、推理内容泄漏等），只纳入综合评价，不计入脚本评分。

## 探针设计

### A. 评分探针（`model_audit.py`，共 8 类）

| 探针 | 做法 | 满分条件 / 扣分逻辑 |
|---|---|---|
| 响应格式 + 流式 | 发 `say hi` 看非流式响应，再发 `stream=true` 且带 `include_usage` 的请求 | 逐项检查：HTTP 200、有 `id` 和 `choices`、usage 三个字段为整数、`total = prompt + completion`、`finish_reason` 合法、返回的 `model` 与请求一致、SSE 的 content-type、`[DONE]` 结束符、末尾 usage 块。得分 = 通过项占比 |
| 身份自述 + 事实题 | 中英文各问一次"你是谁、谁开发的"；再用 `--fact` 问该模型家族的已知事实（如参数量） | 自述命中期望厂商得满分，只提到别家得 0，其它 0.3；有事实题时与自述各占 50%。自称别家会触发硬失败并封顶 50 分，但事实题全对时视为训练数据污染，不封顶 |
| 推理 | 并发 4 题：三个水管进出水问题（答案 5 小时）、数 strawberry 的 r 和 Mississippi 的 s（3 和 4）、球拍与球（5 美分）、17×23（391） | 答对比例。弱化或蒸馏的小模型常在字母计数和陷阱题上失败 |
| 长上下文 | 构造约 1500 条重复记录（约 2.3 万 token），在 50% 深度插入随机 10 位口令，问口令是什么 | 找到口令 0.7，加上 `prompt_tokens` 与输入字符数相称 0.3。token 数远低于字符数说明被截断或 usage 造假 |
| 工具 + JSON | 声明 `get_weather` 工具，检查是否产生 `tool_calls`，再回填工具结果检查最终回答；另测 `response_format: json_object` | 工具调用 0.4，回填往返成功 0.7，JSON 模式可解析再加 0.3 |
| 指令遵循 | system 要求"只用英文且以 `ACK:` 开头"，用户用中文提问 | 以 `ACK:` 开头且不含中文才得分 |
| 错误格式 | 发空 `messages` 和不存在的模型名 | 两者都返回 4xx 且带 JSON `error` 字段。同时从错误里提取中转站渠道分组名（如 `under group 1`） |
| 厂商响应头 | 读取成功响应的头，匹配厂商前缀（Moonshot 的 `Msh-`、OpenAI、Anthropic、Google、DeepSeek 的 `x-ds-`，或自定义 `--header-prefix`） | 命中 1.0，未命中 0.4（中转站可能剥头，只算弱负面）。同时识别 new-api、one-api、LiteLLM、Helicone、OpenRouter 等中转特征并记为说明 |

### B. 错误指纹探针（`error_probe.py`，共 12 种畸形请求）

| 探针 | 请求 | 想暴露什么 |
|---|---|---|
| `empty_messages` | `messages: []` | 上游对空输入的原始报错，经典案例里泄露了 WorkBuddy |
| `no_system_first` | 首条不是 system | 上游是否强制要求 system 在前 |
| `developer_role` | 使用 `developer` 角色 | 上游是否支持 OpenAI 新角色 |
| `bad_role` | 非法角色 `banana` | 上游框架和 role 白名单（如 serde 报错、DeepSeek 自有的 `latest_reminder`） |
| `non_stream` / `stream` | 分别显式关闭、开启流式 | 上游是否只支持流式 |
| `huge_max_tokens` | `max_tokens` 为一千万 | 上游合法范围，有时还会泄露账户余额 |
| `bad_temperature` | `temperature: 99` | 上游参数范围（Kimi 为 0 到 1，DeepSeek 为 0 到 2） |
| `bad_tools` | 缺少 `function` 的工具定义 | 上游的校验措辞与字段名 |
| `bad_response_format` | 不存在的 `response_format.type` | 上游支持的格式枚举 |
| `null_content` | `content: null` | 上游对空内容的处理 |
| `unknown_model` | 在模型名后加后缀 | 中转站渠道分组名、上游支持的模型列表 |

使用方法：对每条模型路由分别运行，原样输出状态码、响应体和响应头；找出不属于中转站自身格式的外来品牌名、数字错误码、渠道名和厂商专属约束；比较不同路由的结果（A 路由 200、B 路由报错，说明错误来自 B 的上游）；再用错误串原文搜索，用至少两个来源交叉验证。

### C. 隐藏 system prompt 注入检测（agent 手动，不计分）

不在脚本里，由 agent 按 `SKILL.md` 的说明手动探测，只写进综合评价：

1. **token 开销差分**：发 3 个词和 203 个词的输入，算出固定提示开销，干净模板一般低于 40，60 以上可疑。
2. **推理泄漏**：在推理模型的 `reasoning_content` 里找没发过却被提到的 system prompt 或身份指令。
3. **复述尝试**：让模型复述上文，只算弱证据，因为官方模型也会拒绝。
4. **短路或混合上游**：裸 `hi` 返回空 `id`、0 个补全 token 之类的异常响应。

至少两个信号同时成立才下结论，并尽量用同厂商官方接口做对照。

## 安装

把仓库内容放到 Claude Code 的 skills 目录下：

```bash
git clone https://github.com/mayuri-wylty/testing-llm-endpoint-authenticity ~/.claude/skills/testing-llm-endpoint-authenticity
```

## 使用

API key 只通过环境变量 `LLM_API_KEY` 传入，不要写入文件或报告。

```bash
# 真伪评分
LLM_API_KEY=sk-... python scripts/model_audit.py \
  --base https://host/v1 --model <id> --vendor moonshot \
  --identity-regex "Kimi|Moonshot|月之暗面" \
  --fact "Kimi K2 的总参数量和激活参数量分别是多少？::1T|1万亿|32B" --out report.json

# 错误指纹探针（可重复 --model 对比多条路由）
LLM_API_KEY=sk-... python scripts/error_probe.py \
  --base https://host/v1 --model <id1> --model <id2> --out probe.json
```

也可以直接对 Claude Code 说“用 testing-llm-endpoint-authenticity 测试这个接口”。

## 注意事项

- 自称别家厂商不等于冒充：部分模型（如 DeepSeek）训练数据含 GPT 语料，会说自己是 OpenAI。只有同时答错该家族事实题才会封顶 50 分，所以务必传入 `--fact`。
- 错误指纹只能说明上游是谁，不能证明模型是真的；官方直连 API 和会改写错误的中转站不会泄露信息。
- 部分中转站被 Cloudflare 拦截 Python 默认 User-Agent，脚本已使用 curl UA。
- Windows 下 Git Bash 的 curl 发中文会乱码，建议用 Python 并设置 `PYTHONUTF8=1`。
- 推理模型的 `max_tokens` 建议不小于 4000。
- 在聊天里贴过的 key 请及时轮换。

## 依赖

Python 3，仅使用标准库。
