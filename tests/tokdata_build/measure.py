import sys, json, os, traceback
sys.stdout.reconfigure(encoding="utf-8")
TEXTS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"texts_def.json"),encoding="utf-8"))
for k,v in TEXTS.items(): assert len(v["unit"])>=40, k
json.dump(TEXTS, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"texts.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)

def slopes(count):
    out={}
    for k,v in TEXTS.items():
        a,b=v["reps"]; u=v["unit"]
        ta,tb=count(u*a),count(u*b)
        out[k]=(tb-ta)/(len(u)*(b-a))
    return out

def hf_download(repo, patterns):
    from huggingface_hub import snapshot_download
    return snapshot_download(repo, allow_patterns=patterns, local_dir=os.path.join(os.environ["TEMP"],"tokdata","hf",repo.replace("/","__")))

results={}
def run(name, fn):
    try:
        r=fn(); results[name]=r; print("OK",name,{k:round(x,4) for k,x in r.items()}, flush=True)
    except Exception as e:
        results[name]=None; print("FAIL",name,type(e).__name__,str(e)[:300], flush=True)

def tk(enc):
    import tiktoken
    e=tiktoken.get_encoding(enc)
    return lambda: slopes(lambda s: len(e.encode(s, disallowed_special=())))
def hf_tok(repo, extra=None, **kw):
    def f():
        from transformers import AutoTokenizer
        t=AutoTokenizer.from_pretrained(repo, **kw)
        return slopes(lambda s: len(t.encode(s, add_special_tokens=False)))
    return f
def hf_raw(repo):
    def f():
        from tokenizers import Tokenizer
        p=hf_download(repo, ["tokenizer.json"])
        t=Tokenizer.from_file(os.path.join(p,"tokenizer.json"))
        return slopes(lambda s: len(t.encode(s, add_special_tokens=False).ids))
    return f
def hf_sp(repo, fname="tokenizer.model"):
    def f():
        import sentencepiece as spm
        p=hf_download(repo,[fname])
        sp=spm.SentencePieceProcessor(model_file=os.path.join(p,fname))
        return slopes(lambda s: len(sp.encode(s)))
    return f

cands = sys.argv[1:]
JOBS = {
 "o200k_base": tk("o200k_base"),
 "cl100k_base": tk("cl100k_base"),
 "deepseek-ai/DeepSeek-V3": hf_raw("deepseek-ai/DeepSeek-V3"),
 "deepseek-ai/DeepSeek-V2.5": hf_raw("deepseek-ai/DeepSeek-V2.5"),
 "Qwen/Qwen2.5-7B-Instruct": hf_raw("Qwen/Qwen2.5-7B-Instruct"),
 "Qwen/Qwen3-8B": hf_raw("Qwen/Qwen3-8B"),
 "zai-org/GLM-4.5": hf_raw("zai-org/GLM-4.5"),
 "zai-org/GLM-4.6": hf_raw("zai-org/GLM-4.6"),
 "unsloth/Llama-3.2-1B": hf_raw("unsloth/Llama-3.2-1B"),
 "NousResearch/Meta-Llama-3-8B": hf_raw("NousResearch/Meta-Llama-3-8B"),
 "unsloth/gemma-2-2b": hf_raw("unsloth/gemma-2-2b"),
 "unsloth/gemma-3-1b-it": hf_raw("unsloth/gemma-3-1b-it"),
 "unsloth/Llama-4-Scout-17B-16E-Instruct": hf_raw("unsloth/Llama-4-Scout-17B-16E-Instruct"),
 "moonshotai/Kimi-K2-Instruct": hf_tok("moonshotai/Kimi-K2-Instruct", trust_remote_code=True),
 "THUDM/glm-4-9b-chat": hf_tok("THUDM/glm-4-9b-chat", trust_remote_code=True),
}
for c in (cands or JOBS):
    run(c, JOBS[c])
json.dump(results, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"results_%s.json" % ("_".join(x.replace("/","_") for x in cands)[:40] or "all")),"w"), indent=1)
