"""第 1 段 試合前の勝率（docs/PREGAME_PREREG.md、sha 433ea750…）。チーム ID の Elo と選手の Elo（5 人の平均）を時刻順に作り、
時代 R の研究の部分の前 80% で設定を選び、後ろ 20% で 1 回だけ測る。

使い方: python3 -P pregame.py --out work/PREGAME-R-<日付>
入力: data/pregame/matches.jsonl（pregame_extract.py）、data/patch.json、out/state.json の league_tiers（格）
2022-04-21（封印 R の始まり）より後の試合は読まない（強さの更新にも使わない）。
"""
import argparse, json, math, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import dota_seal as S

KS = [16, 24, 32, 48, 64]
INITS = [1400, 1500]


def load():
    pid = {x["name"]: x["id"] for x in json.load(open("data/patch.json", encoding="utf-8"))}
    try:
        tiers = json.load(open("out/state.json", encoding="utf-8")).get("league_tiers", {})
    except (OSError, ValueError):
        tiers = {}
    rows = []
    ERA = S.ERA or "R"
    stop = S.SEALS["R"][0] if ERA == "R" else S.SEALS["main"][0]  # 11/11 時代 B（2024-11-10 より後は読まない）
    for line in open("data/pregame/matches.jsonl", encoding="utf-8"):
        m = json.loads(line)
        st = m.get("start_time")
        if st is None or st >= stop or m.get("radiant_win") is None:
            continue
        ps = m.get("p") or []
        m["rad"] = [p[1] for p in ps if p[0] < 128 and p[1]]
        m["dire"] = [p[1] for p in ps if p[0] >= 128 and p[1]]
        m["pid"] = pid.get(m.get("patch"))
        m["tier"] = tiers.get(str(m.get("leagueid")), "")
        m["eval"] = S.era_of(st, m["pid"]) == ERA and not S.in_seal(st, m["pid"])
        rows.append(m)
    rows.sort(key=lambda m: (m["start_time"], m["match_id"]))
    return rows


def run_team(rows, K, init):
    R = {}
    out = np.full(len(rows), np.nan)
    for i, m in enumerate(rows):
        a, b = m.get("radiant_team_id"), m.get("dire_team_id")
        ra = R.get(a, init) if a else init
        rb = R.get(b, init) if b else init
        e = 1 / (1 + 10 ** ((rb - ra) / 400))
        out[i] = (ra - rb) / 400
        d = K * ((1.0 if m["radiant_win"] else 0.0) - e)
        if a:
            R[a] = ra + d
        if b:
            R[b] = rb - d
    return out


def run_player(rows, K, init):
    R = {}
    out = np.full(len(rows), np.nan)
    for i, m in enumerate(rows):
        ra = np.mean([R.get(x, init) for x in m["rad"]]) if m["rad"] else init
        rb = np.mean([R.get(x, init) for x in m["dire"]]) if m["dire"] else init
        e = 1 / (1 + 10 ** ((rb - ra) / 400))
        out[i] = (ra - rb) / 400
        d = K * ((1.0 if m["radiant_win"] else 0.0) - e)
        for x in m["rad"]:
            R[x] = R.get(x, init) + d
        for x in m["dire"]:
            R[x] = R.get(x, init) - d
    return out


def elo_p(diff):
    return 1 / (1 + 10 ** (-diff))


def ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def ece(y, p, bins=10):
    idx = np.minimum((p * bins).astype(int), bins - 1)
    tot = 0.0
    for b in range(bins):
        s = idx == b
        if s.any():
            tot += s.sum() * abs(y[s].mean() - p[s].mean())
    return tot / len(y)


def fit_logit(x, y):
    """p = σ(a + b·x) をニュートン法で（x は Elo の差/400）。"""
    w = np.zeros(2); X = np.c_[np.ones_like(x), x]
    for _ in range(50):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (y - p); H = (X * (p * (1 - p))[:, None]).T @ X
        step = np.linalg.solve(H, g); w += step
        if np.abs(step).max() < 1e-10:
            break
    return w


