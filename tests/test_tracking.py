import copy
import threading
import unittest
from unittest.mock import patch

from core import datasource as ds, tracking
from core.validation import InputError


def quote_line(symbol, price="12.5", date="20261012150000", exchange=None, code=None,
               previous_close="10"):
    fields = [""] * 31
    fields[0] = exchange or {"sh": "1", "sz": "51", "bj": "62"}[symbol[:2]]
    fields[1] = "名称"
    fields[2] = code or symbol[2:]
    fields[3] = str(price)
    fields[4] = str(previous_close) if previous_close is not None else ""
    fields[30] = date
    return 'v_%s="%s";' % (symbol, "~".join(fields))


def row(symbol="sh600000", close=10, buy=None):
    return {"symbol": symbol, "code": symbol[2:], "close": close, "buy": buy or []}


def reply(prices, date="2026-10-12 15:00:00", previous_close=10):
    return {"quotes": {symbol: {"price": price, "date": date, "previous_close": previous_close}
                       for symbol, price in prices.items()},
            "errors": {}}


class QuotePriceTests(unittest.TestCase):
    def test_parse_sh_sz_star_and_bj_exactly(self):
        symbols = ["sh600000", "sz000001", "sh688001", "bj920002"]
        text = "\r\n".join(quote_line(symbol) for symbol in symbols)
        with patch.object(ds, "_get", return_value=text) as get:
            response = ds.quote_prices(symbols)
        self.assertEqual(set(response["quotes"]), set(symbols))
        self.assertEqual(response["errors"], {})
        self.assertEqual(response["quotes"]["sh688001"],
                         {"price": 12.5, "date": "2026-10-12 15:00:00", "previous_close": 10})
        self.assertEqual(get.call_args.args[0], "https://qt.gtimg.cn/q=" + ",".join(symbols))

    def test_same_code_keeps_exchange_and_rejects_mismatches(self):
        text = "\n".join([quote_line("sh000001", "3000"), quote_line("sz000001", "10"),
                           quote_line("sh600000", exchange="51"),
                           quote_line("bj920002", code="920003")])
        with patch.object(ds, "_get", return_value=text):
            response = ds.quote_prices(["sh000001", "sz000001", "sh600000", "bj920002"])
        self.assertEqual(response["quotes"]["sh000001"]["price"], 3000)
        self.assertEqual(response["quotes"]["sz000001"]["price"], 10)
        self.assertEqual(set(response["errors"]), {"sh600000", "bj920002"})

    def test_missing_wrong_symbol_duplicate_and_malformed_never_become_zero(self):
        text = "\n".join([quote_line("sz000001"), quote_line("sh600000"),
                           quote_line("sh600000"), 'v_bj920002="missing";'])
        with patch.object(ds, "_get", return_value=text):
            response = ds.quote_prices(["sh000001", "sh600000", "bj920002"])
        self.assertFalse(response["quotes"])
        self.assertEqual(set(response["errors"]), {"sh000001", "sh600000", "bj920002"})

    def test_invalid_price_or_date_is_an_error(self):
        invalid = [dict(price=p) for p in ("", "0", "-1", "nan", "inf", "1e999")]
        invalid += [dict(date=d) for d in ("", "20261012", "20260230150000",
                                           "20261012250000", "２０２６１０１２１５００００")]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), \
                 patch.object(ds, "_get", return_value=quote_line("sh600000", **kwargs)):
                response = ds.quote_prices(["sh600000"])
            self.assertFalse(response["quotes"])
            self.assertIn("sh600000", response["errors"])

    def test_previous_close_is_provider_field_and_invalid_value_retains_quote(self):
        with patch.object(ds, "_get", return_value=quote_line("sh600000", price="10",
                                                              previous_close="11")):
            quote = ds.quote_prices(["sh600000"])["quotes"]["sh600000"]
        self.assertEqual(quote["previous_close"], 11)
        for previous in (None, "", "bad", "0", "-1", "nan", "inf", "1e999"):
            with self.subTest(previous=previous), patch.object(ds, "_get", return_value=
                    quote_line("sh600000", previous_close=previous)):
                response = ds.quote_prices(["sh600000"])
            self.assertEqual(response["quotes"]["sh600000"]["price"], 12.5)
            self.assertIsNone(response["quotes"]["sh600000"]["previous_close"])
            self.assertEqual(response["errors"], {})

    def test_batch_failure_preserves_other_batch(self):
        symbols = ["sh%06d" % (600000 + i) for i in range(101)]

        def get(url, *args):
            requested = url.split("=", 1)[1].split(",")
            if len(requested) == 100:
                raise TimeoutError("offline")
            return quote_line(requested[0])

        with patch.object(ds, "_get", side_effect=get):
            response = ds.quote_prices(symbols)
        self.assertEqual(set(response["quotes"]), {symbols[-1]})
        self.assertEqual(len(response["errors"]), 100)

    def test_batches_are_bounded_to_100_and_four_workers(self):
        symbols = ["sh%06d" % (600000 + i) for i in range(401)]
        release, lock = threading.Event(), threading.Lock()
        active, peak, sizes = 0, 0, []

        def get(url, *args):
            nonlocal active, peak
            requested = url.split("=", 1)[1].split(",")
            with lock:
                sizes.append(len(requested))
                active += 1
                peak = max(peak, active)
                if active == 4:
                    release.set()
            if not release.wait(3):
                raise TimeoutError("four batches did not run together")
            with lock:
                active -= 1
            return "\n".join(quote_line(symbol) for symbol in requested)

        with patch.object(ds, "_get", side_effect=get):
            response = ds.quote_prices(symbols)
        self.assertEqual(len(response["quotes"]), 401)
        self.assertEqual(sorted(sizes), [1, 100, 100, 100, 100])
        self.assertEqual(peak, 4)

    def test_inputs_deduplicate_and_require_explicit_exchange(self):
        with patch.object(ds, "_get", return_value=quote_line("sh600000")) as get:
            self.assertEqual(len(ds.quote_prices([" SH600000 ", "sh600000"])["quotes"]), 1)
            self.assertEqual(get.call_args.args[0], "https://qt.gtimg.cn/q=sh600000")
        for symbols in ("sh600000", ["600000"], ["sh600000&x=1"], ["../x"]):
            with self.subTest(symbols=symbols), self.assertRaises(InputError):
                ds.quote_prices(symbols)
        with patch.object(ds, "_get") as get:
            self.assertEqual(ds.quote_prices([]), {"quotes": {}, "errors": {}})
            get.assert_not_called()


