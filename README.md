# 大模型接口真实性检测（testing-llm-endpoint-authenticity）

## 简介

本项目是一个 Claude Code 技能，用于检测第三方大模型接口（包括各类中转站、转售渠道）是否真正提供了其所宣称的模型，并尽可能判断接口背后实际使用的服务方。支持三种协议：OpenAI `/chat/completions`、Anthropic `/v1/messages`、OpenAI `/responses`（`--protocol chat|messages|responses`）。

在此基础上，技能还提供两套附加能力：用内置画像（`profiles/`）核对渠道对官方接口规范的遵守程度（`scripts/spec_audit.py`，15 项、独立 `spec_score`），以及把真实性与规范结果渲染成一页单文件 HTML 报告（`scripts/report_html.py`）。

> **当前为评分 v2（`scoring_version=2`）**：权重与检测项已调整，与早期版本的分数不可比较。全部功能目前只在模拟接口（`tests/mock_llm.py`）上验证，尚未经过真实接口的系统校准，请把分数当作证据而非定论。

检测只依赖对接口的正常调用，不需要获得服务方内部信息。结果以证据形式给出，不构成对真实性的绝对证明。

## 检测内容

### 一、综合评分（`scripts/model_audit.py`）

脚本从九个方面分别检测，按权重合成 0 至 100 分（权重集中定义在 `scripts/scoring.py`）。

| 检测项 | 权重 | 检测方法 | 评分依据 |
|---|---|---|---|
| 长文本检索 | 25 | 按 `--claimed-window` 分档构造长文（默认约 3.2 万词元，宣称 128k 以上约 7.7 万至 10 万；1M 档需 `--deep`），在 10%、50%、90% 三个深度各藏一个随机口令，一次请求取回 | 找回比例加输入长度统计是否相称；缺失的深度会提示头部或尾部截断。预估超过 15 万词元且未加 `--deep` 时拒绝运行 |
| 身份自述与事实问答 | 15 | 用中英文询问模型的开发者和名称，再提出关于该模型的已知事实问题 | 自述与预期厂商一致得满分；自称为其他厂商则扣分 |
| 推理能力 | 15 | 4 道基础题加 3 道中高难度题（答案经独立程序校验） | 0.4×基础 + 0.6×难题，结果按档位写入说明 |
| 响应格式与流式输出 | 10 | 分别发起普通请求和流式请求 | 返回结构、用量数字、模型名、流式结束标志（`[DONE]`/`message_stop`/`response.completed`） |
| 工具调用与结构化输出 | 10 | 天气查询工具调用与结果回传；JSON 模式 | 分步计分；`messages` 协议无 JSON 模式，该项不计 |
| 分词器指纹 | 10 | 4 类固定文本（英文、中文、代码、数字）各发两种长度，用输入词元数之差算每字符词元数，对比声称家族的区间（`scripts/data/tokenizer_bands.json`） | 只降分，不触发硬失败；Claude、Gemini 的区间为低置信度近似；中转站可能自行重算用量 |
| 厂商响应头 | 5 | 由 `--claim official/bedrock/vertex/azure/relay/unknown` 驱动 | 官方直连缺失才扣分；声称甲却出现乙的专属头记 0 并标注 `CLAIM CONTRADICTED:`；其余情形不计 |
| 指令遵循 | 5 | 要求模型仅用英文并以固定前缀开头，而用户使用中文提问 | 是否严格遵守 |
| 错误信息格式 | 5 | 发送空内容和不存在的模型名 | 是否返回规范的错误说明；同时记录中转站暴露的渠道名称 |

评分规则：

- 只对适用的检测项（得分不为空的项）重新折算，满分上限为 98 分，以表明外部检测无法给出完全确定的结论。
- 90 分以上为高度可信，75 至 89 分为基本可信，60 至 74 分为存疑，低于 60 分为疑似冒充。
- 模型自称为其他厂商时，总分最高为 50 分。但若关于该模型的事实问答全部答对，则认为这是训练资料的影响，不予限制。因此使用时应始终提供事实问答题。

### 二、错误信息探测（`scripts/error_probe.py`）

许多中转站会把后方服务返回的错误原文直接转给用户。脚本向接口发送十二种不合规的请求（`--protocol` 指定协议，不适用于该协议的探测会标为 SKIPPED，`messages` 另有“缺少 max_tokens”一项），并完整记录返回内容，从中寻找后方服务的名称、错误编号和使用限制，再以这些线索进行检索和交叉验证。

