"""試合前の勝率 P4 の前向きの記録（docs/PREGAME2_PREREG.md の「その後」）。
freeze: 2026-11-12 0 時（JST）より前の全試合で P4（ロジスティック回帰、列は work/PREGAME2-20261112/eval.json の p4_cols）を学び、
        models/forward-20261112/pregame_p4.pkl と pregame_meta.json（sha256）に固める（上書きしない）。
score:  data/pregame/features2.parquet（pregame2.py build で作り直した後）の 2026-11-12 以降の試合で、本番の P3（σ(2.2425・選手 Elo の差)）と P4 を採点し、
        work/FORWARD-DOTA/pregame_latest.md・pregame_history.jsonl に書く。
判定（事前に固定）: professional＋premium が 1,000 試合以上で、P4 − P3 の log loss の差の 95% の幅（試合単位 2,000 回）の上の端が 0 未満なら、試合前の本番を P4 にする。"""
import hashlib, json, os, pickle, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
FREEZE = 1794409200  # 2026-11-12 00:00 JST
MD = "models/forward-20261112"; PK = f"{MD}/pregame_p4.pkl"; META = f"{MD}/pregame_meta.json"
OUT = "work/FORWARD-DOTA"; FP = "data/pregame/features2.parquet"
P3_SLOPE = 2.2425
ll = lambda y, p: -(y * np.log(np.clip(p, 1e-6, 1 - 1e-6)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1)))


def freeze():
    if os.path.exists(PK): sys.exit("もう固めてある（上書きしない）")
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    cols = json.load(open("work/PREGAME2-20261112/eval.json"))["p4_cols"]
    df = pd.read_parquet(FP); tr = df[df["start_time"] < FREEZE]
    sc = StandardScaler().fit(tr[cols]); m = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(tr[cols]), tr["y"])
    pickle.dump({"cols": cols, "scaler": sc, "model": m}, open(PK, "wb"))
    meta = {"freeze": FREEZE, "cols": cols, "train_rows": int(len(tr)), "made": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sha256": hashlib.sha256(open(PK, "rb").read()).hexdigest(), "prereg": "docs/PREGAME2_PREREG.md"}
    json.dump(meta, open(META, "w"), indent=1); print(meta)


def score():
    meta = json.load(open(META))
    if hashlib.sha256(open(PK, "rb").read()).hexdigest() != meta["sha256"]: sys.exit("固めた型が変わっている（止まる）")
    ob = pickle.load(open(PK, "rb"))
    df = pd.read_parquet(FP); te = df[df["start_time"] >= FREEZE].reset_index(drop=True)
    os.makedirs(OUT, exist_ok=True)
    res = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "n": int(len(te))}
    lines = [f"# 試合前の勝率 P4 の前向きの記録 — {res['at']}", "", "型は 2026-11-12 0 時より前で固めた（sha256 " + meta["sha256"][:12] + "）。線は docs/PREGAME2_PREREG.md。", ""]
    if len(te):
        y = te["y"].values; p3 = 1 / (1 + np.exp(-P3_SLOPE * te["elo"].values)); p4 = ob["model"].predict_proba(ob["scaler"].transform(te[ob["cols"]]))[:, 1]
        rng = np.random.default_rng(0)
        for name, mask in (("全体", np.ones(len(te), bool)), ("professional+premium", te["tier"].isin(["professional", "premium"]).values)):
            if mask.sum() == 0: continue
            d = ll(y[mask], p4[mask]) - ll(y[mask], p3[mask]); bs = sorted(d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000))
            res[name] = {"n": int(mask.sum()), "P3_logloss": round(float(ll(y[mask], p3[mask]).mean()), 4), "P4_logloss": round(float(ll(y[mask], p4[mask]).mean()), 4),
                         "P3_acc": round(float(((p3[mask] > .5) == (y[mask] == 1)).mean()), 4), "P4_acc": round(float(((p4[mask] > .5) == (y[mask] == 1)).mean()), 4),
                         "P4_minus_P3": [round(float(d.mean()), 4), round(float(bs[50]), 4), round(float(bs[1949]), 4)]}
            r = res[name]; lines.append(f"- {name}: 試合 {r['n']}、P3 {r['P3_logloss']}/{r['P3_acc']:.1%}、P4 {r['P4_logloss']}/{r['P4_acc']:.1%}、P4 − P3 {r['P4_minus_P3']}")
        pp = res.get("professional+premium", {})
        if pp.get("n", 0) >= 1000:
            res["verdict"] = "P4 を本番に" if pp["P4_minus_P3"][2] < 0 else "P3 のまま"
            lines += ["", f"**判定: {res['verdict']}**（professional+premium {pp['n']} 試合）"]
        else:
            lines += ["", f"記録中（professional+premium が 1,000 試合で判定、今 {pp.get('n', 0)}）"]
    else:
        lines.append("まだ試合が無い。")
    open(f"{OUT}/pregame_latest.md", "w").write("\n".join(lines) + "\n")
    with open(f"{OUT}/pregame_history.jsonl", "a") as f: f.write(json.dumps(res, ensure_ascii=False) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    {"freeze": freeze, "score": score}[sys.argv[1]]()
