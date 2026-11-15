"""公開用のサイト（静的ページ）を作る（2026-11-16、本人「無料のページ公開もお願い」）。
work/LIVE の解説（照合と点検の後の問題が 0 の試合だけ）から 1 試合 1 枚のページ（commentary/page.py）と一覧 index.html を作り、work/SITE/ に置く。
使い方: python3 -P commentary/site.py"""
import html, json, os, shutil, subprocess, sys, datetime
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); os.chdir(ROOT)
OUT = "work/SITE"; os.makedirs(OUT, exist_ok=True)
led = {}
for l in open("work/LIVE/ledger.jsonl"):
    r = json.loads(l)
    if r.get("ok") and r.get("problems_after") == 0: led[r["match_id"]] = r
M = {json.loads(l)["match_id"]: json.loads(l) for l in open("data/matches.jsonl")}
items = []
for mid, r in led.items():
    if not os.path.exists(f"work/LIVE/commentary_{mid}.md"): continue
    x = subprocess.run([sys.executable, "-P", "commentary/page.py", str(mid), "work/LIVE"], capture_output=True, text=True)
    if x.returncode: continue
    shutil.copy(f"work/PAGES/{mid}.html", f"{OUT}/{mid}.html")
    fj = json.load(open(f"work/LIVE/facts_{mid}.json")); g = fj["game"]; m = M.get(mid, {})
    head = next((l.strip("# ").strip() for l in open(f"work/LIVE/commentary_{mid}.md", encoding="utf-8") if l.startswith("#")), g["score"])
    import re; head = re.sub(r"\s*\[F[^\]]*\]", "", head)  # 見出しの根拠の番号は一覧では外す
    items.append((m.get("start_time", 0), mid, m.get("league_name", ""), g["score"], head))
items.sort(reverse=True)
li = "".join(f'<li><a href="{mid}.html"><span class="d">{datetime.datetime.fromtimestamp(t, datetime.timezone(datetime.timedelta(hours=9))).strftime("%m/%d %H:%M")}</span> '
             f'<span class="lg">{html.escape(lg)}</span><br><strong>{html.escape(sc)}</strong> — {html.escape(hd)}</a></li>' for t, mid, lg, sc, hd in items)
page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dota 2 試合解説</title><style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a64;--line:#d9d6cc;--acc:#2f6f9f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--muted:#a3a19a;--line:#3a3934;--acc:#7fb4dc}}}}
body{{background:var(--bg);color:var(--fg);font:16px/1.7 "Hiragino Sans","Noto Sans JP",sans-serif;margin:0;padding:24px 16px}}
main{{max-width:720px;margin:0 auto}} ul{{list-style:none;padding:0}} li{{border-top:1px solid var(--line);padding:.8em 0}} a{{color:var(--fg);text-decoration:none}}
a:hover strong{{color:var(--acc)}} .d,.lg,.meta{{color:var(--muted);font-size:.85rem}}
</style></head><body><main><h1>Dota 2 プロの試合の解説</h1>
<p class="meta">AI が試合の記録だけから書き、機械の照合と別の AI の点検を通した日本語の解説。各文の数字には根拠の脚注があります。更新 {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}（{len(items)} 試合）。</p>
<ul>{li}</ul></main></body></html>"""
open(f"{OUT}/index.html", "w", encoding="utf-8").write(page)
print(f"{OUT}: {len(items)} 試合")
