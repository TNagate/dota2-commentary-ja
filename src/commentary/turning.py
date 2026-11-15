"""1 試合の秒ごとの勝率と、転換点の上位 5（A: 勝率の動きだけ、B: 出来事つき）。規則は docs/TURNING_PREREG.md に先に固定した。

使い方: python3 -P commentary/turning.py <match_id> [<match_id> ...]
入力: 台帳 data/matches.jsonl、Drive の positions/<league>/<id>.{csv,objects.csv,combat.csv}.gz と matches/…json.gz、data/minutes（学習）
出力: commentary/work/turning_<id>.json（5 秒ごとの勝率、出来事の一覧、A・B の上位 5）
"""
import csv, gzip, io, json, math, os, subprocess, sys, glob
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); os.chdir(ROOT); sys.path.insert(0, ROOT)
import winprob as W
from sklearn.linear_model import LogisticRegression
import pandas as pd
RCLONE = os.path.expanduser("~/.local/bin/rclone"); REMOTE = "drive:dota2_data"
OUT = os.path.join(HERE, "work"); os.makedirs(OUT, exist_ok=True)


def cat(path):
    p = subprocess.run(f"{RCLONE} cat {REMOTE}/{path}", shell=True, capture_output=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"Drive から読めない {path}")
    return gzip.decompress(p.stdout)


