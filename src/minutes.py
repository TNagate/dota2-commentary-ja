"""OpenDota の試合の記録（/matches/<id> の JSON）から、勝率の線に使う「分ごとの表」を作る共通部品。

使い方（部品）: minutes.rows(m) → dict。minutes.save(m) → data/minutes/<match_id>.json に書く（collect / backfill / build_minutes が呼ぶ）。
分ごとの値（i 分 = i*60 秒の時点、OpenDota の radiant_gold_adv と同じ刻み）:
  gold_adv, xp_adv: Radiant − Dire（OpenDota の radiant_gold_adv / radiant_xp_adv）
  kills_r, kills_d: その時点までのキル数（players[].kills_log の time ≤ i*60 を陣営ごとに数える）
  towers_r_lost, towers_d_lost, rax_r_lost, rax_d_lost: その時点までに失った塔・兵舎の数（objectives の building_kill。key に goodguys＝Radiant の建物）
  roshan_r, roshan_d: その時点までの Roshan 撃破数（objectives の CHAT_MESSAGE_ROSHAN_KILL、team 2＝Radiant・3＝Dire）
中身の印: radiant_win, duration, start_time, patch, leagueid, game_mode, lobby_type, human_players, version（解析済みか）
"""
import json
import os

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "minutes")


def rows(m):
    ga, xa = m.get("radiant_gold_adv") or [], m.get("radiant_xp_adv") or []
    n = min(len(ga), len(xa))
    if n == 0:
        return None
    kills = {2: [], 3: []}
    for p in m.get("players") or []:
        side = 2 if (p.get("player_slot") or 0) < 128 else 3
        for k in p.get("kills_log") or []:
            kills[side].append(k["time"])
    tw = {2: [], 3: []}
    rx = {2: [], 3: []}
    ro = {2: [], 3: []}
    for o in m.get("objectives") or []:
        if o.get("type") == "building_kill":
            key = o.get("key") or ""
            side = 2 if "goodguys" in key else 3  # 失った側
            if "tower" in key:
                tw[side].append(o["time"])
            elif "rax" in key:
                rx[side].append(o["time"])
        elif o.get("type") == "CHAT_MESSAGE_ROSHAN_KILL":
            t = o.get("team")
            if t in (2, 3):
                ro[t].append(o["time"])
    def cum(lst, t):
        return sum(1 for x in lst if x <= t)
    out = {"match_id": m["match_id"], "radiant_win": m.get("radiant_win"), "duration": m.get("duration"),
           "start_time": m.get("start_time"), "patch": m.get("patch"), "leagueid": m.get("leagueid"),
           "game_mode": m.get("game_mode"), "lobby_type": m.get("lobby_type"), "human_players": m.get("human_players"),
           "version": m.get("version"), "radiant_team_id": m.get("radiant_team_id"), "dire_team_id": m.get("dire_team_id"),
           "minutes": []}
    for i in range(n):
        t = i * 60
        out["minutes"].append([i, ga[i], xa[i], cum(kills[2], t), cum(kills[3], t), cum(tw[2], t), cum(tw[3], t),
                               cum(rx[2], t), cum(rx[3], t), cum(ro[2], t), cum(ro[3], t)])
    out["columns"] = ["minute", "gold_adv", "xp_adv", "kills_r", "kills_d", "towers_r_lost", "towers_d_lost",
                      "rax_r_lost", "rax_d_lost", "roshan_r", "roshan_d"]
    return out


def save(m):
    r = rows(m)
    if r is None:
        return False
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, f"{m['match_id']}.json")
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(r, fh, separators=(",", ":"))
    os.replace(p + ".tmp", p)
    return True
