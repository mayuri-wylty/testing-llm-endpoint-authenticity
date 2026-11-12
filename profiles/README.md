# profiles —— 模型画像与规范测试契约

画像把「官方端点应该是什么样」冻结成静态 JSON：固定采样参数、思考档位、token 上限、探测用例的预期结果、usage 结构、性能基准分位数、达标线、负载参数。所有规范/负载/报告脚本只读画像，不接受运行时向官方端点取数。

**基准来源**：`official_k31_baseline.json`（2026-11-09 用官方 key 实测存档，保存在本技能仓库之外的渠道测试工作目录）。**硬约束：任何脚本、测试、agent 都不得再调用官方 key 或官方端点。需要新基准须先征得用户同意。**

## 文件

| 文件 | 作用 |
|---|---|
| `profiles/<id>.json` | 内置画像；`kimi-k3.1-pro.json` 为首个 |
| `scripts/profile.py` | 加载、深合并 CLI 覆盖、结构校验、官方端点守卫 |
| `scripts/perf_stats.py` | 分位数 / TPOT / TPM 纯函数（画像里的数字由它离线算出） |

CLI：

```bash
python scripts/profile.py --list
python scripts/profile.py kimi-k3.1-pro
python scripts/profile.py kimi-k3.1-pro --check-base https://relay.example/v1
python scripts/profile.py kimi-k3.1-pro --override targets.spec_score_min=85
```

## 画像字段（schema_version = 1）

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✓ | 画像 id，与模型名一致时用于匹配基准块 |
| `display_name`, `vendor`, `protocol` | ✓ | `protocol` ∈ `chat` / `messages` / `responses` |
| `aliases` | | 同一画像可覆盖的模型名列表（如 highspeed 变体） |
| `official_hosts` | ✓ | 官方基准主机名；请求命中即拒绝运行 |
| `audit_defaults.identity_regex` | ✓ | 传给 `model_audit.py --identity-regex` |
| `audit_defaults.facts` | ✓ | `"问题::答案正则"` 列表；无官方来源时只放通用事实题，不得编造厂商事实 |
| `audit_defaults.claimed_window` | ✓ | 声称上下文窗口（如 `1M`） |
| `audit_defaults.tokenizer_family` | ✓ | 传给 `--tokenizer-family`（如 `kimi`） |
| `params.fixed` | ✓ | 五个固定采样参数（temperature/top_p/n/presence_penalty/frequency_penalty） |
| `params.thinking.tiers` | ✓ | 必须含 `low`/`high`/`max`；`default` ∈ tiers |
| `params.thinking.always_on` | | 默认请求是否总带 `reasoning_content` |
| `params.thinking.disabled_completion_tokens_max` | | `thinking.disabled` 时官方 completion_tokens 上限（K3.1 Pro ≤ 20） |
| `params.max_completion_tokens.default/max` | ✓ | 默认值与上限；`official_accepts_over_max` 记录官方宽容行为 |
| `params.timeout_s` | ✓ | 解析后写入 `common.set_default_timeout()`；K3.1 Pro = 3600 |
| `expected_probes` | ✓ | 探测用例名 → `{status}` 或 `{ok}`（可选 `completion_tokens_max`/`note`）；与 `params.matrix` 逐条比对 |
| `usage_schema.required/details/headers` | ✓ | 计费字段与缓存写入响应头；`token_ratio_range` 为 token/char 合理区间（不设 5% 硬阈值） |
| `baseline.short_latency_samples.<model>` | | 官方逐样本；支持 `{records:[...]}` 或紧凑并行数组 `{ttft[],total[],tps[],ct,fin}` |
| `baseline.percentiles` | ✓ | 主模型 p50/p90/p95：`ttft_s`/`latency_s`/`tpot_ms`/`output_tps` |
| `baseline.percentiles_by_model` | | 其它变体覆盖（如 `kimi-k3.1-pro-highspeed`） |
| `baseline.cache` | ✓ | 官方缓存命中率（相同 20k / 多轮 12k / 短 1k） |
| `baseline.reasoning_style` | | 每模型 `article_rate_mean`、`article_rate_min_max`、`top_openers` |
| `baseline.hidden_prompt_mentions` | | 官方自身泄漏比率，如 `"2/6"`；判注入必须与它对比 |
| `targets` | ✓ | `ttft_p90_max_s`、`cache_hit_min_pct`、`perf_p50_ratio_max`、`perf_p90_ratio_max`、`spec_score_min` |
| `load_test` | ✓ | `requests`、`concurrency`、`prompt_chars`、`max_completion_tokens`、`cache_repeats_20k` |

