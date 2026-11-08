#!/usr/bin/env python3
"""nobs が出した H40 の指示書（.start・.dat）を TZ 230 GHz モード（DSB）用に書き換える。

    python3 tz230conv.py 入力の.start 出力先ディレクトリ [-c 設定.json] [--force]

- 出力は元と同じファイル名で、出力先ディレクトリに書く。同じ名前で拡張子だけ違うほかのファイルはそのままコピーする。
- 書き換えるのは決めた行・欄だけ。知らない形の行があれば止まる。
- 書き換えた行の差分を標準出力に出す。

方針は docs/policy.md、欄の一覧と周波数の式は docs/fields.md。
Python 3.6 以上の標準ライブラリだけで動く。
"""

import argparse
import decimal
import difflib
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config", "tz230.json")

# SAM45 の分光: 第 2 ナイキスト 2–4 GHz を 524,288 点に分ける
SAM45_F2_MIN = 2.0e9
SAM45_F2_MAX = 4.0e9
SAM45_NRAW = 524288
SAM45_DF = (SAM45_F2_MAX - SAM45_F2_MIN) / SAM45_NRAW
SAM45_NCH = 4096
N_ARRAY = 32

# 共通 IF 系の 2nd LO の設定範囲
LO2_MIN = 8.0e9
LO2_MAX = 10.0e9

START_CHAN_STEP = 64  # START_CHAN は 16 か 64 の倍数のはず。念のため 64 の倍数に限る

FQDAT_ROUND = 1.0e4  # FQDAT_FQ は 10 kHz に丸める（サンプルで確認）

# 周波数に関係しそうな欄。ここに当たる SET 行は、書き換えるか、下の PASS に入っていないと止まる。
SUSPECT = re.compile(r"FREQ|SIDBD|RX_NAME|FQDAT|BAND|CHAN|POINTING|SETUP_FILE")
PASS = {
    ("SYNTHE", "FREQ_SW"), ("SYNTHE", "FREQ_INTVAL"),
    ("SAM45", "FREQ_SW"), ("SAM45", "FREQ_INTVAL"), ("SAM45", "FREQ_PROF_SYNTH_SUB1"),
    ("SAM45", "FQDAT_CH"), ("SAM45", "CH_RANGE"), ("SAM45", "CH_BIND"),
    ("RXT", "SETUP_FILE"),  # ファイル名は揃えるので変えない
}
BIG_NUMBER = re.compile(r"\d(\.\d*)?E(9|1\d)\b")


class ConvError(Exception):
    pass


# ---------------------------------------------------------------- 数値の書式

def jfmt(x):
    """nobs（Java の Double.toString）と同じ書式。1e7 以上の正の数だけを扱う。"""
    if not x >= 1e7:
        raise ConvError("書式にできない値: %r" % x)
    d = decimal.Decimal(repr(float(x))).normalize()
    sign, digits, exp = d.as_tuple()
    mant = "%d.%s" % (digits[0], "".join(map(str, digits[1:])) or "0")
    return "%sE%d" % (mant, len(digits) - 1 + exp)


def parse_list(text):
    """'(a,b,c)' または "'a,b,c'" を (要素のリスト, 括弧の種類) にする。"""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        return text[1:-1].split(","), "()"
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1].split(","), "''"
    raise ConvError("リストの形ではない: %s" % text)


def join_list(items, kind):
    body = ",".join(items)
    return "(%s)" % body if kind == "()" else "'%s'" % body


# ---------------------------------------------------------------- 周波数の計算

