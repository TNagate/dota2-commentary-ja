"""深い層の事実: 転換点の戦いごとに「なぜ勝つ確率が動いたか」の材料を、リプレイから機械で数える（2026-11-09、本人「深いところの解説をいかに構築するか」）。
書く役はこれを言葉にするだけで、自分の Dota の知識で補わない（write.py の決まり）。判断（数え方・線）はここに固定する。

戦いの場: 倒れたヒーローの位置を場所ごとに分け（中心から 2R より離れた死は別の場所）、一番多く倒れた場所の中心から半径 R=2500（ゲームの距離の単位、1 マス 128）以内。
  時間は「その場所の最初の死の 10 秒前」〜「その場所の最後の死」。別の場所の死は、近くにいた相手と一緒に別に書く。
  始まりの顔ぶれ＝戦いの始まりの 5 秒前に生きていて場にいた。途中参加＝始まり〜終わりに初めて場に入った。場にいなかった＝その間ずっと生きていて一度も場に入らなかった。
最初の被害: 始まりの 5 秒前〜終わりで、場の顔ぶれのヒーローが相手の陣営のヒーローに与えた最初の被害（幻影・自分への被害は除く）。技・アイテムの名前が無いものは通常攻撃。
被害の多い選手: 始まりの 10 秒前〜終わりの 3 秒後に、ヒーローがヒーローに与えた被害の合計の上位（各陣営 2 人まで）。
使われたアイテム: 同じ時間に場の顔ぶれが使ったアイテム（TP・消耗品・靴・杖などのよく使うものは除く）。TP は場の外から来た人の分だけ数える。
煙: 始まりの 90 秒前〜始まりに Smoke of Deceit の効果が付いたヒーロー（陣営ごと）。BKB: 戦いの間に Black King Bar の効果が付いたヒーロー。
ワード: 始まりの 5 秒前に置かれていて、中心から 1600 以内にあった見張りのワード（Observer）の本数（陣営ごと）。
直前の大きな買い物: 始まりの 180 秒前〜始まりに場の顔ぶれが買った、値段 1800 ゴールド以上のアイテム（OpenDota の購入の記録）。
倒れた後: 倒れた人の終わりの 1 秒後の復活までの秒と、買い戻しが冷却中か（冷却中なら買い戻せない）。
"""
import math
R_FIGHT = 2500 / 128.0
R_WARD = 1600 / 128.0
TRIVIAL = {"tpscroll", "travel_boots", "travel_boots_2", "tango", "flask", "clarity", "faerie_fire", "enchanted_mango", "magic_stick", "magic_wand",
           "bottle", "ward_dispenser", "ward_observer", "ward_sentry", "quelling_blade", "power_treads", "phase_boots", "soul_ring", "madstone_bundle",
           "dust", "smoke_of_deceit", "famango", "great_famango", "greater_famango", "blood_grenade", "branches", "boots", "arcane_boots", "tranquil_boots"}
ROLE = {1: "セーフ", 2: "ミッド", 3: "オフ", 4: "ジャングル"}


def mmss(t):
    t = int(round(t)); s = "-" if t < 0 else ""; t = abs(t)
    return f"{s}{t // 60}:{t % 60:02d}"


class Ctx:
    def __init__(self, m, pos, com, wards, consts):
        self.m, self.com, self.wards = m, com, wards
        heroes, items, abil = consts
        self.hname = {v["name"]: v["localized_name"] for v in heroes.values()}
        # ★11/15 決め打ちで書いていたアイテムの名前（煙・BKB・ワード）も表の名前（日本語）にする
        self.iname = lambda k, en: ((self.items.get(k) or {}).get("dname") or en)          # npc_dota_hero_x → 表示名
        self.hid = {int(v["id"]): v["localized_name"] for v in heroes.values()}
        self.items = items; self.abil = abil
        self.track = {}       # 表示名 → {t: (x, y, alive, team, respawn, bbcd)}
        for x in pos:
            n = self.hid.get(int(float(x["hero_id"])), x["hero"])
            self.track.setdefault(n, {})[int(x["t"])] = (float(x["x"]), float(x["y"]), x["alive"] == "1", int(x["team"]),
                                                         float(x["respawn_s"]), float(x["buyback_cd"]))
        self.team_of = {n: next(iter(v.values()))[3] for n, v in self.track.items()}
        self.names_used = set()

    def at(self, n, t):
        tr = self.track.get(n, {}); t = int(t)
        for d in range(0, 4):
            if t - d in tr: return tr[t - d]
        return None

    def disp(self, npc):
        return self.hname.get(npc, npc.replace("npc_dota_hero_", ""))

    def inflictor(self, key):
        if not key or key == "dota_unknown":
            return "通常攻撃"
        if key.startswith("item_"):
            d = (self.items.get(key[5:]) or {}).get("dname") or key[5:]
        else:
            d = (self.abil.get(key) or {}).get("dname") or key
        self.names_used.add(d)
        return d


