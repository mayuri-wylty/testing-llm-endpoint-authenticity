import sys,os,json
sys.argv=[sys.argv[0]]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"measure.py"),encoding="utf8").read().split("cands = sys.argv")[0])
for r in ["moonshotai/Kimi-K2-Thinking","moonshotai/Kimi-K2.5","moonshotai/Kimi-K2-Instruct-0905"]:
    run(r, hf_tok(r, trust_remote_code=True))
json.dump(results, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"results_extra2.json"),"w"), indent=1)
