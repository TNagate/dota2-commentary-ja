# Dota 2 プロの試合の日本語解説（AI）

公開ページ: https://tnagate.github.io/dota2-commentary-ja/

AI が試合の記録（OpenDota・Valve の公開データ・リプレイ）だけから事実の表を作り、勝つ確率の動きから場面を選んで日本語の解説を書きます。
各文は事実の表と機械で照合し、別の AI が点検してから載せています。数字の根拠は各ページの脚注にあります。

非商用の個人の学習の計画です。

## コード（src/）

| フォルダ・ファイル | 役割 |
|---|---|
| `collect.py`・`match_store.py`・`minutes.py`・`build_minutes.py`・`replay_catchup.py` | OpenDota からプロの試合の記録とリプレイを集める |
| `dotapos/` | リプレイ（Go の manta）から 1 秒ごとの位置・戦闘・建物・ワードを取り出す |
| `winprob.py`・`winprob2.py`・`value_events.py`・`value_table.py`・`dota_seal.py` | 試合中の勝つ確率の型と出来事の価値の表、検証の期間の封印 |
| `pregame*.py` | 試合前の勝つ確率（選手の Elo ほか） |
| `forward_dota.py`・`forward_late.py` | 固めた型を新しい試合で前向きに採点する |
| `commentary/` | 転換点の選び方 → 事実の表 → 解説の文 → 機械の照合 → 別の AI の点検 → 書き直し → ページ |

ライセンスは MIT（`LICENSE`）。動かすには OpenDota の公開 API、rclone の置き場、Claude Code の CLI などが要ります（設定は各ファイルの冒頭）。
