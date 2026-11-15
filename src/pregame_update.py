"""data/pregame/matches.jsonl に、まだ無い新しいプロ試合（10 人の account_id・ヒーロー）を足す（前向きの記録用、2026-11-12）。
pregame_extract.py と同じ問い合わせを「今ある一番大きい match_id より上」に限って /explorer に 1〜2 回投げる。
使い方: python3 -P pregame_update.py  （dota2-forward.service の最初の段）
"""
import json, os, sys, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import collect as C

F = "data/pregame/matches.jsonl"
have = set(); top = 0
for line in open(F, encoding="utf-8"):
    mid = json.loads(line)["match_id"]; have.add(mid); top = max(top, mid)
api = C.Api(4)
lo, add = top, []
while True:
    q = ("select m.match_id, m.start_time, m.radiant_win, m.leagueid, m.radiant_team_id, m.dire_team_id, m.series_id, m.series_type, mp.patch, "
         "(select json_agg(json_build_array(pm.player_slot, pm.account_id, pm.hero_id) order by pm.player_slot) from player_matches pm where pm.match_id = m.match_id) as p "
         f"from matches m left join match_patch mp on mp.match_id = m.match_id where m.leagueid > 0 and m.match_id > {lo} order by m.match_id asc limit 5000")
    r = api.get("/explorer?sql=" + urllib.parse.quote(q), tries=3, timeout=120)
    if r is None or r.get("err"):
        raise SystemExit("誤り: " + str(r and r.get("err")))
    rows = [x for x in (r.get("rows") or []) if x["match_id"] not in have]
    add += rows
    if len(r.get("rows") or []) < 5000:
        break
    lo = r["rows"][-1]["match_id"]
# 勝敗がまだ無い（進行中・未解析）の試合は足さない。次の回にまた取る。
add = [x for x in add if x.get("radiant_win") is not None and x.get("p")]
with open(F, "a", encoding="utf-8") as f:
    for x in add:
        f.write(json.dumps(x, separators=(",", ":")) + "\n")
print(f"足した試合 {len(add)}（呼び出し {api.calls}、1 日の残り {api.remaining_day}）")