| 请求类型 | 目的 |
|---|---|
| 消息列表为空 | 获取后方服务对空输入的原始提示 |
| 首条消息不是系统消息 | 了解后方是否强制要求系统消息在前 |
| 使用开发者角色 | 了解后方是否支持该角色 |
| 使用不存在的角色 | 了解后方采用的技术框架及其允许的角色列表 |
| 显式关闭或开启流式输出 | 了解后方是否只支持其中一种 |
| 设置极大的输出长度 | 获取后方允许的长度范围，有时还会暴露账户余额 |
| 设置不合理的温度参数 | 获取后方允许的参数范围 |
| 工具定义不完整 | 观察后方的校验提示用语 |
| 使用不存在的输出格式 | 获取后方支持的格式清单 |
| 消息内容为空值 | 观察后方对空内容的处理方式 |
| 使用不存在的模型名 | 获取中转站的渠道名称和后方支持的模型列表 |

使用要点：

1. 对每个模型分别测试，因为同一中转站的不同模型可能来自不同的后方服务。
2. 关注不属于中转站自身格式的内容，如陌生的品牌名称、数字编号、渠道名称。
3. 比较不同模型的结果。若同一请求在甲模型上正常、在乙模型上报错，说明该错误来自乙模型的后方服务。
4. 用错误原文进行检索，并以至少两个独立来源核实。

局限：官方直接提供的接口不存在隐藏的后方服务；会改写错误信息的中转站也不会暴露线索。此外，查明后方服务的身份并不能说明模型本身是否真实。

### 三、隐藏系统提示词检测（由 agent 手动完成，不计入评分）

部分中转站会在用户请求前悄悄加入一段提示词，例如“你是某某模型”，使模型的自我介绍不再可信。该检测不在脚本中，由 agent 按 `SKILL.md` 的说明手动完成，结论写入综合评价：

1. **输入长度比较**：分别发送很短和较长的输入，据此估算每次请求被额外附加的固定长度。正常情况下通常不超过四十，明显偏大则值得怀疑。
2. **思考过程检查**：查看模型的思考内容中，是否提到了用户从未提供的系统提示词或身份设定。
3. **直接询问**：请模型复述其收到的全部前置内容。该方法证据较弱，因为官方模型也常拒绝回答。
4. **异常响应记录**：例如对极短的输入返回编号为空、内容为空的响应。

应至少有两项迹象同时出现才下结论，并尽量以同一厂商的官方接口作为对照。

### 四、官方规范符合度（`scripts/spec_audit.py`）

真实性评分回答"是不是它"，规范符合度回答"它像不像官方接口"。两者独立计分，规范分低通常意味着渠道改写、限流或降级，而不是模型一定为假。

**画像（profile）**：`profiles/<id>.json` 把"官方端点应该是什么样"冻结成静态数据——固定采样参数、思考档位、token 上限、探测用例预期、usage 结构、性能基准分位数、达标线与负载参数。首个内置画像为 `kimi-k3.1-pro`（含 `kimi-k3.1-pro-highspeed` 变体）。画像基准来自 2026-11-09 的官方实测存档；**任何脚本、测试、agent 都不得再调用官方 key 或官方端点**，需要新基准须先征得用户同意。`profile.guard_base()` 命中 `official_hosts` 即拒绝运行，没有覆盖开关。

| 文件 | 作用 |
|---|---|
| `scripts/profile.py` | 加载、深合并覆盖、结构校验、官方端点守卫 |
| `scripts/perf_stats.py` | 分位数 / TPOT / TPS / TPM 纯函数（画像数字由它离线算出） |
| `scripts/load_test.py` | 标准档负载与缓存场景，单独输出 `load`+`cache` 块 |
| `scripts/spec_checks.py` | 15 项规范判定（纯逻辑，输入为已采集的响应记录） |
| `scripts/spec_audit.py` | 编排参数矩阵、工具、多模态、Formula、注入探测与负载，输出规范 JSON |

15 项及权重：官方基准 10、TTFT 6、缓存命中 8、计费字段 8、思考与参数 10、finish_reason 6、超时 3、视频 4、参数矩阵 8、reasoning_content 6、Formula 5、缓存 TTL 8、工具选择 6、结构化输出 5、隐藏提示词 7。

