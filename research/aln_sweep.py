import aln_backtest as A, itertools, statistics as st
import sys
DATA = sys.argv[1] if len(sys.argv) > 1 else "MNQ_5m.csv"   # TradingView export: time,open,high,low,close
days=A.load(DATA)
GRID=dict(brk=["wick","close"],mode=["all25","all50","split"],stop=[0.5,0.75,1.0],
          tp=["edge","e10","e30","e50","e100"],seq=[False,True])
def evaluate(session,dd):
    out=[]
    for combo in itertools.product(*GRID.values()):
        cfg=dict(zip(GRID.keys(),combo)); cfg["session"]=session
        res=[A.run_day(b,cfg) for b in dd]
        s=A.stats(res); s["cfg"]=cfg
        s["ctr"]=sum(sum(u["q"] for u in r["units"]) for r in res)
        s["perctr"]=s["total"]/s["ctr"] if s["ctr"] else 0
        out.append(s)
    return out
def key(c): return (c["brk"],c["mode"],c["stop"],c["tp"],c["seq"])
if __name__=="__main__":
    half=len(days)//2
    for sess in ["Asia","London"]:
        full=evaluate(sess,days); tr=evaluate(sess,days[:half]); te=evaluate(sess,days[half:])
        print(f"\n{'='*78}\n{sess}  ({len(days)} days: train {days[0][0].t.date()}..{days[half-1][0].t.date()}, test {days[half][0].t.date()}..{days[-1][0].t.date()})")
        print(f"  grid {len(full)} configs | median total ${st.median([x['total'] for x in full]):.0f} | profitable {100*sum(1 for x in full if x['total']>0)/len(full):.0f}%")
        print("\n  MARGINAL EFFECT (mean total $ over all other settings, whole sample)")
        for p,vals in GRID.items():
            parts=[]
            for v in vals:
                sub=[x["total"] for x in full if x["cfg"][p]==v]
                nn=[x["n"] for x in full if x["cfg"][p]==v]
                parts.append(f"{str(v):6}=${st.mean(sub):6.0f} (n~{st.mean(nn):.0f})")
            print(f"    {p:5}: "+" | ".join(parts))
        # in-sample-best -> out-of-sample
        tr.sort(key=lambda x:-x["total"]); tem={key(x["cfg"]):x for x in te}
        print("\n  TRAIN-BEST -> TEST (top 5 in first half, how they did in the second)")
        for x in tr[:5]:
            c=x["cfg"]; t=tem[key(c)]
            print(f"    {c['brk']:5} {c['mode']:5} sl{c['stop']:<4} {c['tp']:4} seq{int(c['seq'])} | "
                  f"train ${x['total']:6.0f} (n{x['n']:2}) -> test ${t['total']:6.0f} (n{t['n']:2}) ev${t['ev']:6.1f}")
        # rank correlation train vs test
        import math
        pairs=[(x["total"],tem[key(x["cfg"])]["total"]) for x in tr]
        rt=sorted(range(len(pairs)),key=lambda i:pairs[i][0]); re_=sorted(range(len(pairs)),key=lambda i:pairs[i][1])
        rk1={v:i for i,v in enumerate(rt)}; rk2={v:i for i,v in enumerate(re_)}
        n=len(pairs); dsum=sum((rk1[i]-rk2[i])**2 for i in range(n))
        rho=1-6*dsum/(n*(n*n-1))
        print(f"    Spearman rank corr train vs test: {rho:+.2f}   (0 = optimisation carries no information)")
        full.sort(key=lambda x:-x["total"])
        print("\n  TOP 8 WHOLE SAMPLE")
        for x in full[:8]:
            c=x["cfg"]
            print(f"    {c['brk']:5} {c['mode']:5} sl{c['stop']:<4} {c['tp']:4} seq{int(c['seq'])} | n={x['n']:2} tot${x['total']:6.0f} "
                  f"ev${x['ev']:6.1f}+-{x['se']:5.1f} win{x['win']:3.0f}% pf{x['pf']:4.2f} $/ctr{x['perctr']:6.1f}")