class Plan:
    """設定から、書き込む値をすべて計算しておく。"""

    def __init__(self, cfg):
        s = cfg["synthe"]
        self.synthe = s["name"]
        self.synthe_rx = s["rx_name"]
        self.obs_freq = float(s["obs_freq"])
        self.freq_if1 = float(s["freq_if1"])
        self.synthe_sb = s["sideband"]
        if self.synthe_sb == "USB":
            self.lo1 = self.obs_freq - self.freq_if1
        elif self.synthe_sb == "LSB":
            self.lo1 = self.obs_freq + self.freq_if1
        else:
            raise ConvError("synthe.sideband は USB か LSB: %r" % self.synthe_sb)

        self.lo2 = float(cfg["lo2_mhz"]) * 1e6
        if not LO2_MIN <= self.lo2 <= LO2_MAX:
            raise ConvError("2nd LO %.3f MHz が設定範囲 %.0f–%.0f MHz の外"
                            % (self.lo2 / 1e6, LO2_MIN / 1e6, LO2_MAX / 1e6))

        w = cfg["window"]
        self.start_chan = int(w["start_chan"])
        self.end_chan = int(w["end_chan"])
        self.chan_avg = int(w["chan_avg"])
        if self.end_chan - self.start_chan + 1 != SAM45_NCH * self.chan_avg:
            raise ConvError("窓 %d–%d を %d ずつ束ねても %d ch にならない"
                            % (self.start_chan, self.end_chan, self.chan_avg, SAM45_NCH))
        if self.start_chan % START_CHAN_STEP:
            raise ConvError("START_CHAN %d が %d の倍数ではない" % (self.start_chan, START_CHAN_STEP))
        if not (0 <= self.start_chan and self.end_chan < SAM45_NRAW):
            raise ConvError("窓が 0–%d の外" % (SAM45_NRAW - 1))
        self.obs_band = (self.end_chan - self.start_chan + 1) * SAM45_DF

        self.pointing = cfg["pointing"]
        self.ql_rms_array = cfg["ql_rms_array"]
        self.dat_switch = cfg["dat_switch"]
        if len(self.dat_switch) != 16:
            raise ConvError("dat_switch は 16 行")

        self.arrays = {}
        for key, a in sorted(cfg["arrays"].items()):
            n = int(key)
            rest = float(a["rest_freq"])
            sb = a["sideband"]
            if1_line = rest - self.lo1 if sb == "USB" else self.lo1 - rest
            if if1_line <= 0:
                raise ConvError("A%02d: %s で静止周波数 %s が 1st LO の反対側" % (n, sb, rest))
            f2_line = self.lo2 - if1_line
            lo, hi = self.f2(0.5), self.f2(SAM45_NCH + 0.5)
            if not lo < f2_line < hi:
                raise ConvError("A%02d: 線が 2nd IF %.4f GHz で、窓 %.4f–%.4f GHz の外"
                                % (n, f2_line / 1e9, lo / 1e9, hi / 1e9))
            # SAM45 の OBS_FREQ・FREQ_IF1 は、その Array のサイドバンドで 1st LO と結ぶ
            if1 = self.freq_if1
            obs = self.lo1 + if1 if sb == "USB" else self.lo1 - if1
            self.arrays[n] = {
                "label": a.get("label", ""),
                "rest": rest, "sb": sb, "rx": a["rx_name"],
                "obs": obs, "if1": if1,
                "if1_line": if1_line, "f2_line": f2_line,
                "fq4096": self.rf(sb, SAM45_NCH), "fq1": self.rf(sb, 1),
            }

    def f2(self, k):
        """束ねたあとの ch k（1 始まり、端は 0.5 刻み）の中心の 2nd IF。"""
        return SAM45_F2_MIN + (self.start_chan + (k - 0.5) * self.chan_avg) * SAM45_DF

    def rf(self, sb, k):
        """ch k の RF（静止系、ドップラー補正なし）。2nd LO は 1st IF の上なので 2nd IF で反転する。"""
        if1 = self.lo2 - self.f2(k)
        f = self.lo1 + if1 if sb == "USB" else self.lo1 - if1
        return round(f / FQDAT_ROUND) * FQDAT_ROUND

    def summary(self):
        out = ["1st LO %.6f GHz（%s %s の OBS_FREQ %.6f − FREQ_IF1 %.6f、ドップラー補正の前）"
               % (self.lo1 / 1e9, self.synthe, self.synthe_sb, self.obs_freq / 1e9, self.freq_if1 / 1e9),
               "2nd LO %.3f MHz" % (self.lo2 / 1e6),
               "窓 %d–%d、%d ずつ束ねる → 2nd IF %.4f–%.4f GHz（%.1f MHz、%.2f kHz/ch）"
               % (self.start_chan, self.end_chan, self.chan_avg, self.f2(0.5) / 1e9,
                  self.f2(SAM45_NCH + 0.5) / 1e9, self.obs_band / 1e6, self.chan_avg * SAM45_DF / 1e3)]
        for n, a in sorted(self.arrays.items()):
            out.append("A%02d %s %s %.6f GHz: 1st IF %.4f GHz → 2nd IF %.4f GHz、ch 1 = %.5f GHz、ch 4096 = %.5f GHz"
                       % (n, a["sb"], a["label"], a["rest"] / 1e9, a["if1_line"] / 1e9, a["f2_line"] / 1e9,
                          a["fq1"] / 1e9, a["fq4096"] / 1e9))
        return "\n".join(out)