_models = {}
def model_for(league):
    """評価する大会を除いた全試合で M2 を学ぶ（大会ごとに 1 回）"""
    if league in _models:
        return _models[league]
    # ★2026-11-12 分ごとの表が 22 万試合に増え、全部を読むと 10 分を超えた。評価する大会を除いた試合から 3 万試合を決まった乱数で選び、
    #   大会ごとに学んだ型を commentary/work/models/ に保存して使い回す（docs/COMMENTARY20_PREREG.md の 20 試合から）。
    import pickle, random as _r
    cache = f"{OUT}/models/{league}_{os.environ.get('TURNING_MODEL', 'M3')}.pkl"
    if os.path.exists(cache):
        c = pickle.load(open(cache, "rb"))
        _models[league] = (_make_pred(c["kind"], c["obj"]), c["kind"], c["ntr"]); return _models[league]
    lgof = {}
    for l in open("data/matches.jsonl", encoding="utf-8"):
        x = json.loads(l); lgof[x["match_id"]] = x.get("leagueid")
    files = sorted(f for f in glob.glob("data/minutes/*.json") if lgof.get(int(os.path.basename(f)[:-5])) != league)
    _r.Random(20261112).shuffle(files); files = files[:30000]
    _g = W.glob.glob
    W.glob.glob = lambda pat: files
    try:
        df, meta = W.load()
    finally:
        W.glob.glob = _g
    tr = df
    kind = os.environ.get("TURNING_MODEL", "M3")
    if kind == "M2":
        sc = W.PerMinuteScaler().fit(tr, W.FEATS)
        m = LogisticRegression(C=1.0, max_iter=1000).fit(sc.transform(tr), tr["y"])
        obj = (sc, m)
    else:  # M3: 5 分刻みで別々に学ぶ（winprob.py と同じ）
        bucket = lambda d: (d["minute"].clip(upper=60) // 5).astype(int)
        mm = {}
        for b, g in tr.groupby(bucket(tr)):
            if g["y"].nunique() < 2 or len(g) < 20:
                continue
            sc = W.BucketScaler().fit(g, W.FEATS)
            mm[b] = (sc, LogisticRegression(C=1.0, max_iter=1000).fit(sc.transform(g), g["y"]))
        obj = mm
    _models[league] = (_make_pred(kind, obj), kind, int(tr.match_id.nunique()))
    os.makedirs(f"{OUT}/models", exist_ok=True)
    pickle.dump({"kind": kind, "obj": obj, "ntr": _models[league][2]}, open(cache, "wb"))
    return _models[league]


def _make_pred(kind, obj):
    if kind == "M2":
        sc, m = obj
        return lambda d: m.predict_proba(sc.transform(d))[:, 1]
    bucket = lambda d: (d["minute"].clip(upper=60) // 5).astype(int)
    def pred(d):
        p = np.full(len(d), 0.5); bk = bucket(d).values
        for b, (sc, mdl) in obj.items():
            i = bk == b
            if i.any():
                p[i] = mdl.predict_proba(sc.transform(d[i]))[:, 1]
        return p
    return pred


def kl(p, q):
    p = min(max(p, 1e-6), 1 - 1e-6); q = min(max(q, 1e-6), 1 - 1e-6)
    return p * math.log(p / q) + (1 - p) * math.log((1 - p) / (1 - q))


def one(mid, led):
    r = led[mid]; lg = r["leagueid"]
    m = json.loads(cat(r["drive"]))
    pos = list(csv.DictReader(io.StringIO(cat(f"positions/{lg}/{mid}.csv.gz").decode())))
    obj = list(csv.DictReader(io.StringIO(cat(f"positions/{lg}/{mid}.objects.csv.gz").decode())))
    com = list(csv.DictReader(io.StringIO(cat(f"positions/{lg}/{mid}.combat.csv.gz").decode())))
    dur = m["duration"]
    # 秒ごとの陣営の値（列 t は OpenDota と同じ整数の時刻）
    per = {}
    for x in pos:
        t = int(x["t"]); s = per.setdefault(t, {"g": [0, 0], "x": [0, 0], "k": [0, 0]})
        side = 0 if x["team"] == "2" else 1
        s["g"][side] += max(0, float(x["gold"])); s["x"][side] += max(0, float(x["xp"])); s["k"][side] += max(0, float(x["kills"]))
    bcols = [c for c in obj[0] if c.startswith("npc_dota_")]
    lost = {}
    for x in obj:
        t = int(x["t"])
        tw = [0, 0]; rx = [0, 0]
        for c in bcols:
            if float(x[c]) == 0:
                side = 0 if "goodguys" in c else 1
                if "tower" in c: tw[side] += 1
                elif "rax" in c: rx[side] += 1
        lost[t] = (tw, rx)
    rosh = [(o["time"], o.get("team")) for o in m.get("objectives") or [] if o.get("type") == "CHAT_MESSAGE_ROSHAN_KILL"]
    ts = sorted(t for t in per if 0 <= t <= dur and t in lost)
    rows = []
    for t in ts:
        s = per[t]; tw, rx = lost[t]
        rows.append({"minute": t // 60, "t": t, "gold_adv": s["g"][0] - s["g"][1], "xp_adv": s["x"][0] - s["x"][1],
                     "kills_diff": s["k"][0] - s["k"][1], "towers_diff": tw[1] - tw[0], "rax_diff": rx[1] - rx[0],
                     "roshan_diff": sum(1 for tt, tm in rosh if tt <= t and tm == 2) - sum(1 for tt, tm in rosh if tt <= t and tm == 3)})
    df = pd.DataFrame(rows)
    g = df.set_index("t")["gold_adv"]
    df["d1"] = df["t"].map(lambda t: g.get(t, 0) - g.get(t - 60, g.iloc[0]))
    df["d3"] = df["t"].map(lambda t: g.get(t, 0) - g.get(t - 180, g.iloc[0]))
    pred, mkind, ntr = model_for(lg)
    df["p"] = pred(df)
    P = dict(zip(df["t"], df["p"]))
    def p_at(t):
        t = int(round(t))
        while t not in P and t > 0: t -= 1
        return float(P.get(t, df["p"].iloc[0]))
    # 出来事
    deaths = sorted((float(x["game_time"]), x["target"], x["tgt_team"]) for x in com
                    if x["type"] == "DEATH" and x["tgt_hero"] == "1" and x["tgt_illusion"] == "0" and x["reincarnate"] != "1")
    fights, cur = [], []
    for d in deaths:
        if cur and d[0] - cur[-1][0] > 15:
            fights.append(cur); cur = []
        cur.append(d)
    if cur: fights.append(cur)
    events = [{"kind": "fight", "start": f[0][0], "end": f[-1][0], "deaths": [(round(t, 1), n.replace("npc_dota_hero_", ""), int(tm)) for t, n, tm in f],
               "buildings": [], "roshan": [], "buybacks": []} for f in fights if len(f) >= 2]
    singles = []
    for x in com:
        t = float(x["game_time"])
        if x["type"] == "DEATH" and x["tgt_building"] == "1" and ("tower" in x["target"] or "rax" in x["target"]):
            singles.append(("buildings", t, x["target"].replace("npc_dota_", "")))
        elif x["type"] == "BUYBACK":
            singles.append(("buybacks", t, x["value"]))
    for tt, tm in rosh:
        singles.append(("roshan", float(tt), {2: "radiant", 3: "dire"}.get(tm, "?")))
    pid_hero = {}
    for x in pos[:200]:
        pid_hero[x["player_id"]] = x["hero"]
    singles = [(k, t, (pid_hero.get(str(w), str(w)) if k == "buybacks" else w)) for k, t, w in singles]
    for kind, t, what in singles:
        host = next((e for e in events if e["kind"] == "fight" and e["start"] - 10 <= t <= e["end"] + 30), None)
        if host: host[kind].append((round(t, 1), what))
        else: events.append({"kind": kind, "start": t, "end": t, "deaths": [], "buildings": [], "roshan": [], "buybacks": [], kind: [(round(t, 1), what)]})
    out = []
    for e in events:
        if e["start"] < 0 or e["end"] > dur - 120:
            continue
        pb, pa = p_at(e["start"] - 10), p_at(e["end"] + 15)
        ga = lambda t: next((float(g[tt]) for tt in range(int(round(t)), -1, -1) if tt in g.index), 0.0)
        e.update(p_before=round(pb, 4), p_after=round(pa, 4), wpa=round(pa - pb, 4), kl=round(kl(pa, pb), 5),
                 gold_before=int(ga(e["start"] - 10)), gold_after=int(ga(e["end"] + 15)))
        bonus = 0.5 * bool(e["roshan"]) + 0.5 * any("rax" in b[1] for b in e["buildings"]) + 0.3 * bool(e["buybacks"]) + 0.3 * (len(e["deaths"]) >= 4)
        e["score_b"] = round(e["kl"] * (1 + bonus), 5); e["bonus"] = bonus
        out.append(e)
    # ★2026-11-12 docs/COMMENTARY20_PREREG.md: S（時刻の効きを除いた振れ）と V（価値の表の重み上位 3）と捕捉率
    MED = {int(k): v for k, v in json.load(open("data/value/gold_swing_bucket_median.json")).items()}
    W_B = {"tower4": 26, "tower3": 14, "tower2": 8, "tower1": 3.5}
    for e in out:
        e["swing_norm"] = round(abs(e["gold_after"] - e["gold_before"]) / max(MED.get(min(int(e["start"]) // 60, 60) // 5, 1.0), 1.0), 3)
        w = 13 * (len(e["deaths"]) >= 2) + 11 * len(e["roshan"])
        for b in e["buildings"]:
            nm = b[1]
            w += 16 if "rax" in nm else next((v for k, v in W_B.items() if k in nm), 0)
        e["value_w"] = w
    topS = sorted(out, key=lambda e: (-e["swing_norm"], -abs(e["wpa"])))[:5]
    topV = [e for e in sorted(out, key=lambda e: (-e["value_w"], -abs(e["wpa"])))[:3] if e["value_w"] > 0]
    topA = sorted(out, key=lambda e: (-e["kl"], -abs(e["wpa"])))[:5]
    # ★2026-11-12 夜 2 節の版（docs/COMMENTARY20_PREREG.md）: 勝負を分けた場面＝A の順に |動き| 1 ポイント以上を最大 3、見せ場＝重ならない残りを価値の重み→S の順に最大 2
    topD = [e for e in sorted(out, key=lambda e: (-e["kl"], -abs(e["wpa"]))) if abs(e["wpa"]) >= 0.01][:3]
    far = lambda e: all(e["end"] < d["start"] - 60 or e["start"] > d["end"] + 60 for d in topD)
    topH = [e for e in sorted(out, key=lambda e: (-e["value_w"], -e["swing_norm"])) if e not in topD and e["value_w"] > 0 and far(e)][:2]
    topB = sorted(out, key=lambda e: (-e["score_b"], -e["kl"]))[:5]
    res = {"match_id": mid, "leagueid": lg, "duration": dur, "radiant_win": m.get("radiant_win"), "trained_matches": ntr, "model": mkind,
           "p_series": [(int(t), round(float(p), 4)) for t, p in zip(df["t"], df["p"]) if t % 5 == 0],
           "events": sorted(out, key=lambda e: e["start"]), "topA": [e["start"] for e in topA], "topB": [e["start"] for e in topB],
           "topS": [e["start"] for e in topS], "topV": [e["start"] for e in topV], "topD": [e["start"] for e in topD], "topH": [e["start"] for e in topH],
           "capture": {"A": sum(e in topA for e in topV), "S": sum(e in topS for e in topV), "n_V": len(topV)}}
    json.dump(res, open(f"{OUT}/turning_{mid}.json", "w"), ensure_ascii=False)
    fmt = lambda e: f"{int(e['end'])//60}:{int(e['end'])%60:02d} {e['kind']} 死{len(e['deaths'])} 勝率 {100*e['p_before']:.0f}→{100*e['p_after']:.0f}% KL {e['kl']:.3f}"
    print(f"{mid}: 型 {mkind}、出来事 {len(out)}、学習 {ntr} 試合、Radiant 勝ち {m.get('radiant_win')}、最終の勝率 {100*df['p'].iloc[-1]:.0f}%")
    print("  A:", " | ".join(fmt(e) for e in topA)); print("  B:", " | ".join(fmt(e) for e in topB))
    return res


def main():
    led = {json.loads(l)["match_id"]: json.loads(l) for l in open("data/matches.jsonl", encoding="utf-8") if l.strip()}
    for a in sys.argv[1:]:
        one(int(a), led)


if __name__ == "__main__":
    main()
