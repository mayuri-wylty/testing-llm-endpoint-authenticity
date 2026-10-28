import json, itertools, os
p=r"C:\Users\20323\.claude\skills\testing-llm-endpoint-authenticity\scripts\data\tokenizer_bands.json"
d=json.load(open(p,encoding="utf-8"))
assert d["version"]==1 and d["generated"] and d["method"]
CLS=["en","zh","code","digits"]
for c in CLS:
    t=d["texts"][c]; assert len(t["unit"])>=40 and len(t["reps"])==2 and t["reps"][0]<t["reps"][1], c
    print(c,len(t["unit"]),t["reps"],"chars diff",len(t["unit"])*(t["reps"][1]-t["reps"][0]))
for n,f in d["families"].items():
    assert f["confidence"] in("high","medium","low","none") and f["source"] and "tokenizers" in f and "notes" in f
    if f["ratios"]:
        for c in CLS:
            lo,hi=f["ratios"][c]; assert 0<lo<hi,(n,c)
    print(f"{n:9} {f['confidence']:5}", f["ratios"] and {c:f['ratios'][c] for c in CLS})
def ov(a,b): return max(a[0],b[0])<=min(a[1],b[1])
print("\nPairs (overlap classes / 4):")
fams=[n for n,f in d["families"].items() if f["ratios"]]
for a,b in itertools.combinations(fams,2):
    A,B=d["families"][a]["ratios"],d["families"][b]["ratios"]
    o=[c for c in CLS if ov(A[c],B[c])]
    sep=[c for c in CLS if c not in o]
    flag="INDISTINGUISHABLE" if len(o)>=3 else ""
    # min relative gap on separating classes
    gaps=[(max(A[c][0],B[c][0])-min(A[c][1],B[c][1]))/max(A[c][1],B[c][1]) for c in sep]
    print(f"{a:8}-{b:8} overlap={len(o)} sep_by={sep} {('min_rel_gap=%.2f'%min(gaps)) if gaps else ''} {flag}")
