"""出来事の価値の表（docs/VALUE_PREREG.md、sha 3d508f32…）。

使い方: DOTA_ERA=R python3 -P value_table.py --out work/VALUE-R-<日付>
入力: data/minutes（winprob.load）、data/pregame/pre_R_K32_1400.json、data/value/events_R.jsonl（value_events.py）
"""
import argparse, json, os, sys
import numpy as np
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import winprob as W
import winprob2 as W2
import dota_seal as D
ERA = D.ERA or "R"
PRE = os.environ.get("DOTA_PRE", "data/pregame/pre_R_K32_1400.json")

ORDER = ["first_blood", "kill", "teamfight_win", "T1", "T2", "T3", "T4", "rax_melee", "rax_range", "mega", "roshan_1", "roshan_2plus", "buyback"]
NAMES = {"first_blood": "ファーストブラッド", "kill": "1 キル", "teamfight_win": "集団戦の勝ち（死亡差 2 以上）", "T1": "1 段の塔", "T2": "2 段の塔",
         "T3": "3 段の塔", "T4": "4 段の塔（本陣前）", "rax_melee": "近接の兵舎", "rax_range": "遠隔の兵舎", "mega": "メガクリープ（兵舎 6 つ）",
         "roshan_1": "Roshan 1 回目", "roshan_2plus": "Roshan 2 回目以降", "buyback": "バイバック（使った側）"}
OPENAI = {"rax_melee": 6, "rax_range": 6, "roshan_1": 5, "roshan_2plus": 5, "T3": 4.5, "T2": 3, "T1": 2.25}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    df, meta = W.load()
    pre = json.load(open(PRE))
    df["pre"] = df["match_id"].astype(str).map(pre).fillna(0.0)
    # 交差で外した勝率（試合を 5 つに分ける）
    ids = np.array(sorted(meta["match_id"]))
    fold = {m: i for i, m in zip(np.random.default_rng(20261111).integers(0, 5, len(ids)), ids)}
    df["fold"] = df["match_id"].map(fold)
    df["wp"] = np.nan
    for k in range(5):
        tr = df[df.fold != k]
        mdl = W2.fit(tr, W.FEATS + ["pre"])
        s = df.fold == k
        df.loc[s, "wp"] = W2.pred(mdl, df[s])
    df = df.sort_values(["match_id", "minute"])
    wp = {m: (g["minute"].values, g["wp"].values, int(g["y"].iloc[0])) for m, g in df.groupby("match_id")}

    def wp_at(m, minute):
        mins, w, y = wp[m]
        if minute > mins[-1]:
            return float(y)  # 試合が終わった後は結果
        j = np.searchsorted(mins, minute)
        return float(w[min(j, len(w) - 1)])

    # 対照: 全部の（試合, 分, 側）の Δ を 5 分区間 × 勝率の十分位で平均
    base_rows = []
    for m, (mins, w, y) in wp.items():
        for i, mn in enumerate(mins):
            d = wp_at(m, mn + 4) - w[i]
            for s in (1, -1):
                p = w[i] if s == 1 else 1 - w[i]
                base_rows.append((min(mn, 60) // 5, min(int(p * 10), 9), s * d))
    B = pd.DataFrame(base_rows, columns=["bk", "dec", "d"]).groupby(["bk", "dec"])["d"].mean()
    # 出来事
    recs = []
    obj_types = {"T1", "T2", "T3", "T4", "rax_melee", "rax_range", "mega", "roshan_1", "roshan_2plus"}
    for line in open(f"data/value/events_{ERA}.jsonl"):
        e = json.loads(line); m = e["match_id"]
        if m not in wp:
            continue
        objt = [(t, ty) for t, ty, s in e["ev"] if ty in obj_types]
        for t, ty, side in e["ev"]:
            if t < 0:
                mn = 0
            else:
                mn = int(t // 60)
            if mn > wp[m][0][-1]:
                continue
            s = 1 if side == 2 else -1
            w0 = wp_at(m, mn)
            p = w0 if s == 1 else 1 - w0
            d = s * (wp_at(m, mn + 4) - w0)
            base = B.get((min(mn, 60) // 5, min(int(p * 10), 9)), 0.0)
            alone = not any(mn * 60 <= t2 < (mn + 4) * 60 and not (t2 == t and ty2 == ty) for t2, ty2 in objt)
            recs.append((m, ty, t, p, (d - base) * 100, alone))
    R = pd.DataFrame(recs, columns=["match_id", "type", "t", "p", "v", "alone"])
    rng = np.random.default_rng(20261111)

    def boot(sub):
        if len(sub) < 30:
            return [float(sub.v.mean()) if len(sub) else float("nan"), float("nan"), float("nan"), int(len(sub))]
        g = sub.groupby("match_id")["v"].agg(["sum", "count"])
        sm, ct = g["sum"].values, g["count"].values; n = len(g)
        idx = rng.integers(0, n, size=(2000, n))
        s = sm[idx].sum(1) / ct[idx].sum(1)
        return [float(sub.v.mean()), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5)), int(len(sub))]

    res = {"n_matches": int(len(wp)), "types": {}}
    for ty in ORDER:
        sub = R[R.type == ty]
        res["types"][ty] = {"all": boot(sub), "even": boot(sub[(sub.p >= 0.4) & (sub.p <= 0.6)]),
                            "alone": boot(sub[sub.alone]), "median_min": float(np.median(sub.t) / 60) if len(sub) else None,
                            "per_match": float(len(sub) / len(wp))}
    json.dump(res, open(f"{a.out}/results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    f = lambda v: f"{v[0]:+.1f} [{v[1]:+.1f}, {v[2]:+.1f}]" if not np.isnan(v[1]) else (f"{v[0]:+.1f}（少ない）" if v[3] else "—")
    lines = [f"# 出来事の価値の表（時代 {ERA} の研究の部分）— {os.path.basename(a.out)}", "",
             f"事前登録 docs/VALUE_PREREG.md。試合 {len(wp):,}。勝率は第 2 段の W1（盤面＋試合前の強さ）をその試合を学習に使わない交差で出した。",
             "値は「出来事の側から見た、直前の分から約 3〜4 分後までの勝率の動き」から、同じ時間帯・同じ勝率の場面の平均の動きを引いたもの（勝率のポイント）。幅は試合単位のブートストラップ 95%。", "",
             "| 出来事 | 1 試合の回数 | 時刻の中央値（分） | 全体 | 互角（40〜60%）の時 | ほかの建物・Roshan と重ならない時 | OpenAI Five の重み |",
             "|---|---|---|---|---|---|---|"]
    for ty in ORDER:
        d = res["types"][ty]
        lines.append(f"| {NAMES[ty]} | {d['per_match']:.2f} | {d['median_min']:.0f} | {f(d['all'])}（{d['all'][3]:,}） | {f(d['even'])}（{d['even'][3]:,}） | "
                     f"{f(d['alone'])}（{d['alone'][3]:,}） | {OPENAI.get(ty, '')} |")
    lines += ["", "結果との関連であって因果ではない（強い側が先に取るから上がる分を、同じ勝率の場面との比べで一部だけ外している）。"]
    open(f"{a.out}/RESULT.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