- `spec_score = round(100 × Σ(权重×状态) / Σ(非 skip 权重))`，通过 1、部分 0.5、失败 0、跳过不计。非 skip 权重低于 60% 时不出分（`score: null`），避免"两项通过也得满分"。
- 证据不全的项只能记"部分"：100MB 视频、58 分钟缓存回读、未实跑的长生成存活验证，均不得记为通过。
- 标准档约 136 次生成请求（负载 60 + 缓存 19 + 规范固定探测 57），运行前会先说明；`spec_audit.py --dry-run` / `load_test.py --print-plan` 可先查看计划且不发送请求。60 个样本的 p95 置信度低，报告会注明。

### 五、单文件 HTML 报告（`scripts/report_html.py`）

把真实性 JSON（`model_audit.py --out`）与规范 JSON（`spec_audit.py --out`）渲染成一个 HTML 文件：Carbon 设计语言、系统字体、无外部资源、无 JS，可离线打开和转发。内容依次为：评分概览（真实性、规范符合度、性能达标、成功率）、核心标准（官方基准 p50/p90/p95 偏差、TTFT、缓存、成功率、TPOT）、全部指标、官方基准对标、15 项规范清单（证据折叠）、计费与缓存、真实性与渠道。缺失数据渲染为"未测"，不补零；渲染前对两份 JSON 再做 `scrub`，所有动态字符串 `html.escape`，报告中只出现主机名。

Markdown 报告（`report-template.md`）仍然保留，二者结论必须一致。

## 安装

将本仓库复制到 Claude Code 的技能目录：

```bash
git clone https://github.com/mayuri-wylty/testing-llm-endpoint-authenticity ~/.claude/skills/testing-llm-endpoint-authenticity
```

## 使用方法

接口密钥只能通过环境变量 `LLM_API_KEY` 传入，不得写入文件或报告。

```bash
# 综合评分（默认 chat 协议）
LLM_API_KEY=sk-... python scripts/model_audit.py \
  --base https://host/v1 --model <模型名> --vendor moonshot --claim relay --claimed-window 128k \
  --identity-regex "Kimi|Moonshot|月之暗面" \
  --fact "Kimi K2 的总参数量和激活参数量分别是多少？::1T|1万亿|32B" --out report.json

# 使用内置画像（自动填充模型名、协议、身份、事实题、窗口、分词器家族，并拒绝官方端点）
LLM_API_KEY=sk-... python scripts/model_audit.py \
  --profile kimi-k3.1-pro --base https://host/v1 --claim relay --out kimi-audit.json

# Anthropic 协议
LLM_API_KEY=sk-... python scripts/model_audit.py \
  --base https://host/v1 --model <模型名> --protocol messages --vendor anthropic \
  --identity-regex "Claude|Anthropic" --out report.json

# 规范符合度 + 标准档负载（15 项，约 136 次生成请求）
LLM_API_KEY=sk-... python scripts/spec_audit.py \
  --profile kimi-k3.1-pro --base https://host/v1 --tier standard --out kimi-spec.json

# 单文件 HTML 报告（真实性 + 规范）
python scripts/report_html.py \
  --audit kimi-audit.json --spec kimi-spec.json --out kimi-report.html

# 错误信息探测（可多次使用 --model 以对比不同模型）
LLM_API_KEY=sk-... python scripts/error_probe.py \
  --base https://host/v1 --model <模型名1> --model <模型名2> --out probe.json
```

也可以直接对 Claude Code 说：“用 testing-llm-endpoint-authenticity 测试这个接口”。

## 注意事项

- 使用海外或受保护的接口时，若返回 403 错误，通常是服务方拒绝了 Python 默认的访问标识，脚本已做处理。
- 在 Windows 的命令行中直接发送中文可能出现乱码，建议通过 Python 发送，并设置环境变量 `PYTHONUTF8=1`。
- 具备思考过程的模型会占用输出额度，建议将输出长度上限设为 4000 以上。
- 已在聊天等公开场合出现过的密钥，应及时更换。

## 测试

`tests/` 下有模拟接口和回归矩阵（三协议 × 多种场景），只使用假密钥，不联网：

```bash
python tests/regress.py --jobs 6           # 既有真实性回归
python tests/selftest_perf_stats.py        # 分位数 / TPOT / TPM 纯函数
python tests/selftest_spec.py              # 规范套件（fixture 模式：score=89）
python tests/selftest_spec.py --live-mock  # good mock 端到端（12 pass + 7/8/12 partial，score 92）
python tests/selftest_spec.py --bad        # 规范套件（mock bad 模式，按预期失败）
python tests/selftest_report_html.py       # HTML 渲染结构、转义、无外链、无 key
```

## 运行环境

Python 3，仅使用标准库，无需安装其他依赖。
