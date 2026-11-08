# -*- coding: utf-8 -*-
"""python -m unittest discover -s tests（Python 2.7 以上・3）"""

import shutil

import copy
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import tz230conv as c  # noqa: E402

SAMPLE = os.path.join(ROOT, "samples", "h40-ori-kl", "ori-kl.start")
SAMPLE_DAT = os.path.join(ROOT, "samples", "h40-ori-kl", "ori-kl.dat")

CFG = c.load_config(c.DEFAULT_CONFIG)
WINDOW = c.read_window(c.read_lines(SAMPLE))  # (245760, 278527, 8)


def sam45_list(lines, par):
    for l in lines:
        m = re.match(r"SET SAM45 %s (.*)$" % par, l)
        if m:
            return c.parse_list(m.group(1))[0]
    raise KeyError(par)


class TestFormulasOnH40Sample(unittest.TestCase):
    """H40 の設定を入れると、サンプルの FQDAT_FQ と 2nd LO が再現できる。"""

    def h40_plan(self, array, rest):
        cfg = copy.deepcopy(CFG)
        cfg["synthe"].update(obs_freq=43.0e9, freq_if1=6.0e9, sideband="USB")
        cfg["arrays"] = {str(array): {"rest_freq": rest, "sideband": "USB", "rx_name": "H40"}}
        return c.Plan(cfg, WINDOW)

    def test_fqdat_and_lo2(self):
        lines = c.read_lines(SAMPLE)
        fq = sam45_list(lines, "FQDAT_FQ")
        dat = c.read_lines(SAMPLE_DAT)
        for array, rest in ((1, 43.122e9), (9, 42.821e9)):
            p = self.h40_plan(array, rest)
            a = p.arrays[array]
            self.assertEqual("%.3f" % (a["lo2"] / 1e6), dat[array - 1].split(",")[3])  # 線を 2nd IF 3 GHz に置く 2nd LO
            self.assertAlmostEqual(a["f2_line"], 3.0e9, delta=1)
            self.assertEqual(c.jfmt(a["fq4096"]), fq[2 * (array - 1)])
            self.assertEqual(c.jfmt(a["fq1"]), fq[2 * (array - 1) + 1])
            self.assertAlmostEqual(p.obs_band, 1.25e8)


class TestJfmt(unittest.TestCase):
    def test_java_style(self):
        for x, s in ((4.3e10, "4.3E10"), (6.0e9, "6.0E9"), (4.3122e10, "4.3122E10"),
                     (4.305952e10, "4.305952E10"), (1.25e8, "1.25E8"), (230.538e9, "2.30538E11")):
            self.assertEqual(c.jfmt(x), s)


