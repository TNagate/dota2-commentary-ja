"""Dota 2 の勝率の線の前向きの記録（docs/FORWARD_PREREG.md、2026-11-12）。

型は 2026-11-12 0 時（JST）より前の試合だけで学習して固め（freeze）、それより後の試合にだけ当てる（score）。
  A  : ゴールド差＋経験値差＋チームの Elo（10・20・30 分のロジスティック回帰、封印 main で合格した最終の型）
  W1 : 第 2 段の本番の線（winprob2 の 5 分区間ごとのロジスティック回帰、8 特徴＋選手の Elo の差、較正なし）
  A2 : 全部入り（26 候補＋ゴールド差・経験値差・チームの Elo・選手の Elo＋127 ヒーローの組）の LightGBM（0・10・20・30 分）
使い方:
  DOTA_ERA=Bx DOTA_UNSEAL=main python -P forward_dota.py freeze   （1 回だけ。models/forward-20261112/ に書き、ハッシュを残す）
  DOTA_ERA=Bx DOTA_UNSEAL=main python -P forward_dota.py score    （毎週。work/FORWARD-DOTA/ に latest.md と history.jsonl）
"""
import datetime as dt, hashlib, json, os, pickle, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import dota_seal as S

assert S.ERA == "Bx" and "main" in S.OPENED, "DOTA_ERA=Bx DOTA_UNSEAL=main で動かす"
FREEZE = S._ts("2026-11-12")
MD = "models/forward-20261112"
OUT = "work/FORWARD-DOTA"
MINS = (0, 10, 20, 30)
A2_ITERS = {0: 106, 10: 143, 20: 351, 30: 250}  # CEILING-20261112 の止め時（研究の新しい方 20% で決めた回数）
JUDGE = S._ts("2027-02-15"); N_REQ = 3000
P2_SET = (16, 1400)


def player_pre():
    """選手の Elo の差（その試合より前の結果だけ）を全試合について計算する。seal_main_check.part_b と同じ作り。"""
    import pregame as P
    pid = {x["name"]: x["id"] for x in json.load(open("data/patch.json", encoding="utf-8"))}
    rows = []
    for line in open("data/pregame/matches.jsonl", encoding="utf-8"):
        m = json.loads(line); st = m.get("start_time")
        if st is None or m.get("radiant_win") is None:
            continue
        ps = m.get("p") or []
        m["rad"] = [p[1] for p in ps if p[0] < 128 and p[1]]; m["dire"] = [p[1] for p in ps if p[0] >= 128 and p[1]]
        m["pid"] = pid.get(m.get("patch")); rows.append(m)
    rows.sort(key=lambda m: (m["start_time"], m["match_id"]))
    pl = P.run_player(rows, *P2_SET)
    pre = {m["match_id"]: float(d) for m, d in zip(rows, pl)}
    heroes = {m["match_id"]: [(s < 128, h) for s, _, h in (m.get("p") or []) if h] for m in rows}
    return pre, heroes


def board_tables(pre, heroes, hids):
    """A・A2 用の分ごとの表（feature_screen の材料、チームの Elo は全試合を時刻順に通す）。"""
    import feature_screen as FS
    recs = FS.load()
    recs = [d for d in recs if S.in_era(d["start_time"], d.get("patch"))]
    hpos = {h: i for i, h in enumerate(hids)}; hcol = [f"h{h}" for h in hids]
    T = {}
    for m in MINS:
        df = FS.table(recs, m)
        df["st"] = df["match_id"].map({d["match_id"]: d["start_time"] for d in recs}); df["pre"] = df["match_id"].map(pre).fillna(0.0)
        df["pre_missing"] = ~df["match_id"].isin(pre.keys())
        H = np.zeros((len(df), len(hids)), dtype=np.float32)
        for r, mid in enumerate(df["match_id"].values):
            for rad, h in heroes.get(mid, []):
                if h in hpos:
                    H[r, hpos[h]] = 1.0 if rad else -1.0
        T[m] = pd.concat([df.reset_index(drop=True), pd.DataFrame(H, columns=hcol)], axis=1)
    return T, FS


