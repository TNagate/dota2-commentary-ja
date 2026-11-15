"""Drive に保存した Dota 2 のリプレイから、戦況判断に要る記録を取り出して Drive に置く。照合もする。

使い方: python -P extract_positions.py [--max 200] [--match <id>]
流れ（1 試合ずつ。VPS のディスクには置かず、メモリの /dev/shm を一時に使う）:
  1. rclone cat replays/<league>/<id>.dem.bz2 | zstd -dc | dotapos -events … -combat … -objects … -wards …  → /dev/shm の CSV 5 つ
     （1 秒ごとの 10 人の位置・体力・マナ・レベル・生死・ネットワース・金・経験値・LH・K/D/A・復活秒・買い戻しの冷却／
       tick 単位の生死の変わり目／コンバットログの抜粋とチャットの出来事／1 秒ごとの建物・Roshan・ワード数・陣営の合計／ワード 1 本ごと）
     （Valve のリプレイは名前が .bz2 でも中身は zstd）
  2. gzip して Drive の dota2_data/positions/<league>/<id>.csv.gz、<id>.events.csv.gz、<id>.combat.csv.gz、<id>.objects.csv.gz、
     <id>.wards.csv.gz へ
  3. OpenDota の記録（matches/<YYYY-MM>/<id>.json.gz）と validate.py で照らす（V0〜V11、線は validate.py の頭に固定）
  4. 結果を data/positions.jsonl（1 試合 1 行、"v": VERSION）に書き、Drive の ledger/positions.jsonl に写す
対象: 台帳 data/matches.jsonl でリプレイが保存済みで、data/positions.jsonl にまだ無いか、版（v）が今より古いか、
  失敗のまま（2 回まで）の試合。版 2（2026-11-08 第 1 段）で combat/objects/wards を足し、版 3 で 1 秒ごとの瞬間を OpenDota の規則（列 t）にそろえ、全試合を流し直した。
終わりに out/positions_last_run.json を書く（台帳 schedule_registry の outcomes が読む）。
終了コード: 0=正常、9=取り出しが 3 試合続けて失敗（解析器が Valve の更新に追いついていない疑い）
"""
import argparse
import datetime as dt
import fcntl
import gzip
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
RCLONE = os.path.expanduser("~/.local/bin/rclone")
REMOTE = "drive:dota2_data"
DOTAPOS = os.path.join(HERE, "dotapos")
VALIDATE = os.path.join(HERE, "validate.py")
LEDGER = "data/matches.jsonl"
POS = "data/positions.jsonl"
LAST = "out/positions_last_run.json"
TMP = "/dev/shm/dotapos"
VERSION = 3  # 出力の版。上げると全試合を流し直す（2: 第 1 段の欄、3: 1 秒ごとの瞬間を OpenDota の規則に）
JST = dt.timezone(dt.timedelta(hours=9))
FIX = ("直し方: ssh <server> で ~/dota_trial/logs/positions.log を見る。解析器の失敗が続くなら "
       "cd ~/dota_trial/dotapos && go get github.com/dotabuff/manta@latest && go build（PATH に ~/.local/go/bin）")


