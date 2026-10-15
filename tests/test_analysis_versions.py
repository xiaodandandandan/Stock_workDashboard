import copy
import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fake_engine import install
install()

from core import engine, engine_identity, store
import scan
import server


def bars():
    start = datetime.date(2026, 1, 1)
    return [{"date": int((start + datetime.timedelta(days=i)).strftime("%Y%m%d")),
             "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000} for i in range(120)]


class AnalysisVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for attr, sub in (("SCAN_DIR", "scan"), ("KLINE_DIR", "kline")):
            changed = patch.object(store, attr, str(self.root / sub))
            changed.start()
            self.addCleanup(changed.stop)
        server._HIST.clear()
        self.addCleanup(server._HIST.clear)

    def legacy_scan(self):
        old = {"generated": "2026-10-09 15:00:00", "freq": "day", "hit": 1, "total_scanned": 1,
               "rows": [{"symbol": "sh600000", "code": "600000", "name": "A", "industry": "银行",
                         "sell": [{"type": "旧规则卖点", "valid": 1}]}]}
        store.save_scan(old)
        return old

    def test_analysis_identity_changes_with_sell_rules_and_keeps_buy_engine_identity(self):
        buy = copy.deepcopy(engine_identity(engine.K))
        with patch.object(engine.S, "SELL_RULES_VERSION", "test-v1", create=True):
            first = engine.analysis_identity()
            self.assertEqual(first, engine.analysis_identity())
        with patch.object(engine.S, "SELL_RULES_VERSION", "test-v2", create=True):
            second = engine.analysis_identity()
        self.assertRegex(first["analysis_version"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(first["analysis_version"], second["analysis_version"])
        self.assertEqual((first["sell_rules_version"], second["sell_rules_version"]), ("test-v1", "test-v2"))
        self.assertEqual(engine_identity(engine.K), buy)
        with patch.object(engine, "_PIPELINE_SHA256", "changed-local-analysis"):
            self.assertNotEqual(engine.analysis_identity()["analysis_version"], second["analysis_version"])

    def test_history_cache_invalidates_identical_bars_when_analysis_version_changes(self):
        data = bars()
        with patch.object(engine, "analysis_identity", return_value={"analysis_version": "v1"}) as identity, \
             patch.object(engine, "scan_history", side_effect=[["old-history"], ["new-history"]]) as calculate:
            self.assertEqual(server.cached_history("day", "sh600000", data), ["old-history"])
            self.assertEqual(server.cached_history("day", "sh600000", data), ["old-history"])
            identity.return_value = {"analysis_version": "v2"}
            self.assertEqual(server.cached_history("day", "sh600000", data), ["new-history"])
            self.assertEqual(calculate.call_count, 2)

    def test_stock_uses_raw_bar_cache_but_reports_current_analysis_identity(self):
        self.legacy_scan()
        data = bars()
        store.save_kline("day", "sh600000", data)
        raw_path = Path(store.kline_path("day", "sh600000"))
        before = raw_path.read_bytes()
        identity = {"analysis_version": "current-analysis", "sell_rules_version": "2026-10-16-v2"}
        with patch.object(engine, "analysis_identity", return_value=identity), \
             patch.object(engine, "scan_history", return_value=[]), \
             patch.object(engine, "analyze", return_value={"signals": {"buy": [], "sell": []}}), \
             patch.object(server.DS, "sina_kline") as fetch:
            result = server.stock_payload("sh600000")
        fetch.assert_not_called()
        self.assertTrue(result["cached"])
        self.assertEqual(result["data_version"], store.bars_version(data))
        self.assertEqual({field: result[field] for field in identity}, identity)
        self.assertEqual(raw_path.read_bytes(), before)

    def test_new_scan_and_attempt_get_versions_without_rewriting_old_history(self):
        old = self.legacy_scan()
        old_path = Path(store.scan_path(old["scan_id"]))
        old_bytes = old_path.read_bytes()
        data = bars()
        store.save_kline("day", "sh600000", data)
        raw_path = Path(store.kline_path("day", "sh600000"))
        raw_bytes = raw_path.read_bytes()
        identity = {"analysis_version": "current-analysis", "sell_rules_version": "2026-10-16-v2"}
        item = {"symbol": "sh600000", "code": "600000", "name": "A"}
        with patch.object(engine, "analysis_identity", return_value=identity), \
             patch.object(engine, "probe", return_value=([], [])), \
             patch.object(server.DS, "market_list", return_value=[item]), \
             patch.object(server.DS, "sina_kline", return_value=data), \
             patch.object(server.DS, "industry_map", return_value={"sh600000": {"l1": "银行"}}):
            result = scan.run_scan(workers=1)
        for payload in (result, store.load_scan(), store.load_scan_attempt()):
            self.assertEqual({field: payload[field] for field in identity}, identity)
        self.assertEqual(store.load_scan_attempt()["status"], "published")
        self.assertEqual(store.load_scan(old["scan_id"]), old)
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertEqual(raw_path.read_bytes(), raw_bytes)
        listing = {entry["id"]: entry for entry in store.list_scans()}
        self.assertEqual(listing[result["scan_id"]]["sell_rules_version"], identity["sell_rules_version"])
        self.assertNotIn("sell_rules_version", listing[old["scan_id"]])
        self.assertNotIn("analysis_version", listing[old["scan_id"]])

    def test_failed_scan_diagnostic_keeps_attempted_rules_but_does_not_relabel_latest(self):
        old = self.legacy_scan()
        identity = {"analysis_version": "new-analysis", "sell_rules_version": "test-v2"}
        with patch.object(engine, "analysis_identity", return_value=identity), \
             patch.object(scan.LOG, "exception"), \
             patch.object(server.DS, "market_list", side_effect=RuntimeError("offline")), self.assertRaises(RuntimeError):
            scan.run_scan(workers=1)
        self.assertEqual(store.load_scan(), old)
        self.assertEqual(store.load_scan_attempt()["sell_rules_version"], "test-v2")
        self.assertNotIn("sell_rules_version", store.load_scan())

    def test_health_distinguishes_running_rules_from_unversioned_latest(self):
        old = self.legacy_scan()
        identity = {"analysis_version": "new-analysis", "sell_rules_version": "test-v2"}
        response = []
        handler = object.__new__(server.Handler)
        handler.send_json = lambda payload: response.append(payload)
        with patch.object(engine, "analysis_identity", return_value=identity), patch.object(server, "CACHE", str(self.root)):
            handler.api_get("/api/health", {})
        self.assertEqual(response[0]["sell_rules_version"], "test-v2")
        self.assertEqual(response[0]["analysis_version"], "new-analysis")
        self.assertNotIn("sell_rules_version", response[0]["scan"])
        self.assertEqual(store.load_scan(), old)

    def test_health_preserves_actual_latest_versions(self):
        old = self.legacy_scan()
        old.update(analysis_version="old-analysis", sell_rules_version="test-v1")
        store.save_scan(old)
        response = []
        handler = object.__new__(server.Handler)
        handler.send_json = lambda payload: response.append(payload)
        with patch.object(engine, "analysis_identity", return_value={"analysis_version": "new-analysis", "sell_rules_version": "test-v2"}), \
             patch.object(server, "CACHE", str(self.root)):
            handler.api_get("/api/health", {})
        self.assertEqual(response[0]["scan"]["sell_rules_version"], "test-v1")
        self.assertEqual(response[0]["sell_rules_version"], "test-v2")

    def test_first_second_sells_allow_equal_stop_third_remains_strict_and_buy_unchanged(self):
        for kind, name, at_stop, above, below in (("s", "一类卖点", 1, 0, 1), ("s", "二类卖点", 1, 0, 1),
                                                 ("s", "三类卖点", 0, 0, 1), ("b", "一类买点", 0, 1, 0)):
            hit = {"type": name, "date": 20260101, "stop": 10, "why": []}
            for price, expected in ((10, at_stop), (10.01, above), (9.99, below)):
                with self.subTest(name=name, price=price):
                    self.assertEqual(engine.pack_signals([hit], kind, {}, price)[0]["valid"], expected)


if __name__ == "__main__":
    unittest.main()
