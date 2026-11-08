# tz230-firstlight

野辺山 45m 電波望遠鏡の TZ 受信機 230 GHz モード（DSB）のファーストライト用。
nobs で作った H40 の観測指示書（`.start`・`.dat`）を、230 GHz 用に書き換える変換スクリプトを置く。

- 方針: [`docs/policy.md`](docs/policy.md)
- 指示書の周波数まわりの欄と式: [`docs/fields.md`](docs/fields.md)

## 使い方

```
python tz230conv.py /cosmos3/45m/obstable/<ユーザー>/<プロジェクト>/ori-kl.start
```

- 既定では、`ori-kl.start` と `ori-kl.dat` を**その場で上書き**する。元のファイルは nobs ですぐ作り直せるので退避しない。
- 変換済みのファイルを渡すと止まる（二重に変換しない）。やり直すときは nobs で作り直してから変換する。
- `-o 出力先のディレクトリ` を付けると、元と同じファイル名でそこに書き、同じ名前のほかの拡張子のファイルもコピーする。
- 書き換えた行の差分と、1st LO・2nd LO・窓・各 Array の線の位置を表示する。
- 知らない形の行があったり、2nd LO・窓・`START_CHAN` が範囲の外だったりすると、何も書かずに止まる。
- 周波数の設定は [`config/tz230.json`](config/tz230.json)。別の設定は `-c` で渡す。
- Python 2.6 以上・Python 3 の標準ライブラリだけで動く（観測所の計算機の `python` は 2 系）。

## テスト

```
python -m unittest discover -s tests   # Python 2.7 以上・3
```

H40 のサンプル（[`samples/h40-ori-kl/`](samples/h40-ori-kl/)、観測者 ID は `obsuser` に置き換えてある）で、
nobs が出した `FQDAT_FQ` と 2nd LO を式から再現できることも確かめている。
