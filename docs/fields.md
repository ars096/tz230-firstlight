# 指示書の周波数まわりの欄

nobs が出す `.start` と `.dat` のうち、周波数に関係する欄の一覧と、欄どうしの関係。
サンプル [`samples/h40-ori-kl/`](../samples/h40-ori-kl/)（H40・43 GHz、nobs 2.230904_public、2026-11-09 生成）から読み取った。

> TZ 86 GHz の指示書でも同じ形かどうかは未確認。

## サンプルの設定

| 項目 | 値 |
|---|---|
| 受信機 | H40（`FE11`、`SYNTHE_K`） |
| `OBS_FREQ` | 43.0 GHz |
| `FREQ_IF1` | 6.0 GHz |
| 1st LO のサイドバンド | USB（1st LO = 43.0 − 6.0 = 37.0 GHz、ドップラー補正の前） |
| A01〜A08 の静止周波数 | 43.122 GHz |
| A09〜A16 の静止周波数 | 42.821 GHz |
| 2nd LO（`.dat`） | A01〜A08: 9122 MHz、A09〜A16: 8821 MHz |
| SAM45 の窓 | `START_CHAN_SUB1` 245760〜`END_CHAN_SUB1` 278527、`CHAN_AVG_SUB1` 8 → 2nd IF 2.9375〜3.0625 GHz（125 MHz、4096 ch） |

## `.start` の欄

### ヘッダ（`%` 行）

| 欄 | 行 | 中身 |
|---|---|---|
| `% OBS_FREQn`（n = 1〜24） | 19〜42 | SG ごとの `OBS_FREQ`。使う SG の番号だけ値が入る（サンプルは n = 11） |
| `% FREQ_IFn`（n = 1〜24） | 43〜66 | SG ごとの `FREQ_IF1` |
| `% FEn` | 67〜90 | SG ごとの受信機名 |
| `% SAM45_FREQn`（n = 1〜32） | 117〜148 | Array ごとの静止周波数 |

### 命令

| 行 | 中身 | 230 GHz での扱い |
|---|---|---|
| `SEND MLPPS '/usr/bin/python /cosmos3/45m/nro/bin/losw_h40.py'` | 受信機ごとの LO スイッチの切り替え | 決定は policy.md |
| `SET ANT POINTING 'H40'` | ポインティングに使う受信機 | 変えない |
| `SET SYNTHE_K OBS_FREQ` | 観測する RF | 変える |
| `SET SYNTHE_K FREQ_IF1` | 1st IF | 変える（230 GHz での値は未決） |
| `SET SYNTHE_K SIDBD_TYP` | 1st LO のサイドバンド | 決定は policy.md |
| `SET SYNTHE_K RX_NAME` | 受信機名 | 決定は policy.md |
| `SET RXT SETUP_FILE 'ori-kl.dat'` | `.dat` のファイル名 | 変えない（ファイル名を揃えるため） |
| `SET RXT OBS_FREQ (…)` | SG の `OBS_FREQ` を 8 つ並べたもの | 変える |
| `SET RXT FREQ_IF1 (…)` | 同じく `FREQ_IF1` | 変える |
| `SET RXT RX_NAME` | 受信機名 | 決定は policy.md |
| `SET SAM45 SIDBD_TYP` | Array ごと（32） | 決定は policy.md |
| `SET SAM45 REST_FREQ` | Array ごとの静止周波数（32） | 変える |
| `SET SAM45 OBS_FREQ` | Array ごとの `OBS_FREQ`（32） | 変える |
| `SET SAM45 FREQ_IF1` | Array ごとの `FREQ_IF1`（32） | 変える |
| `SET SAM45 RX_NAME` | Array ごとの受信機名（32） | 決定は policy.md |
| `SET SAM45 FQDAT_F0` | Array ごとの周波数ラベルの基準（= 静止周波数、32） | 変える |
| `SET SAM45 FQDAT_FQ` | Array ごとに ch 4096 と ch 1 の RF（64） | 変える（計算し直す） |
| `SET SAM45 FQDAT_CH` | `FQDAT_FQ` に対応する ch（4096, 1 の組、64） | 変えない |
| `SET SAM45 OBS_BAND` | Array ごとの帯域（125 MHz） | 変える（窓の幅） |
| `SET SAM45 START_CHAN_SUB1`・`END_CHAN_SUB1`・`CHAN_AVG_SUB1` | 窓（サブアレイ 1、SpW 2 つ分） | 変える |
| `SET SAM45 QL_RMSARY` | QL で RMS を見る Array | 変える |

RF 周波数ラベルとは、`REST_FREQ`・`FQDAT_F0`・`FQDAT_FQ` のことと考えている。

## `.dat` の形

```
01,H40   ,USB,9122.000
```

Array 番号、共通 IF 系のスイッチ（受信機名）、サイドバンド、2nd LO（MHz）の 4 欄。16 行（A01〜A16）。

## 欄どうしの関係（サンプルで検算済み）

USB（1st LO が RF の下）、2nd LO が 1st IF の上の場合:

```
1st LO       = OBS_FREQ − FREQ_IF1                       （ドップラー補正の前）
1st IF(線)   = REST_FREQ − 1st LO
2nd LO       = 1st IF(線) + 3000 MHz                     （線が 2nd IF の 3 GHz に来る）
2nd IF       = 2nd LO − 1st IF                           （2nd IF では周波数が反転する）
RF(ch k)     = 1st LO + 2nd LO − f2(k)
f2(k)        = 2 GHz + (START_CHAN + (k − 0.5) × CHAN_AVG) × 2 GHz / 524288
```

- A01: 2nd LO = (43.122 − 37.0) GHz + 3 GHz = 9122 MHz。A09: (42.821 − 37.0) + 3 = 8821 MHz。どちらも `.dat` と一致。
- A01 の `FQDAT_FQ`: ch 1 = 43.18448 GHz、ch 4096 = 43.05952 GHz。上の式の値（43.184485、43.059515）を 10 kHz に丸めたものと一致。
- `FQDAT_FQ` はドップラー補正を入れない、静止系の値。

LSB の Array では `1st IF(線) = 1st LO − REST_FREQ`、`RF(ch k) = 1st LO − (2nd LO − f2(k))` となり、ch とともに RF が上がる。
変換スクリプト（`tz230conv.py` の `Plan`）はこの式で書き込む値を計算する。
