# tz230-firstlight

野辺山 45m 電波望遠鏡の TZ 受信機 230 GHz モード（DSB）のファーストライト用。
nobs で作った H40 の観測指示書（`.start`・`.dat`）を、230 GHz 用に書き換える変換スクリプトを置く。

- 方針: [`docs/policy.md`](docs/policy.md)
- 指示書の周波数まわりの欄と式: [`docs/fields.md`](docs/fields.md)

## 使い方

```
python3 tz230conv.py 指示書のディレクトリ/ori-kl.start 出力先のディレクトリ
```

- 出力は元と同じファイル名（`ori-kl.start`・`ori-kl.dat` など）。同じ名前のほかの拡張子のファイルはそのままコピーする。
- 入力と同じディレクトリには書かない。出力先に同名のファイルがあれば止まる（`--force` で上書き）。
- 書き換えた行の差分と、1st LO・2nd LO・窓・各 Array の線の位置を表示する。
- 周波数の設定は [`config/tz230.json`](config/tz230.json)。別の設定は `-c` で渡す。
- Python 3.6 以上の標準ライブラリだけで動く。

## テスト

```
python3 -m unittest discover -s tests
```

H40 のサンプル（[`samples/h40-ori-kl/`](samples/h40-ori-kl/)、観測者 ID は `obsuser` に置き換えてある）で、
nobs が出した `FQDAT_FQ` と 2nd LO を式から再現できることも確かめている。
