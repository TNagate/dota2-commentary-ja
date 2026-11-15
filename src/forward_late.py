"""終盤の上積み（B0＋構成の遅さ L1＋倒れている人の残り秒 D2、docs/INGAME4_PREREG.md で封印 main に合格）の前向きの記録。
freeze: 2026-11-12 0 時より前で 30・35・40 分の B0 と C を学び models/forward-20261112/late.pkl・late_meta.json（sha256）に固める（上書きしない）。
score:  2026-11-12 以降の試合（分ごとの表があるもの）の kill を Drive の記録から読み（data/value/events_F.jsonl に貯める）、B0 と C を採点 → work/FORWARD-DOTA/late_latest.md・late_history.jsonl。
判定（事前に固定）: 30 分まで続いた試合が 2,000 以上で、30 分の効き（B0 − C の log loss の差）の 95% の幅の下の端が 0 より上なら、本番の勝率の 30 分以降に C を使う。
DOTA_UNSEAL=main・DOTA_ERA=Bx が要る（学習に封印 main を含めるため）。"""
import glob, hashlib, json, math, os, pickle, sys, time, collections
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
FREEZE = 1794409200; MD = "models/forward-20261112"; PK = f"{MD}/late.pkl"; META = f"{MD}/late_meta.json"; OUT = "work/FORWARD-DOTA"
MINS = (30, 35, 40); EVF = "data/value/events_F.jsonl"
resp = lambda t: min(90.0, 6 + 2.6 * max(t, 0) / 60)
ll = lambda y, p: -(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1)))


def ledger():
    led = {}
    for l in open("data/matches.jsonl"):
        x = json.loads(l); led[x["match_id"]] = x
    return led


def lateness(led):
    pm = {}
    for l in open("data/pregame/matches.jsonl"):
        x = json.loads(l); pm[x["match_id"]] = x
    games = sorted((x["start_time"], x["patch"], [(s < 128, h) for s, a, h in (x.get("p") or []) if h], x["radiant_win"], led[mid]["duration"])
                   for mid, x in pm.items() if mid in led and x.get("radiant_win") is not None and led[mid].get("duration"))
    pstart = {}
    for st, pk, hs, rw, du in games: pstart.setdefault(pk, st)
    late = {}
    for pk, t0 in pstart.items():
        acc = collections.defaultdict(lambda: [0, 0, 0, 0])
        for st, _, hs, rw, du in games:
            if st >= t0: break
            if st < t0 - 365 * 86400 or not (du >= 2400 or du < 1800): continue
            j = 0 if du >= 2400 else 2
            for rad, h in hs: acc[h][j] += 1; acc[h][j + 1] += (rw == rad)
        late[pk] = {h: (v[1] + 10) / (v[0] + 20) - (v[3] + 10) / (v[2] + 20) for h, v in acc.items()}
    def lat(mid):
        x = pm.get(mid)
        if not x: return 0.0
        t = late.get(x["patch"], {}); r = [t.get(h, 0.0) for s, a, h in x["p"] if s < 128 and h]; d = [t.get(h, 0.0) for s, a, h in x["p"] if s >= 128 and h]
        return (np.mean(r) if r else 0.0) - (np.mean(d) if d else 0.0)
    return lat


def table(files, deaths, lat):
    import winprob as W
    _g = W.glob.glob; W.glob.glob = lambda pat: files
    try: df, meta = W.load()
    finally: W.glob.glob = _g
    df = df[df["minute"].isin(MINS)].reset_index(drop=True)
    duplicates = df.duplicated(subset=["match_id", "minute"], keep=False)
    if duplicates.any():
        first = df.loc[duplicates, ["match_id", "minute"]].iloc[0]
        raise ValueError(f"duplicate late-model row: match_id={first.match_id}, minute={first.minute}")
    F = pd.read_parquet("data/pregame/features2.parquet").set_index("match_id")
    df["pre"] = df["match_id"].map(F["elo"]).fillna(0.0)
    df["L1"] = df["match_id"].map({m: lat(m) for m in df["match_id"].unique()})
    D2 = []
    for mid, mi in zip(df["match_id"].values, df["minute"].values):
        T = 60 * mi; rem = {2: 0.0, 3: 0.0}
        for tau, side in deaths.get(mid, []):
            if tau <= T and tau + resp(tau) > T: rem[side] += tau + resp(tau) - T
        D2.append(rem[3] - rem[2])
    df["D2"] = D2
    return df


