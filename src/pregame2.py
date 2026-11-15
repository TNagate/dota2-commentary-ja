"""試合前の勝率の改良 P4（docs/PREGAME2_PREREG.md、sha 7a2d8451）。
1) 2012 年からの全試合を時刻順にたどり、各試合の前の情報だけで候補 C1〜C6 と基準（選手 Elo の差）を作る → data/pregame/features2.parquet
2) 時代 B の研究の部分で月ごとに前へ進める検証（候補を 1 つずつ足す）→ work/PREGAME2-20261112/
使い方: python3 -P pregame2.py build|eval|main   （main は封印 main の既見の記述、DOTA_UNSEAL=main が要る）"""
import json, math, os, sys, collections, itertools, time
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import dota_seal as S
OUT = "work/PREGAME2-20261112"; os.makedirs(OUT, exist_ok=True)
FP = "data/pregame/features2.parquet"
CANDS = {"C1": ["new_diff", "elo_x_new"], "C2": ["ph_wr", "ph_log"], "C3": ["form"], "C4": ["pairs"], "C5": ["hero_patch"], "C6": ["team_elo"]}


def build():
    pid = {x["name"]: x["id"] for x in json.load(open("data/patch.json", encoding="utf-8"))}
    tiers = json.load(open("out/state.json", encoding="utf-8")).get("league_tiers", {})
    rows = []
    for line in open("data/pregame/matches.jsonl", encoding="utf-8"):
        m = json.loads(line)
        if m.get("start_time") is None or m.get("radiant_win") is None: continue
        rows.append(m)
    rows.sort(key=lambda m: (m["start_time"], m["match_id"]))
    EP, ET = {}, {}; G = collections.Counter(); PH = collections.defaultdict(lambda: [0, 0]); FORM = collections.defaultdict(float)
    PAIR = collections.Counter(); HP = collections.defaultdict(lambda: [0, 0])
    out = []
    t0 = time.time()
    for i, m in enumerate(rows):
        ps = m.get("p") or []
        side = {0: [(a, h) for s, a, h in ps if s < 128 and a], 1: [(a, h) for s, a, h in ps if s >= 128 and a]}
        heroes = {0: [h for s, a, h in ps if s < 128 and h], 1: [h for s, a, h in ps if s >= 128 and h]}
        y = 1.0 if m["radiant_win"] else 0.0
        pk = m.get("patch")
        f = {}
        def mean(v): return float(np.mean(v)) if v else 0.0
        ep = [mean([EP.get(a, 1400.0) for a, _ in side[k]]) if side[k] else 1400.0 for k in (0, 1)]
        f["elo"] = (ep[0] - ep[1]) / 400
        a_, b_ = m.get("radiant_team_id"), m.get("dire_team_id")
        ta, tb = (ET.get(a_, 1500.0) if a_ else 1500.0), (ET.get(b_, 1500.0) if b_ else 1500.0)
        f["team_elo"] = (ta - tb) / 400
        new = [sum(G[a] < 10 for a, _ in side[k]) + (5 - len(side[k])) for k in (0, 1)]
        f["new_diff"] = new[0] - new[1]; f["elo_x_new"] = f["elo"] * (new[0] + new[1])
        phw = [mean([(PH[(a, h)][1] + 5) / (PH[(a, h)][0] + 10) - 0.5 for a, h in side[k]]) for k in (0, 1)]
        phl = [mean([math.log1p(PH[(a, h)][0]) for a, h in side[k]]) for k in (0, 1)]
        f["ph_wr"] = phw[0] - phw[1]; f["ph_log"] = phl[0] - phl[1]
        fo = [mean([FORM[a] for a, _ in side[k]]) for k in (0, 1)]; f["form"] = fo[0] - fo[1]
        pr = []
        for k in (0, 1):
            ids = sorted(a for a, _ in side[k]); prs = list(itertools.combinations(ids, 2))
            pr.append(mean([math.log1p(PAIR[q]) for q in prs]))
        f["pairs"] = pr[0] - pr[1]
        hw = [mean([(HP[(pk, h)][1] + 25) / (HP[(pk, h)][0] + 50) - 0.5 for h in heroes[k]]) for k in (0, 1)]
        f["hero_patch"] = hw[0] - hw[1]
        st = m["start_time"]; p_id = pid.get(pk)
        f.update(match_id=m["match_id"], start_time=st, y=y, tier=tiers.get(str(m.get("leagueid")), ""), era=S.era_of(st, p_id),
                 seal=S.in_seal(st, p_id), n_players=len(side[0]) + len(side[1]))
        out.append(f)
        # 更新（この試合の結果を足す）
        e = 1 / (1 + 10 ** ((ep[1] - ep[0]) / 400)); d = 16 * (y - e)
        for a, _ in side[0]: EP[a] = EP.get(a, 1400.0) + d
        for a, _ in side[1]: EP[a] = EP.get(a, 1400.0) - d
        et = 1 / (1 + 10 ** ((tb - ta) / 400)); dt = 16 * (y - et)
        if a_: ET[a_] = ta + dt
        if b_: ET[b_] = tb - dt
        for k, yk, ek in ((0, y, e), (1, 1 - y, 1 - e)):
            for a, h in side[k]:
                G[a] += 1; PH[(a, h)][0] += 1; PH[(a, h)][1] += yk; FORM[a] = 0.9 * FORM[a] + (yk - ek)
            ids = sorted(a for a, _ in side[k])
            for q in itertools.combinations(ids, 2): PAIR[q] += 1
            for h in heroes[k]: HP[(pk, h)][0] += 1; HP[(pk, h)][1] += yk
        if i % 50000 == 0: print(i, round(time.time() - t0), flush=True)
    df = pd.DataFrame(out); df.to_parquet(FP); print("書いた", len(df), FP)


def ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6); return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def fit_pred(tr, te, cols):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    sc = StandardScaler().fit(tr[cols]); m = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(tr[cols]), tr["y"])
    return m.predict_proba(sc.transform(te[cols]))[:, 1]


def rolling(df, test_mask, colsets, lgb_cols=None):
    """月ごとに前へ進める。test_mask の試合を、その月より前の全試合で学んだ型で当てる。"""
    df = df.copy(); df["month"] = pd.to_datetime(df["start_time"], unit="s").dt.to_period("M")
    preds = {k: np.full(len(df), np.nan) for k in colsets}
    if lgb_cols: preds["LGB"] = np.full(len(df), np.nan)
    for mo in sorted(df.loc[test_mask, "month"].unique()):
        te = test_mask & (df["month"] == mo); start = df.loc[te, "start_time"].min()
        tr = df[df["start_time"] < start]
        for k, cols in colsets.items(): preds[k][te.values] = fit_pred(tr, df[te], cols)
        if lgb_cols:
            import lightgbm as lgb
            g = lgb.LGBMClassifier(max_depth=3, n_estimators=300, learning_rate=0.03, verbose=-1).fit(tr[lgb_cols], tr["y"])
            preds["LGB"][te.values] = g.predict_proba(df.loc[te, lgb_cols])[:, 1]
        print(mo, int(te.sum()), flush=True)
    return preds


def summ(y, p):
    return {"n": int(len(y)), "logloss": round(float(ll(y, p).mean()), 4), "acc": round(float(((p > .5) == (y == 1)).mean()), 4),
            "brier": round(float(((p - y) ** 2).mean()), 4)}