# ---------------------------------------------------------------- .start

def convert_start(lines, plan):
    out = list(lines)
    seen = {}

    def mark(key, i):
        if key in seen:
            raise ConvError("%d 行目: %s が 2 回目（1 回目は %d 行目）" % (i + 1, key, seen[key] + 1))
        seen[key] = i

    # SG の名前（SYNTHE_K など）は 1 つだけのはず
    synthes = set()
    for l in lines:
        if not l.startswith(("#", "%")):
            synthes.update(re.findall(r"\bSYNTHE_[A-Z]\b", l))
    if len(synthes) != 1:
        raise ConvError("SG（SYNTHE_x）が 1 つではない: %s" % sorted(synthes))
    old_synthe = synthes.pop()

    in_mlpps = False
    n_mlpps = 0
    for i, l in enumerate(lines):
        if l.startswith(("#", "%")) or not l.strip():
            continue
        tok = l.split()

        # 冒頭の MLPPS（LO スイッチの切り替え）を OPEN から CLOSE までコメントアウトする
        if tok[:2] == ["OPEN", "MLPPS"]:
            if in_mlpps:
                raise ConvError("%d 行目: MLPPS の OPEN が閉じていない" % (i + 1))
            in_mlpps = True
            n_mlpps += 1
        if in_mlpps:
            if not (len(tok) >= 2 and tok[1] == "MLPPS" or tok[0] == "SLEEP"):
                raise ConvError("%d 行目: MLPPS のブロックに知らない行: %s" % (i + 1, l))
            out[i] = "# " + l
            if tok[:2] == ["CLOSE", "MLPPS"]:
                in_mlpps = False
            continue
        if "MLPPS" in tok:
            raise ConvError("%d 行目: OPEN の外に MLPPS: %s" % (i + 1, l))

        if old_synthe in l:
            l = re.sub(r"\b%s\b" % old_synthe, plan.synthe, l)
            out[i] = l
            tok = l.split()

        if tok[0] != "SET":
            if BIG_NUMBER.search(l):
                raise ConvError("%d 行目: 周波数らしい値がある知らない行: %s" % (i + 1, l))
            continue

        m = re.match(r"SET (\S+) (\S+) (.*)$", l)
        if not m:
            raise ConvError("%d 行目: SET の形ではない: %s" % (i + 1, l))
        dev, par, val = m.group(1), m.group(2), m.group(3)
        devkey = "SYNTHE" if dev == plan.synthe else dev
        new = handle_set(devkey, par, val, plan, i)
        if new is not None:
            mark((devkey, par), i)
            out[i] = "SET %s %s %s" % (dev, par, new)
        elif (devkey, par) in PASS:
            pass
        elif SUSPECT.search(par) or BIG_NUMBER.search(val):
            raise ConvError("%d 行目: 周波数に関係しそうな知らない欄: %s" % (i + 1, l))

    if in_mlpps:
        raise ConvError("MLPPS のブロックが CLOSE で閉じていない")
    if n_mlpps != 1:
        raise ConvError("MLPPS のブロックが %d 個（1 個のはず）" % n_mlpps)
    missing = [k for k in REQUIRED if k not in seen]
    if missing:
        raise ConvError("見つからない欄: %s" % ", ".join("%s %s" % k for k in missing))
    return out


REQUIRED = [
    ("ANT", "POINTING"),
    ("SYNTHE", "OBS_FREQ"), ("SYNTHE", "FREQ_IF1"), ("SYNTHE", "RX_NAME"), ("SYNTHE", "SIDBD_TYP"),
    ("RXT", "OBS_FREQ"), ("RXT", "FREQ_IF1"), ("RXT", "RX_NAME"),
    ("SAM45", "SIDBD_TYP"), ("SAM45", "REST_FREQ"), ("SAM45", "OBS_FREQ"), ("SAM45", "FREQ_IF1"),
    ("SAM45", "RX_NAME"), ("SAM45", "OBS_BAND"), ("SAM45", "ARRAY"),
    ("SAM45", "FQDAT_F0"), ("SAM45", "FQDAT_FQ"),
    ("SAM45", "START_CHAN_SUB1"), ("SAM45", "END_CHAN_SUB1"), ("SAM45", "CHAN_AVG_SUB1"),
    ("SAM45", "N_SPEC_WINDOW_SUB1"), ("SAM45", "SUB_ARRAY"), ("SAM45", "QL_RMSARY"),
]


