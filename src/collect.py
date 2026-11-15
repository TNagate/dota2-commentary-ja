"""Dota 2 のプロの試合の記録を OpenDota から集め、Google Drive に直接しまう（VPS のディスクに置かない）。

目的: 大会の全試合の詳細な記録（1 分ごとの各選手のゴールド・経験値、集団戦、ワード、オブジェクトの時刻など）を
      貯めておく（本人の依頼 2026-11-08。解説の仕組みは後で作る。今は集めるだけ）。

使い方:
  python collect.py            定期実行（1 日 2 回）。新しいプロの試合を見つけ、解析済みの記録を Drive に送る
  python collect.py --dry-run  API を読むだけで Drive に書かない
  python collect.py --max-calls 300   この回で使う API の呼び出しの上限（既定 1200。無料の枠は 1 日 3,000・毎分 60）

Drive の置き場（rclone の drive）:
  dota2_data/matches/<YYYY-MM>/<match_id>.json.gz   試合の記録（OpenDota の /matches/<id> をそのまま圧縮）
  dota2_data/promatches/<YYYY-MM-DD>_<HHMM>.json.gz   その回に見たプロの試合の一覧
  dota2_data/leagues/leagues_<YYYY-MM-DD>.json.gz     リーグの一覧（tier が premium / professional / …）
  dota2_data/replays/<leagueid>/<match_id>.dem.bz2    生のリプレイ（既定は premium、定期実行は premium と professional。Valve の置き場は 1〜2 週間で消えるため。全選手の毎秒の位置はここにしか無い）
  dota2_data/ledger/matches.jsonl                      台帳の写し（毎回上書き）
手元に残すのは台帳（data/matches.jsonl、1 試合 1 行）と状態（out/）とログだけ。

流れ:
  1. /health を見る。OpenDota が落ちていれば「待ち」で終わる（3 回続いたら失敗にして知らせる）。
  2. /proMatches（新しい順 100 件、less_than_match_id で遡る）を、台帳にある試合に当たるまで読む。
  3. 台帳に無い試合・解析待ちの試合について /matches/<id> を読む。version が入っていれば解析済み → Drive へ。
     解析前なら POST /request/<id>（10 呼び出し分）で解析を頼み、次の回に見直す。14 日たっても解析されなければ
     解析前の記録のまま Drive に送って「unparsed」と記す。
  4. premium のリーグの試合は replay_url から生のリプレイを curl で落としながら rclone rcat で Drive へ流す（ディスクに置かない）。
  5. どの終わり方でも out/last_run.json に日付と状態を書く（台帳 schedule_registry の outcomes が読む）。

終了コード: 0=正常（待ちを含む）、2=rclone が見つからない、7=OpenDota が 3 回続けて落ちている、8=Drive への書き込みが失敗
"""
import argparse
import datetime as dt
import fcntl
import gzip
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
API = "https://api.opendota.com/api"
RCLONE = os.path.expanduser("~/.local/bin/rclone")
REMOTE = "drive:dota2_data"
LEDGER = "data/matches.jsonl"
STATE = "out/state.json"
LAST_RUN = "out/last_run.json"
JST = dt.timezone(dt.timedelta(hours=9))
UA = "dota2-archive-collector/0.1 (personal research; server)"
REPLAY_TIERS = set(os.environ.get("DOTA_REPLAY_TIERS", "premium").split(","))  # 生のリプレイを残すリーグの tier
REPLAY_MAX_PER_RUN = int(os.environ.get("DOTA_REPLAY_MAX", "25"))
REQUEST_MAX_PER_RUN = int(os.environ.get("DOTA_REQUEST_MAX", "40"))  # 解析の依頼は 1 回 10 呼び出し分なので回数を抑える
PARSE_WAIT_DAYS = 14
REPLAY_KEEP_DAYS = 12  # Valve のリプレイが消える前（1〜2 週間）に取りに行く範囲
API_DOWN_FAIL_AFTER = 3
FIX = ("直し方: ssh <server> で ~/dota_trial/logs/collect.log を見る。OpenDota が落ちているだけなら待つ"
       "（https://api.opendota.com/api/health）。Drive なら ~/.local/bin/rclone about drive: で繋がるかを見て、"
       "cd ~/dota_trial && .venv/bin/python collect.py を手で走らせる")


