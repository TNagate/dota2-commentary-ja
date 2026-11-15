"""試合の記録を読む共通部品（2026-11-11、/explorer のまとめ取りに切り替えた時に足した）。

試合の記録は 2 つの形で Drive にある:
  - 1 試合 1 ファイル（/matches/<id> の JSON、collect・旧 backfill）: 台帳の drive が matches/<YYYY-MM>/<id>.json.gz
  - 100 試合 1 ファイル（/explorer の答えそのまま、explorer_ingest）: 台帳の drive が explorer/<区間>/<最初>_<最後>.json.gz、
    drive_batch が true。中の rows の 1 行が 1 試合（matches の全列＋players＝player_matches の全列＋patch_name ほか）
使い方: match_store.load(台帳の行) → /matches と同じ形の dict。match_store.from_explorer_row(row) → 同じ形の dict。
/explorer の行を /matches の形にそろえる直し（11/11 に 3 試合で特徴 28 列と分ごとの表が完全一致するのを確かめた）:
  - 購入の記録 purchase_log から ward_dispenser の行を除く（/matches は出さない）
  - patch を名前（"7.31"）から番号（50）に直す（dotaconstants の data/patch.json）
"""
import functools, gzip, json, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
RCLONE = os.path.expanduser("~/.local/bin/rclone")
REMOTE = "drive:dota2_data"


@functools.lru_cache(maxsize=1)
def patch_ids():
    return {x["name"]: x["id"] for x in json.load(open(os.path.join(HERE, "data/patch.json"), encoding="utf-8"))}


def from_explorer_row(row):
    m = dict(row)
    players = m.pop("players", None) or []
    name = m.pop("patch_name", None)
    for k in ("league_name", "radiant_name", "dire_name"):
        m.pop(k, None)
    ps = []
    for p in sorted(players, key=lambda p: p.get("player_slot") or 0):
        p = dict(p)
        if p.get("purchase_log"):
            p["purchase_log"] = [x for x in p["purchase_log"] if x.get("key") != "ward_dispenser"]
        ps.append(p)
    m["players"] = ps
    m["patch"] = patch_ids().get(name, name)
    m["source"] = "opendota_explorer"
    return m


@functools.lru_cache(maxsize=4)
def _batch(dest):
    p = subprocess.run([RCLONE, "cat", f"{REMOTE}/{dest}"], capture_output=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"drive_read_failed {dest}")
    return {r["match_id"]: r for r in json.loads(gzip.decompress(p.stdout)).get("rows") or []}


def load(r):
    """台帳の行から試合の記録を読む（読めなければ None）。"""
    dest = r.get("drive")
    if not dest:
        return None
    if r.get("drive_batch"):
        row = _batch(dest).get(r["match_id"])
        return from_explorer_row(row) if row else None
    p = subprocess.run([RCLONE, "cat", f"{REMOTE}/{dest}"], capture_output=True, timeout=300)
    if p.returncode != 0:
        return None
    try:
        return json.loads(gzip.decompress(p.stdout))
    except Exception:
        return None
