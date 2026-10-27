# CONTRACT (frozen by T1 core; teammates may ADD fields, never change/remove)
Dir `scripts/` = S. Run: `python tests/regress.py` (15/15 must stay green) + `python tests/selftest_core.py`. No git: only touch your own files.

## Ownership
core(T1): model_audit.py common.py scoring.py(v2 weights: T9) adapters.py | T2: adapters_anthropic.py (+error_probe messages branch) | T3: adapters_responses.py |
T4: checks_headers.py | T5: checks_longctx.py | T6: checks_reasoning.py (+data/) | T7b: checks_tokenizer.py | checks_format/identity/tools/basic.py: unowned, keep protocol-agnostic.
To change a contract item or another owner's file: SendMessage the owner/lead.

## Rules
1. Checks never touch wire JSON (no `d["choices"]`, `usage["prompt_tokens"]`): use `Audit.chat()` -> `Resp.norm`.
2. A check = module with `NAME` (key of `scoring.WEIGHTS` == output check name) and `run(audit)` that calls `audit.add(...)` exactly once (several names per module: expose extra objects with NAME/run, see `checks_basic.ERRORS`).
3. Register: import it in model_audit.py and put it in `CHECKS` (order = run order; headers/provenance after the checks that fill `audit.headers`). Add its weight to `scoring.WEIGHTS` (unknown name -> KeyError -> recorded as CRASHED).
4. Unsupported feature -> `audit.add(NAME, None, note)` (n/a, excluded from the total, weight renormalised), never a 0.
5. Exceptions in `run` are caught by the orchestrator: score 0, name in `crashed_checks`. Don't swallow real errors silently.

## Norm (adapters.py dataclass; missing -> None/""/[]; parse never raises, empty choices OK)
`ok` (HTTP 200 and JSON object), `text`, `tool_calls [{id,name,args(dict; None if unparseable),args_raw}]`, `usage {prompt,completion,total,reasoning}|None` (values as sent, None per missing),
`finish` (normalise to OpenAI words: stop|length|tool_calls), `id`, `model`, `has_reasoning`, `reasoning_text`, `has_output` (>=1 choice/content block/output item), `echo` (adapter-private assistant turn for append_tool_result).

## Adapter (subclass `adapters.Adapter`; ctor `(model="")`; export class `AnthropicMessages` in adapters_anthropic.py, `OpenAIResponses` in adapters_responses.py)
- `name`, `default_path` (chat `/chat/completions`), `SUPPORTS={json_mode,reasoning_effort,usage_total: bool}` read via `supports(feature)`.
- `headers(key) -> dict` auth + protocol headers only (Content-Type/User-Agent/Accept are added by `common.http`).
- `build(messages, *, system=None, tools=None, max_tokens=None, stream=False, json_mode=False, reasoning_effort=None, extra=None, model=None) -> (path, body)`.
  `messages` = OpenAI-style role/content list; `system` separate; `tools` in OpenAI function format (convert yourself); `max_tokens=None` = omit (adapters needing it supply a default);
  `model` overrides `self.model` (unknown-model probe); `extra` merged into the body top level; the chat adapter adds `stream_options.include_usage` when streaming.
  Your build must also accept the messages returned by your own `append_tool_result`.
- `parse(status, data) -> Norm`; `parse_stream(raw_text, headers=None) -> {done, usage_seen, text, content_type_ok}` (content_type_ok None without headers).
- `append_tool_result(msgs, norm, result_json) -> msgs` (opaque to checks). `get_adapter(protocol, model="")`: chat | messages | responses; missing module -> `ProtocolUnavailable` -> `error: protocol 'x' not available yet...`, rc 1.
- `usage_total` False => format check skips its `usage_sum` sub-test (n/a); `json_mode` False => tools+json renormalises over the 0.7 tool part.

## Audit (model_audit.py)
`chat(messages, *, system, tools, max_tokens, stream, json_mode, reasoning_effort, timeout, extra) -> Resp{status, headers(lowercase), t, ttfb, norm, raw, stream}`
raw = parsed JSON (non-stream 200) | text (stream/errors); `stream` = parse_stream dict (stream calls only). `max_tokens=None` -> `--max-tokens`. Every chat() feeds `calls/ttfbs/speeds`; `audit.headers` collects non-stream 200 headers.
`ask(q, **kw) -> (Resp, text)` (text = norm.text on 200, else `str(raw)[:200]`). `post(path, body, timeout=None) -> (status, headers, data, t)` raw request, not counted in perf (error probes).
`add(name, score|None, note="", weight=None)`; attributes `a` (argparse ns), `adapter`, `notes`, `checks`, `crashed`, `usage_inflated`, `hard_fail`, `key`, `url`, `timeout`.
Explicit `--path` overrides the path returned by `build`.

## CLI `a.*` (all declared; only `--protocol`, `--path` act in T1)
`--protocol {chat,messages,responses}`, `--claim {official,bedrock,vertex,azure,relay,unknown}` (T4), `--claimed-window` str "" (T5), `--deep` flag (T5), `--tokenizer-family` str "" (T7b), plus all v1 args. Output JSON adds `protocol`, `scoring_version` (=2).

## scoring.py
`WEIGHTS` (v2: headers 5/format 10/identity 15/reasoning 15/longctx 25/tools 10/instr 5/error 5/tokenizer 10; v1 history in baseline_v1.json), `total_score(checks, hard_fail)` (renormalise over non-None, cap 98, hard_fail cap 50), `verdict`, `performance(calls, ttfbs, speeds)` (medians), `VERSION`.

## Gotchas
- Check names are the regress keys: `format+stream`, `identity+facts`, `reasoning`, `long context`, `tools+json`, `instruction following`, `error format`, `vendor headers`, `tokenizer`.
- Format check now sends 2 extra 16-token streams (TTFB median of 3); they never affect the score.
- In regress.py's docstring the protocol example is `--protocol anthropic`; the real choice is `messages`.
- Old crash names (`format_and_stream`, `long_context`...) are now the display names in `crashed_checks`.