class TrackingTests(unittest.TestCase):
    def setUp(self):
        tracking._QUOTES.clear()
        self.addCleanup(tracking._QUOTES.clear)

    def test_returns_signed_change_pct_and_does_not_mutate_snapshot(self):
        rows = [row("sh600000", 10), row("sz000001", 20), row("sh688001", 8)]
        original = copy.deepcopy(rows)
        with patch.object(ds, "quote_prices", return_value=reply(
                {"sh600000": 12.345, "sz000001": 18, "sh688001": 8})):
            response = tracking.track_rows(rows)
        self.assertEqual(rows, original)
        quotes = response["quotes"]
        self.assertEqual((quotes["sh600000"]["change"], quotes["sh600000"]["change_pct"]),
                         (2.345, 23.45))
        self.assertEqual((quotes["sz000001"]["change"], quotes["sz000001"]["change_pct"]),
                         (-2, -10))
        self.assertEqual((quotes["sh688001"]["change"], quotes["sh688001"]["change_pct"]),
                         (0, 0))
        self.assertEqual(response["source"], "tencent.qt.gtimg.cn")
        self.assertRegex(response["as_of"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_code_selection_can_track_old_stock_and_is_exchange_qualified(self):
        rows = [row("sh000001", 3000), row("sz000001", 10), row("bj920002", 50)]
        with patch.object(ds, "quote_prices", return_value=reply({"sh000001": 3150})) as get:
            response = tracking.track_rows(rows, codes=["sh000001", "sh688999"])
        get.assert_called_once_with(["sh000001"])
        self.assertEqual(set(response["quotes"]), {"sh000001"})
        self.assertEqual(response["quotes"]["sh000001"]["change_pct"], 5)
        for codes in ("sh000001", ["000001"], ["sh000001,sz000001"]):
            with self.subTest(codes=codes), self.assertRaises(InputError):
                tracking.track_rows(rows, codes=codes)

    def test_cache_reuses_only_price_and_recomputes_snapshot_baseline(self):
        with patch.object(tracking.time, "monotonic", return_value=100), \
             patch.object(ds, "quote_prices", return_value=reply({"sh600000": 12})) as get:
            first = tracking.track_rows([row(close=10, buy=[
                {"type": "一类买点", "stop": 11, "valid": 1}])])
            second = tracking.track_rows([row(close=8, buy=[
                {"type": "二类买点", "stop": 13, "valid": 1}])])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(first["quotes"]["sh600000"]["change_pct"], 20)
        self.assertEqual(second["quotes"]["sh600000"]["change_pct"], 50)
        self.assertEqual(first["quotes"]["sh600000"]["daily_change"], 2)
        self.assertEqual(first["quotes"]["sh600000"]["daily_change_pct"], 20)
        self.assertEqual(second["quotes"]["sh600000"]["daily_change"], 2)
        self.assertEqual(second["quotes"]["sh600000"]["daily_change_pct"], 20)
        self.assertEqual(first["quotes"]["sh600000"]["scan_change_pct"], 20)
        self.assertEqual(second["quotes"]["sh600000"]["scan_change_pct"], 50)
        self.assertFalse(first["quotes"]["sh600000"]["buy_stop_breached"])
        self.assertTrue(second["quotes"]["sh600000"]["buy_stop_breached"])

    def test_cache_expires_and_refresh_forces_fetch(self):
        with patch.object(tracking.time, "monotonic", return_value=100), \
             patch.object(ds, "quote_prices", return_value=reply({"sh600000": 12})):
            tracking.track_rows([row()])
        with patch.object(tracking.time, "monotonic", return_value=159), \
             patch.object(ds, "quote_prices", return_value=reply({"sh600000": 13})) as get:
            tracking.track_rows([row()])
            get.assert_not_called()
            fresh = tracking.track_rows([row()], refresh=True)
            self.assertEqual(fresh["quotes"]["sh600000"]["price"], 13)
            self.assertEqual(get.call_count, 1)
        with patch.object(tracking.time, "monotonic", return_value=219), \
             patch.object(ds, "quote_prices", return_value=reply({"sh600000": 14})) as get:
            response = tracking.track_rows([row()])
            get.assert_called_once()
            self.assertEqual(response["quotes"]["sh600000"]["price"], 14)

    def test_new_calendar_day_invalidates_raw_quote_before_ttl(self):
        with patch.object(tracking.time, "monotonic", return_value=100), \
             patch.object(tracking, "_cache_day", return_value="2026-10-12"), \
             patch.object(ds, "quote_prices", return_value=reply({"sh600000": 10}, previous_close=11)):
            first = tracking.track_rows([row(close=8)])
        with patch.object(tracking.time, "monotonic", return_value=101), \
             patch.object(tracking, "_cache_day", return_value="2026-10-13"), \
             patch.object(ds, "quote_prices", return_value=reply({"sh600000": 12},
                         date="2026-10-13 09:30:00", previous_close=10)) as get:
            next_day = tracking.track_rows([row(close=8)])
        get.assert_called_once_with(["sh600000"])
        self.assertEqual(first["quotes"]["sh600000"]["daily_change_pct"], -9.09)
        self.assertEqual(next_day["quotes"]["sh600000"]["daily_change_pct"], 20)
        self.assertEqual(next_day["quotes"]["sh600000"]["previous_close"], 10)

    def test_failure_is_unknown_not_zero_and_is_not_cached(self):
        with patch.object(ds, "quote_prices", side_effect=[
                {"quotes": {}, "errors": {"sh600000": "offline"}}, reply({"sh600000": 11})]) as get:
            failed = tracking.track_rows([row()])
            good = tracking.track_rows([row()])
        quote = failed["quotes"]["sh600000"]
        self.assertEqual(quote["status"], "unavailable")
        for field in ("price", "date", "previous_close", "daily_change", "daily_change_pct",
                      "scan_change", "scan_change_pct", "change", "change_pct", "buy_stop_breached"):
            self.assertIsNone(quote[field])
        self.assertEqual(quote["daily_status"], "unavailable")
        self.assertEqual(quote["scan_status"], "unavailable")
        self.assertEqual(failed["errors"]["sh600000"], "offline")
        self.assertEqual(good["quotes"]["sh600000"]["change_pct"], 10)
        self.assertEqual(get.call_count, 2)

    def test_failed_refresh_does_not_reuse_or_keep_previous_success(self):
        with patch.object(ds, "quote_prices", side_effect=[reply({"sh600000": 11}),
                RuntimeError("offline"), reply({"sh600000": 12})]) as get:
            tracking.track_rows([row()])
            failed = tracking.track_rows([row()], refresh=True)
            recovered = tracking.track_rows([row()])
        self.assertEqual(failed["quotes"]["sh600000"]["status"], "unavailable")
        self.assertEqual(recovered["quotes"]["sh600000"]["price"], 12)
        self.assertEqual(get.call_count, 3)

    def test_missing_and_invalid_provider_quote_cannot_enter_cache(self):
        invalid = [None, "wrong", {}, {"price": float("nan"), "date": "2026-10-12 15:00:00"},
                   {"price": 0, "date": "2026-10-12 15:00:00"},
                   {"price": 12, "date": "2026-02-30 15:00:00"},
                   {"price": 12, "date": "2026-10-12"}]
        for quote in invalid:
            with self.subTest(quote=quote), patch.object(ds, "quote_prices", return_value={
                    "quotes": {"sh600000": quote}, "errors": {}}):
                response = tracking.track_rows([row()])
                self.assertEqual(response["quotes"]["sh600000"]["status"], "unavailable")
                for field in ("daily_change", "daily_change_pct", "scan_change", "scan_change_pct"):
                    self.assertIsNone(response["quotes"]["sh600000"][field])
                self.assertNotIn("sh600000", tracking._QUOTES)

    def test_invalid_baseline_retains_price_but_does_not_invent_return(self):
        for baseline in (0, -1, None, "bad", float("nan"), float("inf"), True, 5e-324):
            with self.subTest(baseline=baseline), \
                 patch.object(ds, "quote_prices", return_value=reply({"sh600000": 12})):
                quote = tracking.track_rows([row(close=baseline)])["quotes"]["sh600000"]
            self.assertEqual(quote["status"], "invalid_baseline")
            self.assertEqual(quote["price"], 12)
            self.assertIsNone(quote["change"])
            self.assertIsNone(quote["change_pct"])
            self.assertEqual(quote["daily_status"], "ok")
            self.assertEqual(quote["daily_change"], 2)
            self.assertEqual(quote["daily_change_pct"], 20)
            self.assertIsNone(quote["scan_change"])
            self.assertIsNone(quote["scan_change_pct"])

    def test_daily_and_scan_moves_can_have_opposite_directions(self):
        rows = [row(close=8)]
        original = copy.deepcopy(rows)
        with patch.object(ds, "quote_prices", return_value=reply({"sh600000": 10}, previous_close=11)):
            quote = tracking.track_rows(rows)["quotes"]["sh600000"]
        self.assertEqual(quote["previous_close"], 11)
        self.assertEqual((quote["daily_change"], quote["daily_change_pct"]), (-1, -9.09))
        self.assertEqual((quote["scan_change"], quote["scan_change_pct"]), (2, 25))
        self.assertEqual((quote["change"], quote["change_pct"]), (2, 25))
        self.assertEqual((quote["status"], quote["daily_status"], quote["scan_status"]), ("ok", "ok", "ok"))
        self.assertEqual(rows, original)

    def test_missing_previous_close_never_uses_snapshot_or_invents_daily_zero(self):
        for previous in (None, 0, -1, "bad", float("nan"), float("inf"), True, 5e-324):
            with self.subTest(previous=previous), patch.object(ds, "quote_prices",
                    return_value=reply({"sh600000": 10}, previous_close=previous)):
                response = tracking.track_rows([row(close=8)], refresh=True)
            quote = response["quotes"]["sh600000"]
            self.assertEqual(quote["price"], 10)
            self.assertEqual(quote["status"], "ok")
            self.assertEqual(quote["daily_status"], "invalid_previous_close")
            self.assertIsNone(quote["daily_change"])
            self.assertIsNone(quote["daily_change_pct"])
            self.assertEqual(quote["scan_change_pct"], 25)
            self.assertEqual(quote["change_pct"], 25)
            self.assertIn("昨收", response["errors"]["sh600000"])

    def test_independent_zero_and_negative_scan_move_with_valid_daily(self):
        with patch.object(ds, "quote_prices", return_value=reply({"sh600000": 10}, previous_close=10)):
            quote = tracking.track_rows([row(close=12)])["quotes"]["sh600000"]
        self.assertEqual((quote["daily_change"], quote["daily_change_pct"]), (0, 0))
        self.assertEqual((quote["scan_change"], quote["scan_change_pct"]), (-2, -16.67))
        self.assertEqual((quote["daily_status"], quote["scan_status"]), ("ok", "ok"))

    def test_stop_equality_breaches_only_originally_valid_finite_buy_stops(self):
        buy = [{"type": "一类买点", "stop": 10, "valid": 1},
               {"type": "二类买点", "stop": 11, "valid": 1},
               {"type": "二类买点", "stop": 12, "valid": 1},
               {"type": "三类买点", "stop": 15, "valid": 0},
               {"type": "bad", "stop": float("inf"), "valid": 1},
               {"type": "bad", "stop": 0, "valid": 1}]
        with patch.object(ds, "quote_prices", return_value=reply({"sh600000": 10})):
            quote = tracking.track_rows([row(close=13, buy=buy)])["quotes"]["sh600000"]
        self.assertTrue(quote["buy_stop_breached"])
        self.assertEqual(quote["breached_types"], ["一类买点", "二类买点"])
        with patch.object(ds, "quote_prices", return_value=reply({"sh600000": 13})):
            quote = tracking.track_rows([row(close=13, buy=buy)], refresh=True)["quotes"]["sh600000"]
        self.assertFalse(quote["buy_stop_breached"])

    def test_empty_selection_does_not_request_and_rejects_ambiguous_rows(self):
        with patch.object(ds, "quote_prices") as get:
            self.assertEqual(tracking.track_rows([row()], codes=[])["quotes"], {})
            self.assertEqual(tracking.track_rows([])["quotes"], {})
            get.assert_not_called()
        for rows in ([row(), row()], [row(symbol="600000")], [None]):
            with self.subTest(rows=rows), self.assertRaises(InputError):
                tracking.track_rows(rows)


if __name__ == "__main__":
    unittest.main()
