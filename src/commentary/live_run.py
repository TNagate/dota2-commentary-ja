"""進行中の大会での通し（三者会議 20261112 の B②、2026-11-12）: リプレイから位置を取り出せた新しい試合を、解説まで通しで回し、遅れ・抜け・誤りを記録する。
対象: work/LIVE/config.json の leagues（既定 DreamLeague Division 2 Series 5・BLAST SLAM IX）で、2026-11-12 以降に始まった試合。
1 回に max_per_run 試合まで（使用量）。流れは commentary/run_game.sh と同じ（書く sonnet・点検 opus・書き直し 1 回）。
記録（work/LIVE/ledger.jsonl、1 試合 1 行）: 試合の終わり→位置の取り出し→解説の出来上がりの時刻と遅れ、照合と点検の問題の数（書き直しの前と後）、失敗。
抜け: 対象の大会で台帳にあるのに位置が無い試合の数も毎回数える（リプレイが取れない試合）。
使い方: python3 -P commentary/live_run.py   （dota2-live-commentary.timer、06:30・18:45）"""
import fcntl, json, os, shutil, subprocess, sys, time, datetime
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
O = "work/LIVE"; os.makedirs(O, exist_ok=True)
lock = open(f"{O}/lock", "a+")
try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: sys.exit(0)
# ★11/12 夜 ほかの一括の実行と commentary/work を取り合って材料が混ざった（9085858818）→ 共有の鍵を待って取る
wlock = open("commentary/work/run.lock", "a+"); fcntl.flock(wlock, fcntl.LOCK_EX)
CF = f"{O}/config.json"
if not os.path.exists(CF):
    json.dump({"leagues": [20511, 20208], "since": "2026-11-12", "max_per_run": 3}, open(CF, "w"), indent=1)
cf = json.load(open(CF))
since = datetime.datetime.fromisoformat(cf["since"]).replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9))).timestamp()
P = os.path.expanduser("python3")
led = {}
for l in open("data/matches.jsonl"):
    x = json.loads(l)
    if x.get("leagueid") in cf["leagues"] and (x.get("start_time") or 0) >= since: led[x["match_id"]] = x
pos = {}
for l in open("data/positions.jsonl"):
    p = json.loads(l)
    if p["match_id"] in led: pos[p["match_id"]] = p
done = set()
LG = f"{O}/ledger.jsonl"
if os.path.exists(LG):
    for l in open(LG): done.add(json.loads(l)["match_id"])
ready = sorted((m for m, p in pos.items() if p.get("state") == "ok" and (p.get("check") or {}).get("pass") and m not in done), key=lambda m: led[m]["start_time"])
missing = [m for m in led if m not in pos or pos[m].get("state") != "ok"]
env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
W = "commentary/work"
def ts(s):
    try: return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9))).timestamp()
    except Exception: return None
n_ok = n_fail = 0
for mid in ready[: cf["max_per_run"]]:
    r = led[mid]; end = r["start_time"] + (r.get("duration") or 0); t_pos = ts(pos[mid].get("at", ""))
    log = open(f"{O}/log_{mid}.txt", "w"); t0 = time.time(); steps = []
    def step(args):
        x = subprocess.run([P, "-P"] + args, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=3600); steps.append((args[0], x.returncode)); return x.returncode == 0
    ok = (step(["commentary/turning.py", str(mid)]) and step(["commentary/facts.py", str(mid)]) and step(["commentary/write.py", str(mid), "--model", "sonnet"])
          and step(["commentary/check.py", str(mid)]))
    cb = json.load(open(f"{W}/check_{mid}.json")) if ok else None
    ok = ok and step(["commentary/verify.py", str(mid), "--model", "opus"]); vb = json.load(open(f"{W}/verify_{mid}.json")) if ok else None
    ok = ok and step(["commentary/rewrite.py", str(mid), "--model", "sonnet"]) and step(["commentary/check.py", str(mid)]) and step(["commentary/verify.py", str(mid), "--model", "opus"])
    row = {"match_id": mid, "league": r.get("league_name"), "ok": ok, "steps": steps, "match_end": end, "positions_at": t_pos, "commentary_at": time.time(),
           "delay_min_end_to_positions": round((t_pos - end) / 60, 1) if t_pos else None, "delay_min_end_to_commentary": round((time.time() - end) / 60, 1),
           "pipeline_min": round((time.time() - t0) / 60, 1)}
    if ok:
        ca = json.load(open(f"{W}/check_{mid}.json")); va = json.load(open(f"{W}/verify_{mid}.json"))
        row.update(sentences=ca.get("sentences_checked"), problems_before=len(cb["problems"]) + sum(v["verdict"] != "支持" for v in vb),
                   problems_after=len(ca["problems"]) + sum(v["verdict"] != "支持" for v in va),
                   capture=json.load(open(f"{W}/turning_{mid}.json")).get("capture"))
        for f in (f"commentary_{mid}.md", f"facts_{mid}.json", f"turning_{mid}.json", f"check_{mid}.json", f"verify_{mid}.json"):
            shutil.copy(f"{W}/{f}", O)
        # ★11/15 解説ページの試作（work/PAGES/<id>.html、公開しない）。失敗しても解説の成否には数えない
        pg = subprocess.run([P, "-P", "commentary/page.py", str(mid), O], stdout=log, stderr=subprocess.STDOUT, env=env, timeout=300)
        row["page"] = pg.returncode == 0
        n_ok += 1
    else:
        n_fail += 1
    with open(LG, "a") as f: f.write(json.dumps(row, ensure_ascii=False) + "\n")
st = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "matches_in_leagues_since": len(led), "positions_missing": len(missing), "ready_left": max(0, len(ready) - cf["max_per_run"]),
      "ran_ok": n_ok, "ran_failed": n_fail}
with open(f"{O}/runs.jsonl", "a") as f: f.write(json.dumps(st, ensure_ascii=False) + "\n")
print(json.dumps(st, ensure_ascii=False))
if n_fail and not n_ok: sys.exit(1)  # 全部失敗したときだけ失敗にして知らせる（S2）