def fit_slope(x, y):
    """p = σ(b·x)（切片なし＝陣営の有利を持ち込まない。docs/MAIN_B_PREREG.md 変える点 1）。"""
    b = 0.0
    for _ in range(50):
        p = 1 / (1 + np.exp(-b * x))
        g = float(x @ (y - p)); h = float((x * x * p * (1 - p)).sum())
        step = g / h; b += step
        if abs(step) < 1e-10:
            break
    return np.array([0.0, b])


def summary(y, p):
    return {"n": int(len(y)), "logloss": float(ll(y, p).mean()), "brier": float(((p - y) ** 2).mean()),
            "acc": float(((p > 0.5) == (y == 1)).mean()), "ece": float(ece(y, p))}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rows = load()
    y_all = np.array([1.0 if m["radiant_win"] else 0.0 for m in rows])
    ev = np.array([m["eval"] for m in rows])
    ev_idx = np.where(ev)[0]
    cut = ev_idx[int(len(ev_idx) * 0.8)]
    explore = ev & (np.arange(len(rows)) < cut)
    test = ev & (np.arange(len(rows)) >= cut)
    yE, yT = y_all[explore], y_all[test]
    trials = []
    best = {}
    for name, fn in (("P1_team", run_team), ("P2_player", run_player)):
        for K in KS:
            for init in INITS:
                diff = fn(rows, K, init)
                l = float(ll(yE, elo_p(diff[explore])).mean())
                trials.append({"model": name, "K": K, "init": init, "explore_logloss": l})
                if name not in best or l < best[name][0]:
                    best[name] = (l, K, init, diff)
    base = yE.mean()
    preds = {"P0_const": np.full(test.sum(), base),
             "P1_team": elo_p(best["P1_team"][3][test]),
             "P2_player": elo_p(best["P2_player"][3][test])}
    w = (fit_logit if (S.ERA or "R") == "R" else fit_slope)(best["P2_player"][3][explore], yE)
    preds["P3_player_calib"] = 1 / (1 + np.exp(-(w[0] + w[1] * best["P2_player"][3][test])))
    # 線 1 の主役: P2・P3 のうち選ぶ用で良かった方
    lE2 = float(ll(yE, elo_p(best["P2_player"][3][explore])).mean())
    lE3 = float(ll(yE, 1 / (1 + np.exp(-(w[0] + w[1] * best["P2_player"][3][explore])))).mean())
    main_model = "P3_player_calib" if lE3 < lE2 else "P2_player"
    res = {"n_rows_used": len(rows), "n_explore": int(explore.sum()), "n_test": int(test.sum()),
           "explore_range": [rows[np.where(explore)[0][0]]["start_time"], rows[np.where(explore)[0][-1]]["start_time"]],
           "test_range": [rows[np.where(test)[0][0]]["start_time"], rows[np.where(test)[0][-1]]["start_time"]],
           "best": {k: {"K": v[1], "init": v[2], "explore_logloss": v[0]} for k, v in best.items()},
           "P3_coef": {"a": float(w[0]), "b": float(w[1])}, "explore_ll_P2": lE2, "explore_ll_P3": lE3, "main_model": main_model,
           "test": {k: summary(yT, p) for k, p in preds.items()}}
    rng = np.random.default_rng(20261111)
    L = {k: ll(yT, p) for k, p in preds.items()}
    n = len(yT); B = 2000
    def boot(d):
        idx = rng.integers(0, n, size=(B, n))
        s = d[idx].mean(axis=1)
        return [float(d.mean()), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))]
    res["line1_main_minus_P1"] = boot(L[main_model] - L["P1_team"])
    res["line2_main_minus_P0"] = boot(L[main_model] - L["P0_const"])
    res["P2_minus_P1"] = boot(L["P2_player"] - L["P1_team"])
    res["line1_pass"] = res["line1_main_minus_P1"][2] < 0
    res["line2_pass"] = res["line2_main_minus_P0"][2] < 0
    # 必ず書くもの
    tr = [m for m, t in zip(rows, test) if t]
    seen = set(); new_team = []
    for i, m in enumerate(rows):
        if test[i]:
            new_team.append((m.get("radiant_team_id") not in seen) or (m.get("dire_team_id") not in seen))
        seen.add(m.get("radiant_team_id")); seen.add(m.get("dire_team_id"))
    res["test_new_team_share"] = float(np.mean(new_team))
    res["by_tier"] = {}
    for tier in sorted({m["tier"] for m in tr}):
        s = np.array([m["tier"] == tier for m in tr])
        if s.sum() >= 50:
            res["by_tier"][tier or "不明"] = {k: summary(yT[s], p[s]) for k, p in preds.items()}
    res["radiant_rate_by_patch"] = {}
    for m, yv, e in zip(rows, y_all, ev):
        if e:
            d = res["radiant_rate_by_patch"].setdefault(str(m.get("patch")), [0, 0]); d[0] += yv; d[1] += 1
    res["radiant_rate_by_patch"] = {k: [round(v[0] / v[1], 4), v[1]] for k, v in res["radiant_rate_by_patch"].items()}
    json.dump(res, open(f"{a.out}/results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(f"{a.out}/trials.csv", "w") as fh:
        fh.write("model,K,init,explore_logloss\n")
        for t in trials:
            fh.write(f"{t['model']},{t['K']},{t['init']},{t['explore_logloss']:.6f}\n")
    T = res["test"]
    lines = [f"# 第 1 段 試合前の勝率（時代 {S.ERA or 'R'} の研究の部分）— {os.path.basename(a.out)}", "",
             f"事前登録 docs/PREGAME_PREREG.md。選ぶ用 {res['n_explore']:,} 試合、試験用 {res['n_test']:,} 試合（1 回だけ）。試した設定 {len(trials)} 通り。",
             f"選んだ設定: チーム K={best['P1_team'][1]}・初期 {best['P1_team'][2]}、選手 K={best['P2_player'][1]}・初期 {best['P2_player'][2]}。"
             f"線 1 の主役は {main_model}（選ぶ用の log loss P2 {lE2:.4f}・P3 {lE3:.4f}）。", "",
             "| 型 | log loss | Brier | 正解率 | ECE |", "|---|---|---|---|---|"]
    for k, s in T.items():
        lines.append(f"| {k} | {s['logloss']:.4f} | {s['brier']:.4f} | {s['acc']:.1%} | {s['ece']:.3f} |")
    f3 = lambda v: f"{v[0]:+.4f} [{v[1]:+.4f}, {v[2]:+.4f}]"
    lines += ["", f"- 線 1（{main_model} − P1、log loss の差）: {f3(res['line1_main_minus_P1'])} → {'合格' if res['line1_pass'] else '不合格'}",
              f"- 線 2（{main_model} − P0）: {f3(res['line2_main_minus_P0'])} → {'合格' if res['line2_pass'] else '不合格'}",
              f"- 参考 P2 − P1: {f3(res['P2_minus_P1'])}",
              f"- 試験用で少なくとも一方のチーム ID が初めての試合: {res['test_new_team_share']:.1%}",
              f"- P3 の係数: a（Radiant の有利の分）{w[0]:+.3f}、b（差の傾き、Elo どおりなら ln10≈2.303）{w[1]:.3f}",
              "", "格ごと（試験用）:"]
    for tier, d in res["by_tier"].items():
        lines.append(f"- {tier}: " + "、".join(f"{k} {v['logloss']:.4f}/{v['acc']:.1%}（{v['n']}）" for k, v in d.items()))
    lines += ["", "パッチ別の Radiant の勝率（測る試合全体）: " + "、".join(f"{k} {v[0]:.1%}（{v[1]:,}）" for k, v in res["radiant_rate_by_patch"].items()),
              "", "参考（先行研究、自己申告・本研究の封印の期間で測った値）: 定数 0.693・チーム ID の Elo 0.663・選手の Elo の平均 0.641・＋ドラフト 0.639（rubick5）。"]
    open(f"{a.out}/RESULT.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