class TestConvert(unittest.TestCase):
    def setUp(self):
        self.plan = c.Plan(CFG, WINDOW)
        self.src = c.read_lines(SAMPLE)
        self.out = c.convert_start(self.src, self.plan)

    def test_lines_kept(self):
        self.assertEqual(len(self.src), len(self.out))
        changed = {i for i, (a, b) in enumerate(zip(self.src, self.out)) if a != b}
        for i in changed:  # 書き換えるのは決めた行だけ
            l = self.src[i]
            self.assertTrue(re.search(r"MLPPS|SLEEP|SYNTHE_K|^SET (ANT|RXT|SAM45) ", l), l)
        for a, b in zip(self.src, self.out):  # コメントの行は変えない
            if a.startswith(("%", "#")):
                self.assertEqual(a, b)

    def test_values(self):
        out = self.out
        self.assertIn("SET SYNTHE_N OBS_FREQ 2.30538E11", out)
        self.assertIn("SET SYNTHE_N FREQ_IF1 7.338E9", out)
        self.assertIn("SET SYNTHE_N RX_NAME 'TZ230DSB'", out)
        self.assertIn("SET ANT POINTING 'TZ2'", out)
        self.assertIn("SET RXT RX_NAME 'TZ230DSB,,,,,,,'", out)
        self.assertIn("SET SAM45 QL_RMSARY 'A15'", out)
        self.assertFalse(any("SYNTHE_K" in l for l in out if not l.startswith(("#", "%"))))
        self.assertTrue(all(l.startswith("# ") for l in out if "MLPPS" in l))
        self.assertEqual(sam45_list(out, "RX_NAME")[14:16], ["TZ2H", "TZ2H"])
        self.assertEqual(sam45_list(out, "SIDBD_TYP")[14:16], ["USB", "LSB"])
        self.assertEqual(sam45_list(out, "REST_FREQ")[14:16], ["2.30538E11", "2.1559595E11"])
        for par in ("START_CHAN_SUB1", "END_CHAN_SUB1", "CHAN_AVG_SUB1", "OBS_BAND"):  # 窓は入力のまま
            self.assertEqual(sam45_list(out, par), sam45_list(self.src, par), par)
        for n, a in self.plan.arrays.items():  # 線は 2nd IF 3 GHz（2nd LO の 1 kHz の丸めの分だけずれる）
            self.assertAlmostEqual(a["f2_line"], 3.0e9, delta=1e3)
        # A01〜A14 は元のまま
        for par in ("REST_FREQ", "OBS_FREQ", "FREQ_IF1", "RX_NAME", "SIDBD_TYP", "OBS_BAND", "FQDAT_F0"):
            self.assertEqual(sam45_list(out, par)[:14], sam45_list(self.src, par)[:14], par)
        self.assertEqual(sam45_list(out, "FQDAT_FQ")[:28], sam45_list(self.src, "FQDAT_FQ")[:28])

    def test_lines_inside_window(self):
        for n, a in self.plan.arrays.items():
            lo = min(a["fq1"], a["fq4096"])
            hi = max(a["fq1"], a["fq4096"])
            self.assertTrue(lo < a["rest"] < hi, n)

    def test_unknown_freq_line_stops(self):
        bad = list(self.src)
        bad.insert(240, "SET SAM45 NEW_FREQ_THING 1.0E10")
        with self.assertRaises(c.ConvError):
            c.convert_start(bad, self.plan)

    def test_lo2_range(self):
        cfg = copy.deepcopy(CFG)
        cfg["synthe"]["freq_if1"] = 9.5e9  # CO の 1st IF 9.5 GHz → 2nd LO 12.5 GHz
        with self.assertRaises(c.ConvError):
            c.Plan(cfg, WINDOW)

    def test_line_outside_window(self):
        cfg = copy.deepcopy(CFG)
        cfg["window"] = {"start_chan": 65536, "end_chan": 196607, "chan_avg": 32}  # 2.25–2.75 GHz
        with self.assertRaises(c.ConvError):
            c.Plan(cfg, WINDOW)

    def test_dat(self):
        out, notes = c.convert_dat(c.read_lines(SAMPLE_DAT), self.plan)
        self.assertEqual(notes, [])
        self.assertEqual(out[14], "15,TZ2H  ,LSB,10338.000")
        self.assertEqual(out[15], "16,TZ2H  ,LSB,10604.050")
        self.assertEqual(out[0], "01,H20ch1,USB,9122.000")
        self.assertEqual(out[8], "09,H40   ,USB,8821.000")
        self.assertEqual(out[4], "05,#     ,")

    def test_dat_any_input(self):
        """入力の形によらず、決まった書式で 16 行を書く（実機の nobs は `01,#     ,,` のような行も出す）。"""
        src = ["01,#     ,,", "02,H40   ,USB,9122.000", "junk", "15,#     ,,"]
        out, notes = c.convert_dat(src, self.plan)
        self.assertEqual(len(out), 16)
        self.assertEqual(out[0], "01,H20ch1,USB,")
        self.assertEqual(out[1], "02,H20ch1,USB,9122.000")
        self.assertEqual(out[2], "03,H20ch2,USB,")
        self.assertEqual(out[4], "05,#     ,")
        self.assertEqual(out[14], "15,TZ2H  ,LSB,10338.000")
        self.assertEqual(out[15], "16,TZ2H  ,LSB,10604.050")
        self.assertEqual(len(notes), 1)
        out2, _ = c.convert_dat([], self.plan)
        self.assertEqual(out2[14], "15,TZ2H  ,LSB,10338.000")

    def test_start_chan_multiple_of_64(self):
        self.assertEqual(self.plan.start_chan % 64, 0)
        cfg = copy.deepcopy(CFG)
        cfg["window"] = {"start_chan": 245760 + 32, "end_chan": 278527 + 32, "chan_avg": 8}
        with self.assertRaises(c.ConvError):
            c.Plan(cfg, WINDOW)


class TestMain(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_overwrite_in_place(self):
        src = os.path.join(self.d, "in")
        os.mkdir(src)
        for x in ("ori-kl.start", "ori-kl.dat"):
            shutil.copy(os.path.join(os.path.dirname(SAMPLE), x), src)
        with open(os.path.join(src, "ori-kl.foo"), "w") as f:
            f.write("x\n")
        start = os.path.join(src, "ori-kl.start")
        self.assertEqual(c.main([start]), 0)
        self.assertEqual(sorted(os.listdir(src)), ["ori-kl.dat", "ori-kl.foo", "ori-kl.start"])
        out = c.read_lines(start)
        self.assertEqual(out, c.convert_start(c.read_lines(SAMPLE), c.Plan(CFG, WINDOW)))
        self.assertEqual(c.main([start]), 1)  # 変換済みはもう一度変換しない
        self.assertEqual(c.read_lines(start), out)

    def test_outdir(self):
        self.assertEqual(c.main([SAMPLE, "-o", self.d]), 0)
        self.assertEqual(sorted(os.listdir(self.d)), ["ori-kl.dat", "ori-kl.start"])
        self.assertEqual(c.main([SAMPLE, "-o", self.d]), 0)  # 出力先は上書きしてよい


if __name__ == "__main__":
    unittest.main()
