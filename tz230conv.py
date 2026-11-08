#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""nobs が出した H40 の指示書（.start・.dat）を TZ 230 GHz モード（DSB）用に書き換える。

    python tz230conv.py 入力の.start [-o 出力先のディレクトリ] [-c 設定.json]

- 既定では、入力の .start と、同じ名前の .dat をその場で上書きする（元のファイルは nobs ですぐ作り直せる）。
  変換済みのファイルを渡すと止まる。
- -o を付けると、元と同じファイル名で出力先に書く。同じ名前で拡張子だけ違うほかのファイルはそのままコピーする。
- 書き換えるのは決めた行・欄だけ。知らない形の行があれば止まる。
- 書き換えた行の差分を標準出力に出す。

方針は docs/policy.md、欄の一覧と周波数の式は docs/fields.md。
Python 2.6 以上・Python 3 の標準ライブラリだけで動く（観測所の計算機の python は 2 系）。
"""

from __future__ import division, print_function

import decimal
import json
import math
import optparse
import os
import re
import shutil
import sys

PY2 = sys.version_info[0] == 2
TEXT = type(u"")

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
LO2_MAX = 12.0e9  # 12 GHz くらいまで大丈夫かもしれない（2026-11-09 時点で未確認）

START_CHAN_STEP = 64  # START_CHAN は 16 か 64 の倍数のはず。念のため 64 の倍数に限る

FQDAT_ROUND = 1.0e4  # FQDAT_FQ は 10 kHz に丸める（サンプルで確認）

# 周波数に関係しそうな欄。ここに当たる SET 行は、書き換えるか、下の PASS に入っていないと止まる。
SUSPECT = re.compile(r"FREQ|SIDBD|RX_NAME|FQDAT|BAND|CHAN|POINTING|SETUP_FILE")
PASS = set([
    ("SYNTHE", "FREQ_SW"), ("SYNTHE", "FREQ_INTVAL"),
    ("SAM45", "FREQ_SW"), ("SAM45", "FREQ_INTVAL"), ("SAM45", "FREQ_PROF_SYNTH_SUB1"),
    ("SAM45", "FQDAT_CH"), ("SAM45", "CH_RANGE"), ("SAM45", "CH_BIND"),
    ("RXT", "SETUP_FILE"),  # ファイル名は揃えるので変えない
])
BIG_NUMBER = re.compile(r"\d(\.\d*)?E(9|1\d)\b")


class ConvError(Exception):
    pass


# ---------------------------------------------------------------- 数値の書式

def jfmt(x):
    """nobs（Java の Double.toString）と同じ書式。1e7 以上の正の数だけを扱う。"""
    if not x >= 1e7:
        raise ConvError("書式にできない値: %r" % x)
    x = float(x)
    # 整数の Hz なら Python のどの版でも正確に。そうでなければ最短の表記（2.7 以上）から
    d = decimal.Decimal(int(x) if x == int(x) else repr(x)).normalize()
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

    def __init__(self, cfg, input_window=None):
        """input_window: 入力の .start の窓 (START_CHAN, END_CHAN, CHAN_AVG)。設定の window が null のときに使う。"""
        s = cfg["synthe"]
        self.synthe = s["name"]
        self.synthe_rx = s["rx_name"]
        self.ref_num = int(s["ref_num"])  # SAM45 REF_NUM で SG を指す番号（SYNTHE_N は 14）
        self.obs_freq = float(s["obs_freq"])
        self.freq_if1 = float(s["freq_if1"])
        self.synthe_sb = s["sideband"]
        if self.synthe_sb == "USB":
            self.lo1 = self.obs_freq - self.freq_if1
        elif self.synthe_sb == "LSB":
            self.lo1 = self.obs_freq + self.freq_if1
        else:
            raise ConvError("synthe.sideband は USB か LSB: %r" % self.synthe_sb)

        # 線を 2nd IF のこの周波数に置くよう、Array ごとに 2nd LO を決める
        self.line_f2 = float(cfg["line_f2"])

        w = cfg.get("window")
        self.keep_window = w is None
        if self.keep_window:
            if input_window is None:
                raise ConvError("窓を変えない設定なのに、入力の窓がわからない")
            self.start_chan, self.end_chan, self.chan_avg = input_window
        else:
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
        for key, a in cfg["arrays"].items():
            n = int(key)
            if not 1 <= n <= 16:
                raise ConvError("arrays の A%02d は A01〜A16 のどれか" % n)
            if self.dat_switch[n - 1][0] != a["rx_name"]:
                raise ConvError("A%02d: .dat のスイッチ %s と SAM45 RX_NAME %s が食い違う"
                                % (n, self.dat_switch[n - 1][0], a["rx_name"]))

        self.arrays = {}
        for key, a in sorted(cfg["arrays"].items()):
            n = int(key)
            rest = float(a["rest_freq"])
            sb = a["sideband"]
            if1_line = rest - self.lo1 if sb == "USB" else self.lo1 - rest
            if if1_line <= 0:
                raise ConvError("A%02d: %s で静止周波数 %s が 1st LO の反対側" % (n, sb, rest))
            lo2 = if1_line + self.line_f2
            lo2 = math.floor(lo2 / 1e3 + 0.5) * 1e3  # .dat は MHz で小数 3 桁（1 kHz）まで
            if not LO2_MIN <= lo2 <= LO2_MAX:
                raise ConvError("A%02d: 2nd LO %.3f MHz が設定範囲 %.0f–%.0f MHz の外"
                                % (n, lo2 / 1e6, LO2_MIN / 1e6, LO2_MAX / 1e6))
            f2_line = lo2 - if1_line
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
                "if1_line": if1_line, "f2_line": f2_line, "lo2": lo2,
                "fq4096": self.rf(sb, lo2, SAM45_NCH), "fq1": self.rf(sb, lo2, 1),
            }

    def f2(self, k):
        """束ねたあとの ch k（1 始まり、端は 0.5 刻み）の中心の 2nd IF。"""
        return SAM45_F2_MIN + (self.start_chan + (k - 0.5) * self.chan_avg) * SAM45_DF

    def rf(self, sb, lo2, k):
        """ch k の RF（静止系、ドップラー補正なし）。2nd LO は 1st IF の上なので 2nd IF で反転する。"""
        if1 = lo2 - self.f2(k)
        f = self.lo1 + if1 if sb == "USB" else self.lo1 - if1
        return math.floor(f / FQDAT_ROUND + 0.5) * FQDAT_ROUND  # 四捨五入（round は 2 と 3 で違う）

    def summary(self):
        out = ["1st LO %.6f GHz（%s %s の OBS_FREQ %.6f − FREQ_IF1 %.6f、ドップラー補正の前）"
               % (self.lo1 / 1e9, self.synthe, self.synthe_sb, self.obs_freq / 1e9, self.freq_if1 / 1e9),
               "窓（%s）%d–%d、%d ずつ束ねる → 2nd IF %.4f–%.4f GHz（%.1f MHz、%.2f kHz/ch）"
               % ("入力のまま" if self.keep_window else "書き換え", self.start_chan, self.end_chan, self.chan_avg, self.f2(0.5) / 1e9,
                  self.f2(SAM45_NCH + 0.5) / 1e9, self.obs_band / 1e6, self.chan_avg * SAM45_DF / 1e3)]
        for n, a in sorted(self.arrays.items()):
            out.append("A%02d %s %s %.6f GHz: 1st IF %.6f GHz、2nd LO %.3f MHz → 2nd IF %.6f GHz、"
                       "ch 1 = %.5f GHz、ch 4096 = %.5f GHz"
                       % (n, a["sb"], a["label"], a["rest"] / 1e9, a["if1_line"] / 1e9, a["lo2"] / 1e6,
                          a["f2_line"] / 1e9, a["fq1"] / 1e9, a["fq4096"] / 1e9))
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
        elif (devkey, par) in PASS:
            pass
        elif SUSPECT.search(par) or BIG_NUMBER.search(val):
            raise ConvError("%d 行目: 周波数に関係しそうな知らない欄: %s" % (i + 1, l))
        if dev == "SAM45":
            r = reset_other_arrays(new if new is not None else val, plan, i, par)
            if r is not None:
                new = r
        if new is not None and new != val:
            out[i] = "SET %s %s %s" % (dev, par, new)

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
    ("SAM45", "SIDBD_TYP"), ("SAM45", "REF_NUM"), ("SAM45", "REST_FREQ"), ("SAM45", "OBS_FREQ"), ("SAM45", "FREQ_IF1"),
    ("SAM45", "RX_NAME"), ("SAM45", "OBS_BAND"), ("SAM45", "ARRAY"),
    ("SAM45", "FQDAT_F0"), ("SAM45", "FQDAT_FQ"),
    ("SAM45", "START_CHAN_SUB1"), ("SAM45", "END_CHAN_SUB1"), ("SAM45", "CHAN_AVG_SUB1"),
    ("SAM45", "N_SPEC_WINDOW_SUB1"), ("SAM45", "SUB_ARRAY"), ("SAM45", "QL_RMSARY"),
]


def reset_other_arrays(val, plan, i, par):
    """SAM45 の Array ごとのリストで、使う Array（設定の arrays、今は A11・A12）以外をデフォルト値にする。

    要素が 32 個なら A01〜A32 に 1 つずつ、64 個なら 2 つずつ（FQDAT_FQ・FQDAT_CH・CH_RANGE）。
    2 個以下は Array ごとではない（SpW・サブアレイごと、天体の位置など）ので変えない。
    デフォルト値は、文字列のリストなら空、数のリストは小数点か指数があれば 0.0、なければ 0。
    """
    v = val.strip()
    if not (v.startswith("(") or v.startswith("'")) or "," not in v:
        return None
    items, kind = parse_list(v)
    if len(items) <= 2:
        return None
    if len(items) not in (N_ARRAY, 2 * N_ARRAY):
        raise ConvError("%d 行目: SAM45 %s の要素が %d 個（Array ごとなら 32 か 64 のはず）" % (i + 1, par, len(items)))
    if kind == "''":
        default = ""
    elif any(re.search(r"[.eE]", x) for x in items):
        default = "0.0"
    else:
        default = "0"
    per = len(items) // N_ARRAY
    for j in range(len(items)):
        if j // per + 1 not in plan.arrays:
            items[j] = default
    return join_list(items, kind)


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
    if par == "REF_NUM":
        def f(items):
            for n in A:
                items[n - 1] = str(plan.ref_num)
        return per_array(val, N_ARRAY, f, i, par)
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

def convert_dat(plan):
    """決まった書式（config の dat_switch）で 16 行を書く。入力の .dat の中身は使わない。

    使う Array（設定の arrays）はそれぞれの 2nd LO を書く。使わない行も 2nd LO が要るので、
    いちばん番号の小さい使う Array（今は A11）と同じ値を入れる。`#` の行は周波数を書かない。
    """
    fill = plan.arrays[min(plan.arrays)]["lo2"]
    out = []
    for n in range(1, 17):
        name, sb = plan.dat_switch[n - 1]
        if name.startswith("#"):
            out.append("%02d,%-6s," % (n, name))
            continue
        lo2 = plan.arrays[n]["lo2"] if n in plan.arrays else fill
        out.append("%02d,%-6s,%-3s,%.3f" % (n, name, sb, lo2 / 1e6))
    return out


# ---------------------------------------------------------------- ファイル

def native(o):
    """json が返す unicode を、Python 2 では UTF-8 の str にそろえる（日本語のメッセージと混ぜるため）。"""
    if PY2 and isinstance(o, TEXT):
        return o.encode("utf-8")
    if isinstance(o, dict):
        return dict((native(k), native(v)) for k, v in o.items())
    if isinstance(o, list):
        return [native(v) for v in o]
    return o


def load_config(path):
    with open(path, "rb") as f:
        return native(json.loads(f.read().decode("utf-8")))


def read_lines(path, strict=True):
    with open(path, "rb") as f:
        data = f.read()
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise ConvError("%s: ASCII 以外の文字がある" % path)
    if PY2:
        text = data
    if not strict:  # .dat は形によらず書き直すので、改行の違いは気にしない
        return text.replace("\r\n", "\n").rstrip("\n").split("\n") if text.strip() else []
    if "\r" in text:
        raise ConvError("%s: 改行が LF ではない" % path)
    if not text.endswith("\n"):
        raise ConvError("%s: 最後の行に改行がない" % path)
    return text[:-1].split("\n")


def write_lines(path, lines):
    """一時ファイルに書いてから置き換える。途中で止まっても半端なファイルを残さない。"""
    text = "\n".join(lines) + "\n"
    tmp = path + ".tz230conv.tmp"
    with open(tmp, "wb") as f:
        f.write(text if PY2 else text.encode("ascii"))
    os.rename(tmp, path)


def read_window(lines):
    """入力の .start から SAM45 の窓 (START_CHAN, END_CHAN, CHAN_AVG) を読む（SpW 1 つ目）。"""
    got = {}
    for l in lines:
        m = re.match(r"SET SAM45 (START_CHAN_SUB1|END_CHAN_SUB1|CHAN_AVG_SUB1) (.*)$", l)
        if m:
            got[m.group(1)] = int(parse_list(m.group(2))[0][0])
    try:
        return got["START_CHAN_SUB1"], got["END_CHAN_SUB1"], got["CHAN_AVG_SUB1"]
    except KeyError as e:
        raise ConvError("入力の .start に SAM45 %s がない" % e)


def check_not_converted(lines, plan):
    """上書きが既定なので、変換済みの指示書をもう一度変換しないようにする。"""
    for i, l in enumerate(lines):
        if not l.startswith(("#", "%")) and plan.synthe_rx in l:
            raise ConvError("%d 行目: 変換済みの指示書のよう（nobs で作り直してから変換する）: %s" % (i + 1, l))


def show_diff(name, a, b):
    n = max(len(a), len(b))
    a = a + [None] * (n - len(a))
    b = b + [None] * (n - len(b))
    changed = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
    print("==== %s: %d 行を書き換え" % (name, len(changed)))
    for i, x, y in changed:
        if x is not None:
            print("%5d - %s" % (i + 1, x))
        if y is not None:
            print("%5d + %s" % (i + 1, y))


def main(argv=None):
    p = optparse.OptionParser(usage="python tz230conv.py 入力の.start [-o 出力先のディレクトリ] [-c 設定.json]")
    p.add_option("-o", "--outdir", default=None,
                 help="出力先（既定: 入力のファイルをその場で上書き）。同じ名前のほかの拡張子のファイルもコピーする")
    p.add_option("-c", "--config", default=DEFAULT_CONFIG, help="設定（既定: %default）")
    args, rest = p.parse_args(argv)
    if len(rest) != 1:
        p.error("入力の .start を 1 つ指定する")
    start = rest[0]

    try:
        if not start.endswith(".start"):
            raise ConvError("入力は .start のファイル: %s" % start)
        indir = os.path.dirname(os.path.abspath(start))
        stem = os.path.basename(start)[:-len(".start")]
        outdir = os.path.abspath(args.outdir) if args.outdir else indir

        files = sorted(x for x in os.listdir(indir)
                       if x.startswith(stem + ".") and "." not in x[len(stem) + 1:])
        if stem + ".dat" not in files:
            raise ConvError("%s.dat がない" % stem)

        start_in = read_lines(os.path.join(indir, stem + ".start"))
        dat_in = read_lines(os.path.join(indir, stem + ".dat"), strict=False)
        window = read_window(start_in)
        try:
            plan = Plan(load_config(args.config), window)
        except KeyError as e:
            raise ConvError("設定 %s に %s がない。スクリプトと設定ファイル（config/tz230.json）の版がそろっているか確かめる"
                            % (args.config, e))
        check_not_converted(start_in, plan)
        start_out = convert_start(start_in, plan)
        dat_out = convert_dat(plan)

        if not os.path.isdir(outdir):
            os.makedirs(outdir)
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
        elif outdir != indir:
            shutil.copy2(os.path.join(indir, x), dst)
        else:
            continue
        print("書き出し: %s" % dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