def fight_lines(ctx, e, side_name):
    """1 つの転換点（集団戦）の深い事実を、文の並びで返す"""
    if not e["deaths"]:
        return []
    # 倒れた位置を場所ごとに分ける（中心から 2R より離れた死は別の場所）。一番多く倒れた場所を「戦いの場」にする（2026-11-09: 15 秒でつないだ
    # 1 つの出来事が、地図の別々の場所の小競り合いだったことがあった）
    sites = []
    for t, n, tm in e["deaths"]:
        nm = ctx.hname.get("npc_dota_hero_" + n, n)
        p = ctx.at(nm, t - 1)
        if not p: continue
        for st in sites:
            if math.hypot(st["cx"] - p[0], st["cy"] - p[1]) <= 2 * R_FIGHT:
                st["d"].append((t, nm, tm, p[0], p[1])); k = len(st["d"])
                st["cx"] = sum(x[3] for x in st["d"]) / k; st["cy"] = sum(x[4] for x in st["d"]) / k
                break
        else:
            sites.append({"cx": p[0], "cy": p[1], "d": [(t, nm, tm, p[0], p[1])]})
    if not sites:
        return []
    main = max(sites, key=lambda st: (len(st["d"]), -st["d"][0][0]))
    cx, cy = main["cx"], main["cy"]
    s0, s1 = main["d"][0][0] - 10, main["d"][-1][0]
    near0, joined, away = {2: [], 3: []}, {2: [], 3: []}, {2: [], 3: []}
    for nm, tr in ctx.track.items():
        team = ctx.team_of[nm]
        p0 = ctx.at(nm, s0)
        inside0 = bool(p0 and p0[2] and math.hypot(p0[0] - cx, p0[1] - cy) <= R_FIGHT)
        entered = None; alive_all = True
        for t in range(int(s0), int(s1) + 1):
            p = ctx.at(nm, t)
            if not p: continue
            if not p[2]: alive_all = False
            if p[2] and math.hypot(p[0] - cx, p[1] - cy) <= R_FIGHT and entered is None:
                entered = t
        if inside0: near0[team].append(nm)
        elif entered is not None: joined[team].append(f"{nm}（{mmss(entered)}）")
        elif alive_all and p0 and p0[2]: away[team].append(nm)
    L = []
    if len(sites) > 1:
        L.append(f"この転換点の死は {len(sites)} か所に分かれていた。主な戦いの場で倒れたのは " + "、".join(f"{x[1]}（{mmss(x[0])}）" for x in main["d"]))
        for st in sites:
            if st is main: continue
            for t, nm, tm, x, y in st["d"]:
                foes = [o for o in ctx.track if ctx.team_of[o] != tm and (lambda q: q and q[2] and math.hypot(q[0] - x, q[1] - y) <= R_FIGHT)(ctx.at(o, t - 1))]
                L.append(f"別の場所で倒れた: {nm}（{side_name.get(tm, '?')}、{mmss(t)}、近くにいた相手 {'、'.join(foes) or 'なし'}）")
    L.append(f"戦いの始まり（{mmss(s0)}）に戦いの場にいたのは {side_name[2]} {len(near0[2])} 人（{'、'.join(near0[2]) or 'なし'}）・"
             f"{side_name[3]} {len(near0[3])} 人（{'、'.join(near0[3]) or 'なし'}）")
    if joined[2] or joined[3]:
        L.append("途中から戦いの場に入った: " + "、".join(f"{x}（{side_name[tm]}）" for tm in (2, 3) for x in joined[tm]))
    if away[2] or away[3]:
        L.append("生きていたが戦いの場に一度も入らなかった: " + "、".join(f"{x}（{side_name[tm]}）" for tm in (2, 3) for x in away[tm]))
    w0, w1 = s0 - 5, s1
    dmg = [r for r in ctx.com if r["type"] == "DAMAGE" and r["atk_hero"] == "1" and r["tgt_hero"] == "1" and r["atk_illusion"] == "0"
           and r["tgt_illusion"] == "0" and r["atk_team"] != r["tgt_team"] and r["attacker"] != r["target"] and w0 <= float(r["game_time"]) <= w1]
    inv = set(near0[2] + near0[3] + [j.split("（")[0] for tm in (2, 3) for j in joined[tm]])
    first = next((r for r in sorted(dmg, key=lambda r: float(r["game_time"])) if ctx.disp(r["attacker"]) in inv and ctx.disp(r["target"]) in inv), None)
    if first:
        a, tg = ctx.disp(first["attacker"]), ctx.disp(first["target"])
        L.append(f"最初にヒーローへ被害を与えたのは {a}（{side_name.get(ctx.team_of.get(a), '?')}）の {ctx.inflictor(first['inflictor'])}、相手は {tg}（{mmss(float(first['game_time']))}）")
    tot = {}
    for r in dmg:
        if float(r["game_time"]) < s0 or float(r["game_time"]) > s1 + 3: continue
        a = ctx.disp(r["attacker"])
        if a in inv and ctx.disp(r["target"]) in inv:
            tot[a] = tot.get(a, 0) + int(r["value"])
    for tm in (2, 3):
        top = sorted([(v, a) for a, v in tot.items() if ctx.team_of.get(a) == tm], reverse=True)[:2]
        if top:
            L.append(f"この戦いでヒーローに与えた被害の多い {side_name[tm]} の選手: " + "、".join(f"{a} {v}" for v, a in top))
    used = {}
    for r in ctx.com:
        t = float(r["game_time"])
        if r["type"] == "ITEM" and s0 <= t <= s1 + 3:
            a = ctx.disp(r["attacker"]); key = r["inflictor"].replace("item_", "")
            if a in inv and key not in TRIVIAL:
                used.setdefault((a, ctx.inflictor(r["inflictor"])), t)
    if used:
        L.append("戦いの間に使われたアイテム: " + "、".join(f"{d}（{a}、{mmss(t)}）" for (a, d), t in sorted(used.items(), key=lambda x: x[1])))
    tp = sorted({(ctx.disp(r["attacker"]), float(r["game_time"])) for r in ctx.com if r["type"] == "ITEM" and r["inflictor"] in ("item_tpscroll", "item_travel_boots", "item_travel_boots_2")
                 and s0 - 20 <= float(r["game_time"]) <= s1 and ctx.disp(r["attacker"]) in inv and ctx.disp(r["attacker"]) not in near0[2] + near0[3]}, key=lambda x: x[1])
    if tp:
        L.append("戦いの前後に TP（テレポート）を使った場の外の選手: " + "、".join(f"{a}（{side_name.get(ctx.team_of.get(a), '?')}、{mmss(t)}）" for a, t in tp))
    smoke = {}
    for r in ctx.com:
        t = float(r["game_time"])
        if r["type"] == "MODIFIER_ADD" and r["inflictor"] == "modifier_smoke_of_deceit" and s0 - 80 <= t <= s0 + 10 and r["tgt_hero"] == "1":
            n = ctx.disp(r["target"]); smoke.setdefault(ctx.team_of.get(n), {})[n] = t
    for tm, d in smoke.items():
        if tm in side_name:
            ctx.names_used.add(ctx.iname("smoke_of_deceit", "Smoke of Deceit"))
            L.append(f"戦いの前（{mmss(min(d.values()))}）に {side_name[tm]} の {len(d)} 人に {ctx.iname('smoke_of_deceit', 'Smoke of Deceit')} の効果が付いた（{'、'.join(d)}）")
    bkb = sorted({ctx.disp(r["target"]) for r in ctx.com if r["type"] == "MODIFIER_ADD" and r["inflictor"] == "modifier_black_king_bar_immune"
                  and s0 <= float(r["game_time"]) <= s1 and ctx.disp(r["target"]) in inv})
    if bkb:
        ctx.names_used.add(ctx.iname("black_king_bar", "Black King Bar"))
        L.append(f"戦いの間に {ctx.iname('black_king_bar', 'Black King Bar')} の効果が付いた: " + "、".join(f"{n}（{side_name.get(ctx.team_of.get(n), '?')}）" for n in bkb))
    wc = {2: 0, 3: 0}
    for w in ctx.wards:
        if w["kind"] != "obs": continue
        placed = float(w["placed"]); gone = float(w["gone"]) if w["gone"] else 1e9
        if placed <= s0 < gone and math.hypot(float(w["x"]) - cx, float(w["y"]) - cy) <= R_WARD:
            wc[int(float(w["team"]))] = wc.get(int(float(w["team"])), 0) + 1
    L.append(f"戦いの始まりに戦いの場の近く（1600 以内）にあった {ctx.iname('ward_observer', 'Observer Ward')}: {side_name[2]} {wc.get(2, 0)} 本・{side_name[3]} {wc.get(3, 0)} 本")
    ctx.names_used.add(ctx.iname("ward_observer", "Observer Ward"))
    big = []
    for p in ctx.m["players"]:
        nm = ctx.hid.get(p["hero_id"])
        if nm not in inv: continue
        for b in p.get("purchase_log") or []:
            it = ctx.items.get(b["key"]) or {}
            if (it.get("cost") or 0) >= 1800 and s0 - 170 <= b["time"] <= s0 + 10:
                big.append((b["time"], nm, it.get("dname") or b["key"])); ctx.names_used.add(it.get("dname") or b["key"])
    if big:
        L.append("戦いの前の 3 分以内に場の顔ぶれが買った大きなアイテム（1800 ゴールド以上）: " + "、".join(f"{d}（{n}、{mmss(t)}）" for t, n, d in sorted(big)))
    after = []
    for t, n, tm in e["deaths"]:
        nm = ctx.hname.get("npc_dota_hero_" + n, n); p = ctx.at(nm, e["end"] + 1)
        if p and not p[2] and p[4] >= 0:
            after.append(f"{nm} 復活まで {int(p[4])} 秒・買い戻し{'は冷却中' if p[5] > 0 else 'の冷却なし'}")
    if after:
        L.append("倒れた後（" + mmss(e["end"] + 1) + "）: " + "、".join(dict.fromkeys(after)))
    return L


