"""第 2 段 試合中の勝率に試合前の強さ（選手の Elo の差）を足し、5 分の区間ごとに較正する（docs/INGAME_PREREG.md、sha 83756d27…）。

使い方: DOTA_ERA=R python3 -P winprob2.py --out work/INGAME-R-<日付>
入力: data/minutes（winprob.load と同じ絞り込み・分け方）、data/pregame/pre_R_K32_1400.json（pregame_export.py）
"""
import argparse, json, os, sys
import numpy as np
from sklearn.linear_model import LogisticRegression
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import winprob as W
import dota_seal as D
ERA = D.ERA or "R"
PRE = os.environ.get("DOTA_PRE", "data/pregame/pre_R_K32_1400.json")  # 11/11 時代 B は pregame_export が書いたファイルを渡す

FEATS = W.FEATS
bucket = lambda d: (d["minute"].clip(upper=60) // 5).astype(int).values


def fit(train, feats):
    out = {}
    bk = bucket(train)
    for b in np.unique(bk):
        g = train[bk == b]
        if g["y"].nunique() < 2 or len(g) < 20:
            continue
        sc = W.BucketScaler().fit(g, feats)
        out[b] = (sc, LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(g), g["y"]))
    return out


def pred(models, d):
    p = np.full(len(d), 0.5); bk = bucket(d)
    for b, (sc, m) in models.items():
        s = bk == b
        if s.any():
            p[s] = m.predict_proba(sc.transform(d[s]))[:, 1]
    return p