def freeze():
    if os.path.exists(PK): sys.exit("もう固めてある（上書きしない）")
    import winprob as W
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    led = ledger(); deaths = {}
    for f in ("data/value/events_R.jsonl", "data/value/events_B.jsonl", "data/value/events_M.jsonl"):
        for l in open(f):
            x = json.loads(l); deaths[x["match_id"]] = [(e[0], 5 - e[2]) for e in x["ev"] if e[1] == "kill"]
    files = [f for f in glob.glob("data/minutes/*.json") if (lambda m: m in deaths and m in led and led[m]["start_time"] < FREEZE)(int(os.path.basename(f)[:-5]))]
    df = table(files, deaths, lateness(led))
    B0 = W.FEATS + ["pre"]; C = B0 + ["L1", "D2"]; M = {"B0": B0, "C": C}
    for mi in MINS:
        d = df[df["minute"] == mi]
        for k, cols in (("B0", B0), ("C", C)):
            sc = StandardScaler().fit(d[cols]); M[(k, mi)] = (sc, LogisticRegression(C=1.0, max_iter=3000).fit(sc.transform(d[cols]), d["y"]))
        M[("n", mi)] = int(len(d))
    pickle.dump(M, open(PK, "wb"))
    meta = {"freeze": FREEZE, "made": time.strftime("%Y-%m-%d %H:%M:%S"), "train_rows": {str(mi): M[("n", mi)] for mi in MINS},
            "sha256": hashlib.sha256(open(PK, "rb").read()).hexdigest(), "prereg": "docs/INGAME4_PREREG.md"}
    json.dump(meta, open(META, "w"), indent=1); print(meta)


def score():
    import match_store as MS, value_events as VE
    meta = json.load(open(META))
    if hashlib.sha256(open(PK, "rb").read()).hexdigest() != meta["sha256"]: sys.exit("固めた型が変わっている（止まる）")
    M = pickle.load(open(PK, "rb")); led = ledger()
    have = {}
    if os.path.exists(EVF):
        for l in open(EVF): x = json.loads(l); have[x["match_id"]] = x["kills"]
    files = [f for f in glob.glob("data/minutes/*.json") if (lambda m: m in led and led[m]["start_time"] >= FREEZE)(int(os.path.basename(f)[:-5]))]
    with open(EVF, "a") as fo:
        for f in files:
            mid = int(os.path.basename(f)[:-5])
            if mid in have or (led[mid].get("duration") or 0) < 1800: continue
            try: m = MS.load(led[mid])
            except Exception: m = None
            if not m: continue
            k = [(e[0], 5 - e[2]) for e in VE.events(m) if e[1] == "kill"]; have[mid] = k
            fo.write(json.dumps({"match_id": mid, "kills": k}) + "\n")
    deaths = {m: [tuple(x) for x in v] for m, v in have.items()}
    files = [f for f in files if int(os.path.basename(f)[:-5]) in deaths]
    os.makedirs(OUT, exist_ok=True)
    res = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "minutes": {}}
    lines = [f"# 終盤の上積みの前向きの記録 — {res['at']}", "", f"型は 2026-11-12 0 時より前で固めた（sha256 {meta['sha256'][:12]}）。線は docs/INGAME4_PREREG.md と forward_late.py の頭。", ""]
    if files:
        df = table(files, deaths, lateness(led)); rng = np.random.default_rng(0)
        for mi in MINS:
            d = df[df["minute"] == mi]
            if len(d) == 0: continue
            y = d["y"].values
            pb = M[("B0", mi)][1].predict_proba(M[("B0", mi)][0].transform(d[M["B0"]]))[:, 1]; pc = M[("C", mi)][1].predict_proba(M[("C", mi)][0].transform(d[M["C"]]))[:, 1]
            dd = ll(y, pb) - ll(y, pc); bs = sorted(dd[rng.integers(0, len(dd), len(dd))].mean() for _ in range(2000))
            r = {"n": int(len(d)), "B0": round(float(ll(y, pb).mean()), 4), "C": round(float(ll(y, pc).mean()), 4), "effect": [round(float(dd.mean()), 4), round(float(bs[50]), 4), round(float(bs[1949]), 4)]}
            res["minutes"][mi] = r; lines.append(f"- {mi} 分: 試合 {r['n']}、B0 {r['B0']}、C {r['C']}、効き {r['effect']}")
        r30 = res["minutes"].get(30, {})
        if r30.get("n", 0) >= 2000:  # ★11/13 会議 dota4: 30 分の効き 0.004・ばらつき 0.078 から 1,000 では足りない→2,000
            res["verdict"] = "30 分以降に C を使う" if r30["effect"][1] > 0 else "B0 のまま"; lines += ["", f"**判定: {res['verdict']}**"]
        else:
            lines += ["", f"記録中（30 分まで続いた試合 2,000 で判定、今 {r30.get('n', 0)}）"]
    else:
        lines.append("まだ試合が無い。")
    open(f"{OUT}/late_latest.md", "w").write("\n".join(lines) + "\n")
    with open(f"{OUT}/late_history.jsonl", "a") as f: f.write(json.dumps(res, ensure_ascii=False) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    {"freeze": freeze, "score": score}[sys.argv[1]]()
