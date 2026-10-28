import json, os, glob, datetime, itertools
D=os.path.dirname(os.path.abspath(__file__))
R={}
for f in ["results_all.json","results_extra.json","results_extra2.json"]:
    for k,v in json.load(open(os.path.join(D,f))).items():
        if v: R[k]=v
texts=json.load(open(os.path.join(D,"texts_def.json"),encoding="utf-8"))
CLS=["en","zh","code","digits"]
def fam(confidence, toks, source, notes, expand=0.05, extra=None):
    measured={t:{c:round(R[t][c],4) for c in CLS} for t in toks}
    ratios={c:[round(min(m[c] for m in measured.values())*(1-expand),4), round(max(m[c] for m in measured.values())*(1+expand),4)] for c in CLS}
    d={"confidence":confidence,"tokenizers":toks,"ratios":ratios,"source":source,"notes":notes,"measured":measured}
    if extra: d.update(extra)
    return d
F={}
F["openai"]=fam("high",["o200k_base","cl100k_base"],
 "tiktoken 0.14.0 offline encodings o200k_base (gpt-4o/4.1/5 family) and cl100k_base (gpt-4/gpt-3.5); openai/gpt-oss-20b tokenizer.json (o200k_harmony) measured identical to o200k_base",
 "Band spans both encodings; the zh gap (o200k 0.71 vs cl100k 1.29) makes the zh band wide. o1/o3/o4 also use o200k_base. Special tokens not counted (encode with disallowed_special=()).")
F["deepseek"]=fam("high",["deepseek-ai/DeepSeek-V3","deepseek-ai/DeepSeek-V3.1","deepseek-ai/DeepSeek-V3.2"],
 "HuggingFace tokenizer.json of deepseek-ai/DeepSeek-V3, V3.1, V3.2 via tokenizers lib; add_special_tokens=False",
 "V3/V3.1/V3.2/R1 share one 128k vocab (measured identical on these 4 texts). Legacy deepseek-ai/DeepSeek-V2.5 (100k vocab, splits digits one by one) measured en 0.2074 zh 0.5161 code 0.3206 digits 1.0 and is NOT included in the band; an endpoint serving V2.x would fall outside the digits band.")
F["qwen"]=fam("high",["Qwen/Qwen2.5-7B-Instruct","Qwen/Qwen3-8B","Qwen/Qwen3-Next-80B-A3B-Instruct","Qwen/Qwen3.5-397B-A17B"],
 "HuggingFace tokenizer.json of Qwen2.5-7B-Instruct, Qwen3-8B, Qwen3-Next-80B-A3B-Instruct, Qwen3.5-397B-A17B",
 "Qwen2.5/Qwen3/Qwen3-Next identical on the 4 texts; Qwen3.5 has an extended vocab (slightly different zh/code). Splits digits one by one (digits ~1.0) which is the strongest discriminator vs o200k/deepseek/kimi/llama (0.333).")
F["kimi"]=fam("high",["moonshotai/Kimi-K2-Instruct","moonshotai/Kimi-K2-Instruct-0905","moonshotai/Kimi-K2-Thinking","moonshotai/Kimi-K2.5"],
 "moonshotai/Kimi-K2-* tokenizers (tiktoken-based tokenization_kimi.py, loaded with transformers trust_remote_code=True)",
 "K2 Instruct / 0905 / Thinking / K2.5 measured identical on the 4 texts. Older Moonshot (moonshot-v1-*) models are not covered.")
F["glm"]=fam("high",["THUDM/glm-4-9b-chat","zai-org/GLM-4.5","zai-org/GLM-4.6","zai-org/GLM-4.7","zai-org/GLM-5"],
 "HuggingFace tokenizers of THUDM/glm-4-9b-chat (trust_remote_code) and zai-org/GLM-4.5/4.6/4.7/5 (tokenizer.json)",
 "All GLM-4.x/5 measured identical. Digits slope (0.6353) is specific to the fixed digit string because GLM merges multi-digit runs irregularly; do not change the digits text without recomputing.")