def evaluate():
    from scipy.stats import norm
    df = pd.read_parquet(FP)
    df = df[df["start_time"] < S.SEALS["main"][0]].reset_index(drop=True)
    test = (df["era"] == "B") & (~df["seal"])
    base = ["elo"]
    colsets = {"B0": base, **{c: base + v for c, v in CANDS.items()}}
    P = rolling(df, test, colsets)
    y = df.loc[test, "y"].values; pro = df.loc[test, "tier"].isin(["professional", "premium"]).values
    thr = float(norm.ppf(1 - 0.025 / len(CANDS)))
    res = {"n_test": int(test.sum()), "n_pro": int(pro.sum()), "t_threshold": round(thr, 3), "models": {}, "cands": {}}
    for k in colsets: res["models"][k] = {"all": summ(y, P[k][test.values]), "pro": summ(y[pro], P[k][test.values][pro])}
    passed = []
    for c in CANDS:
        d = ll(y, P["B0"][test.values]) - ll(y, P[c][test.values]); dp = d[pro]
        t = d.mean() / (d.std(ddof=1) / math.sqrt(len(d))); tp = dp.mean() / (dp.std(ddof=1) / math.sqrt(len(dp)))
        ok = d.mean() >= 0.002 and t >= thr
        res["cands"][c] = {"effect": round(float(d.mean()), 5), "t": round(float(t), 2), "effect_pro": round(float(dp.mean()), 5), "t_pro": round(float(tp), 2), "pass": bool(ok)}
        if ok: passed.append(c)
    res["passed"] = passed
    p4cols = base + [x for c in passed for x in CANDS[c]]
    allcols = base + [x for v in CANDS.values() for x in v]
    P2 = rolling(df, test, {"P4": p4cols, "ALL_LR": allcols}, lgb_cols=allcols)
    for k in P2:
        res["models"][k] = {"all": summ(y, P2[k][test.values]), "pro": summ(y[pro], P2[k][test.values][pro])}
    rng = np.random.default_rng(0)
    for k in ("P4", "LGB"):
        d = ll(y, P2[k][test.values]) - ll(y, P["B0"][test.values])
        bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(1000)]
        res["models"][k]["minus_B0"] = [round(float(d.mean()), 4), round(float(np.percentile(bs, 2.5)), 4), round(float(np.percentile(bs, 97.5)), 4)]
    res["p4_cols"] = p4cols
    json.dump(res, open(f"{OUT}/eval.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def main_seen():
    """封印 main（既見）での記述: P3 相当（選手 Elo だけ）と P4 を月ごとに前へ進めて並べる。DOTA_UNSEAL=main を要する。"""
    if os.environ.get("DOTA_UNSEAL") != "main": sys.exit("DOTA_UNSEAL=main が要る")
    with open("logs/unseal.log", "a") as f: f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pregame2.py main（既見の記述、docs/PREGAME2_PREREG.md）\n")
    r = json.load(open(f"{OUT}/eval.json")); p4 = r["p4_cols"]
    df = pd.read_parquet(FP).reset_index(drop=True)
    test = (df["start_time"] >= S.SEALS["main"][0]) & (df["start_time"] < 1794409200)  # 2026-11-12 0 時（JST）より前
    P = rolling(df, test, {"P3": ["elo"], "P4": p4})
    y = df.loc[test, "y"].values; tier = df.loc[test, "tier"].values; st = df.loc[test, "start_time"].values
    out = {}
    rng = np.random.default_rng(0)
    def block(mask, name):
        a, b = P["P3"][test.values][mask], P["P4"][test.values][mask]; yy = y[mask]
        d = ll(yy, b) - ll(yy, a); bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(1000)]
        out[name] = {"P3": summ(yy, a), "P4": summ(yy, b), "P4_minus_P3": [round(float(d.mean()), 4), round(float(np.percentile(bs, 2.5)), 4), round(float(np.percentile(bs, 97.5)), 4)]}
        print(name, out[name], flush=True)
    block(np.ones(len(y), bool), "全体")
    for t in ("professional", "premium", "excluded"):
        if (tier == t).sum() > 100: block(tier == t, t)
    block(np.isin(tier, ["professional", "premium"]), "professional+premium")
    block(st < 1794409200 - 365 * 86400, "1年目まで"); block(st >= 1794409200 - 365 * 86400, "直近 1 年")
    json.dump(out, open(f"{OUT}/main_seen.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    {"build": build, "eval": evaluate, "main": main_seen}[sys.argv[1]]()
