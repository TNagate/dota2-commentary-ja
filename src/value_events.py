"""価値の表の材料: 時代 R の研究の部分の試合から出来事（塔・兵舎・メガ・Roshan・FB・キル・集団戦・バイバック）を取り出す（docs/VALUE_PREREG.md）。
使い方: python3 -P value_events.py → data/value/events_R.jsonl（試合ごとに 1 行）。Drive の記録を読む（まとめのファイルは並べて読み回す）。"""
import glob, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import collect as C, match_store as S, dota_seal as D
ERA = D.ERA or "R"  # 11/11 時代 B にも使う（DOTA_ERA=B → events_B.jsonl）

def side_of_slot(ps):
    return 2 if ps < 128 else 3

def events(m):
    ev = []
    rax_lost = {2: 0, 3: 0}
    for o in sorted(m.get("objectives") or [], key=lambda o: o.get("time") or 0):
        t, ty = o.get("time"), o.get("type")
        if t is None:
            continue
        if ty == "building_kill":
            k = o.get("key") or ""
            owner = 2 if "goodguys" in k else 3 if "badguys" in k else None
            if owner is None:
                continue
            gain = 5 - owner
            for tag, name in (("tower1", "T1"), ("tower2", "T2"), ("tower3", "T3"), ("tower4", "T4"), ("melee_rax", "rax_melee"), ("range_rax", "rax_range")):
                if tag in k:
                    ev.append([t, name, gain])
                    if "rax" in tag:
                        rax_lost[owner] += 1
                        if rax_lost[owner] == 6:
                            ev.append([t, "mega", gain])
                    break
        elif ty == "CHAT_MESSAGE_ROSHAN_KILL" and o.get("team") in (2, 3):
            n = sum(1 for e in ev if e[1].startswith("roshan"))
            ev.append([t, "roshan_1" if n == 0 else "roshan_2plus", o["team"]])
        elif ty == "CHAT_MESSAGE_FIRSTBLOOD" and o.get("player_slot") is not None:
            ev.append([t, "first_blood", side_of_slot(o["player_slot"])])
    ps = sorted(m.get("players") or [], key=lambda p: p.get("player_slot") or 0)
    for p in ps:
        s = side_of_slot(p.get("player_slot") or 0)
        for k in p.get("kills_log") or []:
            if k.get("time") is not None:
                ev.append([k["time"], "kill", s])
        for b in p.get("buyback_log") or []:
            if b.get("time") is not None:
                ev.append([b["time"], "buyback", s])
    for f in m.get("teamfights") or []:
        pl = f.get("players") or []
        if len(pl) != 10 or f.get("end") is None:
            continue
        dr = sum(x.get("deaths", 0) for x in pl[:5]); dd = sum(x.get("deaths", 0) for x in pl[5:])
        if abs(dr - dd) >= 2:
            ev.append([f["end"], "teamfight_win", 2 if dd > dr else 3])
    return sorted(ev)

def main():
    os.makedirs("data/value", exist_ok=True)
    want = {}
    for f in glob.glob("data/minutes/*.json"):
        d = json.load(open(f))
        st, pt = d.get("start_time"), d.get("patch")
        if D.in_era(st, pt) and not D.in_seal(st, pt):  # 11/12 in_era に（R・B は同じ、M＝封印 main の中も読めるように）
            want[d["match_id"]] = pt
    led = C.load_ledger()
    rows = sorted((led[m] for m in want if m in led), key=lambda r: (r.get("drive") or ""))
    t0 = time.time(); ok = fail = 0
    with open(f"data/value/events_{ERA}.jsonl.tmp", "w") as out:
        for i, r in enumerate(rows):
            try:
                m = S.load(r)
            except Exception:
                m = None
            if not m:
                fail += 1; continue
            out.write(json.dumps({"match_id": r["match_id"], "ev": events(m)}, separators=(",", ":")) + "\n"); ok += 1
            if i % 2000 == 0:
                print(f"{i}/{len(rows)} {time.time()-t0:.0f} 秒", flush=True)
    os.replace(f"data/value/events_{ERA}.jsonl.tmp", f"data/value/events_{ERA}.jsonl")
    print(f"試合 {ok}、読めない {fail}、{time.time()-t0:.0f} 秒。DONE")

if __name__ == "__main__":
    main()