def per_array(val, n_expect, set_fn, i, par):
    items, kind = parse_list(val)
    if len(items) != n_expect:
        raise ConvError("%d 行目: SAM45 %s の要素が %d 個（%d 個のはず）" % (i + 1, par, len(items), n_expect))
    set_fn(items)
    return join_list(items, kind)


def handle_set(dev, par, val, plan, i):
    """書き換えた値（SET の 3 つ目以降）を返す。書き換えない欄は None。"""
    A = plan.arrays

    if dev == "ANT" and par == "POINTING":
        return "'%s'" % plan.pointing

    if dev == "SYNTHE":
        return {
            "OBS_FREQ": jfmt(plan.obs_freq),
            "FREQ_IF1": jfmt(plan.freq_if1),
            "RX_NAME": "'%s'" % plan.synthe_rx,
            "SIDBD_TYP": "'%s'" % plan.synthe_sb,
        }.get(par)

    if dev == "RXT" and par in ("OBS_FREQ", "FREQ_IF1", "RX_NAME"):
        items, kind = parse_list(val)
        if len(items) != 8 or any(x not in ("0.0", "") for x in items[1:]):
            raise ConvError("%d 行目: RXT %s は 1 つ目だけ使う形のはず: %s" % (i + 1, par, val))
        items[0] = {"OBS_FREQ": jfmt(plan.obs_freq), "FREQ_IF1": jfmt(plan.freq_if1),
                    "RX_NAME": plan.synthe_rx}[par]
        return join_list(items, kind)

    if dev != "SAM45":
        return None

    def each(key, fmt):
        def f(items):
            for n, a in A.items():
                items[n - 1] = fmt(a[key])
        return f

    if par == "SIDBD_TYP":
        return per_array(val, N_ARRAY, each("sb", str), i, par)
    if par == "RX_NAME":
        return per_array(val, N_ARRAY, each("rx", str), i, par)
    if par in ("REST_FREQ", "FQDAT_F0"):
        return per_array(val, N_ARRAY, each("rest", jfmt), i, par)
    if par == "OBS_FREQ":
        return per_array(val, N_ARRAY, each("obs", jfmt), i, par)
    if par == "FREQ_IF1":
        return per_array(val, N_ARRAY, each("if1", jfmt), i, par)
    if par == "OBS_BAND":
        def f(items):
            for n in A:
                items[n - 1] = jfmt(plan.obs_band)
        return per_array(val, N_ARRAY, f, i, par)
    if par == "ARRAY":  # 書き換えない。使う Array が有効になっているかだけ確かめる
        def check(items):
            off = [n for n in A if items[n - 1] != "1"]
            if off:
                raise ConvError("%d 行目: SAM45 ARRAY で A%s が使われていない" % (i + 1, ",".join("%02d" % n for n in off)))
        return per_array(val, N_ARRAY, check, i, par)
    if par == "FQDAT_FQ":
        def f(items):
            for n, a in A.items():
                items[2 * (n - 1)] = jfmt(a["fq4096"])
                items[2 * (n - 1) + 1] = jfmt(a["fq1"])
        return per_array(val, 2 * N_ARRAY, f, i, par)
    if par in ("START_CHAN_SUB1", "END_CHAN_SUB1", "CHAN_AVG_SUB1"):
        items, kind = parse_list(val)
        if len(items) != 2 or items[1] != "0":
            raise ConvError("%d 行目: SAM45 %s は窓 1 つの形のはず: %s" % (i + 1, par, val))
        items[0] = str({"START_CHAN_SUB1": plan.start_chan, "END_CHAN_SUB1": plan.end_chan,
                        "CHAN_AVG_SUB1": plan.chan_avg}[par])
        return join_list(items, kind)
    if par == "N_SPEC_WINDOW_SUB1":
        if val.strip() != "1":
            raise ConvError("%d 行目: 窓が 1 つの指示書のはず: %s" % (i + 1, val))
        return val
    if par == "SUB_ARRAY":
        if val.strip() != "'OFF'":
            raise ConvError("%d 行目: サブアレイごとに帯域を変える指示書には対応していない: %s" % (i + 1, val))
        return val
    if par == "QL_RMSARY":
        return "'%s'" % plan.ql_rms_array
    return None


