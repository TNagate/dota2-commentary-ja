"""分ごとの勝率の線（第 3 段）。規則は docs/WINPROB_PREREG.md に先に固定した。

使い方: python3 -P winprob.py [--out work/WINPROB-YYYYMMDD]
入力: data/minutes/<id>.json（minutes.py）。出力: RESULT.md, per_minute.csv, calibration.csv, results.json, fraction.csv
"""
import argparse, datetime as dt, glob, json, os, sys
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE)
FEATS = ["gold_adv", "xp_adv", "kills_diff", "towers_diff", "rax_diff", "roshan_diff", "d1", "d3"]


def tiers():
    try:
        return json.load(open("out/state.json")).get("league_tiers", {})
    except Exception:
        return {}


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -P で起動すると同じ場所の部品が見えないため（11/11 直し）
from dota_seal import sealed


def load():
    rows, meta = [], []
    tier_of = tiers()
    for p in glob.glob("data/minutes/*.json"):
        d = json.load(open(p))
        if d.get("version") is None or (d.get("duration") or 0) < 600 or d.get("radiant_win") is None:
            continue
        if sealed(d.get("start_time"), d.get("patch")):
            continue
        if d.get("human_players") is not None and d["human_players"] != 10:
            continue
        df = pd.DataFrame(d["minutes"], columns=d["columns"])
        df = df[df["minute"] * 60 <= d["duration"]].copy()
        if df.empty:
            continue
        df["kills_diff"] = df["kills_r"] - df["kills_d"]
        df["towers_diff"] = df["towers_d_lost"] - df["towers_r_lost"]
        df["rax_diff"] = df["rax_d_lost"] - df["rax_r_lost"]
        df["roshan_diff"] = df["roshan_r"] - df["roshan_d"]
        df["d1"] = df["gold_adv"].diff(1).fillna(0)
        df["d3"] = df["gold_adv"].diff(3).fillna(df["gold_adv"] - df["gold_adv"].iloc[0])
        df["match_id"] = d["match_id"]; df["y"] = int(bool(d["radiant_win"])); df["start_time"] = d["start_time"]
        df["duration"] = d["duration"]; df["patch"] = d.get("patch"); df["tier"] = tier_of.get(str(d.get("leagueid")), "?")
        rows.append(df)
        meta.append({"match_id": d["match_id"], "start_time": d["start_time"], "patch": d.get("patch"), "duration": d["duration"],
                     "tier": tier_of.get(str(d.get("leagueid")), "?")})
    return pd.concat(rows, ignore_index=True), pd.DataFrame(meta)


def split(meta):
    m = meta.sort_values("start_time")
    n = len(m); n_test = int(round(n * 0.2))
    test_ids = set(m["match_id"].iloc[n - n_test:])
    tr = m.iloc[: n - n_test]; n_val = int(round(len(tr) * 0.2))
    val_ids = set(tr["match_id"].iloc[len(tr) - n_val:])
    train_ids = set(tr["match_id"].iloc[: len(tr) - n_val])
    return train_ids, val_ids, test_ids


class PerMinuteScaler:
    """特徴をその分の学習データの平均・標準偏差で標準化する（60 分以上は 1 つに）。"""
    def fit(self, df, feats):
        g = df.assign(mb=df["minute"].clip(upper=60)).groupby("mb")[feats]
        self.mu, self.sd = g.mean(), g.std().replace(0, 1).fillna(1)
        self.feats = feats
        return self
    def transform(self, df):
        mb = df["minute"].clip(upper=60)
        mu = self.mu.reindex(mb).ffill().bfill().values
        sd = self.sd.reindex(mb).ffill().bfill().values
        return np.clip((df[self.feats].values - mu) / sd, -4, 4)


class BucketScaler:
    """5 分の区間の中の全部の行の平均と標準偏差で標準化する（2026-11-09 追加）。分ごとに標準化すると、0 分台のキル差のように
    ほとんど 0 の分で標準偏差が極端に小さくなり、1 キルが上限（±4）まで効いていた（開始 8 秒の 2 対 1 で勝つ確率 50%→14%）。"""
    def fit(self, df, feats):
        self.mu = df[feats].mean(); self.sd = df[feats].std().replace(0, 1).fillna(1); self.feats = feats
        return self
    def transform(self, df):
        return np.clip((df[self.feats].values - self.mu.values) / self.sd.values, -4, 4)


