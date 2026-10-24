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
