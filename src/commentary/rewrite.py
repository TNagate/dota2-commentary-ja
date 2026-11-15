"""照合と意味の点検で引っかかった文だけを、書く役に直させる（1 回だけ）。MLB の rewrite.py と同じ。
使い方: python3 -P commentary/rewrite.py <match_id> [--model sonnet]
"""
import json, re, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from write import CLAUDE, RULES  # noqa: E402


def main():
    mid = sys.argv[1]
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "sonnet"
    md = (HERE / f"work/commentary_{mid}.md").read_text(encoding="utf-8")
    facts = json.load(open(HERE / f"work/facts_{mid}.json", encoding="utf-8"))
    chk = json.load(open(HERE / f"work/check_{mid}.json", encoding="utf-8"))
    vp = HERE / f"work/verify_{mid}.json"
    ver = json.load(open(vp, encoding="utf-8")) if vp.exists() else []
    issues = [f"- 照合: {p['type']} {p.get('number') or p.get('name') or p.get('missing') or ''} ／ 文: {p['sentence']}" for p in chk["problems"]]
    issues += [f"- 点検: {v['verdict']}（{v['reason']}）／ 文: {v['sentence']}" for v in ver if v["verdict"] != "支持"]
    if not issues:
        print("直すところなし"); return
    table = "\n".join(f"{x['id']}: {x['text']}" for x in facts["facts"])
    prompt = (f"事実の表:\n\n{table}\n\n下の解説の文章のうち、指摘のある文だけを直して、文章の全体を返してください。指摘の無い文は一字も変えない。"
              "直し方: 根拠の番号を足す／主張を根拠の行で言える範囲に狭める／根拠の行にある反対向きの数字も書く。転換点・見せ場が抜けていれば、その小見出しと文を足す。"
              "直せない文は消す。解説の文章だけを返し、指摘への返事・補足・作業のメモは書かない。\n\n指摘:\n" + "\n".join(issues) + f"\n\n解説の文章:\n\n{md}")
    cmd = [str(CLAUDE), "-p", "--model", model, "--append-system-prompt", RULES, "--allowedTools", "", "--max-turns", "3", "--output-format", "json"]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=900)
    j = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {"result": r.stdout}
    text = (j.get("result") or "").strip()
    text = "\n".join(l for l in text.split("\n") if not re.match(r"^\s*(補足|注|メモ)[:：]", l)).rstrip()
    (HERE / f"work/commentary_{mid}.before_rewrite.md").write_text(md, encoding="utf-8")
    (HERE / f"work/commentary_{mid}.md").write_text(text + "\n", encoding="utf-8")
    print(f"指摘 {len(issues)} 件を直させた → commentary/work/commentary_{mid}.md（直す前は .before_rewrite.md）")


if __name__ == "__main__":
    main()
