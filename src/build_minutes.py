"""Drive に保存済みの試合の記録から、分ごとの表（data/minutes/<id>.json）をまだ無いぶんだけ作る。

使い方: python3 -P build_minutes.py [--max 2000]
（backfill は取った時に作る。毎日の収集で増える分と、2026-11-08 以前に保存した分はこれで追いつく。勝率の線を引く前に走らせる）
"""
import argparse, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import minutes
import match_store
RCLONE = os.path.expanduser("~/.local/bin/rclone"); REMOTE = "drive:dota2_data"

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--max", type=int, default=2000); a = ap.parse_args()
    led = [json.loads(l) for l in open("data/matches.jsonl", encoding="utf-8") if l.strip()]
    todo = [r for r in led if r.get("status") == "saved" and r.get("drive") and not os.path.exists(os.path.join(minutes.OUT_DIR, f"{r['match_id']}.json"))]
    todo.sort(key=lambda r: -r["match_id"])
    ok = fail = 0
    for r in todo[:a.max]:
        try:
            m = match_store.load(r)  # 1 試合 1 ファイルと 100 試合まとめ（/explorer、11/11〜）の両方を読む
        except Exception:
            m = None
        if m is None:
            fail += 1; continue
        ok += bool(minutes.save(m))
    print(f"分ごとの表: 対象 {len(todo)}、作った {ok}、失敗 {fail}、合計 {len(os.listdir(minutes.OUT_DIR)) if os.path.isdir(minutes.OUT_DIR) else 0}")
    return 0 if fail == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
