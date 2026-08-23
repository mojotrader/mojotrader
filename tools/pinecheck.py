import sys,io,re
BUILTIN=set('''open high low close volume time hlc3 hl2 ohlc4 na bar_index syminfo timeframe strategy math str
array color line label box table input request ta alert plot plotshape bgcolor fill hour minute second year
month dayofmonth dayofweek weekofyear nz barstate xloc yloc extend size text shape location position runtime
true false if else for while and or not var varip float int bool string type export import method switch to by
in continue break timenow last_bar_index barmerge order session adjustment currency display format scale
dividends earnings splits hline polyline chart ticker matrix map log'''.split())
def strip_code(src):
    """Remove comments and string literals with a real scanner (apostrophes inside "..." broke the regex)."""
    out=[];i=0;n=len(src);mode=None
    while i<n:
        c=src[i]
        if mode is None:
            if c=='"' or c=="'": mode=c; out.append(' ')
            elif c=='/' and i+1<n and src[i+1]=='/':
                while i<n and src[i]!='\n': i+=1
                continue
            else: out.append(c)
        else:
            if c=='\\' and i+1<n: i+=2; continue
            if c==mode: mode=None
            out.append(' ')
        i+=1
    return ''.join(out)
def check(path):
    src=io.open(path,encoding='utf-8').read()
    code=strip_code(src)
    declared=set()
    for m in re.finditer(r'^[ \t]*(?:var(?:ip)?\s+)?(?:float|bool|int|string|box|label|table|line|color|array<[^>]+>)?\s*([A-Za-z_]\w*)\s*(?::=|=)(?!=)',code,re.M):
        declared.add(m.group(1))
    for m in re.finditer(r'^[ \t]*\[([^\]]+)\]\s*=',code,re.M):
        declared.update(x.strip() for x in m.group(1).split(','))
    for m in re.finditer(r'^([A-Za-z_]\w*)\(([^)]*)\)\s*=>',code,re.M):
        declared.add(m.group(1))
        for a in m.group(2).split(','):
            a=a.strip()
            if a: declared.add(a.split()[-1])
    for m in re.finditer(r'\bfor\s+([A-Za-z_]\w*)\s*=',code): declared.add(m.group(1))
    used={}
    for ln,line in enumerate(code.split('\n'),1):
        for m in re.finditer(r'(?<![\w.#])([a-z][A-Za-z0-9_]*)',line):
            nm=m.group(1)
            e=m.end()
            # skip named-argument keywords:  foo = ...  inside a call
            if re.match(r'\s*=(?!=)',line[e:]) and re.search(r'[(,]\s*$',line[:m.start()]): continue
            used.setdefault(nm,ln)
    bad=sorted((v,k) for k,v in used.items() if k not in declared and k not in BUILTIN)
    print(f"--- {path}")
    if bad:
        for ln,nm in bad: print(f"    line {ln}: UNDECLARED '{nm}'")
    else:
        print("    no undeclared identifiers")
    return len(bad)
sys.exit(min(1,sum(check(p) for p in sys.argv[1:])))
