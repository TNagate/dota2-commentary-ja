// dotapos: Dota 2 のリプレイ（展開済みの .dem を標準入力から）を読み、戦況判断に要る記録を CSV で出す。
//
// 使い方: zstd -dc X.dem.bz2 | dotapos [-every 30] [-events ev.csv] [-combat c.csv] [-objects o.csv] [-wards w.csv] > X.csv
//
//	-every   何 tick ごとに書くか（Dota は 1 秒 30 tick。既定 30＝1 秒ごと）
//	-events  生死が変わった瞬間（tick 単位）
//	-combat  コンバットログの抜粋（死・ダメージ（対象がヒーロー・建物・Roshan・クーリエ）・能力・アイテム・金・経験値・購入・
//	         買い戻し・建物の破壊・Aegis・ルーン・テレポート・煙や BKB などの印の付け外し）。時刻はホーンからの秒
//	-objects 1 秒ごとの建物（塔・兵舎・本陣）の体力、Roshan、ワードの数、陣営のネットワースの合計
//	-wards   ワード 1 本ごとの置いた時刻・消えた時刻・場所・置いた選手
//	-fields  最初のヒーローと試合の規則の実体が持つ欄の名前を標準エラーに出して終わる（欄の名前を確かめる用）
//
// 標準出力（1 秒ごと・ヒーロー 10 人）の列: 位置・体力・マナ・レベル・生死に加えて（2026-11-08 第 1 段で足した）
//
//	networth, gold（総獲得）, xp（総獲得）, lh, dn, kills, deaths, assists, respawn_s（復活までの秒、生きていれば -1）,
//	buyback_cd（買い戻しの冷却の残り秒、0 なら使える）, reliable_gold, unreliable_gold, streak
//
// 座標: x, y は OpenDota と同じ「マス」の座標（cellX + vecX/128。7.3x の地図では 60〜197）。
//
//	wx, wy は世界の座標（cell*128 + vec − 16384）。コンバットログの位置（金・経験値だけ入る）もマスに直して出す。
//
// 時刻: 標準出力と events はサーバーの tick（NetTick）から、combat は Valve の時計（ts）からホーン（m_flGameStartTime）を引いたもの。
//
//	どちらも OpenDota の time と同じ基準（11/8 に塔の破壊と死で確かめた）。
//
// ヒーローは各選手が選んだ実体だけ（CDOTA_PlayerResource から）。幻影・分身・召喚は除く。
package main

import (
	"bufio"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"sort"
	"strings"

	"github.com/dotabuff/manta"
	"github.com/dotabuff/manta/dota"
)

const invalidHandle = 16777215

func num(e *manta.Entity, name string) (float64, bool) {
	if e == nil {
		return 0, false
	}
	v := e.Get(name)
	if v == nil {
		return 0, false
	}
	switch t := v.(type) {
	case uint64:
		return float64(t), true
	case uint32:
		return float64(t), true
	case int32:
		return float64(t), true
	case int64:
		return float64(t), true
	case float32:
		return float64(t), true
	case float64:
		return t, true
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	}
	return 0, false
}

func first(e *manta.Entity, names ...string) (float64, bool) {
	for _, n := range names {
		if v, ok := num(e, n); ok {
			return v, true
		}
	}
	return 0, false
}

func cellOf(e *manta.Entity) (float64, float64, bool) {
	cx, ok1 := num(e, "CBodyComponent.m_cellX")
	cy, ok2 := num(e, "CBodyComponent.m_cellY")
	vx, _ := num(e, "CBodyComponent.m_vecX")
	vy, _ := num(e, "CBodyComponent.m_vecY")
	return cx + vx/128, cy + vy/128, ok1 && ok2
}

func f0(v float64) string { return fmt.Sprintf("%.0f", v) }

// コンバットログの 1 行（ホーンの時刻が最後まで確定しないので溜めてから書く）
type clRow struct {
	ts                                    float32
	typ                                   int32
	atk, tgt, infl                        string
	value                                 uint32
	health                                int32
	atkHero, tgtHero, atkIll, tgtIll, bld bool
	atkTeam, tgtTeam, goldR, xpR          uint32
	x, y                                  float32
	stun, slow, modDur                    float32
	networth, abilLvl, bType, evLoc       uint32
	assists                               string
	reinc, longRange, ult                 bool
}

