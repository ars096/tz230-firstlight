# 変換で書き換える項目（H40 → TZ 230 GHz）

nobs が出した H40 の指示書（`.start`・`.dat`）のうち、`tz230conv.py` がどこを書き換えるかの一覧。
値はファーストライト時の設定（[`config/tz230.json`](../config/tz230.json)）のもの。
例の「元」は [`samples/h40-ori-kl/`](../samples/h40-ori-kl/) の値。
決めた理由は [`policy.md`](policy.md)、式は [`fields.md`](fields.md)、知見のまとめは [`lessons.md`](lessons.md) を見る。

## `.start`

### 書き換える行

| 場所 | 元（H40） | 変換後（TZ 230 GHz） | 備考 |
|---|---|---|---|
| 冒頭の MLPPS（`OPEN MLPPS` 〜 `CLOSE MLPPS`、`SLEEP` を含む） | `losw_h40.py` で LO スイッチを切り替える | 各行の頭に `# ` を付けてコメントアウト | TZ 230 GHz の LO は指示書の外で設定する |
| SG の名前（`SET`・`GROUP` の行） | `SYNTHE_K` | `SYNTHE_N` | `GROUP GRPTRK …`・`GROUP SYNTHE …` も変える |
| `SET SYNTHE_N OBS_FREQ` | `4.3E10` | `2.30538E11` | CO(2–1)。1st LO = OBS_FREQ − FREQ_IF1 = 223.2 GHz |
| `SET SYNTHE_N FREQ_IF1` | `6.0E9` | `7.338E9` | |
| `SET SYNTHE_N RX_NAME` | `'H40'` | `'TZ230DSB'` | |
| `SET SYNTHE_N SIDBD_TYP` | `'USB'` | `'USB'` | ドップラー追尾するのは USB の CO |
| `SET ANT POINTING` | `'H40'` | `'TZ2'` | |
| `SET RXT OBS_FREQ` | `(4.3E10,0.0,…)` | `(2.30538E11,0.0,…)` | 8 枠の 1 つ目だけ。SYNTHE と同じ |
| `SET RXT FREQ_IF1` | `(6.0E9,0.0,…)` | `(7.338E9,0.0,…)` | 同上 |
| `SET RXT RX_NAME` | `'H40,,,,,,,'` | `'TZ230DSB,,,,,,,'` | 同上 |
| `SET SAM45 QL_RMSARY` | `'A01'` | `'A11'` | |

### `SET SAM45` の Array ごとのリスト

使う Array は **A11（USB、CO(2–1)）と A12（LSB、SiO v=1 (5–4)）**。

| 項目 | A11 | A12 | 備考 |
|---|---|---|---|
| `SIDBD_TYP` | `USB` | `LSB` | |
| `REF_NUM` | `14` | `14` | `SYNTHE_N` の番号（H40 の `SYNTHE_K` は 11） |
| `RX_NAME` | `TZ2V` | `TZ2V` | IF を TZ2V のポートから取る |
| `REST_FREQ` | `2.30538E11` | `2.1559595E11` | 線の静止周波数 |
| `FQDAT_F0` | `2.30538E11` | `2.1559595E11` | `REST_FREQ` と同じ |
| `OBS_FREQ` | `2.30538E11` | `2.15862E11` | LSB は 1st LO − FREQ_IF1（書き方は未決、`policy.md`） |
| `FREQ_IF1` | `7.338E9` | `7.338E9` | |
| `OBS_BAND` | `1.25E8` | `1.25E8` | 窓の幅から計算（窓を変えないので元と同じ） |
| `FQDAT_FQ`（2 つずつ） | `2.3047552E11, 2.3060048E11` | `2.1565843E11, 2.1553347E11` | ch 4096 と ch 1 の RF。2nd LO から計算 |
| `ARRAY`・`IFATT`・`CH_BIND`・`FQDAT_CH`・`CH_RANGE` など | 元の値 | 元の値 | 値は変えない |