def game_lines(ctx, side_name):
    """試合全体の深い事実: 10 分の役割とネットワース、レーンの効率、主なアイテムの購入時刻"""
    out = []
    for tm in (2, 3):
        ps = [p for p in ctx.m["players"] if (p["player_slot"] < 128) == (tm == 2)]
        parts = []
        for p in sorted(ps, key=lambda p: p.get("lane_role") or 9):
            nm = ctx.hid.get(p["hero_id"], "?")
            nw10 = (p.get("networth_t") or p.get("gold_t") or [None] * 11)
            nw10 = nw10[10] if len(nw10) > 10 else None
            parts.append(f"{nm}（{ROLE.get(p.get('lane_role'), '?')}、10 分のネットワース {nw10}、レーンの効率 {p.get('lane_efficiency_pct')}%）")
        out.append(("lane", f"{side_name[tm]} の役割と序盤（OpenDota の判定）: " + "、".join(parts) + "。"))
    for tm in (2, 3):
        ps = [p for p in ctx.m["players"] if (p["player_slot"] < 128) == (tm == 2)]
        parts = []
        for p in ps:
            nm = ctx.hid.get(p["hero_id"], "?"); seen = []
            for b in p.get("purchase_log") or []:
                it = ctx.items.get(b["key"]) or {}
                if (it.get("cost") or 0) >= 2000 and b["key"] not in TRIVIAL and b["time"] > 0:
                    d = it.get("dname") or b["key"]
                    if d not in [s for s, _ in seen]:
                        seen.append((d, b["time"])); ctx.names_used.add(d)
            if seen:
                parts.append(f"{nm}: " + "、".join(f"{d} {mmss(t)}" for d, t in seen[:4]))
        if parts:
            out.append(("items", f"{side_name[tm]} の主なアイテム（値段 2000 ゴールド以上、買った時刻、各選手 4 つまで）: " + "／".join(parts) + "。"))
    return out
