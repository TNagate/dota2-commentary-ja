"""保存済みの試合の生のリプレイを、Valve の置き場から消える前に Drive へ流す（OpenDota の API を使わない）。

使い方: python -P replay_catchup.py [--max 300] [--tiers premium,professional]
collect.py と同じ鍵（locks/collect.lock）を取るので、定期実行と台帳を同時に書かない。
OpenDota が落ちていても動く（replay_url は台帳に保存済み）。
"""
import argparse
import datetime as dt
import fcntl
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as C  # noqa: E402  （import すると作業場所が ~/dota_trial に移る）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=300)
    ap.add_argument("--tiers", default="premium,professional")
    a = ap.parse_args()
    tiers = set(a.tiers.split(","))
    lock = open("locks/collect.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)  # 定期実行が動いていれば終わるまで待つ
    ledger = C.load_ledger()
    now = dt.datetime.now(C.JST).timestamp()
    todo = [r for r in ledger.values()
            if r["status"] in ("saved", "unparsed") and r.get("tier") in tiers and r.get("replay_url")
            and r.get("replay") in (None, "failed") and r.get("replay_tries", 0) < 2
            and (now - (r.get("start_time") or 0)) / 86400 <= C.REPLAY_KEEP_DAYS]
    todo.sort(key=lambda r: -r["match_id"])
    C.log(f"リプレイの取り込み: 対象 {len(todo)} 試合（上限 {a.max}）")
    ok_n = fail_n = 0
    total = 0
    for i, r in enumerate(todo[:a.max], 1):
        mid = r["match_id"]
        dest = f"replays/{r.get('leagueid')}/{mid}.dem.bz2"
        ok, nbytes = C.rclone_stream_url(r["replay_url"], dest)
        r["replay_tries"] = r.get("replay_tries", 0) + 1
        if ok:
            r["replay"], r["replay_bytes"] = dest, nbytes
            ok_n += 1
            total += max(nbytes, 0)
        else:
            r["replay"] = "failed"
            fail_n += 1
        if i % 10 == 0:
            C.save_ledger(ledger, False)  # 途中で止まっても進みを残す
            C.log(f"  {i}/{min(len(todo), a.max)} 済み（成功 {ok_n}、失敗 {fail_n}、{total/1e9:.1f}GB）")
    C.save_ledger(ledger, False)
    C.log(f"リプレイの取り込み: 成功 {ok_n}、失敗 {fail_n}、計 {total/1e9:.1f}GB。DONE")
    return 0 if ok_n or not todo else 1


if __name__ == "__main__":
    sys.exit(main())