def log(msg):
    line = f"{dt.datetime.now(JST):%F %T} {msg}"
    print(line, flush=True)
    with open("logs/collect.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def finish(code, state, **extra):
    rec = {"date": dt.datetime.now(JST).date().isoformat(), "at": f"{dt.datetime.now(JST):%F %T}",
           "state": state, "exit": code, **extra}
    with open(LAST_RUN + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False)
    os.replace(LAST_RUN + ".tmp", LAST_RUN)
    return code


class Api:
    """OpenDota の呼び出し。毎分 60 回の枠に収めるため 1.1 秒あける。1 回の実行の上限を数える。"""

    def __init__(self, max_calls):
        self.max_calls = max_calls
        self.calls = 0
        self.last = 0.0
        # OpenDota が返事ごとに返す 1 日の残り（x-rate-limit-remaining-day）。2026-11-10 に測った: この枠は
        # UTC の 0 時に戻る形ではなく直近の時間で数えていて、こちらの数え方とずれて 429 で落ちた。残りは返事の値を正とする。
        self.remaining_day = None
        self.http_requests = 0  # 試し直しを含めた実際の通信の回数（枠はこちらで減る）

    def _note(self, headers):
        v = headers.get("x-rate-limit-remaining-day") if headers is not None else None
        try:
            self.remaining_day = int(v)
        except (TypeError, ValueError):
            pass

    def _wait(self):
        gap = time.time() - self.last
        if gap < 1.1:
            time.sleep(1.1 - gap)
        self.last = time.time()

    def get(self, path, cost=1, tries=3, timeout=90):
        if self.calls + cost > self.max_calls:
            raise RuntimeError("calls_exhausted")
        self.calls += cost
        err = None
        for i in range(tries):
            self._wait()
            self.http_requests += 1
            try:
                req = urllib.request.Request(API + path, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    self._note(r.headers)
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                err = f"HTTP {e.code}"
                self._note(e.headers)
                if e.code == 404:
                    return None
                if e.code == 429:
                    if self.remaining_day is not None and self.remaining_day <= 0:
                        raise RuntimeError(f"quota_exhausted {path}: HTTP 429（1 日の残り {self.remaining_day}）")
                    time.sleep(30 * (i + 1))
            except Exception as e:  # 通信の失敗は待って試し直す
                err = str(e)[:120]
            time.sleep(5 * (i + 1))
        raise RuntimeError(f"api_error {path}: {err}")

    def post(self, path, cost=10):
        if self.calls + cost > self.max_calls:
            raise RuntimeError("calls_exhausted")
        self.calls += cost
        self._wait()
        self.http_requests += 1
        req = urllib.request.Request(API + path, headers={"User-Agent": UA}, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=90) as r:
            self._note(r.headers)
            return json.loads(r.read().decode("utf-8") or "{}")


def rclone_put(data_bytes, dest, dry):
    """バイト列を Drive に直接書く（ディスクに置かない）。"""
    if dry:
        return True
    p = subprocess.run([RCLONE, "rcat", f"{REMOTE}/{dest}", "--retries", "5", "--low-level-retries", "20"],
                       input=data_bytes, capture_output=True, timeout=600)
    if p.returncode != 0:
        log(f"Drive 書き込み失敗 {dest}: {p.stderr.decode(errors='replace')[-300:]}")
        return False
    return True


def rclone_stream_url(url, dest):
    """URL の中身を curl で落としながら rclone rcat で Drive へ流す。戻りは (成功か, バイト数)。"""
    curl = subprocess.Popen(["curl", "-fsSL", "--max-time", "1800", "--retry", "3", url], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    rc = subprocess.Popen([RCLONE, "rcat", f"{REMOTE}/{dest}", "--retries", "3"], stdin=curl.stdout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    curl.stdout.close()
    rc_out, rc_err = rc.communicate(timeout=2400)
    curl.wait(timeout=60)
    if curl.returncode != 0 or rc.returncode != 0:
        log(f"リプレイの転送失敗 {dest}: curl={curl.returncode} rclone={rc.returncode} "
            f"{(curl.stderr.read() + rc_err).decode(errors='replace')[-300:]}")
        return False, 0
    # 大きさは Drive 側に聞く（rcat は返さない）
    p = subprocess.run([RCLONE, "size", f"{REMOTE}/{dest}", "--json"], capture_output=True, text=True, timeout=120)
    try:
        return True, json.loads(p.stdout)["bytes"]
    except Exception:
        return True, -1


def gz(obj):
    return gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), compresslevel=6)


def load_ledger():
    rows = {}
    if os.path.exists(LEDGER):
        for ln in open(LEDGER, encoding="utf-8"):
            if ln.strip():
                r = json.loads(ln)
                rows[r["match_id"]] = r
    return rows


def save_ledger(rows, dry):
    with open(LEDGER + ".tmp", "w", encoding="utf-8") as fh:
        for mid in sorted(rows):
            fh.write(json.dumps(rows[mid], ensure_ascii=False) + "\n")
    os.replace(LEDGER + ".tmp", LEDGER)
    rclone_put(open(LEDGER, "rb").read(), "ledger/matches.jsonl", dry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-calls", type=int, default=1200)
    ap.add_argument("--max-new", type=int, default=400, help="この回に新しく見る試合の上限")
    a = ap.parse_args()
    for d in ("data", "out", "logs", "locks"):
        os.makedirs(d, exist_ok=True)
    lock = open("locks/collect.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("別の収集が動いている。何もしない")
        return 0
    if not a.dry_run and not os.path.exists(RCLONE):
        log(f"失敗: rclone が無い（{RCLONE}）。{FIX}")
        return finish(2, "no_rclone")
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    api = Api(a.max_calls)
    now = dt.datetime.now(JST)

    # ---- 1. OpenDota が生きているか ----
    try:
        api.get("/health", tries=2, timeout=60)
        st["api_down_streak"] = 0
        st["quota_wait_streak"] = 0
    except Exception as e:
        if str(e).startswith("quota_exhausted"):
            # 1 日の枠が尽きている（OpenDota は動いている）。落ちた扱いにせず待つ。続いていることは
            # quota_check.py（毎朝 09:20）が知らせる。2026-11-10〜 OpenDota の数え表が 9 時に戻らない件で足した
            st["quota_wait_streak"] = st.get("quota_wait_streak", 0) + 1
            json.dump(st, open(STATE, "w", encoding="utf-8"))
            log(f"OpenDota の 1 日の残りが尽きている（{e}）。{st['quota_wait_streak']} 回目。次の回に取り直す")
            return finish(0, "quota_wait", streak=st["quota_wait_streak"], rem=api.remaining_day)
        st["api_down_streak"] = st.get("api_down_streak", 0) + 1
        json.dump(st, open(STATE, "w", encoding="utf-8"))
        if st["api_down_streak"] >= API_DOWN_FAIL_AFTER:
            log(f"失敗: OpenDota が {st['api_down_streak']} 回続けて応答しない（{e}）。{FIX}")
            return finish(7, "api_down_long", streak=st["api_down_streak"])
        log(f"OpenDota が応答しない（{e}）。{st['api_down_streak']} 回目。次の回に取り直す")
        return finish(0, "api_down", streak=st["api_down_streak"])

    ledger = load_ledger()
    known_max = max(ledger) if ledger else 0

    # ---- 2. リーグの一覧（日に 1 回）----
    tiers = st.get("league_tiers", {})
    if st.get("leagues_date") != now.date().isoformat():
        leagues = api.get("/leagues") or []
        tiers = {str(l["leagueid"]): (l.get("tier") or "") for l in leagues if isinstance(l, dict)}
        rclone_put(gz(leagues), f"leagues/leagues_{now:%F}.json.gz", a.dry_run)
        st["leagues_date"], st["league_tiers"] = now.date().isoformat(), tiers
        log(f"リーグ {len(leagues)} 件（premium {sum(1 for t in tiers.values() if t == 'premium')}）")

    # ---- 3. 新しいプロの試合の一覧 ----
    new = []
    less_than = None
    pages = 0
    while pages < 20:
        page = api.get("/proMatches" + (f"?less_than_match_id={less_than}" if less_than else "")) or []
        pages += 1
        if not page:
            break
        hit_known = False
        for m in page:
            if m["match_id"] in ledger:
                hit_known = True
                continue
            new.append(m)
        less_than = page[-1]["match_id"]
        if hit_known or (known_max and less_than < known_max) or len(new) >= a.max_new:
            break
        if not ledger and pages >= 3:  # 初回は直近 300 件までで止める（遡りは別の回で）
            break
    if new:
        rclone_put(gz(new), f"promatches/{now:%F_%H%M}.json.gz", a.dry_run)
    for m in new:
        ledger[m["match_id"]] = {"match_id": m["match_id"], "leagueid": m.get("leagueid"), "league_name": m.get("league_name"),
                                 "start_time": m.get("start_time"), "duration": m.get("duration"),
                                 "radiant": m.get("radiant_name"), "dire": m.get("dire_name"), "radiant_win": m.get("radiant_win"),
                                 "tier": tiers.get(str(m.get("leagueid")), ""), "status": "new", "seen_at": f"{now:%F %T}"}
    log(f"プロの試合: 新しく {len(new)} 件（台帳 {len(ledger)} 件、API {api.calls} 回）")

    # ---- 4. 試合の記録を取って Drive へ ----
    todo = [r for r in ledger.values() if r["status"] in ("new", "parse_requested")]
    todo.sort(key=lambda r: -r["match_id"])
    saved = requested = unparsed_saved = replays = errors = 0
    replay_bytes = 0
    try:
        for r in todo:
            mid = r["match_id"]
            try:
                m = api.get(f"/matches/{mid}")
            except RuntimeError as e:
                if str(e).startswith("calls_exhausted"):
                    raise
                errors += 1
                log(f"読めなかった {mid}: {str(e)[:160]}")
                if errors >= 5:
                    raise RuntimeError("api_errors_5")
                continue
            if not m or "match_id" not in m:
                r["status"], r["note"] = "missing", "OpenDota に無い"
                continue
            parsed = m.get("version") is not None
            age_days = (now.timestamp() - (r.get("start_time") or now.timestamp())) / 86400
            if not parsed and age_days < PARSE_WAIT_DAYS:
                if r["status"] == "new" and requested < REQUEST_MAX_PER_RUN:
                    try:
                        api.post(f"/request/{mid}")
                        r["status"], r["parse_requested_at"] = "parse_requested", f"{now:%F %T}"
                        requested += 1
                    except RuntimeError:
                        raise
                    except Exception as e:
                        log(f"解析の依頼に失敗 {mid}: {str(e)[:120]}")
                continue
            ym = dt.datetime.fromtimestamp(m.get("start_time") or now.timestamp(), JST).strftime("%Y-%m")
            dest = f"matches/{ym}/{mid}.json.gz"
            if not rclone_put(gz(m), dest, a.dry_run):
                return finish(8, "drive_write_failed", match_id=mid)
            r["status"] = "saved" if parsed else "unparsed"
            r["drive"] = dest
            r["saved_at"] = f"{now:%F %T}"
            r["replay_url"] = m.get("replay_url")
            saved += parsed
            unparsed_saved += (not parsed)
            # 生のリプレイ（premium のリーグだけ、1 回の上限つき）
            if r.get("tier") in REPLAY_TIERS and m.get("replay_url") and replays < REPLAY_MAX_PER_RUN and not a.dry_run:
                ok, nbytes = rclone_stream_url(m["replay_url"], f"replays/{r.get('leagueid')}/{mid}.dem.bz2")
                r["replay_tries"] = r.get("replay_tries", 0) + 1
                if ok:
                    r["replay"], r["replay_bytes"] = f"replays/{r.get('leagueid')}/{mid}.dem.bz2", nbytes
                    replays += 1
                    replay_bytes += max(nbytes, 0)
                else:
                    r["replay"] = "failed"
        # ---- 5. 保存済みでリプレイがまだの試合（tier を広げた後の取りこぼし・失敗は 2 回まで）を、消える前に取る ----
        for r in sorted(ledger.values(), key=lambda r: -r["match_id"]):
            if replays >= REPLAY_MAX_PER_RUN or a.dry_run:
                break
            if r["status"] not in ("saved", "unparsed") or r.get("tier") not in REPLAY_TIERS or not r.get("replay_url"):
                continue
            if r.get("replay") not in (None, "failed") or r.get("replay_tries", 0) >= 2:
                continue
            if (now.timestamp() - (r.get("start_time") or 0)) / 86400 > REPLAY_KEEP_DAYS:
                continue
            mid = r["match_id"]
            ok, nbytes = rclone_stream_url(r["replay_url"], f"replays/{r.get('leagueid')}/{mid}.dem.bz2")
            r["replay_tries"] = r.get("replay_tries", 0) + 1
            if ok:
                r["replay"], r["replay_bytes"] = f"replays/{r.get('leagueid')}/{mid}.dem.bz2", nbytes
                replays += 1
                replay_bytes += max(nbytes, 0)
            else:
                r["replay"] = "failed"
    except RuntimeError as e:
        log(f"途中で止めた: {e}（API {api.calls} 回）。残りは次の回")
    finally:
        save_ledger(ledger, a.dry_run)
        json.dump(st, open(STATE, "w", encoding="utf-8"))

    waiting = sum(1 for r in ledger.values() if r["status"] == "parse_requested")
    log(f"保存 {saved} 件、解析前のまま保存 {unparsed_saved} 件、解析の依頼 {requested} 件、解析待ち {waiting} 件、"
        f"リプレイ {replays} 本 {replay_bytes/1e6:.0f}MB、API {api.calls} 回。DONE")
    return finish(0, "dry_run" if a.dry_run else "ok", new=len(new), saved=saved, unparsed=unparsed_saved,
                  requested=requested, waiting=waiting, replays=replays, replay_mb=round(replay_bytes / 1e6), calls=api.calls)


if __name__ == "__main__":
    sys.exit(main())
