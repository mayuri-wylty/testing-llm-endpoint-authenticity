import sys,os,json
sys.argv=[sys.argv[0]]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"measure.py"),encoding="utf8").read().split("cands = sys.argv")[0])
for r in ["zai-org/GLM-5","zai-org/GLM-4.7","moonshotai/Kimi-K2.5","moonshotai/Kimi-K2-Thinking","deepseek-ai/DeepSeek-V3.2","deepseek-ai/DeepSeek-V3.1","Qwen/Qwen3.5-397B-A17B","Qwen/Qwen3-Next-80B-A3B-Instruct","openai/gpt-oss-20b","unsloth/Llama-3.3-70B-Instruct"]:
    run(r, hf_raw(r))
json.dump(results, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"results_extra.json"),"w"), indent=1)