def fit_models(train, valid, n_train_matches):
    models = {}
    # M1
    s1 = PerMinuteScaler().fit(train, ["gold_adv"])
    m1 = LogisticRegression(C=1.0, max_iter=1000).fit(s1.transform(train), train["y"])
    models["M1_gold"] = ("pooled", s1, m1)
    # M2
    s2 = PerMinuteScaler().fit(train, FEATS)
    m2 = LogisticRegression(C=1.0, max_iter=1000).fit(s2.transform(train), train["y"])
    models["M2_pooled"] = ("pooled", s2, m2)
    # M3: 5 分刻み
    bucket = lambda df: (df["minute"].clip(upper=60) // 5).astype(int)
    m3 = {}
    for b, g in train.groupby(bucket(train)):
        if g["y"].nunique() < 2 or len(g) < 20:
            continue
        sc = BucketScaler().fit(g, FEATS)
        m3[b] = (sc, LogisticRegression(C=1.0, max_iter=1000).fit(sc.transform(g), g["y"]))
    models["M3_buckets"] = ("buckets", bucket, m3)
    # M4
    if n_train_matches >= 2000:
        import lightgbm as lgb
        X = train[FEATS + ["minute"]]; Xv = valid[FEATS + ["minute"]]
        m4 = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15, verbose=-1)
        m4.fit(X, train["y"], eval_set=[(Xv, valid["y"])], callbacks=[lgb.early_stopping(30, verbose=False)])
        models["M4_lgbm"] = ("lgbm", None, m4)
    return models


def predict(models, name, df):
    kind, a, b = models[name]
    if kind == "pooled":
        return b.predict_proba(a.transform(df))[:, 1]
    if kind == "buckets":
        p = np.full(len(df), 0.5); bk = a(df).values
        for bb, (sc, mdl) in b.items():
            idx = bk == bb
            if idx.any():
                p[idx] = mdl.predict_proba(sc.transform(df[idx]))[:, 1]
        return p
    return b.predict_proba(df[FEATS + ["minute"]])[:, 1]


def m0(df):
    return np.where(df["gold_adv"] > 0, 1.0, np.where(df["gold_adv"] < 0, 0.0, 0.5))


