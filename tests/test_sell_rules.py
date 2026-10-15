"""User-defined sell rules with deterministic, causal structure fixtures.

The fixtures supply recognized stroke endpoints, not a replacement Chan engine.
They keep the project's existing last-endpoint behavior; only a fractal's right
bar must be visible before the endpoint can appear in an historical prefix.
"""
import copy
from contextlib import ExitStack
from datetime import date, timedelta
import sys
import unittest
from unittest.mock import patch

from fake_engine import install

if "chanlun_core" not in sys.modules:
    install()

from core import sell


def point(index, kind, price):
    return {"i": index, "mi": index, "t": kind, "p": price}


def candles(points, count):
    """Linear prices between fixture endpoints, with exact endpoint extremes."""
    result = []
    for index in range(count):
        left = next((p for p in reversed(points) if p["i"] <= index), points[0])
        right = next((p for p in points if p["i"] >= index), points[-1])
        if index < points[0]["i"]:
            close = points[0]["p"] + 1
        elif index > points[-1]["i"]:
            close = points[-1]["p"] + (-1 if points[-1]["t"] == 1 else 1) * (1 + (index - points[-1]["i"]) * .05)
        elif left["i"] == right["i"]:
            close = left["p"] + (-.1 if left["t"] == 1 else .1)
        else:
            close = left["p"] + (right["p"] - left["p"]) * (index - left["i"]) / (right["i"] - left["i"])
        high, low = close + .1, close - .1
        if left["i"] == index:
            if left["t"] == 1:
                high = left["p"]
            else:
                low = left["p"]
        day = date(2026, 1, 1) + timedelta(days=index)
        result.append({"date": int(day.strftime("%Y%m%d")), "open": close, "high": high,
                       "low": low, "close": close, "volume": 1000})
    return result


def structure(points, areas=None, centers=None):
    """Patch only public engine dependencies and honor the supplied prefix length."""
    stack = ExitStack()
    def fractals(raw):
        last_index = raw[-1][0] if raw else -1
        return copy.deepcopy([p for p in points if p["i"] + 1 <= last_index])
    def zones(visible):
        last_point = visible[-1]["i"] if visible else -1
        return copy.deepcopy([z for z in centers or [] if z["i1"] <= last_point])
    def momentum(closes):
        histogram = [-1.0] * len(closes)
        for start, end, value in areas or []:
            if start + 1 < len(histogram):
                histogram[start + 1] = value
        return [0] * len(closes), [0] * len(closes), histogram
    def area(histogram, start, end, sign):
        return sum(value for value in histogram[start:end + 1]
                   if (value > 0 if sign > 0 else value < 0))
    for name, callback in {"merge_bars": lambda raw: raw, "find_fenxing": fractals,
                           "find_bi": lambda fx: fx, "find_zhongshu": zones,
                           "macd": momentum, "area": area}.items():
        stack.enter_context(patch.object(sell, name, side_effect=callback))
    return stack


def of_type(signals, name):
    return [signal for signal in signals if signal["type"] == name]