def w1_table(pre):
    import winprob as W
    df, meta = W.load()
    df = df[[S.in_era(s, p) for s, p in zip(df["start_time"].values, df["patch"].values)]].reset_index(drop=True)
    df["pre"] = df["match_id"].map(pre).fillna(0.0)
    return df, W


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def freeze():
    if os.path.exists(f"{MD}/models.pkl"):
        raise SystemExit("もう固めてある（上書きしない）")
    os.makedirs(MD, exist_ok=True)
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    import winprob2 as W2
    pre, heroes = player_pre()
    hids = sorted({h for v in heroes.values() for _, h in v})
    T, FS = board_tables(pre, heroes, hids)
    hcol = [f"h{h}" for h in hids]
    BASEA = FS.BASE + ["T1_elo"]; RICH = FS.BASE + FS.CAND + ["pre"] + hcol
    M = {"A": {}, "A2": {}, "hids": hids, "BASEA": BASEA, "RICH": RICH}
    ntr = {}
    for m in MINS:
        tr = T[m][T[m]["st"] < FREEZE]; ntr[m] = int(len(tr))
        sc = StandardScaler().fit(tr[BASEA].values.astype(float))
        M["A"][m] = (sc, LogisticRegression(C=1.0, max_iter=3000).fit(sc.transform(tr[BASEA].values.astype(float)), tr["y"]))
        prm = dict(objective="binary", learning_rate=0.03, num_leaves=31, min_child_samples=100, subsample=0.8, subsample_freq=1,
                   colsample_bytree=0.8, reg_lambda=1.0, verbose=-1, seed=0, num_threads=4)
        M["A2"][m] = lgb.train(prm, lgb.Dataset(tr[RICH].values.astype(np.float32), tr["y"].values), A2_ITERS[m]).model_to_string()
    del T
    df, W = w1_table(pre)
    tr = df[df["start_time"] < FREEZE]
    M["W1"] = W2.fit(tr, W.FEATS + ["pre"]); M["W1_FEATS"] = W.FEATS + ["pre"]
    meta = {"freeze": "2026-11-12 00:00 JST", "train_rows_board": ntr, "train_matches_w1": int(tr["match_id"].nunique()),
            "made": dt.datetime.now().isoformat(timespec="seconds")}
    pickle.dump(M, open(f"{MD}/models.pkl", "wb"))
    meta["sha256"] = sha(f"{MD}/models.pkl")
    json.dump(meta, open(f"{MD}/meta.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False))


def ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6); return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def ece(y, p, bins=10):
    idx = np.clip((p * bins).astype(int), 0, bins - 1); e = 0.0
    for b in range(bins):
        s = idx == b
        if s.any():
            e += abs(p[s].mean() - y[s].mean()) * s.sum() / len(y)
    return float(e)


def boot(d, n=2000, seed=20261112):
    if len(d) < 30:
        return [float(d.mean()) if len(d) else None, None, None]
    r = np.random.default_rng(seed); m = [d[r.integers(0, len(d), len(d))].mean() for _ in range(n)]
    return [float(d.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))]