// ワード 1 本
type ward struct {
	idx            int32
	kind           string // obs / sen
	team           float64
	x, y           float64
	placed, gone   float64
	owner          float64
	ownerHandle    float64
	havePos, isSet bool
}

func main() {
	every := flag.Uint("every", 30, "何 tick ごとに書くか")
	fields := flag.Bool("fields", false, "欄の名前を出して終わる")
	eventsPath := flag.String("events", "", "生死が変わった瞬間（tick 単位）を書く CSV")
	combatPath := flag.String("combat", "", "コンバットログの抜粋を書く CSV")
	objectsPath := flag.String("objects", "", "1 秒ごとの建物・Roshan・ワード数・陣営の合計を書く CSV")
	wardsPath := flag.String("wards", "", "ワード 1 本ごとの記録を書く CSV")
	flag.Parse()

	p, err := manta.NewStreamParser(bufio.NewReaderSize(os.Stdin, 1<<20))
	if err != nil {
		fmt.Fprintln(os.Stderr, "開けない:", err)
		os.Exit(3)
	}
	out := bufio.NewWriterSize(os.Stdout, 1<<20)
	defer out.Flush()
	fmt.Fprintln(out, "tick,net_tick,game_time,t,hero,hero_id,player_id,team,x,y,wx,wy,hp,max_hp,mana,max_mana,level,alive,"+
		"networth,gold,xp,lh,dn,kills,deaths,assists,respawn_s,buyback_cd,reliable_gold,unreliable_gold,streak")

	var last uint32
	lastOD := -1 << 30
	var rows, samples int
	var rules, pres *manta.Entity
	dataTeam := map[int32]*manta.Entity{} // 実体の番号 → CDOTA_DataRadiant / CDOTA_DataDire
	dumped := false

	// ホーン（0:00）の Valve の時計での時刻。state 5 以降の値を最終とする
	hornTime := func() float64 {
		if rules == nil {
			return 0
		}
		state, _ := num(rules, "m_pGameRules.m_nGameState")
		horn, _ := num(rules, "m_pGameRules.m_flGameStartTime")
		if horn <= 0 && state == 4 {
			horn, _ = num(rules, "m_pGameRules.m_flStateTransitionTime")
		}
		return horn
	}
	// 試合の時刻: ホーンからの秒。止めていた tick を除く（OpenDota と同じ考え方）。
	// 状態 4＝準備（ホーン前、負の時刻）、5＝試合中、6＝終了後（最後の 1 秒の死を落とさないため書く）。選択中は書かない。
	// 時刻はサーバーの tick（NetTick）で数える。デモの tick（p.Tick）は 17 秒ほどずれていた
	gameTime := func() (float64, bool) {
		if rules == nil {
			return 0, false
		}
		state, _ := num(rules, "m_pGameRules.m_nGameState")
		if state != 4 && state != 5 && state != 6 {
			return 0, false
		}
		paused, _ := num(rules, "m_pGameRules.m_nTotalPausedTicks")
		if pz, _ := num(rules, "m_pGameRules.m_bGamePaused"); pz == 1 {
			if ps, ok := num(rules, "m_pGameRules.m_nPauseStartTick"); ok && ps > 0 {
				paused += float64(p.NetTick) - ps
			}
		}
		return (float64(p.NetTick)-paused)/30 - hornTime(), true
	}

	// OpenDota の整数の時刻（odota/parser Parse.java: time = round((tick − 止めた tick)/30)、gameStartTime = round(m_flGameStartTime)、
	// 1 秒ごとの行はこの整数が次の値に達した最初の tick で書く）。同じ規則で書けば、分ごとの値の照合が定義上そろう（11/8 に 2 試合で確かめた）
	odTime := func() (int, bool) {
		if rules == nil {
			return 0, false
		}
		state, _ := num(rules, "m_pGameRules.m_nGameState")
		if state != 4 && state != 5 && state != 6 {
			return 0, false
		}
		paused, _ := num(rules, "m_pGameRules.m_nTotalPausedTicks")
		if pz, _ := num(rules, "m_pGameRules.m_bGamePaused"); pz == 1 {
			if ps, ok := num(rules, "m_pGameRules.m_nPauseStartTick"); ok && ps > 0 {
				paused += float64(p.NetTick) - ps
			}
		}
		return int(math.Round((float64(p.NetTick)-paused)/30)) - int(math.Round(hornTime())), true
	}

	var ev *bufio.Writer
	if *eventsPath != "" {
		f, err := os.Create(*eventsPath)
		if err != nil {
			fmt.Fprintln(os.Stderr, "events を書けない:", err)
			os.Exit(3)
		}
		defer f.Close()
		ev = bufio.NewWriter(f)
		defer ev.Flush()
		fmt.Fprintln(ev, "tick,net_tick,game_time,hero,player_id,team,from,to,x,y,hp")
	}
	// 本物のヒーローは CDOTA_PlayerResource の m_vecPlayerTeamData.000k.m_hSelectedHero（k＝0〜9）が指す実体だけ。
	// 名前が CDOTA_Unit_Hero_ で始まっても、Beastmaster の猪・鷹、Meepo の分身、Arc Warden の分身、幻影は数えない
	heroK := map[int32]int{}
	heroID := map[int]float64{}
	refreshHeroes := func() {
		if pres == nil {
			return
		}
		for k := 0; k < 10; k++ {
			h, ok := num(pres, fmt.Sprintf("m_vecPlayerTeamData.%04d.m_hSelectedHero", k))
			if !ok || h == invalidHandle {
				continue
			}
			heroK[int32(uint64(h)&0x3FFF)] = k
			if id, ok := num(pres, fmt.Sprintf("m_vecPlayerTeamData.%04d.m_nSelectedHeroID", k)); ok {
				heroID[k] = id
			}
		}
	}
	// 選手の経済は CDOTA_DataRadiant / CDOTA_DataDire の m_vecDataTeam.j。m_nPlayerID は 2 刻みの選手番号
	// （Radiant 0,2,…,8、Dire 10,…,18。ヒーローの m_iPlayerID と同じ。PlayerResource の 0〜9 とは違う。11/8 に確かめた）
	type dref struct {
		e *manta.Entity
		j int
	}
	dataOf := map[int]dref{}
	refreshData := func() {
		if len(dataOf) == 10 {
			return
		}
		for _, e := range dataTeam {
			for j := 0; j < 10; j++ {
				pid, ok := num(e, fmt.Sprintf("m_vecDataTeam.%04d.m_nPlayerID", j))
				if !ok {
					break
				}
				dataOf[int(pid)] = dref{e, j}
			}
		}
	}
	dnum := func(k int, field string) float64 {
		d, ok := dataOf[k]
		if !ok {
			return -1
		}
		v, ok := num(d.e, fmt.Sprintf("m_vecDataTeam.%04d.%s", d.j, field))
		if !ok {
			return -1
		}
		return v
	}
	pnum := func(k int, field string) float64 {
		v, ok := num(pres, fmt.Sprintf("m_vecPlayerTeamData.%04d.%s", k, field))
		if !ok {
			return -1
		}
		return v
	}

	// 建物・Roshan・ワードの実体を追う
	buildings := map[int32]*manta.Entity{}
	bName := map[int32]string{}
	var bOrder []int32 // 列の順（最初の書き出しで固定）
	bGone := map[int32]bool{}
	var roshan *manta.Entity
	wards := map[int32]*ward{}
	var wardList []*ward
	lifePrev := map[int32]float64{}
	nEvents := 0
	nameOf := func(e *manta.Entity) string {
		if i, ok := num(e, "m_iUnitNameStringTableIndex"); ok {
			if s, ok := p.LookupStringByIndex("UnitNames", int32(i)); ok && s != "" {
				return s
			}
		}
		return fmt.Sprintf("%s_%d", e.GetClassName(), e.GetIndex())
	}

	p.OnEntity(func(e *manta.Entity, op manta.EntityOp) error {
		c := e.GetClassName()
		switch c {
		case "CDOTAGamerulesProxy":
			if rules == nil {
				rules = e
			}
			return nil
		case "CDOTA_PlayerResource":
			if pres == nil {
				pres = e
			}
			return nil
		case "CDOTA_DataRadiant", "CDOTA_DataDire":
			dataTeam[e.GetIndex()] = e
			return nil
		case "CDOTA_BaseNPC_Tower", "CDOTA_BaseNPC_Barracks", "CDOTA_BaseNPC_Fort":
			if op&manta.EntityOpDeleted != 0 {
				bGone[e.GetIndex()] = true
				delete(buildings, e.GetIndex())
			} else {
				buildings[e.GetIndex()] = e
				if _, ok := bName[e.GetIndex()]; !ok {
					bName[e.GetIndex()] = nameOf(e)
				}
			}
			return nil
		case "CDOTA_Unit_Roshan":
			if op&manta.EntityOpDeleted != 0 {
				if roshan != nil && roshan.GetIndex() == e.GetIndex() {
					roshan = nil
				}
			} else {
				roshan = e
			}
			return nil
		case "CDOTA_NPC_Observer_Ward", "CDOTA_NPC_Observer_Ward_TrueSight":
			w := wards[e.GetIndex()]
			gt, okT := gameTime()
			if op&manta.EntityOpDeleted != 0 {
				if w != nil && w.gone == 0 && okT {
					w.gone = gt
				}
				delete(wards, e.GetIndex())
				return nil
			}
			if w == nil {
				w = &ward{idx: e.GetIndex(), kind: "obs", placed: gt, gone: 0}
				if c == "CDOTA_NPC_Observer_Ward_TrueSight" {
					w.kind = "sen"
				}
				if !okT {
					w.placed = -9999
				}
				wards[e.GetIndex()] = w
				wardList = append(wardList, w)
			}
			if !w.havePos {
				if x, y, ok := cellOf(e); ok {
					w.x, w.y, w.havePos = x, y, true
				}
			}
			if !w.isSet {
				if t, ok := num(e, "m_iTeamNum"); ok {
					w.team = t
				}
				w.owner = -1
				if o, ok := num(e, "m_nPlayerOwnerID"); ok {
					w.owner = o
					w.isSet = true
				} else if h, ok := num(e, "m_hOwnerEntity"); ok && h != invalidHandle {
					w.ownerHandle = h
					w.isSet = true
				}
			}
			if life, ok := num(e, "m_lifeState"); ok && life != 0 && w.gone == 0 && okT {
				w.gone = gt
			}
			return nil
		}
		if ev == nil || !strings.HasPrefix(c, "CDOTA_Unit_Hero_") {
			return nil
		}
		if _, ok := heroK[e.GetIndex()]; !ok {
			refreshHeroes()
			if _, ok := heroK[e.GetIndex()]; !ok {
				return nil
			}
		}
		life, ok := num(e, "m_lifeState")
		if !ok {
			return nil
		}
		idx := e.GetIndex()
		prev, seen := lifePrev[idx]
		lifePrev[idx] = life
		if !seen || prev == life {
			return nil
		}
		gt, ok := gameTime()
		if !ok {
			return nil
		}
		x, y, _ := cellOf(e)
		pid, _ := first(e, "m_iPlayerID", "m_nPlayerID")
		team, _ := num(e, "m_iTeamNum")
		hp, _ := num(e, "m_iHealth")
		fmt.Fprintf(ev, "%d,%d,%.2f,%s,%.0f,%.0f,%.0f,%.0f,%.2f,%.2f,%.0f\n", p.Tick, p.NetTick, gt,
			strings.TrimPrefix(c, "CDOTA_Unit_Hero_"), pid, team, prev, life, x, y, hp)
		nEvents++
		return nil
	})

	// コンバットログ
	var combat []clRow
	if *combatPath != "" {
		clName := func(i uint32) string {
			s, _ := p.LookupStringByIndex("CombatLogNames", int32(i))
			return s
		}
		keepMod := func(s string) bool {
			if s == "modifier_smoke_of_deceit" {
				return true
			}
			for _, k := range []string{"modifier_item_aegis", "black_king_bar", "fountain_glyph", "modifier_rune_", "modifier_teleporting", "tpscroll", "boots_of_travel", "modifier_item_cheese", "modifier_item_ultimate_scepter_consumed", "modifier_item_aghanims_shard"} {
				if strings.Contains(s, k) {
					return true
				}
			}
			return false
		}
		p.Callbacks.OnCMsgDOTACombatLogEntry(func(m *dota.CMsgDOTACombatLogEntry) error {
			t := int32(m.GetType())
			tgt := clName(m.GetTargetName())
			keep := false
			switch t {
			case 4, 17, 12, 18, 30, 15, 16, 8, 10, 11, 5, 6, 21, 25, 9, 41, 20, 23, 36:
				keep = true
			case 0, 1: // ダメージ・回復は対象がヒーロー（幻影でない）・建物・Roshan・クーリエのときだけ
				keep = (m.GetIsTargetHero() && !m.GetIsTargetIllusion()) || m.GetIsTargetBuilding() ||
					strings.Contains(tgt, "roshan") || strings.Contains(tgt, "courier") || strings.Contains(tgt, "miniboss")
			case 2, 3:
				keep = keepMod(clName(m.GetInflictorName()))
			}
			if !keep {
				return nil
			}
			as := make([]string, 0, 4)
			for _, a := range m.GetAssistPlayers() {
				as = append(as, fmt.Sprint(a))
			}
			combat = append(combat, clRow{ts: m.GetTimestamp(), typ: t, atk: clName(m.GetAttackerName()), tgt: tgt, infl: clName(m.GetInflictorName()),
				value: m.GetValue(), health: m.GetHealth(), atkHero: m.GetIsAttackerHero(), tgtHero: m.GetIsTargetHero(),
				atkIll: m.GetIsAttackerIllusion(), tgtIll: m.GetIsTargetIllusion(), bld: m.GetIsTargetBuilding(),
				atkTeam: m.GetAttackerTeam(), tgtTeam: m.GetTargetTeam(), goldR: m.GetGoldReason(), xpR: m.GetXpReason(),
				x: m.GetLocationX(), y: m.GetLocationY(), stun: m.GetStunDuration(), slow: m.GetSlowDuration(), modDur: m.GetModifierDuration(),
				networth: m.GetNetworth(), abilLvl: m.GetAbilityLevel(), bType: m.GetBuildingType(), evLoc: m.GetEventLocation(),
				assists: strings.Join(as, "|"), reinc: m.GetWillReincarnate(), longRange: m.GetLongRangeKill(), ult: m.GetIsUltimateAbility()})
			return nil
		})
	}

	if *combatPath != "" {
		p.Callbacks.OnCDOTAUserMsg_ChatEvent(func(m *dota.CDOTAUserMsg_ChatEvent) error {
			gt, ok := gameTime()
			if !ok {
				return nil
			}
			name := strings.TrimPrefix(dota.DOTA_CHAT_MESSAGE_name[int32(m.GetType())], "CHAT_MESSAGE_")
			// ts はゲームの時計に合わせる（ホーン＋試合の時刻）。value はメッセージごとの値（塔の陣営、Roshan を倒した陣営など）
			combat = append(combat, clRow{ts: float32(gt + hornTime()), typ: -2, atk: fmt.Sprint(m.GetPlayerid_1()), tgt: fmt.Sprint(m.GetPlayerid_2()),
				infl: "CHAT_" + name, value: m.GetValue()})
			return nil
		})
	}

	var obj *bufio.Writer
	if *objectsPath != "" {
		f, err := os.Create(*objectsPath)
		if err != nil {
			fmt.Fprintln(os.Stderr, "objects を書けない:", err)
			os.Exit(3)
		}
		defer f.Close()
		obj = bufio.NewWriter(f)
		defer obj.Flush()
	}

	p.Callbacks.OnCNETMsg_Tick(func(m *dota.CNETMsg_Tick) error {
		t := p.Tick
		od, odOK := odTime()
		if *every == 30 && !*fields {
			// 1 秒ごと: OpenDota と同じ瞬間（整数の時刻が変わった最初の tick）
			if !odOK || od == lastOD {
				return nil
			}
		} else if last != 0 && t-last < uint32(*every) {
			return nil
		}
		var heroes []*manta.Entity
		if *fields {
			heroes = p.FilterEntity(func(e *manta.Entity) bool { return strings.HasPrefix(e.GetClassName(), "CDOTA_Unit_Hero_") })
		} else {
			refreshHeroes()
			for idx := range heroK {
				if e := p.FindEntity(idx); e != nil && strings.HasPrefix(e.GetClassName(), "CDOTA_Unit_Hero_") {
					heroes = append(heroes, e)
				}
			}
			sort.Slice(heroes, func(i, j int) bool { return heroK[heroes[i].GetIndex()] < heroK[heroes[j].GetIndex()] })
		}
		if len(heroes) == 0 {
			return nil
		}
		last = t
		lastOD = od
		if *fields && !dumped {
			dumped = true
			dump := func(label string, e *manta.Entity) {
				keys := make([]string, 0)
				for k := range e.Map() {
					keys = append(keys, k)
				}
				sort.Strings(keys)
				fmt.Fprintf(os.Stderr, "== %s %s (%d 欄)\n", label, e.GetClassName(), len(keys))
				for _, k := range keys {
					fmt.Fprintf(os.Stderr, "  %s = %v\n", k, e.Get(k))
				}
			}
			dump("hero", heroes[0])
			if rules != nil {
				dump("rules", rules)
			}
			for _, w := range wards {
				if e := p.FindEntity(w.idx); e != nil {
					dump("ward", e)
					break
				}
			}
			out.Flush()
			os.Exit(0)
		}
		g, ok := gameTime()
		if !ok {
			return nil
		}
		refreshData()
		horn := hornTime()
		if os.Getenv("DOTAPOS_DEBUG") != "" && samples%600 == 0 {
			fmt.Fprintf(os.Stderr, "[debug] sample %d gt %.1f dataTeam %d dataOf %d\n", samples, g, len(dataTeam), len(dataOf))
			for idx, e := range dataTeam {
				fmt.Fprintf(os.Stderr, "  data idx=%d class=%s:", idx, e.GetClassName())
				for j := 0; j < 10; j++ {
					pid, ok := num(e, fmt.Sprintf("m_vecDataTeam.%04d.m_nPlayerID", j))
					if !ok {
						fmt.Fprintf(os.Stderr, " [%d:none]", j)
						continue
					}
					nw, _ := num(e, fmt.Sprintf("m_vecDataTeam.%04d.m_iNetWorth", j))
					fmt.Fprintf(os.Stderr, " [%d:pid=%.0f nw=%.0f]", j, pid, nw)
				}
				fmt.Fprintln(os.Stderr)
			}
			for k, d := range dataOf {
				fmt.Fprintf(os.Stderr, "  dataOf[%d] = idx %d j %d\n", k, d.e.GetIndex(), d.j)
			}
			for _, w := range wards {
				if e := p.FindEntity(w.idx); e != nil {
					keys := []string{}
					for k2 := range e.Map() {
						if strings.Contains(k2, "Owner") || strings.Contains(k2, "Player") || strings.Contains(k2, "hero") || strings.Contains(k2, "Hero") {
							keys = append(keys, fmt.Sprintf("%s=%v", k2, e.Get(k2)))
						}
					}
					fmt.Fprintf(os.Stderr, "  ward %s idx %d: %v\n", w.kind, w.idx, keys)
				}
			}
		}
		gt := fmt.Sprintf("%.2f", g)
		samples++
		for _, e := range heroes {
			x, y, ok := cellOf(e)
			if !ok {
				continue
			}
			k := heroK[e.GetIndex()]
			pid, _ := first(e, "m_iPlayerID", "m_nPlayerID")
			team, _ := num(e, "m_iTeamNum")
			hp, _ := num(e, "m_iHealth")
			mhp, _ := num(e, "m_iMaxHealth")
			mana, _ := num(e, "m_flMana")
			mmana, _ := num(e, "m_flMaxMana")
			lvl, _ := num(e, "m_iCurrentLevel")
			life, _ := num(e, "m_lifeState")
			alive := 0
			if life == 0 {
				alive = 1
			}
			ip := int(pid)
			bb := dnum(ip, "m_flBuybackCooldownTime")
			bbcd := 0.0
			if bb > 0 {
				bbcd = bb - horn - g
				if bbcd < 0 {
					bbcd = 0
				}
			}
			name := strings.TrimPrefix(e.GetClassName(), "CDOTA_Unit_Hero_")
			fmt.Fprintf(out, "%d,%d,%s,%d,%s,%.0f,%.0f,%.0f,%.2f,%.2f,%.0f,%.0f,%.0f,%.0f,%.0f,%.0f,%.0f,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%.1f,%s,%s,%s\n",
				t, p.NetTick, gt, od, name, heroID[k], pid, team, x, y, x*128-16384, y*128-16384,
				hp, mhp, mana, mmana, lvl, alive,
				f0(dnum(ip, "m_iNetWorth")), f0(dnum(ip, "m_iTotalEarnedGold")), f0(dnum(ip, "m_iTotalEarnedXP")),
				f0(dnum(ip, "m_iLastHitCount")), f0(dnum(ip, "m_iDenyCount")),
				f0(pnum(k, "m_iKills")), f0(pnum(k, "m_iDeaths")), f0(pnum(k, "m_iAssists")), f0(pnum(k, "m_iRespawnSeconds")),
				bbcd, f0(dnum(ip, "m_iReliableGold")), f0(dnum(ip, "m_iUnreliableGold")), f0(pnum(k, "m_iStreak")))
			rows++
		}
		if obj != nil {
			if bOrder == nil {
				for idx := range buildings {
					bOrder = append(bOrder, idx)
				}
				sort.Slice(bOrder, func(i, j int) bool {
					if bName[bOrder[i]] == bName[bOrder[j]] {
						return bOrder[i] < bOrder[j]
					}
					return bName[bOrder[i]] < bName[bOrder[j]]
				})
				seenName := map[string]int{}
				for _, idx := range bOrder {
					seenName[bName[idx]]++
					if n := seenName[bName[idx]]; n > 1 {
						bName[idx] = fmt.Sprintf("%s#%d", bName[idx], n)
					}
				}
				cols := []string{"tick", "game_time", "t", "radiant_networth", "dire_networth", "radiant_xp", "dire_xp", "radiant_alive", "dire_alive",
					"roshan_alive", "roshan_hp", "roshan_max_hp", "roshan_phase", "roshan_phase_end",
					"obs_radiant", "obs_dire", "sen_radiant", "sen_dire"}
				for _, idx := range bOrder {
					cols = append(cols, bName[idx])
				}
				fmt.Fprintln(obj, strings.Join(cols, ","))
			}
			var nw, xp [2]float64
			var alive [2]int
			for _, e := range heroes {
				pid, _ := first(e, "m_iPlayerID", "m_nPlayerID")
				ip := int(pid)
				team, _ := num(e, "m_iTeamNum")
				side := int(team) - 2
				if side < 0 || side > 1 {
					continue
				}
				if v := dnum(ip, "m_iNetWorth"); v >= 0 {
					nw[side] += v
				}
				if v := dnum(ip, "m_iTotalEarnedXP"); v >= 0 {
					xp[side] += v
				}
				if life, _ := num(e, "m_lifeState"); life == 0 {
					alive[side]++
				}
			}
			rAlive, rHP, rMax := 0, 0.0, 0.0
			if roshan != nil {
				if life, _ := num(roshan, "m_lifeState"); life == 0 {
					rAlive = 1
				}
				rHP, _ = num(roshan, "m_iHealth")
				rMax, _ = num(roshan, "m_iMaxHealth")
			}
			phase, phaseEnd := -1.0, -1.0
			for _, e := range dataTeam {
				if v, ok := num(e, "m_eRoshanPhase"); ok {
					phase = v
					if pe, ok := num(e, "m_flRoshanPhaseEndTime"); ok && pe > 0 {
						phaseEnd = pe - horn
					}
					break
				}
			}
			var wc [4]int
			for _, w := range wards {
				if w.gone != 0 {
					continue
				}
				i := 0
				if w.kind == "sen" {
					i = 2
				}
				if w.team == 3 {
					i++
				}
				wc[i]++
			}
			fmt.Fprintf(obj, "%d,%s,%d,%.0f,%.0f,%.0f,%.0f,%d,%d,%d,%.0f,%.0f,%.0f,%.1f,%d,%d,%d,%d", t, gt, od, nw[0], nw[1], xp[0], xp[1], alive[0], alive[1],
				rAlive, rHP, rMax, phase, phaseEnd, wc[0], wc[1], wc[2], wc[3])
			for _, idx := range bOrder {
				e := buildings[idx]
				hp := 0.0
				if e != nil && !bGone[idx] {
					if life, _ := num(e, "m_lifeState"); life == 0 {
						hp, _ = num(e, "m_iHealth")
					}
				}
				fmt.Fprintf(obj, ",%.0f", hp)
			}
			fmt.Fprintln(obj)
		}
		return nil
	})

	if err := p.Start(); err != nil && err != io.EOF {
		out.Flush()
		fmt.Fprintln(os.Stderr, "途中で失敗:", err)
		os.Exit(4)
	}
	out.Flush()
	horn := hornTime()
	if *combatPath != "" {
		f, err := os.Create(*combatPath)
		if err != nil {
			fmt.Fprintln(os.Stderr, "combat を書けない:", err)
			os.Exit(3)
		}
		w := bufio.NewWriterSize(f, 1<<20)
		fmt.Fprintln(w, "game_time,ts,type,attacker,target,inflictor,value,health,atk_hero,tgt_hero,atk_illusion,tgt_illusion,tgt_building,"+
			"atk_team,tgt_team,gold_reason,xp_reason,x,y,stun,slow,networth,ability_level,assists,modifier_duration,building_type,event_location,reincarnate,long_range,ultimate")
		typeName := func(r clRow) string {
			if r.typ == -2 {
				return r.infl
			}
			return strings.TrimPrefix(dota.DOTA_COMBATLOG_TYPES_name[r.typ], "DOTA_COMBATLOG_")
		}
		inflOf := func(r clRow) string {
			if r.typ == -2 {
				return ""
			}
			return r.infl
		}
		sort.SliceStable(combat, func(i, j int) bool { return combat[i].ts < combat[j].ts })
		b2i := func(b bool) int {
			if b {
				return 1
			}
			return 0
		}
		for _, r := range combat {
			x, y := "", ""
			if r.x != 0 || r.y != 0 {
				x = fmt.Sprintf("%.2f", (float64(r.x)+16384)/128)
				y = fmt.Sprintf("%.2f", (float64(r.y)+16384)/128)
			}
			fmt.Fprintf(w, "%.2f,%.2f,%s,%s,%s,%s,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%s,%s,%.2f,%.2f,%d,%d,%s,%.1f,%d,%d,%d,%d,%d\n",
				float64(r.ts)-horn, r.ts, typeName(r), r.atk, r.tgt, inflOf(r), r.value, r.health,
				b2i(r.atkHero), b2i(r.tgtHero), b2i(r.atkIll), b2i(r.tgtIll), b2i(r.bld), r.atkTeam, r.tgtTeam, r.goldR, r.xpR, x, y, r.stun, r.slow,
				r.networth, r.abilLvl, r.assists, r.modDur, r.bType, r.evLoc, b2i(r.reinc), b2i(r.longRange), b2i(r.ult))
		}
		w.Flush()
		f.Close()
	}
	if *wardsPath != "" {
		f, err := os.Create(*wardsPath)
		if err != nil {
			fmt.Fprintln(os.Stderr, "wards を書けない:", err)
			os.Exit(3)
		}
		w := bufio.NewWriter(f)
		fmt.Fprintln(w, "kind,team,owner_player_id,x,y,placed,gone,entity")
		refreshHeroes()
		for _, wd := range wardList {
			if wd.owner < 0 && wd.ownerHandle != 0 {
				if he := p.FindEntity(int32(uint64(wd.ownerHandle) & 0x3FFF)); he != nil {
					if pid, ok := first(he, "m_iPlayerID", "m_nPlayerID"); ok {
						wd.owner = pid
					}
				}
			}
			gone := ""
			if wd.gone != 0 {
				gone = fmt.Sprintf("%.2f", wd.gone)
			}
			fmt.Fprintf(w, "%s,%.0f,%.0f,%.2f,%.2f,%.2f,%s,%d\n", wd.kind, wd.team, wd.owner, wd.x, wd.y, wd.placed, gone, wd.idx)
		}
		w.Flush()
		f.Close()
	}
	fmt.Fprintf(os.Stderr, "DONE tick %d、書いた時点 %d、行 %d、生死の変わり目 %d、コンバットログ %d、ワード %d、建物 %d、ホーン %.2f\n",
		p.Tick, samples, rows, nEvents, len(combat), len(wardList), len(bOrder), horn)
}
