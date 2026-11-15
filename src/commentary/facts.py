"""1 試合の「事実の表」（F1, F2, …）を作る。解説を書く役はこの表だけを材料にし、照合もこの表と行う（MLB の facts.py と同じ考え方）。

使い方: python3 -P commentary/facts.py <match_id>   （先に commentary/turning.py <match_id> を走らせる）
入力: OpenDota の記録（Drive matches/…）、commentary/work/turning_<id>.json、data/hero_constants.json（OpenDota /constants/heroes）
出力: commentary/work/facts_<id>.json  {"game": {...}, "facts": [{"id": "F1", "kind": ..., "text": ...}, ...]}
決まり: 判断は機械がする（転換点の選び方は docs/TURNING_PREREG.md の A）。数字は表の形のまま書く。順番・時刻は表に書く（書く役に推し量らせない）。
"""
import gzip, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); os.chdir(ROOT)
RCLONE = os.path.expanduser("~/.local/bin/rclone"); REMOTE = "drive:dota2_data"
LANE = {"top": "上レーン", "mid": "中央レーン", "bot": "下レーン"}


def mmss(t):
    t = int(round(t)); s = "-" if t < 0 else ""; t = abs(t)
    return f"{s}{t // 60}:{t % 60:02d}"


def consts_ja():
    """★11/15 ヒーローとアイテムの名前を Valve 公式の日本語名に（data/ja/names_ja.json）。DOTA_JA=0 で英語のまま。辞書に無いものは英語のまま。"""
    he = json.load(open("data/hero_constants.json")); it = json.load(open("data/items_constants.json"))
    if os.environ.get("DOTA_JA", "1") != "1": return he, it
    ja = json.load(open("data/ja/names_ja.json"))
    hj = {v["internal"]: v["ja"] for v in ja["heroes"].values() if v.get("ja")}
    ij = {v["internal"]: v["ja"] for v in ja["items"].values() if v.get("ja")}
    for v in he.values():
        if v.get("name") in hj: v["localized_name"] = hj[v["name"]]
    for k, v in it.items():
        if ("item_" + k) in ij: v["dname"] = ij["item_" + k]
    return he, it


def abil_ja():
    """★11/15 技の名前も日本語に（abilitylist?language=japanese）。DOTA_JA=0 で英語。"""
    ab = json.load(open("data/abilities_constants.json"))
    if os.environ.get("DOTA_JA", "1") != "1": return ab
    aj = {v["internal"]: v["ja"] for v in json.load(open("data/ja/names_ja.json")).get("abilities", {}).values() if v.get("ja")}
    for k, v in ab.items():
        if k in aj and isinstance(v, dict): v["dname"] = aj[k]
    return ab