def score():
    import lightgbm as lgb
    import winprob2 as W2
    meta = json.load(open(f"{MD}/meta.json"))
    if sha(f"{MD}/models.pkl") != meta["sha256"]:
        raise SystemExit("固めた型のハッシュが合わない（書き換えられた）")
    M = pickle.load(open(f"{MD}/models.pkl", "rb"))
    pre, heroes = player_pre()
    df, W = w1_table(pre)  # 先に W1 を当てて大きな表を捨てる（メモリ）
    te_w = df[df["start_time"] >= FREEZE].reset_index(drop=True)
    del df
    pw = W2.pred(M["W1"], te_w) if len(te_w) else np.array([])
    wmap = {(a, b): p for a, b, p in zip(te_w["match_id"].values, te_w["minute"].values, pw)}
    T, FS = board_tables(pre, heroes, M["hids"])
    tier = json.load(open("out/state.json")).get("league_tiers", {})
    res = {"run": dt.datetime.now().isoformat(timespec="seconds"), "models_sha256": meta["sha256"], "min": {}}
    for m in MINS:
        te = T[m][T[m]["st"] >= FREEZE].reset_index(drop=True)
        if te.empty:
            res["min"][m] = {"n": 0}; continue
        y = te["y"].values.astype(float)
        sc, lr = M["A"][m]
        P = {"A": lr.predict_proba(sc.transform(te[M["BASEA"]].values.astype(float)))[:, 1],
             "A2": lgb.Booster(model_str=M["A2"][m]).predict(te[M["RICH"]].values.astype(np.float32)),
             "W1": np.array([wmap.get((a, m), np.nan) for a in te["match_id"].values])}
        ok = ~np.isnan(P["W1"])
        te, y = te[ok].reset_index(drop=True), y[ok]; P = {k: v[ok] for k, v in P.items()}
        g = te["gold_adv"].values
        pro = (te["leagueid"].astype(str).map(lambda x: tier.get(x, "") or "不明") != "excluded").values
        d = {"n": int(len(te)), "n_pro": int(pro.sum()), "pre_missing": float(te["pre_missing"].mean()),
             "first": str(dt.datetime.fromtimestamp(te["st"].min())), "last": str(dt.datetime.fromtimestamp(te["st"].max())),
             "sign_acc": float(np.where(g == 0, .5, ((g > 0) == (y == 1))).mean()) if m else None, "models": {}}
        for k, p in P.items():
            d["models"][k] = {"logloss": float(ll(y, p).mean()), "acc": float(((p > .5) == (y == 1)).mean()), "ece": ece(y, p),
                              "pro_logloss": float(ll(y[pro], p[pro]).mean()) if pro.any() else None,
                              "pro_acc": float(((p[pro] > .5) == (y[pro] == 1)).mean()) if pro.any() else None}
        d["A2_better_than_W1"] = boot(ll(y, P["W1"]) - ll(y, P["A2"]))
        d["W1_better_than_A"] = boot(ll(y, P["A"]) - ll(y, P["W1"]))
        res["min"][m] = d
    m20 = res["min"].get(20, {})
    n20 = m20.get("n", 0)
    now = dt.datetime.now().timestamp()
    v = "記録中（判定は 2027-02-15 以降、20 分の試合 3,000 以上）"
    if now >= JUDGE and n20 >= N_REQ:
        lb = m20["A2_better_than_W1"][1]
        a2 = lb is not None and lb > 0 and m20["models"]["A2"]["ece"] <= 0.04
        w1_ok = all(res["min"][k]["models"]["W1"]["ece"] <= 0.04 for k in (10, 20, 30) if res["min"].get(k, {}).get("n"))
        v = ("判定: A2 を本番の線にする" if a2 else "判定: W1 のまま") + ("" if w1_ok else "（W1 の較正のずれが 0.04 を超えた分がある＝見直す）")
    res["verdict"] = v
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/history.jsonl", "a") as f:
        f.write(json.dumps(res, ensure_ascii=False) + "\n")
    md = [f"# Dota 2 勝率の線の前向きの記録 — {res['run']}", "", f"型は 2026-11-12 0 時より前で固めた（sha256 {meta['sha256'][:12]}）。線は docs/FORWARD_PREREG.md。", "",
          f"**{v}**", "", "| 分 | 試合（格の内） | 期間 | A log loss/当たり | W1 | A2 | 符号の規則 | A2 − W1 の効き [95%] | W1 の較正のずれ |", "|---|---|---|---|---|---|---|---|---|"]
    f3 = lambda x: "—" if x[1] is None else f"{x[0]:+.4f} [{x[1]:+.4f}, {x[2]:+.4f}]"
    for m in MINS:
        d = res["min"][m]
        if not d.get("n"):
            md.append(f"| {m} | 0 | — | — | — | — | — | — | — |"); continue
        mm = d["models"]
        md.append(f"| {m} | {d['n']:,}（{d['n_pro']:,}） | {d['first'][:10]}〜{d['last'][:10]} | {mm['A']['logloss']:.4f}/{mm['A']['acc']:.1%} | "
                  f"{mm['W1']['logloss']:.4f}/{mm['W1']['acc']:.1%} | {mm['A2']['logloss']:.4f}/{mm['A2']['acc']:.1%} | "
                  + (f"{d['sign_acc']:.1%}" if d['sign_acc'] is not None else "—") + f" | {f3(d['A2_better_than_W1'])} | {mm['W1']['ece']:.3f} |")
    md += ["", "効きは正なら A2 の方が外れが小さい。選手の Elo が無い試合（新しい選手だけ等）は差 0 として扱う: 20 分で "
           + (f"{m20.get('pre_missing', 0):.1%}" if n20 else "—") + "。"]
    open(f"{OUT}/latest.md", "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    {"freeze": freeze, "score": score}[sys.argv[1]]()