class SellRuleTests(unittest.TestCase):
    def first_points(self, new_high=110):
        return [point(5, -1, 80), point(10, 1, 100), point(15, -1, 85), point(20, 1, new_high)]

    def test_first_sell_accepts_99_percent_positive_area_without_85_percent_gate(self):
        points = self.first_points()
        with structure(points, [(5, 10, 10), (15, 20, 9.9)]):
            result = of_type(sell.detect_sell(candles(points, 24)), "一类卖点")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["stop"], 110)

    def test_first_sell_rejects_equal_larger_area_equal_high_and_zero_prior_area(self):
        for high, prior, current in [(110, 10, 10), (110, 10, 10.01), (100, 10, 9),
                                      (99.99, 10, 9), (110, 0, 0)]:
            points = self.first_points(high)
            with self.subTest(high=high, prior=prior, current=current), \
                 structure(points, [(5, 10, prior), (15, 20, current)]):
                result = sell.detect_sell(candles(points, 24))
            self.assertEqual(of_type(result, "一类卖点"), [])

    def test_first_sell_zero_current_red_area_is_less_than_positive_prior_area(self):
        points = self.first_points()
        with structure(points, [(5, 10, 10), (15, 20, 0)]):
            result = sell.detect_sell(candles(points, 24))
        self.assertEqual(len(of_type(result, "一类卖点")), 1)

    def test_second_sell_retains_first_anchor_after_next_upstroke_and_accepts_equal_high(self):
        points = self.first_points() + [point(25, -1, 90), point(30, 1, 110)]
        bars = candles(points, 34)
        with structure(points, [(5, 10, 10), (15, 20, 9.9), (25, 30, 12)]):
            result = of_type(sell.detect_sell(bars), "二类卖点")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], bars[30]["date"])
        self.assertEqual(result[0]["stop"], 110)

    def test_second_sell_can_use_old_first_anchor_when_its_own_rebound_is_recent(self):
        points = self.first_points() + [point(105, -1, 90), point(110, 1, 109)]
        with structure(points, [(5, 10, 10), (15, 20, 9.9), (105, 110, 12)]):
            result = of_type(sell.detect_sell(candles(points, 114)), "二类卖点")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["stop"], 110)

    def test_second_sell_rejects_raw_high_break_of_anchor_even_if_endpoint_is_lower(self):
        points = self.first_points() + [point(25, -1, 90), point(30, 1, 105)]
        bars = candles(points, 34)
        bars[27]["high"] = 110.01
        with structure(points, [(5, 10, 10), (15, 20, 9.9), (25, 30, 12)]):
            result = sell.detect_sell(bars)
        self.assertEqual(of_type(result, "二类卖点"), [])

    def test_second_sell_break_is_not_resurrected_after_a_later_lower_rebound(self):
        points = self.first_points() + [point(25, -1, 90), point(30, 1, 111),
                                        point(35, -1, 92), point(40, 1, 105)]
        with structure(points, [(5, 10, 10), (15, 20, 9.9), (25, 30, 12), (35, 40, 15)]):
            result = sell.detect_sell(candles(points, 44))
        self.assertEqual(of_type(result, "二类卖点"), [])

    def test_second_sell_date_stays_at_first_qualifying_rebound(self):
        points = self.first_points() + [point(25, -1, 90), point(30, 1, 105),
                                        point(35, -1, 92), point(40, 1, 106)]
        bars = candles(points, 44)
        with structure(points, [(5, 10, 10), (15, 20, 9.9), (25, 30, 8), (35, 40, 12)]):
            result = of_type(sell.detect_sell(bars), "二类卖点")
        self.assertEqual([signal["date"] for signal in result], [bars[30]["date"]])

    def test_second_sell_has_no_three_percent_drop_gate_and_needs_a_rebound_endpoint(self):
        points = self.first_points() + [point(25, -1, 109.5), point(30, 1, 109.9)]
        with structure(points, [(5, 10, 10), (15, 20, 9.9), (25, 30, 12)]):
            result = sell.detect_sell(candles(points, 34))
        self.assertEqual(len(of_type(result, "二类卖点")), 1)
        points = points[:-1]
        with structure(points, [(5, 10, 10), (15, 20, 9.9)]):
            result = sell.detect_sell(candles(points, 29))
        self.assertEqual(of_type(result, "二类卖点"), [])

    def third_fixture(self, rebound=97, bottom=90):
        points = [point(0, -1, 99), point(4, 1, 110), point(8, -1, 100),
                  point(12, 1, 108), point(18, -1, bottom), point(24, 1, rebound)]
        zone = {"i0": 0, "i1": 12, "ZD": 100, "ZG": 108, "k": 0}
        return points, zone

    def test_third_sell_uses_rebound_after_bottom_not_break_or_downleg_high(self):
        points, zone = self.third_fixture()
        bars = candles(points, 28)
        bars[15]["high"] = 103  # The breakdown candle itself traded above ZD.
        bars[16]["high"] = 99.8  # A declining-leg high exceeds the later rebound top.
        with structure(points, centers=[zone]):
            result = of_type(sell.detect_sell(bars), "三类卖点")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], bars[24]["date"])
        self.assertEqual(result[0]["stop"], 100)

    def test_third_sell_rejects_touch_and_above_zd_but_accepts_strictly_below(self):
        for top, expected in [(99.99, 1), (100, 0), (100.01, 0)]:
            points, zone = self.third_fixture(top)
            with self.subTest(top=top), structure(points, centers=[zone]):
                result = of_type(sell.detect_sell(candles(points, 28)), "三类卖点")
            self.assertEqual(len(result), expected)

    def test_third_sell_no_five_percent_rebound_gate_and_no_thirty_percent_distance_gate(self):
        for bottom, top in [(99.5, 99.9), (50, 60)]:
            points, zone = self.third_fixture(top, bottom)
            with self.subTest(bottom=bottom, top=top), structure(points, centers=[zone]):
                result = sell.detect_sell(candles(points, 28))
            self.assertEqual(len(of_type(result, "三类卖点")), 1)

    def test_monotonic_fall_without_rebound_stroke_cannot_be_third_sell(self):
        points, zone = self.third_fixture()
        points = points[:-1]
        bars = candles(points, 23)
        for index in range(19, len(bars)):
            bars[index].update(close=90 - (index - 18), high=90.5 - (index - 18), low=89.5 - (index - 18))
        with structure(points, centers=[zone]):
            result = sell.detect_sell(bars)
        self.assertEqual(of_type(result, "三类卖点"), [])

    def test_center_completed_after_departure_cannot_justify_earlier_break(self):
        points, zone = self.third_fixture()
        zone["i1"] = 24
        with structure(points, centers=[zone]):
            result = sell.detect_sell(candles(points, 28))
        self.assertEqual(of_type(result, "三类卖点"), [])

    def test_recovery_to_zd_before_rebound_invalidates_old_break_cycle(self):
        points, zone = self.third_fixture()
        bars = candles(points, 28)
        bars[21].update(close=100, high=100.5, low=99.5)
        with structure(points, centers=[zone]):
            result = sell.detect_sell(bars)
        self.assertEqual(of_type(result, "三类卖点"), [])

    def test_prefix_replay_cannot_backdate_first_detection_to_peak(self):
        points = [point(p["i"] + 30, p["t"], p["p"]) for p in self.first_points()]
        bars = candles(points, 54)
        with structure(points, [(35, 40, 10), (45, 50, 9.9)]):
            early = sell.detect_sell(bars[:51])
            visible = sell.detect_sell(bars[:52])
            history = sell.scan_signals_sell(bars, min_bars=40, types=("一类卖点",))
        self.assertEqual(of_type(early, "一类卖点"), [])
        self.assertEqual(len(of_type(visible, "一类卖点")), 1)
        self.assertEqual(history[0]["first_i"], 51)
        self.assertEqual(history[0]["sig_date"], bars[50]["date"])

    def test_history_splits_new_signal_date_and_new_stop_anchor(self):
        bars = candles(self.first_points(), 50)
        for changed in ({"date": 20260125}, {"stop": 120}):
            def detect(prefix):
                signal = {"type": "一类卖点", "date": 20260120, "stop": 110, "ZG": None, "ZD": None}
                if len(prefix) >= 45:
                    signal.update(changed)
                return [signal]
            with self.subTest(changed=changed), patch.object(sell, "detect_sell", side_effect=detect):
                history = sell.scan_signals_sell(bars, min_bars=40)
            self.assertEqual(len(history), 2)
            self.assertEqual((history[0]["first_i"], history[0]["last_i"]), (39, 43))
            self.assertEqual((history[1]["first_i"], history[1]["last_i"]), (44, 49))


if __name__ == "__main__":
    unittest.main()
