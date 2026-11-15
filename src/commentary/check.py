"""解説の文章を事実の表と機械で照合する（判定 A＝事実の誤り 0 の機械の部分）。MLB の check.py を Dota に合わせた。
使い方: python3 -P commentary/check.py <match_id>
見ること: 根拠の番号の有無と実在／文の数字が付けた根拠の行にあるか（単位つきは単位も）／文に出る名前（選手・ヒーロー・チーム、表の game.names の語）が
付けた根拠の行にあるか／転換点の行がどれも 1 回は引かれているか。拾えない誤り（勝ち負けの取り違えなど）は verify.py と人が読む。
"""
import json, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
NUM = re.compile(r"(?<![A-Za-z0-9.])[-+]?\d+(?:\.\d+)?")
UNIT = re.compile(r"(?<![A-Za-z0-9.:])([-+]?\d+(?:\.\d+)?)\s*(%|ゴールド|キル|デス|アシスト|レベル|人|回目|回|段目|分|本)")
CITE = re.compile(r"\[(F\d+(?:\s*[,、]\s*F\d+)*)\]")


def nums(s):
    return {abs(float(x)) for x in NUM.findall(s.replace(",", ""))}


def num_units(s):
    return {(abs(float(n)), u) for n, u in UNIT.findall(s.replace(",", ""))}


def main():
    mid = sys.argv[1]
    fj = json.load(open(HERE / f"work/facts_{mid}.json", encoding="utf-8"))
    facts = {x["id"]: x["text"] for x in fj["facts"]}
    names = sorted({n for n in fj["game"]["names"] if n and len(n) >= 2}, key=len, reverse=True)
    text = open(HERE / f"work/commentary_{mid}.md", encoding="utf-8").read()
    problems, n_sent = [], 0
    all_u = num_units(" ".join(facts.values()))
    for line in text.splitlines():
        s = line.strip()
        if not s or s == "---":
            continue
        if s.startswith("#"):
            for x, u in num_units(s):
                if (x, u) not in all_u:
                    problems.append({"type": "見出しの表に無い数字", "number": f"{x:g} {u}", "sentence": s})
            continue
        for sent in re.split(r"(?<=\])\s*(?=[^\s\[])|(?<=。)(?!\s*\[)", s):
            sent = sent.strip()
            if not sent or len(re.sub(r"[\s*_\-]", "", sent)) < 4:
                continue
            body = CITE.sub("", sent)
            cites = [c.strip() for mm in CITE.findall(sent) for c in re.split(r"[,、]", mm)]
            if not cites:
                if re.fullmatch(r"\**[^。]{1,60}\**", body.strip()):
                    continue
                problems.append({"type": "根拠なし", "sentence": sent}); continue
            n_sent += 1
            unknown = [c for c in cites if c not in facts]
            if unknown:
                problems.append({"type": "無い番号", "sentence": sent, "cites": unknown}); continue
            src = " ".join(facts[c] for c in cites)
            have, have_u = nums(src), num_units(src)
            for x in nums(body):
                if x not in have:
                    problems.append({"type": "表に無い数字", "number": x, "sentence": sent, "cites": cites})
            for x, u in num_units(body):
                if x in have and (x, u) not in have_u:
                    problems.append({"type": "単位の違う数字", "number": f"{x:g} {u}", "sentence": sent, "cites": cites})
            rest = body
            for nm in names:  # 長い名前から順に消していく（Anti-Mage の中の Mage などの二重数えを避ける）
                if nm in rest:
                    if nm not in src:
                        problems.append({"type": "根拠に無い名前", "name": nm, "sentence": sent, "cites": cites})
                    rest = rest.replace(nm, " ")
    cited = set(c.strip() for mm in CITE.findall(text) for c in re.split(r"[,、]", mm))
    for x in fj["facts"]:
        if x["kind"] == "turning_situation" and x["id"] not in cited:
            problems.append({"type": "転換点の抜け", "missing": x["id"], "sentence": x["text"][:60]})
    res = {"match_id": mid, "sentences_checked": n_sent, "problems": problems, "pass_A": not problems}
    json.dump(res, open(HERE / f"work/check_{mid}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"照合した文 {n_sent}、問題 {len(problems)} 件 → {'A 合格（機械の照合の範囲で）' if not problems else 'A 不合格'}")
    for p in problems:
        print(" -", p["type"], p.get("number") or p.get("name") or p.get("cites") or p.get("missing") or "", "|", p["sentence"][:140])


if __name__ == "__main__":
    main()