def log(msg):
    line = f"{dt.datetime.now(JST):%F %T} {msg}"
    print(line, flush=True)
    with open("logs/positions.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def finish(code, state, **kw):
    json.dump({"date": f"{dt.datetime.now(JST):%F}", "at": f"{dt.datetime.now(JST):%F %T}", "state": state,
               "exit": code, **kw}, open(LAST, "w", encoding="utf-8"), ensure_ascii=False)
    return code


def load(path):
    rows = {}
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            if ln.strip():
                r = json.loads(ln)
                rows[r["match_id"]] = r
    return rows


def save(rows):
    with open(POS + ".tmp", "w", encoding="utf-8") as fh:
        for k in sorted(rows):
            fh.write(json.dumps(rows[k], ensure_ascii=False) + "\n")
    os.replace(POS + ".tmp", POS)
    subprocess.run([RCLONE, "copyto", POS, f"{REMOTE}/ledger/positions.jsonl"], capture_output=True, timeout=300)


def one(r):
    mid, lg = r["match_id"], r.get("leagueid")
    csv_path, js_path, ev_path = f"{TMP}/{mid}.csv", f"{TMP}/{mid}.json", f"{TMP}/{mid}.events.csv"
    extra = {k: f"{TMP}/{mid}.{k}.csv" for k in ("combat", "objects", "wards")}
    cmd = (f"{RCLONE} cat {REMOTE}/{r['replay']} | zstd -dc | {DOTAPOS} -events {ev_path} "
           f"-combat {extra['combat']} -objects {extra['objects']} -wards {extra['wards']} > {csv_path}")
    p = subprocess.run(["bash", "-o", "pipefail", "-c", cmd], capture_output=True, text=True, timeout=1800)
    out = {"match_id": mid, "leagueid": lg, "at": f"{dt.datetime.now(JST):%F %T}", "v": VERSION}
    done = [x for x in p.stderr.splitlines() if x.startswith("DONE")]
    if p.returncode != 0 or not done:
        out.update(state="extract_failed", err=p.stderr[-300:])
        return out
    out["dotapos"] = done[-1]
    dest = f"positions/{lg}/{mid}.csv.gz"
    with open(csv_path, "rb") as fh:
        data = gzip.compress(fh.read(), compresslevel=6)
    w = subprocess.run([RCLONE, "rcat", f"{REMOTE}/{dest}"], input=data, capture_output=True, timeout=600)
    if w.returncode != 0:
        out.update(state="drive_failed", err=w.stderr.decode(errors="replace")[-300:])
        return out
    with open(ev_path, "rb") as fh:
        w2 = subprocess.run([RCLONE, "rcat", f"{REMOTE}/positions/{lg}/{mid}.events.csv.gz"],
                            input=gzip.compress(fh.read()), capture_output=True, timeout=600)
    if w2.returncode != 0:
        out.update(state="drive_failed", err=w2.stderr.decode(errors="replace")[-300:])
        return out
    sizes = {}
    for k, path in extra.items():
        with open(path, "rb") as fh:
            blob = gzip.compress(fh.read(), compresslevel=6)
        w3 = subprocess.run([RCLONE, "rcat", f"{REMOTE}/positions/{lg}/{mid}.{k}.csv.gz"], input=blob, capture_output=True, timeout=600)
        if w3.returncode != 0:
            out.update(state="drive_failed", err=w3.stderr.decode(errors="replace")[-300:])
            return out
        sizes[k] = len(blob)
    out.update(positions=dest, events=f"positions/{lg}/{mid}.events.csv.gz", bytes=len(data), extra_bytes=sizes)
    # 照合（OpenDota の記録が解析済みのときだけ）
    if r.get("drive") and r.get("status") == "saved":
        g = subprocess.run(f"{RCLONE} cat {REMOTE}/{r['drive']} | gunzip > {js_path}", shell=True, capture_output=True,
                           timeout=300)
        if g.returncode == 0:
            v = subprocess.run([sys.executable, "-I", VALIDATE, csv_path, js_path, "--events", ev_path, "--combat", extra["combat"],
                                "--objects", extra["objects"], "--wards", extra["wards"]], capture_output=True, text=True, timeout=600)
            try:
                out["check"] = json.loads(v.stdout.splitlines()[0])
            except Exception:
                out["check"] = {"error": (v.stderr or v.stdout)[-300:]}
    out["state"] = "ok"
    for f in (csv_path, js_path, ev_path, *extra.values()):
        if os.path.exists(f):
            os.remove(f)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=200)
    ap.add_argument("--match", type=int)
    a = ap.parse_args()
    for d in ("data", "out", "logs", "locks", TMP):
        os.makedirs(d, exist_ok=True)
    lock = open("locks/positions.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("別の取り出しが動いている。何もしない")
        return 0
    led = load(LEDGER)
    pos = load(POS)
    def need(r):
        q = pos.get(r["match_id"])
        if q is None:
            return True
        if q.get("state") == "ok":
            return q.get("v", 1) < VERSION
        return q.get("tries", 0) < 2
    todo = [r for r in led.values() if str(r.get("replay", "")).startswith("replays/") and need(r)]
    if a.match:
        todo = [r for r in led.values() if r["match_id"] == a.match]
    todo.sort(key=lambda r: -r["match_id"])
    log(f"位置の取り出し: 対象 {len(todo)} 試合（上限 {a.max}）")
    ok = fail = streak = passed = checked = 0
    for r in todo[:a.max]:
        res = one(r)
        prev = pos.get(r["match_id"], {})
        res["tries"] = (prev.get("tries", 0) + 1) if prev.get("v", 1) == VERSION else 1
        pos[r["match_id"]] = res
        if res["state"] == "ok":
            ok += 1
            streak = 0
            c = res.get("check") or {}
            if "pass" in c:
                checked += 1
                passed += bool(c["pass"])
            log(f"  {r['match_id']}: {res['dotapos']}、照合 {'合格' if c.get('pass') else ('不合格 ' + json.dumps(c, ensure_ascii=False)[:400] if c else 'なし')}")
        else:
            fail += 1
            streak += 1
            log(f"  {r['match_id']}: 失敗 {res['state']} {res.get('err', '')[:200]}")
            if streak >= 3:
                save(pos)
                log(f"失敗: 3 試合続けて取り出せない。{FIX}")
                return finish(9, "extract_failing", ok=ok, fail=fail)
        if (ok + fail) % 10 == 0:
            save(pos)
    save(pos)
    log(f"位置の取り出し: 成功 {ok}、失敗 {fail}、照合 {passed}/{checked} 合格。DONE")
    return finish(0, "ok", ok=ok, fail=fail, checked=checked, passed=passed)


if __name__ == "__main__":
    sys.exit(main())