def main():
    mid = int(sys.argv[1])
    led = {json.loads(l)["match_id"]: json.loads(l) for l in open("data/matches.jsonl", encoding="utf-8") if l.strip()}
    r = led[mid]
    m = json.loads(gzip.decompress(subprocess.run(f"{RCLONE} cat {REMOTE}/{r['drive']}", shell=True, capture_output=True, timeout=300).stdout))
    tp = json.load(open(f"{HERE}/work/turning_{mid}.json"))
    HE, IT = consts_ja()
    H = {v["id"]: v["localized_name"] for v in HE.values()}
    HN = {v["name"].replace("npc_dota_hero_", ""): v["localized_name"] for v in HE.values()}
    rad, dire = m.get("radiant_name") or r.get("radiant") or "Radiant", m.get("dire_name") or r.get("dire") or "Dire"
    side_name = {2: rad, 3: dire}
    pl = sorted(m["players"], key=lambda p: p["player_slot"])
    who = {p["player_slot"]: (p.get("name") or p.get("personaname") or f"選手{p['player_slot']}") for p in pl}
    hero_of_slot = {p["player_slot"]: H.get(p["hero_id"], str(p["hero_id"])) for p in pl}
    team_of_hero = {}
    for p in pl:
        team_of_hero[H.get(p["hero_id"])] = rad if p["player_slot"] < 128 else dire
    F = []
    def add(kind, text):
        F.append({"id": f"F{len(F) + 1}", "kind": kind, "text": text})
    win = rad if m.get("radiant_win") else dire
    add("meta", f"試合: {m.get('league', {}).get('name') or r.get('league_name')}。{rad}（Radiant）対 {dire}（Dire）。勝ったのは {win}。"
                f"試合時間 {mmss(m['duration'])}。キルの数 {rad} {m.get('radiant_score')}・{dire} {m.get('dire_score')}。")
    for side, lab in ((0, rad), (1, dire)):
        ps = [p for p in pl if (p["player_slot"] < 128) == (side == 0)]
        add("draft", f"{lab} の 5 人と使ったヒーロー: " + "、".join(f"{who[p['player_slot']]}（{hero_of_slot[p['player_slot']]}）" for p in ps) + "。")
    for p in pl:
        lab = rad if p["player_slot"] < 128 else dire
        add("player", f"{who[p['player_slot']]}（{lab}、{hero_of_slot[p['player_slot']]}）の最終の成績: キル {p['kills']}・デス {p['deaths']}・アシスト {p['assists']}、"
                      f"ネットワース {p.get('net_worth')} ゴールド、レベル {p.get('level')}、ラストヒット {p.get('last_hits')}、1 分あたりの獲得ゴールド {p.get('gold_per_min')}。")
    adv = m.get("radiant_gold_adv") or []
    pts = [f"{i} 分 {adv[i]:+d}" for i in (5, 10, 15, 20, 25, 30, 35, 40, 50, 60) if i < len(adv)]
    if adv:
        imax = max(range(len(adv)), key=lambda i: adv[i]); imin = min(range(len(adv)), key=lambda i: adv[i])
        add("lead", f"総獲得ゴールドの差（{rad} − {dire}、プラスは {rad} が上）: " + "、".join(pts) +
                    f"。{rad} の最大のリード {adv[imax]:+d}（{imax} 分）、{dire} の最大のリード {-adv[imin]:+d}（{imin} 分）。")
    ps = dict(tp["p_series"])
    wp = [f"{i} 分 {round(100 * ps[i * 60])}%" for i in (5, 10, 15, 20, 25, 30, 40, 50) if i * 60 in ps]
    win_side_p = [(t, (p if m.get("radiant_win") else 1 - p)) for t, p in tp["p_series"] if t >= 300]
    lo_t, lo_p = min(win_side_p, key=lambda x: x[1]) if win_side_p else (0, 0.5)
    add("winprob", f"このAIの見積もった {rad} の勝つ確率（試合の状況だけから計算）: " + "、".join(wp) +
                   f"。勝った {win} の勝つ確率が最も低かったのは {mmss(lo_t)} の {round(100 * lo_p)}%（5 分以降）。")
    for o in m.get("objectives") or []:
        if o.get("type") == "CHAT_MESSAGE_FIRSTBLOOD":
            add("event", f"ファーストブラッド: {mmss(o['time'])}、{hero_of_slot.get(o.get('player_slot'), '?')}（{who.get(o.get('player_slot'), '?')}）が "
                         f"{hero_of_slot.get(o.get('victim_player_slot'), '?') if o.get('victim_player_slot') is not None else '?'} を倒した。")
    aeg = {o["time"]: hero_of_slot.get(o.get("player_slot"), "?") for o in m.get("objectives") or [] if o.get("type") == "CHAT_MESSAGE_AEGIS"}
    ro = [o for o in m.get("objectives") or [] if o.get("type") == "CHAT_MESSAGE_ROSHAN_KILL"]
    for k, o in enumerate(ro, 1):
        holder = next((h for t, h in aeg.items() if 0 <= t - o["time"] <= 30), None)
        add("event", f"Roshan の撃破 {k} 回目: {mmss(o['time'])}、{side_name.get(o.get('team'), '?')}。" + (f"Aegis を取ったのは {holder}。" if holder else ""))
    def bname(key):
        side = rad if "goodguys" in key else dire
        lane = next((v for k, v in LANE.items() if key.endswith(k)), "")
        if "tower" in key:
            lv = "".join(ch for ch in key.split("tower")[1][:1] if ch.isdigit())
            return f"{side} の{lane}の塔（{lv} 段目）" if lane else f"{side} の本陣前の塔（{lv} 段目）"
        if "rax" in key:
            return f"{side} の{lane}の{'近接' if 'melee' in key else '遠隔'}兵舎"
        if "fort" in key:
            return f"{side} の本陣"
        return key
    bk = [o for o in m.get("objectives") or [] if o.get("type") == "building_kill"]
    lost = {rad: [], dire: []}
    for o in bk:
        k = o.get("key", "")
        lost[rad if "goodguys" in k else dire].append(f"{mmss(o['time'])} {bname(k)}")
    for side in (rad, dire):
        add("buildings", f"{side} が失った建物（時刻の順）: " + ("、".join(lost[side]) if lost[side] else "なし") + "。")
    bb = [(b["time"], hero_of_slot.get(p["player_slot"])) for p in pl for b in (p.get("buyback_log") or [])]
    if bb:
        add("event", "買い戻し（時刻の順）: " + "、".join(f"{mmss(t)} {h}（{team_of_hero.get(h, '?')}）" for t, h in sorted(bb)) + "。")
    # 深い層（commentary/deep.py）: リプレイの位置・戦闘の記録・ワードから数える
    import csv, io
    sys.path.insert(0, HERE); import deep
    lg = r["leagueid"]
    def dcat(path):
        return gzip.decompress(subprocess.run(f"{RCLONE} cat {REMOTE}/{path}", shell=True, capture_output=True, timeout=600).stdout).decode()
    DEEP = os.environ.get("DOTA_DEEP", "1") == "1"
    if DEEP:
        consts = (HE, IT, abil_ja())
        ctx = deep.Ctx(m, list(csv.DictReader(io.StringIO(dcat(f"positions/{lg}/{mid}.csv.gz")))),
                       list(csv.DictReader(io.StringIO(dcat(f"positions/{lg}/{mid}.combat.csv.gz")))),
                       list(csv.DictReader(io.StringIO(dcat(f"positions/{lg}/{mid}.wards.csv.gz")))), consts)
        for kind, text in deep.game_lines(ctx, side_name):
            add(kind, text)
    ev = {round(e["start"], 1): e for e in tp["events"]}
    PICK = os.environ.get("TURNING_PICK", "two")  # ★11/12 夜 20 試合で線を満たしたので既定を 2 節に  # ★11/12 topS＝振れの版、two＝2 節の版（勝負を分けた場面＋見せ場）
    if PICK == "two":
        top = sorted([ev[round(s, 1)] for s in tp["topD"]], key=lambda e: e["start"]); hl = sorted([ev[round(s, 1)] for s in tp["topH"]], key=lambda e: e["start"])
    else:
        top = sorted([ev[round(s, 1)] for s in tp[PICK]], key=lambda e: e["start"]); hl = []
    if PICK == "two":
        add("turning_rule", f"場面の選び方: 試合中の出来事（2 人以上が倒れた戦い・建物・Roshan・買い戻し）のうち、「転換点」はこのAIの見積もった勝つ確率が大きく動いた順に {len(top)} つ。「見せ場」は転換点と時刻が重ならない残りの出来事から、兵舎・塔・Roshan・集団戦を含む規模の大きい順に {len(hl)} つ（勝つ確率がほとんど動かなかったものもある）。以下、それぞれ時刻の順。")
    else:
        add("turning_rule", f"転換点の選び方: 試合中の出来事（2 人以上が倒れた戦い・建物・Roshan・買い戻し）のうち、" + ("総獲得ゴールドの差の動きが（時間帯の普通の動きに比べて）大きい順" if os.environ.get("TURNING_PICK") == "topS" else "このAIの見積もった勝つ確率が大きく動いた順") + f" に {len(top)} つ。以下、時刻の順。")
    for (label, k, e) in [("転換点", k, e) for k, e in enumerate(top, 1)] + [("見せ場", k, e) for k, e in enumerate(hl, 1)]:
        dr = [HN.get(n, n) for t, n, tm in e["deaths"] if tm == 2]; dd = [HN.get(n, n) for t, n, tm in e["deaths"] if tm == 3]
        parts = [f"{label} {k}（{mmss(e['start'])}〜{mmss(e['end'])}）"]
        if e["deaths"]:
            parts.append(f"倒れたヒーロー {rad} 側 {len(dr)} 人（{'、'.join(dr) or 'なし'}）・{dire} 側 {len(dd)} 人（{'、'.join(dd) or 'なし'}）")
        if e["buildings"]:
            parts.append("壊れた建物 " + "、".join(f"{mmss(t)} {bname('npc_dota_' + b)}" for t, b in e["buildings"]))
        if e["roshan"]:
            parts.append("Roshan の撃破 " + "、".join(f"{mmss(t)} {rad if w == 'radiant' else dire}" for t, w in e["roshan"]))
        if e["buybacks"]:
            parts.append("買い戻し " + "、".join(f"{mmss(t)} {w}" for t, w in e["buybacks"]))
        parts.append(f"この前後で {rad} の勝つ確率 {round(100 * e['p_before'])}% → {round(100 * e['p_after'])}%、"
                     f"総獲得ゴールドの差（{rad} − {dire}）{e['gold_before']:+d} → {e['gold_after']:+d}")
        add("turning_situation", "。".join(parts) + "。")
        if DEEP:
            dl = deep.fight_lines(ctx, e, side_name)
            if dl:
                add("turning_detail", f"{label} {k} の詳しい事実: " + "。".join(dl) + "。")
    game = {"match_id": mid, "score": f"{rad} {m.get('radiant_score')}–{m.get('dire_score')} {dire}", "radiant": rad, "dire": dire, "winner": win,
            "names": sorted({*who.values(), *H.values(), rad, dire, *(ctx.names_used if DEEP else set())})}
    json.dump({"game": game, "facts": F}, open(f"{HERE}/work/facts_{mid}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{mid}: 事実の表 {len(F)} 行 → commentary/work/facts_{mid}.json")
    for x in F:
        print(" ", x["id"], x["text"][:200])


if __name__ == "__main__":
    main()