# ---------------------------------------------------------------- .dat

DAT_LINE = re.compile(r"^(\d\d),([^,]*),([^,]*),(\d+\.\d+)$")


def convert_dat(lines, plan):
    if len(lines) != 16:
        raise ConvError(".dat が %d 行（16 行のはず）" % len(lines))
    out = []
    for i, l in enumerate(lines):
        m = DAT_LINE.match(l)
        if not m or int(m.group(1)) != i + 1:
            raise ConvError(".dat %d 行目: 知らない形: %s" % (i + 1, l))
        name, sb = plan.dat_switch[i]
        freq = m.group(4)
        if (i + 1) in plan.arrays:
            freq = "%.3f" % (plan.lo2 / 1e6)
        if name.startswith("#"):  # 使わない行は周波数を書かない
            out.append("%s,%-6s," % (m.group(1), name))
        else:
            out.append("%s,%-6s,%-3s,%s" % (m.group(1), name, sb, freq))
    return out


# ---------------------------------------------------------------- ファイル

def read_lines(path):
    with open(path, newline="") as f:
        text = f.read()
    if "\r" in text:
        raise ConvError("%s: 改行が LF ではない" % path)
    if not text.endswith("\n"):
        raise ConvError("%s: 最後の行に改行がない" % path)
    return text[:-1].split("\n")


def write_lines(path, lines):
    with open(path, "w", newline="") as f:
        f.write("\n".join(lines) + "\n")


def show_diff(name, a, b):
    changed = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
    print("==== %s: %d 行を書き換え" % (name, len(changed)))
    for i, x, y in changed:
        print("%5d - %s" % (i + 1, x))
        print("%5d + %s" % (i + 1, y))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("start", help="nobs が出した .start")
    p.add_argument("outdir", help="出力先のディレクトリ（入力と別の場所）")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="設定（既定: %(default)s）")
    p.add_argument("--force", action="store_true", help="出力先の同名のファイルを上書きする")
    args = p.parse_args(argv)

    try:
        if not args.start.endswith(".start"):
            raise ConvError("入力は .start のファイル: %s" % args.start)
        indir = os.path.dirname(os.path.abspath(args.start))
        stem = os.path.basename(args.start)[:-len(".start")]
        outdir = os.path.abspath(args.outdir)
        if outdir == indir:
            raise ConvError("出力先が入力と同じディレクトリ。元のファイルを上書きしてしまう")

        with open(args.config) as f:
            plan = Plan(json.load(f))

        files = sorted(x for x in os.listdir(indir)
                       if x.startswith(stem + ".") and "." not in x[len(stem) + 1:])
        if stem + ".dat" not in files:
            raise ConvError("%s.dat がない" % stem)

        start_in = read_lines(os.path.join(indir, stem + ".start"))
        dat_in = read_lines(os.path.join(indir, stem + ".dat"))
        start_out = convert_start(start_in, plan)
        dat_out = convert_dat(dat_in, plan)

        os.makedirs(outdir, exist_ok=True)
        clash = [x for x in files if os.path.exists(os.path.join(outdir, x))]
        if clash and not args.force:
            raise ConvError("出力先に同じ名前のファイルがある（--force で上書き）: %s" % ", ".join(clash))
    except ConvError as e:
        print("エラー: %s" % e, file=sys.stderr)
        return 1

    print(plan.summary())
    print()
    show_diff(stem + ".start", start_in, start_out)
    show_diff(stem + ".dat", dat_in, dat_out)
    print()
    for x in files:
        dst = os.path.join(outdir, x)
        if x == stem + ".start":
            write_lines(dst, start_out)
        elif x == stem + ".dat":
            write_lines(dst, dat_out)
        else:
            shutil.copy2(os.path.join(indir, x), dst)
        print("書き出し: %s" % dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