分位数定义与 `common.percentile()` 一致（线性插值，同 `run_compare.pct`）；TPOT = `(total-ttft)*1000/max(ct-1,1)`，输出速度 = `ct/(total-ttft)`。**画像里的分位数必须是离线算好的定值，脚本运行时只做对比，不重算基准。**

## 新增画像

1. 复制 `kimi-k3.1-pro.json`，改 `id`/`display_name`/`vendor`/`protocol`/`aliases`/`official_hosts`。
2. 官方基准数据必须来自**已存档的实测结果**（用户书面同意后才允许新采）；没有基准就删掉 `baseline.percentiles` 以外的推断值，不可用 0 或猜测填充。
3. `python scripts/profile.py <id>` 必须通过校验；`--check-base https://<官方主机>/v1` 必须被拒绝。
4. 新增画像不修改任何脚本；`profiles/README.md` 的冻结 schema 也不改。

## 官方端点守卫

`profile.guard_base(profile, base)` 在第一次请求前调用：命中 `official_hosts`（含子域）直接 `sys.exit("refusing to call the official baseline endpoint; use a channel URL")`。**没有覆盖开关。** `model_audit.py --profile`、`spec_audit.py`、`load_test.py` 三条入口都必须先过守卫。

## 报告如何读取画像

`report_html.py` / `report_md.py` 通过 `--profile <id|path>` 读取画像（只读，不改画像，不请求任何端点），用来给"官方 / 实测"并排展示提供官方值与达标线。实测值取自 spec JSON 的 `load.percentiles` 与 `cache.scenarios`。没传 `--profile` 时官方值显示"未提供"，报告照常生成。

| 画像字段 | 报告中的用途 |
|---|---|
| `baseline.percentiles`（实测模型命中 `baseline.percentiles_by_model` 时以后者为准） | 官方 `ttft_s` / `latency_s` / `tpot_ms` / `output_tps` 的 p50 / p90 / p95，与实测分位数逐项对照，给出偏差与判定 |
| `baseline.cache` | 官方缓存命中率（相同 20k / 多轮 12k / 短 1k），与 `cache.scenarios.*.hit_rate_pct` 对照 |
| `targets` | 判定门槛：`perf_p50_ratio_max` / `perf_p90_ratio_max`（实测÷官方）、`ttft_p90_max_s`、`cache_hit_min_pct`；字段缺失时沿用 `spec_checks` 的默认值 |
| `baseline_source.collected` | 页脚与概览中的"官方基准存档日期" |

- 官方值只来自画像里的存档数字，报告不重算基准；画像缺某个分位数或场景时，该行官方值显示"未提供"，不补零、不估算。
- 本节只描述读取方式，上文的 schema 与下文的 spec 输出契约均未改动。

## 冻结输出：`spec_audit.py` JSON（version 1）

实现阶段已冻结；Wave 1 各成员不得改结构。`profiles/README.md` 是唯一契约来源。

