#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from model import _read_speed, encode, map_layers, score_live, train, weak_labels


class LearnedModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.speed = ROOT / "speed_log.csv"
        cls.traffic = ROOT / "traffic_log.csv"
        if not cls.speed.exists():
            raise unittest.SkipTest("speed_log.csv missing")
        cls.out = ROOT / "models" / "pathwatch.joblib"
        cls.report = train(cls.speed, cls.traffic, cls.out)

    def test_logs_have_contrast(self):
        df = _read_speed(self.speed)
        self.assertGreater(len(df), 1000)
        self.assertGreaterEqual(df["label"].nunique(), 4)
        self.assertTrue({"novpn"}.issubset(set(df["vpn"].dropna().astype(str))))
        cells = df.groupby("label")["down_mbps"].median()
        self.assertGreater(float(cells.max()), float(cells.min()) * 1.5)

    def test_weak_labels_two_classes(self):
        df = _read_speed(self.speed)
        y = weak_labels(df)
        labeled = y.dropna()
        self.assertGreater(len(labeled), 200)
        self.assertEqual(set(labeled.unique()), {0.0, 1.0})
        self.assertGreater(int((labeled == 1).sum()), 50)
        self.assertGreater(int((labeled == 0).sum()), 50)

    def test_encode_shape(self):
        df = _read_speed(self.speed).head(20)
        df["dest_lat"] = 9.02
        df["dest_lon"] = 38.75
        df["dest_n"] = 3
        x = encode(df)
        self.assertEqual(len(x), 20)
        self.assertIn("hour_sin", x.columns)
        self.assertTrue(x["hour_sin"].between(-1, 1).all())

    def test_train_beats_chance(self):
        self.assertGreaterEqual(self.report["cv_auc"], 0.70)
        self.assertGreaterEqual(self.report["auc"], 0.75)
        self.assertGreater(self.report["n_labeled"], 200)
        top = next(iter(self.report["importances"]))
        self.assertIn(top, {"tod_i", "vpn_i", "hour_sin", "hour_cos", "conn_i", "dow", "dest_n", "dest_lat", "dest_lon", "up_mbps"})

    def test_live_score_range(self):
        from model import load
        load(self.out)
        slow = score_live(0.04, 0.02, "wifi_novpn_evening")
        fast = score_live(2.5, 0.4, "wifi_vpn_evening")
        self.assertGreaterEqual(slow["score"], 0)
        self.assertLessEqual(slow["score"], 100)
        self.assertGreaterEqual(fast["score"], 0)
        self.assertLessEqual(fast["score"], 100)
        self.assertEqual(slow["source"], "model")
        self.assertGreater(slow["p_throttled"], fast["p_throttled"] - 0.05)

    def test_map_from_logs(self):
        from model import load
        load(self.out)
        speed = _read_speed(self.speed)
        layers = map_layers(speed, pd.DataFrame(), {"connection": "wifi", "vpn": "novpn", "tod": "evening", "down_mbps": 0.08, "up_mbps": 0.04})
        self.assertTrue(layers["city_heat"])
        self.assertTrue(layers["model"].get("trained"))
        self.assertGreater(layers["path_meta"]["intensity"], 0)
        self.assertTrue(any(p["kind"] == "vantage" for p in layers["city_heat"]))


if __name__ == "__main__":
    unittest.main()