F["llama"]=fam("high",["NousResearch/Meta-Llama-3-8B","unsloth/Llama-3.2-1B","unsloth/Llama-3.3-70B-Instruct","unsloth/Llama-4-Scout-17B-16E-Instruct"],
 "HuggingFace tokenizer.json from public mirrors (NousResearch / unsloth) of Llama-3, 3.2, 3.3 and Llama-4-Scout (gated meta-llama repos not used)",
 "Llama-3.x share one 128k vocab; Llama-4 has a 202k vocab (lower zh). Llama-2 (sentencepiece 32k) not included. Hosted Llama endpoints usually serve 3.x or 4.")
F["gemini"]=fam("low",["unsloth/gemma-2-2b","unsloth/gemma-3-1b-it"],
 "Approximation: Gemma SentencePiece tokenizers (unsloth mirrors). Google states Gemma uses a subset of Gemini's SentencePiece tokenizer (arXiv 2403.08295: splits digits, keeps whitespace); community gemini-tokenizer crate uses the Gemma3 model for all Gemini models. Google docs say ~4 chars/token for English (https://ai.google.dev/gemini-api/docs/tokens).",
 "Not the real Gemini tokenizer; band expanded +/-15% instead of 5% to reflect approximation. Google's 4 chars/token English rule of thumb (0.25 tok/char) is above the measured gemma en slope (~0.20 on this rare-word-heavy text), so a real Gemini endpoint may sit above this band on en; treat en as weak evidence and rely on digits/zh/code.",
 expand=0.15, extra={"approximation_of":"gemini"})
F["claude"]={"confidence":"low","tokenizers":[],
 "ratios":{"en":[0.17,0.32],"zh":[0.85,1.30],"code":[0.22,0.42],"digits":[0.30,0.55]},
 "source":"NOT computed. Anthropic publishes no tokenizer. Hand-set wide bands from third-party measurements via /v1/messages/count_tokens: claudecodecamp.com 2026-04-16 'I Measured Claude 4.7's New Tokenizer' (Claude 4.6 vs 4.7 tokens/char: English prose 0.231->0.278, technical docs 0.188->0.277, Python 0.271->0.349, Chinese prose 1.039->1.052, numeric CSV 0.528->0.567; single-shot counts incl. ~8 token message overhead); Sander Land 'On the Biology of Claude's Tokenizer' (tokencontributions.substack.com, 2026-07-29: Han chars tokenized one per token, digits in groups of 3, unofficial reverse engineering); Anthropic Opus 4.7 migration note: new tokenizer uses roughly 1.0-1.35x tokens vs previous.",
 "notes":"Bands are deliberately wide and unverified against this skill's fixed texts; the Claude tokenizer changed between model generations (4.6 vs 4.7+ differ 1.0-1.47x on English/code, ~1.0x on CJK), so one family band must cover several tokenizers. digits band is inferred (groups of 3 -> 0.333; numeric CSV measured 0.53-0.57 including commas). Use only as weak evidence (e.g. 'Claude-like' vs 'digits~1.0 Qwen/Gemini-like'); do not claim exact match.",
 "measured":None}
out={"version":1,"generated":datetime.date.today().isoformat(),
 "method":"Differential slope tok/char: slope = (tokens(unit*b) - tokens(unit*a)) / (len(unit*b) - len(unit*a)), reps=[a,b], which cancels fixed endpoint overhead (chat template, hidden system prompt). Offline values computed with tiktoken 0.14.0 / tokenizers 0.23.3 / transformers 5.20.2 with special tokens disabled; ratios[class]=[min,max] over the family's tokenizers expanded by 5% each side (gemini +/-15%; claude hand-set from third-party measurements). 'measured' holds the raw per-tokenizer slopes (extra field, may be ignored).",
 "texts":texts,"families":F}
p=r"C:\Users\20323\.claude\skills\testing-llm-endpoint-authenticity\scripts\data"
os.makedirs(p,exist_ok=True)
json.dump(out,open(os.path.join(p,"tokenizer_bands.json"),"w",encoding="utf-8"),ensure_ascii=False,indent=1)
print("written")
