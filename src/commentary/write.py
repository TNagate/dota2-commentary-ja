"""事実の表から Dota 2 の試合の解説を書かせる（server の claude -p、道具なし。書く役だけ）。MLB の write_commentary.py と同じ形。
使い方: python3 -P commentary/write.py <match_id> [--model sonnet]   入力 work/facts_<id>.json → 出力 work/commentary_<id>.md
"""
import json, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
CLAUDE = Path.home() / ".local/bin/claude"
RULES = """あなたは Dota 2 のプロの試合の解説を書く係です。渡す「事実の表」だけを材料に、日本語で解説を書きます。

守ること（破ると解説は捨てられます）:
1. 書いてよいのは事実の表にあることだけ。選手やチームの経歴・過去の試合・ヒーローの能力やアイテム・パッチ・定石など、表に無いことは一切書かない。あなたの Dota の知識で補わない。
2. すべての文（「。」で終わる一つ一つ）の終わりに、その文の根拠の行の番号を [F12] や [F21,F22] の形で付ける。見出しだけは番号が要らない。解釈の文にも元の数字の行の番号を付ける。
3. 数字は表に書かれた形のまま使う。足し算・引き算・割合など新しい数字を作らない。人数・回数・本数を自分で数えない。時刻は表の「12:43」の形のまま。
4. 選手・ヒーロー・チーム・アイテムの名前は表のとおりの綴りで書く（選手とチームの英字をカタカナにしない、ヒーローとアイテムは表の日本語名のまま、略さない）。
5. 順番（「最初の」「その後」「2 回目」など）と時刻は、表に書いてあるときだけ書く。表の行の並びから推し量らない。
6. 「狙っていた」「焦った」「読み合い」のような、表から確かめられない意図や心の中を書かない。何が起き、数字がどう動いたかを書く。
7. 「勝つ確率」は「このAIの見積もり」であることを最初に一度書く。確率の数字は表の数字だけを使う。
8. 同じ行に向きの違う数字（例: 勝った側が先に塔を失っている）があれば、片方だけを書いて言い切らない。
9. 「転換点 k の詳しい事実」「見せ場 k の詳しい事実」の行があれば、その場面の「何が違ったか」を書くのに使う（人数の差、場にいなかった人、最初に仕掛けた側と技、被害の多かった選手、煙・BKB・使われたアイテム、近くのワードの本数、直前の買い物、倒れた後の復活と買い戻し）。
   書き方は「〜だった」「〜が重なった」と事実を並べる。「〜だから勝った」「〜が決め手だった」と因果を言い切らない。役割と序盤・主なアイテムの行も、選手の欄や流れの説明に使ってよい。

形:
- 見出し（試合の結果を一行で）
- 「試合の流れ」: 3〜5 文
- 「勝負を分けた場面」: 表の転換点ごとに小見出しを付け、何が起き、勝つ確率と総獲得ゴールドの差がどう動いたかを書き、詳しい事実の行があれば、その場面で何が違ったかを 2〜4 文で書く（転換点はすべて取り上げる）
- 「見せ場」: 表に「見せ場 k」の行があるときだけ。見せ場ごとに小見出しを付け、何が起き、勝つ確率と総獲得ゴールドの差がどう動いたか（ほとんど動かなかったなら、そのまま書く）、詳しい事実の行があれば 2〜4 文で書く（見せ場はすべて取り上げる）。「見せ場」の行が無ければこの節は作らない
- 「選手」: 2〜4 文
"""


def main():
    mid = sys.argv[1]
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "sonnet"
    facts = json.load(open(HERE / f"work/facts_{mid}.json", encoding="utf-8"))
    table = "\n".join(f"{x['id']}: {x['text']}" for x in facts["facts"])
    prompt = f"事実の表（{facts['game']['score']}）:\n\n{table}\n\n上の表だけを使って、決まりどおりに解説を書いてください。"
    cmd = [str(CLAUDE), "-p", "--model", model, "--append-system-prompt", RULES, "--allowedTools", "", "--max-turns", "3", "--output-format", "json"]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=900)
    j = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {"result": r.stdout}
    text = (j.get("result") or "").strip()
    (HERE / f"work/commentary_{mid}.md").write_text(text + "\n", encoding="utf-8")
    print(f"{len(text)} 字 → commentary/work/commentary_{mid}.md（{model}、rc={r.returncode}）")


if __name__ == "__main__":
    main()