def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def rowll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def ece(y, p, bins=10):
    idx = np.minimum((p * bins).astype(int), bins - 1); t = 0.0
    for b in range(bins):
        s = idx == b
        if s.any():
            t += s.sum() * abs(y[s].mean() - p[s].mean())
    return float(t / len(y))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    df, meta = W.load()
    pre = json.load(open(PRE))
    df["pre"] = df["match_id"].astype(str).map(pre)
    missing = int(df.drop_duplicates("match_id")["pre"].isna().sum())
    df["pre"] = df["pre"].fillna(0.0)
    tr_ids, va_ids, te_ids = W.split(meta)
    tr, va, te = df[df.match_id.isin(tr_ids)], df[df.match_id.isin(va_ids)], df[df.match_id.isin(te_ids)].reset_index(drop=True)
    m0, m1 = fit(tr, FEATS), fit(tr, FEATS + ["pre"])
    p0, p1 = pred(m0, te), pred(m1, te)
    # 区間ごとの Platt（検証用の試合で）
    pv = pred(m1, va); bv = bucket(va); cal = {}
    for b in np.unique(bv):
        s = bv == b
        if va["y"].values[s].std() > 0 and s.sum() >= 50:
            cal[b] = LogisticRegression(C=1e6, max_iter=2000).fit(logit(pv[s]).reshape(-1, 1), va["y"].values[s])
    p1c = p1.copy(); bt = bucket(te)
    for b, m in cal.items():
        s = bt == b
        if s.any():
            p1c[s] = m.predict_proba(logit(p1[s]).reshape(-1, 1))[:, 1]
    y = te["y"].values.astype(float)
    P = {"W0": p0, "W1": p1, "W1c": p1c}
    L = {k: rowll(y, p) for k, p in P.items()}
    # 試合を単位にしたブートストラップ
    codes, uniq = np.unique(te["match_id"].values, return_inverse=True)[1], None
    nm = codes.max() + 1
    cnt = np.bincount(codes, minlength=nm)
    rng = np.random.default_rng(20261111)
    def boot(d):
        sm = np.bincount(codes, weights=d, minlength=nm)
        idx = rng.integers(0, nm, size=(2000, nm))
        s = sm[idx].sum(1) / cnt[idx].sum(1)
        return [float(d.mean()), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))]
    lineA = boot(L["W1"] - L["W0"]); dB = boot(L["W1c"] - L["W1"])
    late = te["minute"].values >= 5
    eW1, eW1c = ece(y[late], p1[late]), ece(y[late], p1c[late])
    res = {"n_matches": {"train": len(tr_ids), "valid": len(va_ids), "test": len(te_ids)}, "pre_missing_matches": missing,
           "overall": {k: {"logloss": float(L[k].mean()), "brier": float(((P[k] - y) ** 2).mean()),
                           "acc": float(((P[k] > 0.5) == (y == 1)).mean()), "ece_5min_plus": ece(y[late], P[k][late])} for k in P},
           "lineA_W1_minus_W0": lineA, "lineA_pass": lineA[2] < 0,
           "lineB_W1c_minus_W1": dB, "lineB_ece": [eW1, eW1c], "lineB_pass": (eW1c < eW1) and dB[2] < 0.001}
    mins = {}
    for mn in (0, 5, 10, 15, 20, 25, 30, 40):
        s = te["minute"].values == mn
        if s.sum() >= 50:
            mins[mn] = {k: {"n": int(s.sum()), "acc": float(((P[k][s] > 0.5) == (y[s] == 1)).mean()),
                            "brier": float(((P[k][s] - y[s]) ** 2).mean()), "logloss": float(L[k][s].mean()),
                            "ece": ece(y[s], P[k][s])} for k in P}
    res["by_minute"] = mins
    s0 = te["minute"].values == 0
    pg = 1 / (1 + 10 ** (-te["pre"].values[s0]))
    res["minute0_pregame_only"] = {"acc": float(((pg > 0.5) == (y[s0] == 1)).mean()), "logloss": float(rowll(y[s0], pg).mean())}
    res["pre_coef_by_bucket"] = {}
    for b, (sc, m) in sorted(m1.items()):
        j = (FEATS + ["pre"]).index("pre")
        res["pre_coef_by_bucket"][f"{b*5}-{b*5+4}分"] = round(float(m.coef_[0][j]), 4)
    res["by_tier"] = {}
    for tier in sorted(te["tier"].unique()):
        s = (te["tier"].values == tier) & (te["minute"].values == 20)
        if s.sum() >= 100:
            res["by_tier"][tier] = {k: {"n": int(s.sum()), "acc": float(((P[k][s] > 0.5) == (y[s] == 1)).mean()),
                                        "logloss": float(L[k][s].mean())} for k in P}
    json.dump(res, open(f"{a.out}/results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    f3 = lambda v: f"{v[0]:+.4f} [{v[1]:+.4f}, {v[2]:+.4f}]"
    o = res["overall"]
    lines = [f"# 第 2 段 試合中の勝率＋試合前の強さ＋局面ごとの較正（時代 {ERA} の研究の部分）— {os.path.basename(a.out)}", "",
             f"事前登録 docs/INGAME_PREREG.md。試合 学習 {len(tr_ids):,}・検証 {len(va_ids):,}・試験 {len(te_ids):,}（試験は {'3 回目の使用、最終の確かめは封印 R' if ERA == 'R' else '時代 B の新しい方 20%、最終の確かめは封印 main'}）。試合前の強さが無い試合 {missing}。", "",
             "| 型 | log loss（全部の分） | Brier | 正解率 | ECE（5 分以降） |", "|---|---|---|---|---|"]
    for k in P:
        lines.append(f"| {k} | {o[k]['logloss']:.4f} | {o[k]['brier']:.4f} | {o[k]['acc']:.1%} | {o[k]['ece_5min_plus']:.4f} |")
    lines += ["", f"- 線 A（W1 − W0）: {f3(lineA)} → {'合格' if res['lineA_pass'] else '不合格'}",
              f"- 線 B（W1c − W1 の log loss {f3(dB)}、ECE {eW1:.4f} → {eW1c:.4f}）→ {'合格' if res['lineB_pass'] else '不合格'}", "",
              "| 分 | 試合数 | W0 正解率 | W1 正解率 | W1c 正解率 | W0 Brier | W1 Brier | W1c Brier | W1c ECE |", "|---|---|---|---|---|---|---|---|---|"]
    for mn, d in mins.items():
        lines.append(f"| {mn} | {d['W0']['n']} | {d['W0']['acc']:.1%} | {d['W1']['acc']:.1%} | {d['W1c']['acc']:.1%} | "
                     f"{d['W0']['brier']:.4f} | {d['W1']['brier']:.4f} | {d['W1c']['brier']:.4f} | {d['W1c']['ece']:.3f} |")
    lines += ["", f"0 分の時点: 試合前の強さだけ 正解率 {res['minute0_pregame_only']['acc']:.1%}・log loss {res['minute0_pregame_only']['logloss']:.4f}",
              "", "試合前の強さの係数（標準化した特徴の上、区間ごと）: " + "、".join(f"{k} {v:+.3f}" for k, v in res["pre_coef_by_bucket"].items()),
              "", "格ごと（20 分）: " + "；".join(f"{t}: " + "、".join(f"{k} {v['acc']:.1%}" for k, v in d.items()) + f"（{d['W0']['n']}）" for t, d in res["by_tier"].items())]
    open(f"{a.out}/RESULT.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
