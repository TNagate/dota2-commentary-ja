"""1 試合の解説ページ（HTML 1 枚）を作る試作（2026-11-15、出口の案 1「日本語の試合ごとの解説ページ」、公開はしない）。
使い方: python3 -P commentary/page.py <match_id> [材料のフォルダ（既定 work/LIVE）]
材料: commentary_<id>.md・facts_<id>.json（と turning_<id>.json があれば勝つ確率の線）。出力 work/PAGES/<id>.html。
本文の [F12] は脚注（事実の表の行）への番号にする。外の資源は読まない（1 枚で完結）。"""
import html, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); os.chdir(ROOT)
mid = sys.argv[1]; src = sys.argv[2] if len(sys.argv) > 2 else "work/LIVE"
md = open(f"{src}/commentary_{mid}.md", encoding="utf-8").read()
fj = json.load(open(f"{src}/facts_{mid}.json", encoding="utf-8")); facts = {x["id"]: x for x in fj["facts"]}; g = fj["game"]
tp = next((p for p in (f"{src}/turning_{mid}.json", f"commentary/work/turning_{mid}.json") if os.path.exists(p)), None)
used = []
def cite(m):
    out = []
    for f in re.split(r"\s*[,、]\s*", m.group(1)):
        if f not in used: used.append(f)
        out.append(f'<a class="fn" href="#{f}">{used.index(f) + 1}</a>')
    return "<sup>" + ",".join(out) + "</sup>"
body = []
for line in md.splitlines():
    s = line.strip()
    if not s or s == "---": continue
    t = re.sub(r"\[(F\d+(?:\s*[,、]\s*F\d+)*)\]", cite, html.escape(s))
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    h = re.match(r"^(#{1,4})\s*(.*)$", t)
    if h: lv = min(len(h.group(1)) + 1, 4); body.append(f"<h{lv}>{h.group(2)}</h{lv}>")
    elif t.startswith(("- ", "・")): body.append(f"<p class='li'>{t[2:]}</p>")
    else: body.append(f"<p>{t}</p>")
svg = ""
if tp:
    t = json.load(open(tp)); ps = [(a, b) for a, b in t["p_series"]]; T = max(a for a, _ in ps) or 1
    W, H, P = 640, 200, 28
    pts = " ".join(f"{P + (W - 2 * P) * a / T:.1f},{P + (H - 2 * P) * (1 - b):.1f}" for a, b in ps)
    ticks = "".join(f'<line x1="{P + (W - 2 * P) * m * 60 / T:.1f}" y1="{H - P}" x2="{P + (W - 2 * P) * m * 60 / T:.1f}" y2="{H - P + 4}" class="ax"/><text x="{P + (W - 2 * P) * m * 60 / T:.1f}" y="{H - 8}" class="tk">{m}分</text>'
                    for m in range(0, int(T // 60) + 1, 10))
    svg = (f'<figure><svg viewBox="0 0 {W} {H}" role="img" aria-label="このAIの見積もった {html.escape(g["radiant"])} の勝つ確率">'
           f'<line x1="{P}" y1="{H / 2}" x2="{W - P}" y2="{H / 2}" class="mid"/><line x1="{P}" y1="{H - P}" x2="{W - P}" y2="{H - P}" class="ax"/>{ticks}'
           f'<polyline points="{pts}" class="wp"/><text x="{P}" y="{P - 8}" class="tk">{html.escape(g["radiant"])} 100%</text>'
           f'<text x="{P}" y="{H - P + 16}" class="tk"> </text></svg>'
           f'<figcaption>このAIの見積もった {html.escape(g["radiant"])} の勝つ確率（試合の状況だけから計算、50% の線が互角）</figcaption></figure>')
notes = "".join(f'<li id="{f}">{html.escape(facts[f]["text"])}</li>' for f in used if f in facts)
page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(g["score"])} 解説</title><style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a64;--line:#d9d6cc;--acc:#2f6f9f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--muted:#a3a19a;--line:#3a3934;--acc:#7fb4dc}}}}
body{{background:var(--bg);color:var(--fg);font:16px/1.8 "Hiragino Sans","Noto Sans JP",sans-serif;margin:0;padding:24px 16px}}
main{{max-width:720px;margin:0 auto}} h2{{font-size:1.5rem;line-height:1.4}} h3{{font-size:1.15rem;margin-top:2em;border-top:1px solid var(--line);padding-top:1em}} h4{{font-size:1rem}}
.meta{{color:var(--muted);font-size:.85rem}} sup a.fn{{color:var(--acc);text-decoration:none;font-size:.7rem;margin-left:1px}}
svg{{width:100%;height:auto}} .wp{{fill:none;stroke:var(--acc);stroke-width:2}} .mid{{stroke:var(--line);stroke-dasharray:4 4}} .ax{{stroke:var(--line)}} .tk{{fill:var(--muted);font-size:11px}}
figcaption{{color:var(--muted);font-size:.8rem}} ol.notes{{font-size:.8rem;color:var(--muted);line-height:1.6}} p.li{{margin:.2em 0 .2em 1em}}
</style></head><body><main>
<p class="meta"><a href="index.html">一覧へ</a>・試合 {mid}・AI が試合の記録（事実の表）だけから書き、機械の照合と別の AI の点検を通した文章。数字の根拠は各文の番号の脚注。</p>
{svg}
{''.join(body)}
<h3>脚注（事実の表）</h3><ol class="notes">{notes}</ol>
<p class="meta">データ: OpenDota・Valve の公開データ・試合のリプレイ。勝つ確率はこの AI の見積もりです。</p>
</main></body></html>"""
os.makedirs("work/PAGES", exist_ok=True); open(f"work/PAGES/{mid}.html", "w", encoding="utf-8").write(page)
print(f"work/PAGES/{mid}.html（脚注 {len(used)}）")