```jsonc
{
  "profile": "kimi-k3.1-pro", "model": "...", "protocol": "chat",
  "tested_at": "2026-11-11T12:00:00+08:00", "host": "relay.example.com", "tier": "standard",
  "spec": {"version": 1, "score": 89, "items": [ /* 15 项，见下 */ ]},
  "params": {"matrix": [{"case": "P:temp_0.6", "expected_status": 400,
                          "actual_status": 400, "match": true, "detail": "..."}]},
  "probes": {
    "usage": {"required_present": true, "total_matches": true, "details_present": true,
              "cache_write_present": true, "token_ratio": 0.34},
    "thinking": {"tiers_ok": ["low", "high", "max"], "default_reasoning": true,
                 "disabled_completion_tokens": 0, "fixed_ok": true, "mct_ok": true,
                 "assistant_echo_ok": true},
    "reasoning": {"present": true, "is_string": true, "independent": true,
                  "stream_order": ["reasoning", "content"], "reasoning_tokens": 0},
    "structured": {"reasoning_is_string": true, "content_is_string": true,
                   "nested_parts": false, "tool_calls_standard": true},
    "tools": {"auto_calls": true, "none_no_calls": true, "required_calls": true,
              "specified_fn_status": 400, "id_shape": "..."},
    "timeout": {"configured_s": 3600, "long_generation_survival": "未测"}
  },
  "load": {"samples": 60, "success": 60, "success_rate": 100.0,
           "percentiles": {"latency_s": {"p50":0,"p90":0,"p95":0,"n":0,"min":0,"max":0},
                           "ttft_s": {...}, "tpot_ms": {...}, "output_tps": {...}},
           "throughput": {"rpm": 0, "tpm": 0, "input_tpm": 0, "output_tpm": 0},
           "totals": {"prompt_tokens": 0, "completion_tokens": 0,
                      "cached_tokens": 0, "cache_write_tokens": 0},
           "finish": {"stop": 0, "length": 0, "tool_calls": 0, "empty": 0, "other": 0},
           "raw": [{"ok": true, "ttft": 0, "total": 0, "completion_tokens": 0,
                    "prompt_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0, "finish": "stop"}]},
  "cache": {"scenarios": {"A_identical_20k": {"pass": true, "hit_rate_pct": 0,
                                              "rounds": [{"i": 1, "cached_tokens": 0, "prompt_tokens": 0, "hit_rate_pct": 0}]},
                          "B_multiturn_12k": {...}, "C_short_1k": {...}, "E_cache_key": {...}},
            "ttl_5m": {"write_tokens": 0, "header": "...", "read_back_hit_pct": 0, "pass": true},
            "ttl_1h": {"write_tokens": 0, "header": "...", "read_back_after_s": 480,
                       "read_back_hit_pct": 0, "read_back_58m": "未测", "pass": true}},
  "multimodal": {"image_b64": {"ok": true, "status": 200, "detail": "..."},
                 "video_b64_1mb": {...}, "video_url": {...},
                 "video_100mb": {"status": "未测"}},
  "formula": {"tools": {"status": 200, "ok": true, "detail": "..."},
              "fibers": {"status": 200, "ok": true, "detail": "..."},
              "no_v1": {"status": 404, "ok": false, "detail": "..."}},
  "hidden_prompt": {"fixed_overhead": 0, "leak_rate": "0/6",
                    "identity": {"ok": true, "detail": "..."},
                    "extraction_attempt": {"tested": true, "leaked": false, "detail": "..."},
                    "probe_successes": 6, "probe_total": 6, "verdict": "..."},
  "notes": []
}
```

- `spec.score = null` 表示非 skip 权重占比 < 60%，覆盖不足不出分。
- 所有脚本输出前对全文做 `common.scrub(key)`；`host` 只写主机名，不写完整 URL 或 key。

### 15 项（`spec.items[]`）

单项结构：`{"id", "key", "title", "weight", "status", "measured", "expected", "delta", "evidence", "note"}`，`status` ∈ `pass|partial|fail|skip`，`evidence` 为字符串列表。

| # | key | 权重 | 输入来源 |
|---|---|---|---|
| 1 | `perf_baseline` | 10 | `load.percentiles` vs 画像 `baseline.percentiles` |
| 2 | `ttft` | 6 | `load.percentiles.ttft_s` |
| 3 | `cache_hit` | 8 | `cache.scenarios` |
| 4 | `usage_schema` | 8 | `probes.usage` + 画像 `usage_schema` |
| 5 | `thinking_params` | 10 | `probes.thinking` |
| 6 | `finish_reason` | 6 | `load.finish` + 流式尾部 |
| 7 | `timeout` | 3 | `probes.timeout`（未跑存活 → `partial`/`skip`，不得 `pass`） |
| 8 | `video` | 4 | `multimodal`；100MB 未实测最高 `partial` |
| 9 | `param_matrix` | 8 | `params.matrix` 一致率 |
| 10 | `reasoning_content` | 6 | `probes.reasoning` |
| 11 | `formula_tools` | 5 | `formula` |
| 12 | `cache_ttl` | 8 | `cache.ttl_5m` / `cache.ttl_1h` |
| 13 | `tools_choice` | 6 | `probes.tools` |
| 14 | `structured_output` | 5 | `probes.structured` |
| 15 | `hidden_prompt` | 7 | `hidden_prompt`（要求 6/6 泄漏探针成功才可 pass） |

`probes` 为探测结果原文；缺失时依赖它的项按 `skip` 处理并计入覆盖率下限。

权重合计 100。`spec_score = round(100 × Σ(weight × status_value) / Σ(非 skip 权重))`，`pass=1`、`partial=0.5`、`fail=0`、`skip` 不入分母。使用 Python 内置 `round()`。

## fixtures

| 文件 | 内容 |
|---|---|
| `tests/fixtures/sample_spec.json` | 完整 `spec_audit` 输出（15 项 + load 分位数 + cache 场景），覆盖第 1/2/3/12 项；报告与自测共用 |
| `tests/fixtures/sample_audit.json` | `model_audit.py` v2 输出（score/verdict/checks/performance），供 HTML 第 06 章与自测使用 |

fixture 中的每个数字都只用于渲染/断言，不代表任何真实渠道结果。
