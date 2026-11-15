"""解説の文の意味を、付けた根拠の行と照らす（書く役とは別の Claude。読むだけ）。MLB の verify.py を Dota に合わせた。
使い方: python3 -P commentary/verify.py <match_id> [--model opus] → work/verify_<id>.json
"""
import json, re, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
CLAUDE = Path.home() / ".local/bin/claude"
CITE = re.compile(r"\[(F\d+(?:\s*[,、]\s*F\d+)*)\]")
RULES = """あなたは Dota 2 の試合の解説の文を点検する係です。各文について、その文に付いた根拠の行だけを読み、文の言っていることが根拠の行で支えられているかを判定します。あなたの Dota の知識で補ってはいけません。

判定:
- 支持: 文の主張がすべて根拠の行から言える。言い換え・要約・控えめな解釈は支持でよい。
- 言い過ぎ: 根拠の行より主張が広い・強い。例: 根拠に無い因果（〜したから勝った）を断定する／根拠の行にある反対向きの数字を落として印象を一方に寄せる／「決定的」「圧倒」など根拠の数字より強い言葉。
- 誤り: 根拠の行と食い違う（数字・時刻・順番・どちらの陣営か・誰が誰を倒したか・結果）、または根拠の行に無い事実を書いている。

答えは JSON の配列だけを返す（前後に文章を書かない）。形: [{"n": 1, "verdict": "支持", "reason": "短く"}, ...]。すべての n について 1 つずつ。"""


def sentences(md):
    out = []
    for line in md.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        for sent in re.split(r"(?<=\])\s*(?=[^\s\[])", s):
            sent = sent.strip()
            cites = [c.strip() for m in CITE.findall(sent) for c in re.split(r"[,、]", m)]
            if cites:
                out.append((sent, cites))
    return out


def main():
    mid = sys.argv[1]
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "opus"
    facts = {x["id"]: x["text"] for x in json.load(open(HERE / f"work/facts_{mid}.json", encoding="utf-8"))["facts"]}
    sents = sentences(open(HERE / f"work/commentary_{mid}.md", encoding="utf-8").read())
    blocks = []
    for i, (s, cites) in enumerate(sents, 1):
        src = "\n".join(f"  {c}: {facts.get(c, '（無い番号）')}" for c in cites)
        blocks.append(f"[{i}] 文: {CITE.sub('', s).strip()}\n  根拠の行:\n{src}")
    prompt = "次の各文を判定してください。\n\n" + "\n\n".join(blocks)
    cmd = [str(CLAUDE), "-p", "--model", model, "--append-system-prompt", RULES, "--allowedTools", "", "--max-turns", "3", "--output-format", "json"]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=1200)
    j = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {"result": r.stdout}
    text = (j.get("result") or "").strip()
    m = re.search(r"\[\s*\{.*\}\s*\]", text, flags=re.S)
    by_n = {v.get("n"): v for v in (json.loads(m.group(0)) if m else [])}
    res = []
    for i, (s, cites) in enumerate(sents, 1):
        v = by_n.get(i, {"verdict": "判定なし", "reason": "点検の答えに無い"})
        res.append({"n": i, "sentence": s, "cites": cites, "verdict": v.get("verdict"), "reason": v.get("reason")})
    json.dump(res, open(HERE / f"work/verify_{mid}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = [x for x in res if x["verdict"] != "支持"]
    print(f"意味の点検（{model}）: 文 {len(res)}、支持されない {len(bad)}")
    for x in bad:
        print(" -", x["verdict"], "|", x["reason"], "|", x["sentence"][:120])


if __name__ == "__main__":
    main()
