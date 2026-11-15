"""時代と封印（docs/SEAL_PREREG.md・docs/REHEARSAL_PREREG.md）。

封印（開けるまで学習・特徴探し・型の選び直しに使わない）:
  R    2022-04-21〜2023-04-21 JST … 昔の時代（7.28〜7.32）で練習の研究をして答え合わせする分（2026-11-10 本人）
  main 2024-11-10 JST〜（これからの試合も含む）… 最新の時代の研究の答え合わせ（2026-11-10 本人、見た試合も含めて約 2 年）
時代（DOTA_ERA で研究をその時代に絞る。未指定なら絞らない＝封印だけ外す）:
  R 2020-12-18〜2023-04-21（7.28〜7.32、7.33 でマップが大きく広がる前）
  B 2023-04-21〜2024-11-10（7.33〜7.37）
開けるときだけ DOTA_UNSEAL=R か DOTA_UNSEAL=main で動かす（logs/unseal.log に時刻とコマンドが残る）。
直し 2026-11-11（本人「マップ拡大の前後で分ける」）: 時代 R と B の境を日付でなく試合のパッチ番号で切る。7.33（番号 52、
公開 2023-04-21 10:22 JST）の後も旧パッチで戦った試合が 27、R の初日の後も 7.27（46）の試合が 18 あったため。
  R = パッチ 47〜51（7.28〜7.32）、B = パッチ 52 以上（7.33〜）かつ 2024-11-10 より前。封印 R = R のうち 2022-04-21 以降。
  パッチの分からない試合だけ前の日付の境で決める。
"""
import datetime as dt, os, sys


def _ts(day):
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone(dt.timedelta(hours=9))).timestamp())


SEALS = {"R": (_ts("2022-04-21"), _ts("2023-04-21")), "main": (_ts("2024-11-10"), None)}
ERAS = {"R": (_ts("2020-12-18"), _ts("2023-04-21")), "B": (_ts("2023-04-21"), _ts("2024-11-10"))}
SEAL_START = SEALS["main"][0]  # 互換（backfill などが読む）
ERA = os.environ.get("DOTA_ERA") or None
# "Bx" = 時代 B ＋封印 main（パッチ 52 以上で終わりなし）。封印 main の答え合わせ（docs/MAIN_FINAL.md、2026-11-12）だけで使う。
# "M" = 封印 main の中だけ（2024-11-10 以降・パッチ 52 以上）。開けた後に価値の表を作り直す用（2026-11-12）。
if ERA is not None and ERA not in ERAS and ERA not in ("Bx", "M"):
    raise SystemExit(f"DOTA_ERA={ERA} は無い時代（{list(ERAS) + [Bx, M]}）")
OPENED = set(x for x in (os.environ.get("DOTA_UNSEAL") or "").split(",") if x)
if "1" in OPENED:
    raise SystemExit("DOTA_UNSEAL=1 は使わない。開ける封印の名前（R か main）を書く")
if OPENED:
    _logdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(_logdir, exist_ok=True)
    with open(os.path.join(_logdir, "unseal.log"), "a") as f:
        f.write(dt.datetime.now().isoformat(timespec="seconds") + f" 開けた {sorted(OPENED)} 時代 {ERA}: " + " ".join(sys.argv) + "\n")


PATCH_R = (47, 51)  # 7.28〜7.32
PATCH_B_FROM = 52  # 7.33〜


def _pnum(patch):
    try:
        return int(patch)
    except (TypeError, ValueError):
        return None


def era_of(start_time, patch=None):
    """試合の時代（"R"・"B"・None）。パッチ番号があればそれで、無ければ日付で決める。"""
    if start_time is None:
        return None
    pn = _pnum(patch)
    if pn is not None:
        if PATCH_R[0] <= pn <= PATCH_R[1]:
            return "R"
        if pn >= PATCH_B_FROM and start_time < ERAS["B"][1]:
            return "B"
        return None
    for name, (a, b) in ERAS.items():
        if a <= start_time < b:
            return name
    return None


def in_seal(start_time, patch=None):
    """開けていない封印の中なら True。封印 R は「時代 R のうち 2022-04-21 以降」（パッチが分かれば R の終わりはパッチで切る）。"""
    if start_time is None:
        return True
    if "main" not in OPENED and start_time >= SEALS["main"][0]:
        return True
    if "R" not in OPENED and start_time >= SEALS["R"][0]:
        if _pnum(patch) is not None:
            return era_of(start_time, patch) == "R"
        return start_time < SEALS["R"][1]
    return False


def in_era(start_time, patch=None):
    if ERA is None:
        return True
    if ERA in ("Bx", "M"):
        if ERA == "Bx" and era_of(start_time, patch) == "B":
            return True
        pn = _pnum(patch)
        return start_time >= SEALS["main"][0] and (pn is None or pn >= PATCH_B_FROM)
    return era_of(start_time, patch) == ERA


def sealed(start_time, patch=None):
    """使わない試合なら True（開けていない封印の中、または DOTA_ERA の時代の外）。start_time が無い試合は使わない（安全側）。
    patch（OpenDota のパッチ番号）を渡すと時代と封印 R の境をパッチで切る（11/11 から。渡さなければ日付）。"""
    if start_time is None:
        return True
    return in_seal(int(start_time), patch) or not in_era(int(start_time), patch)