**A11・A12 以外の Array は、32 個・64 個のリストすべてでデフォルト値にする**
（文字列は空、小数（`.` か `E` を含むリスト）は `0.0`、整数は `0`）。
上の表の項目のほか、`ARRAY`・`IFATT`・`CH_BIND`・`FQDAT_CH`・`CH_RANGE`・`FREQ_INTVAL`・`MULT_NUM` も対象になる。

### 変えない行

- `%` と `#` で始まるコメントの行（`% OBS_FREQn=…` などのヘッダも含む）
- 観測の手順（`EXECUTE …`）、マップ・ポインティングのオフセット、天体の位置
  - `EXECUTE IFATT CMD(5,5,…)` も元のまま（A11・A12 に効くので問題ない）
- SAM45 の窓: `START_CHAN_SUB1`・`END_CHAN_SUB1`・`CHAN_AVG_SUB1`（例では 245760〜278527、8 ch 平均 = 2nd IF 2.9375〜3.0625 GHz）
  - 設定の `window` で変えることもできる。`START_CHAN` は 64 の倍数でなければ止める
- SpW・サブアレイごとの 2 個のリスト（`INPUT_MODE`・`MISC_FLAGS_*` など）と `SRC_POS`
- `SET RXT SETUP_FILE`（`.dat` のファイル名はそのまま）

### 変換を止める条件

- `SYNTHE_x` が 1 つでない、MLPPS のブロックが 1 つでない
- 知らない周波数らしい `SET` の行がある
- `N_SPEC_WINDOW_SUB1` が 1 でない、`SUB_ARRAY` が `OFF` でない
- 必要な行（`SAM45 REF_NUM` など）がない
- 2nd LO が 8〜12 GHz の外、線が窓の外、`START_CHAN` が 64 の倍数でない
- 使う Array の `.dat` のスイッチ欄と `SAM45 RX_NAME` が食い違う
- すでに変換済み（コメント以外に `TZ230DSB` がある）

## `.dat`

入力の中身は使わず、**16 行すべてを設定から作り直す**。書式は
`Array 番号,スイッチ（6 文字）,サイドバンド,2nd LO（MHz、小数 3 桁）`。

| 行 | 元（H40） | 変換後 | 備考 |
|---|---|---|---|
| 01–02 | `H40   ,USB,9122.000` | `H20ch1,USB,10338.000` | 使わない。2nd LO は A11 と同じ値 |
| 03–04 | `H40   ,USB,9122.000` | `H20ch2,USB,10338.000` | 同上 |
| 05–08 | `H40   ,USB,9122.000` | `#     ,,` | 使わない行。カンマ 3 つ、周波数なし |
| 09–10 | `H40   ,USB,8821.000` | `H40   ,USB,10338.000` | 使わない |
| **11** | `H40   ,USB,8821.000` | **`TZ2V  ,LSB,10338.000`** | CO(2–1)。線が 2nd IF 3 GHz に来る |
| **12** | `H40   ,USB,8821.000` | **`TZ2V  ,LSB,10604.050`** | SiO。同上 |
| 13–14 | `H40   ,USB,8821.000` | `H40   ,USB,10338.000` | 使わない |
| 15–16 | `H40   ,USB,8821.000` | `TZ2H  ,LSB,10338.000` | 使わない |

- スイッチ欄とサイドバンドは、受信機名とサイドバンドの組み合わせでスイッチを制御するためのもの。
  周波数の計算には使わないので、USB の線の A11 も `LSB` でよい。
- 2nd LO = 1st IF（線）+ 3 GHz を 1 kHz に丸めたもの。
  A11: 230.538 − 223.2 = 7.338 GHz → 10338.000 MHz。
  A12: 223.2 − 215.59595 = 7.60405 GHz → 10604.050 MHz。
- 使わない行にも 2nd LO が要る（空だと動かなかった）ので、A11 の値を入れる。