def metrics(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"acc": float(np.mean((p >= 0.5) == (y == 1))), "brier": float(np.mean((p - y) ** 2)),
            "logloss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), "n": int(len(y))}


def calib(y, p, bins=10):
    e = np.linspace(0, 1, bins + 1); idx = np.clip(np.digitize(p, e) - 1, 0, bins - 1)
    rows = []; ece = 0.0
    for b in range(bins):
        s = idx == b
        if s.sum() == 0:
            rows.append({"bin": b, "p_mean": None, "y_rate": None, "n": 0}); continue
        pm, yr = float(p[s].mean()), float(y[s].mean()); rows.append({"bin": b, "p_mean": pm, "y_rate": yr, "n": int(s.sum())})
        ece += abs(pm - yr) * s.sum() / len(y)
    return rows, float(ece)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=f"work/WINPROB-{dt.date.today():%Y%m%d}"); a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    df, meta = load()
    train_ids, val_ids, test_ids = split(meta)
    tr, va, te = df[df.match_id.isin(train_ids)], df[df.match_id.isin(val_ids)], df[df.match_id.isin(test_ids)]
    models = fit_models(tr, va, len(train_ids))
    # 型の選択（valid の log loss）
    sel = {k: metrics(va["y"].values, predict(models, k, va))["logloss"] for k in models}
    best = min(sel, key=sel.get)
    # 試験用: 学習用＋検証用で学び直してから当てる
    trv = pd.concat([tr, va]); models_f = fit_models(trv, va, len(train_ids) + len(val_ids))
    y = te["y"].values
    preds = {"M0_sign": m0(te)}
    for k in models_f:
        preds[k] = predict(models_f, k, te)
    res = {"n_matches": {"all": int(len(meta)), "train": len(train_ids), "valid": len(val_ids), "test": len(test_ids)},
           "preliminary": bool(len(meta) < 500), "selected_by_valid_logloss": best, "valid_logloss": sel,
           "test_period": [str(dt.datetime.fromtimestamp(int(meta[meta.match_id.isin(test_ids)].start_time.min()), dt.timezone.utc).date()),
                           str(dt.datetime.fromtimestamp(int(meta[meta.match_id.isin(test_ids)].start_time.max()), dt.timezone.utc).date())],
           "patches": {str(k): int(v) for k, v in meta["patch"].value_counts().items()},
           "overall_test": {k: metrics(y, p) for k, p in preds.items()}}
    # 分ごと
    pm = []
    for mnt, g in te.assign(mb=te["minute"].clip(upper=60)).groupby("mb"):
        row = {"minute": int(mnt), "n_matches": int(g.match_id.nunique())}
        pos = te.index.get_indexer(g.index)  # te の行番号（予測 p は te の並び）
        for k, p in preds.items():
            mt = metrics(te["y"].values[pos], p[pos])
            row[f"{k}_acc"], row[f"{k}_brier"], row[f"{k}_logloss"] = mt["acc"], mt["brier"], mt["logloss"]
        pm.append(row)
    pm = pd.DataFrame(pm); pm.to_csv(f"{a.out}/per_minute.csv", index=False)
    # 経過割合
    fr = []
    for q in range(1, 10):
        f = q / 10
        idx = (te["minute"] * 60 - te["duration"] * f).abs().groupby(te["match_id"]).idxmin()
        pos = te.index.get_indexer(idx.values)
        row = {"fraction": f, "n": int(len(pos))}
        for k, p in preds.items():
            mt = metrics(te["y"].values[pos], p[pos])
            row[f"{k}_acc"], row[f"{k}_brier"] = mt["acc"], mt["brier"]
        fr.append(row)
    fr = pd.DataFrame(fr); fr.to_csv(f"{a.out}/fraction.csv", index=False)
    # 較正（採用モデル）
    pb = preds[best]; cal = {}
    for label, mask in (("10min", te["minute"] == 10), ("20min", te["minute"] == 20), ("30min", te["minute"] == 30), ("all_5plus", te["minute"] >= 5)):
        rows, ece = calib(y[mask.values], pb[mask.values]); cal[label] = {"ece": ece, "bins": rows}
    pd.DataFrame([{"where": k, **r} for k, v in cal.items() for r in v["bins"]]).to_csv(f"{a.out}/calibration.csv", index=False)
    res["calibration"] = {k: v["ece"] for k, v in cal.items()}
    # ブートストラップ（試合単位）
    rng = np.random.default_rng(0); ids = np.array(sorted(test_ids)); boots = {"brier10": [], "brier20": [], "brier30": [], "acc20": []}
    te2 = te.assign(p=pb)
    byid = {i: g for i, g in te2.groupby("match_id")}
    for _ in range(1000):
        samp = rng.choice(ids, len(ids), replace=True)
        s = pd.concat([byid[i] for i in samp])
        for mnt, keys in ((10, ["brier10"]), (20, ["brier20", "acc20"]), (30, ["brier30"])):
            g = s[s["minute"] == mnt]
            if len(g) == 0:
                continue
            mt = metrics(g["y"].values, g["p"].values)
            for k in keys:
                boots[k].append(mt["brier"] if k.startswith("brier") else mt["acc"])
    res["bootstrap95"] = {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] if v else None for k, v in boots.items()}
    # 反転
    flips = te2[te2["minute"] >= 5].groupby("match_id")["p"].apply(lambda s: int((np.sign(s.values[1:] - 0.5) != np.sign(s.values[:-1] - 0.5)).sum()))
    res["reversals_per_match"] = {"median": float(flips.median()), "max": int(flips.max()), "mean": float(flips.mean())}
    # 追記（2026-11-09、本番の前）: 大会の格ごと・20 分の |ネットワース差| の帯ごと
    by_tier = []
    for tr_, g in te2.groupby("tier"):
        for mn in (10, 20, 30):
            gg = g[g["minute"] == mn]
            if len(gg):
                pos = te.index.get_indexer(gg.index)
                row = {"tier": tr_, "minute": mn, "n_matches": int(gg.match_id.nunique())}
                for k, p in preds.items():
                    mt = metrics(te["y"].values[pos], p[pos]); row[f"{k}_acc"], row[f"{k}_brier"] = mt["acc"], mt["brier"]
                by_tier.append(row)
    pd.DataFrame(by_tier).to_csv(f"{a.out}/by_tier.csv", index=False)
    res["by_tier"] = by_tier
    g20 = te2[te2["minute"] == 20]
    bands = []
    for lo, hi in ((0, 1000), (1000, 2500), (2500, 5000), (5000, 10**9)):
        gg = g20[(g20["gold_adv"].abs() >= lo) & (g20["gold_adv"].abs() < hi)]
        if len(gg):
            pos = te.index.get_indexer(gg.index)
            row = {"band": f"{lo}-{hi}", "n": int(len(gg))}
            for k, p in preds.items():
                row[f"{k}_acc"] = metrics(te["y"].values[pos], p[pos])["acc"]
            bands.append(row)
    res["gold_bands_20min"] = bands
    res["patches_test"] = {str(k): int(v) for k, v in meta[meta.match_id.isin(test_ids)]["patch"].value_counts().items()}
    json.dump(res, open(f"{a.out}/results.json", "w"), ensure_ascii=False, indent=1)
    # 報告
    def pmrow(mn, k):
        r = pm[pm.minute == mn]
        return (f"{100*r[f'{k}_acc'].iloc[0]:.1f}%", f"{r[f'{k}_brier'].iloc[0]:.3f}", int(r.n_matches.iloc[0])) if len(r) else ("-", "-", 0)
    L = [f"# 分ごとの勝率の線 {'（予備、試合が 500 未満）' if res['preliminary'] else ''} — {dt.date.today()}", "",
         f"試合 {res['n_matches']['all']}（学習 {res['n_matches']['train']}・検証 {res['n_matches']['valid']}・試験 {res['n_matches']['test']}、試験は {res['test_period'][0]}〜{res['test_period'][1]} の新しい方 20%）。パッチ {res['patches']}。",
         f"検証の log loss で選んだ型: **{best}**（{', '.join(f'{k} {v:.3f}' for k, v in sel.items())}）。", "",
         "## 試験用の分ごとの成績（採用の型と、底の M0 符号・M1 金だけ）", "", "| 分 | 試合数 | 採用 正解率 | 採用 Brier | M1 正解率 | M1 Brier | M0 正解率 |", "|---|---|---|---|---|---|---|"]
    for mn in (5, 10, 15, 20, 25, 30, 40, 50, 60):
        a1, b1, n1 = pmrow(mn, best); a2, b2, _ = pmrow(mn, "M1_gold"); a3, _, _ = pmrow(mn, "M0_sign")
        L.append(f"| {mn} | {n1} | {a1} | {b1} | {a2} | {b2} | {a3} |")
    L += ["", "## 経過割合ごと（試合の 10%〜90% の時点）", "", "| 割合 | 採用 正解率 | 採用 Brier | M1 正解率 |", "|---|---|---|---|"]
    for _, r in fr.iterrows():
        L.append(f"| {int(r.fraction*100)}% | {100*r[f'{best}_acc']:.1f}% | {r[f'{best}_brier']:.3f} | {100*r['M1_gold_acc']:.1f}% |")
    b = res["bootstrap95"]
    L += ["", f"較正 ECE: 10 分 {cal['10min']['ece']:.3f}、20 分 {cal['20min']['ece']:.3f}、30 分 {cal['30min']['ece']:.3f}、5 分以降の全行 {cal['all_5plus']['ece']:.3f}（0.05 超は較正が悪い）。",
          f"ブートストラップ 95% の幅（試合単位 1,000 回）: Brier 10 分 {b['brier10']}, 20 分 {b['brier20']}, 30 分 {b['brier30']}; 正解率 20 分 {b['acc20']}。",
          f"反転（5 分以降に優勢が入れ替わった回数）: 中央値 {res['reversals_per_match']['median']}、最大 {res['reversals_per_match']['max']}。",
          "", "## 大会の格ごと（試験用）", "", "| 格 | 分 | 試合数 | 採用 正解率 | 採用 Brier | M1 正解率 | M0 正解率 |", "|---|---|---|---|---|---|---|"]
    for r in by_tier:
        L.append(f"| {r['tier']} | {r['minute']} | {r['n_matches']} | {100*r[best+'_acc']:.1f}% | {r[best+'_brier']:.3f} | {100*r['M1_gold_acc']:.1f}% | {100*r['M0_sign_acc']:.1f}% |")
    L += ["", "## 20 分の |ネットワース差| の帯ごと（試験用）", "", "| 帯 | 試合数 | 採用 正解率 | M0 正解率 |", "|---|---|---|---|"]
    for r in bands:
        L.append(f"| {r['band']} | {r['n']} | {100*r[best+'_acc']:.1f}% | {100*r['M0_sign_acc']:.1f}% |")
    L += ["", "先行研究の目安: プロ 20 分 72〜75%（Hodge 2021）、試合の半分で 68〜72%（MOBA-Slice）、Dota Plus 平均 68%。20 分で 90% 超なら漏れを疑う（事前登録）。"]
    open(f"{a.out}/RESULT.md", "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
