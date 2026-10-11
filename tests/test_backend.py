import copy
import datetime
import http.client
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fake_engine import install
install()

import core
from core import datasource as ds, engine, store
from core.validation import InputError, to_symbol
import scan
import server

logging.disable(logging.CRITICAL)


def bars(n=120, close=10):
    start = datetime.date(2026, 1, 1)
    return [dict(date=int((start + datetime.timedelta(days=i)).strftime('%Y%m%d')),
                 open=close, high=close + 1, low=close - 1, close=close, volume=1000) for i in range(n)]


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for attr, sub in (("KLINE_DIR", "kline"), ("SCAN_DIR", "scan")):
            p = patch.object(store, attr, str(self.root / sub))
            p.start()
            self.addCleanup(p.stop)
        server._HIST.clear()

    def saved_scan(self):
        old = dict(generated="2026-10-09 15:00:00", rows=[dict(code="600000", symbol="sh600000")], hit=1)
        store.save_scan(old)
        return old

    def test_symbols_preserve_exchange_and_reject_paths(self):
        cases = {"000001": "sz000001", " SH000001 ": "sh000001", "510300": "sh510300",
                 "159915": "sz159915", "920007": "bj920007", "00700": "hk00700"}
        for raw, expected in cases.items():
            self.assertEqual(to_symbol(raw), expected)
        for raw in ("../x", "sh../x", "600000/../../", "sh123", "600000&x=y", "", "１２３４５６"):
            with self.subTest(raw=raw), self.assertRaises(InputError): to_symbol(raw)

    def test_exchange_caches_are_independent(self):
        store.save_kline("day", "sz000001", bars(close=10))
        store.save_kline("day", "sh000001", bars(close=3000))
        self.assertEqual(store.load_kline("day", "sz000001")[-1]["close"], 10)
        self.assertEqual(store.load_kline("day", "sh000001")[-1]["close"], 3000)

    def test_legacy_or_mismatched_cache_is_not_reused(self):
        store.write_json(str(self.root / "kline/day/000001.json"), {"d": [20260101]})
        self.assertIsNone(store.load_kline("day", "sh000001"))
        store.save_kline("day", "sz000001", bars())
        data = store.read_json(store.kline_path("day", "sz000001"))
        store.write_json(store.kline_path("day", "sh000001"), data)
        self.assertIsNone(store.load_kline("day", "sh000001"))

    def test_stock_metadata_uses_symbol(self):
        store.save_scan(dict(generated="2026-10-09 15:00:00", rows=[
            dict(symbol="sz000001", code="000001", name="Bank", industry="Banking")]))
        store.save_kline("day", "sz000001", bars(close=10))
        store.save_kline("day", "sh000001", bars(close=3000))
        with patch.object(ds, "quote_name", return_value="Index") as quote, \
             patch.object(server, "industry_lookup", return_value={}):
            p = server.stock_payload("sh000001")
        self.assertEqual((p["name"], p["close"], p["in_scan"]), ("Index", 3000, False))
        quote.assert_called_once_with("sh000001")

    def test_history_cache_tracks_all_bar_revisions_and_length(self):
        original = bars()
        revised = copy.deepcopy(original)
        revised[-1]["close"] = 12
        historical = copy.deepcopy(revised)
        historical[3]["high"] = 20
        with patch.object(engine, "scan_history", side_effect=lambda b: [store.bars_version(b)]) as compute:
            results = [server.cached_history("day", "sh600000", b)
                       for b in (original, revised, historical, historical[1:])]
            self.assertEqual(len(set(x[0] for x in results)), 4)
            self.assertEqual(server.cached_history("day", "sh600000", original), results[0])
            self.assertEqual(compute.call_count, 4)

    def test_missing_report_does_not_block_analysis(self):
        with patch.dict("sys.modules", {"run_chanlun": None}):
            self.assertNotIn("report", engine.analyze(bars()))
            self.assertIn("文字报告不可用", engine.analyze(bars(), with_report=True)["report"])

    def test_concurrent_writes_have_independent_temporary_files(self):
        target = str(self.root / "atomic.json")
        real_replace = os.replace
        a_ready, b_done = threading.Event(), threading.Event()
        errors, sources = [], []

        def replace(src, dst):
            sources.append(src)
            if threading.current_thread().name == "A":
                a_ready.set()
                if not b_done.wait(5): raise TimeoutError()
                return real_replace(src, dst)
            try: return real_replace(src, dst)
            finally: b_done.set()

        def write(name):
            try:
                if name == "B" and not a_ready.wait(5): raise TimeoutError()
                store.write_json(target, {"writer": name, "values": list(range(1000))})
            except Exception as e: errors.append(e)

        with patch.object(store.os, "replace", side_effect=replace):
            threads = [threading.Thread(target=write, args=(name,), name=name) for name in ("A", "B")]
            for t in threads: t.start()
            for t in threads: t.join(10)
        self.assertFalse(errors)
        self.assertEqual(len(set(sources)), 2)
        self.assertEqual(store.read_json(target)["writer"], "A")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_failed_write_cleans_its_temporary_file(self):
        with patch.object(store.os, "replace", side_effect=OSError("disk")), self.assertRaises(OSError):
            store.write_json(str(self.root / "bad.json"), {"x": 1})
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_snapshot_write_failure_does_not_publish(self):
        old = self.saved_scan()
        with patch.object(store, 'write_json', side_effect=OSError('disk full')), self.assertRaises(OSError):
            store.save_scan(dict(generated='2026-10-11 12:00:00', rows=[], hit=0))
        self.assertEqual(store.load_scan(), old)

    def test_compute_failure_is_separate_from_fetch_failure(self):
        old = self.saved_scan()
        with patch.object(ds, 'market_list', return_value=[dict(symbol='sh600000', code='600000', name='A')]), \
             patch.object(ds, 'sina_kline', return_value=bars()), \
             patch.object(engine, 'probe', side_effect=RuntimeError('engine failure')), self.assertRaises(RuntimeError):
            scan.run_scan(workers=1)
        self.assertEqual(store.load_scan(), old)
        self.assertEqual(store.load_scan_attempt()['error_details'][0]['stage'], 'compute')

    def test_list_failure_preserves_published_scan(self):
        old = self.saved_scan()
        with patch.object(ds, "market_list", side_effect=ds.MarketListError("page timeout")), self.assertRaises(RuntimeError):
            scan.run_scan()
        self.assertEqual(store.load_scan(), old)
        self.assertEqual(store.load_scan_attempt()["status"], "failed")

    def test_empty_list_preserves_published_scan(self):
        old = self.saved_scan()
        with patch.object(ds, "market_list", return_value=[]), self.assertRaises(RuntimeError): scan.run_scan()
        self.assertEqual(store.load_scan(), old)

    def test_all_kline_failures_preserve_published_scan(self):
        old = self.saved_scan()
        items = [dict(symbol="sh600000", code="600000", name="A")]
        with patch.object(ds, "market_list", return_value=items), \
             patch.object(ds, "sina_kline", side_effect=TimeoutError("offline")), self.assertRaises(RuntimeError):
            scan.run_scan(workers=1)
        self.assertEqual(store.load_scan(), old)
        attempt = store.load_scan_attempt()
        self.assertEqual(attempt["errors"], 1)
        self.assertEqual(attempt["error_details"][0]["stage"], "fetch")

    def test_acceptable_partial_scan_explicitly_reports_coverage(self):
        items = [dict(symbol="sh%06d" % (600000 + i), code=str(600000 + i), name="A") for i in range(20)]
        def fetch(symbol, *a):
            if symbol == "sh600000": raise TimeoutError("one unavailable stock")
            return bars()
        with patch.object(ds, "market_list", return_value=items), patch.object(ds, "sina_kline", side_effect=fetch), \
             patch.object(ds, "industry_map", return_value={}):
            result = scan.run_scan(workers=2)
        self.assertEqual((result["errors"], result["complete"], result["hit"]), (1, False, 0))
        self.assertEqual(result["skipped"]["no_signal"], 19)

    def test_successful_zero_signal_scan_is_publishable(self):
        self.saved_scan()
        items = [dict(symbol="sh600000", code="600000", name="A")]
        with patch.object(ds, "market_list", return_value=items), patch.object(ds, "sina_kline", return_value=bars()), \
             patch.object(ds, "industry_map", return_value={}):
            result = scan.run_scan(workers=1)
        self.assertTrue(result["complete"])
        self.assertEqual(store.load_scan()["hit"], 0)

    def test_market_page_error_is_not_an_empty_page(self):
        def fetch(url, *a):
            if "page=2&" in url: raise TimeoutError()
            return json.dumps([dict(symbol="sh600000", name="A", code="600000")])
        with patch.object(ds, "_get", side_effect=fetch), self.assertRaises(ds.MarketListError):
            ds.market_list(pages=2, num=100)

    def test_industry_filters_market_and_preserves_exchange_across_pages(self):
        rows = [dict(SECUCODE="000001.SZ", SECURITY_CODE="000001", EM2016="金融-银行"),
                dict(SECUCODE="600000.SH", SECURITY_CODE="600000", EM2016="金融-银行"),
                dict(SECUCODE="AAPL.US", SECURITY_CODE="AAPL", EM2016="信息技术-硬件"),
                dict(SECUCODE="920403.BJ", SECURITY_CODE="920403", EM2016="农林牧渔-农业")]
        requested = []
        def fetch(url, *args):
            qs = parse_qs(urlparse(url).query)
            self.assertEqual(qs["filter"], ['(SECURITY_TYPE="A股")(LISTING_STATE="0")'])
            self.assertIn("SECUCODE", qs["columns"][0])
            self.assertEqual(qs["sortColumns"], ["SECUCODE"])
            p = int(qs["pageNumber"][0])
            requested.append(p)
            return json.dumps(dict(success=True, result=dict(pages=2, count=4, data=rows[(p - 1)*2:p*2])))
        with patch.object(ds, "_get", side_effect=fetch):
            mapping = ds.industry_map(size=2)
        self.assertEqual(set(mapping), {"sz000001", "sh600000", "bj920403"})
        self.assertEqual(mapping["bj920403"]["l1"], "农林牧渔")
        self.assertEqual(sorted(requested), [1, 2])

    def test_industry_rejects_business_errors_and_truncated_pagination(self):
        row = dict(SECUCODE="000001.SZ", SECURITY_CODE="000001", EM2016="金融-银行")
        for response in (dict(success=False, message="服务不可用", result=None),
                         dict(success=True, result=dict(pages=1, count=2, data=[row]))):
            with self.subTest(response=response), patch.object(ds, "_get", return_value=json.dumps(response)), \
                 self.assertRaises(ds.IndustryError):
                ds.industry_map()
        first = json.dumps(dict(success=True, result=dict(pages=2, count=2, data=[row])))
        with patch.object(ds, "_get", side_effect=[first, TimeoutError("page 2")]), \
             self.assertRaisesRegex(ds.IndustryError, "第 2 页"):
            ds.industry_map(size=1)
        with patch.object(ds, "_get", return_value=first), self.assertRaises(ds.IndustryError):
            ds.industry_map(pages=1, size=1)

    def test_industry_failure_does_not_poison_process_cache(self):
        mapping = {"sh600000": dict(l1="金融", full="金融-银行")}
        with patch.object(server, "_IND", None), \
             patch.object(ds, "industry_map", side_effect=[ds.IndustryError("offline"), {}, mapping]) as fetch:
            self.assertEqual(server.industry_lookup(), {})
            self.assertEqual(server.industry_lookup(), {})
            self.assertEqual(server.industry_lookup(), mapping)
            self.assertEqual(server.industry_lookup(), mapping)
            self.assertEqual(fetch.call_count, 3)

    def test_scan_records_industry_failure_and_partial_coverage(self):
        item = dict(symbol="sh600000", code="600000", name="A", buy=[], sell=[])
        other = dict(item, symbol="sz000001", code="000001", name="B")
        for fail in (False, True):
            with self.subTest(fail=fail), patch.object(ds, "market_list", return_value=[item, other]), \
                 patch.object(scan, "scan_one", side_effect=lambda x, *a: dict(x)), \
                 patch.object(ds, "industry_map", side_effect=ds.IndustryError("offline") if fail else None,
                              return_value={"sh600000": dict(l1="金融", full="金融-银行")}):
                result = scan.run_scan(workers=1)
            status = result["industry_status"]
            self.assertEqual(status["status"], "failed" if fail else "partial")
            self.assertEqual(status["matched"], 0 if fail else 1)
            self.assertEqual(status["missing"], 2 if fail else 1)
            self.assertEqual(store.load_scan_attempt()["industry_status"], status)
            self.assertEqual(result["errors"], 0)  # K-line/engine status remains separate.

    def test_stock_refreshes_industry_when_saved_row_is_unclassified(self):
        store.save_scan(dict(rows=[dict(symbol="sh600000", code="600000", name="A", industry="未分类")]))
        store.save_kline("day", "sh600000", bars())
        with patch.object(server, "industry_lookup", return_value={"sh600000": dict(l1="金融", full="金融-银行")}):
            result = server.stock_payload("sh600000")
        self.assertEqual(result["industry"], "金融")

    def test_market_page_limit_requires_end_of_market(self):
        with patch.object(ds, "_get", return_value='[{"symbol":"sh600000","name":"A"}]'), \
             self.assertRaises(ds.MarketListError): ds.market_list(pages=1, num=1)

    def test_market_normal_empty_tail_is_accepted(self):
        def fetch(url, *a):
            return 'null' if "page=2&" in url else '[{"symbol":"sh600000","name":"A"}]'
        with patch.object(ds, "_get", side_effect=fetch):
            self.assertEqual(len(ds.market_list(pages=2)), 1)

    def test_transport_retries_are_bounded_and_tls_is_verified(self):
        with patch.object(ds.urllib.request, "urlopen", side_effect=[TimeoutError(), TimeoutError(), io.BytesIO(b'[]')]) as request, \
             patch.object(ds.time, "sleep"):
            self.assertEqual(ds._get("https://example.invalid", {}), '[]')
            self.assertEqual(request.call_count, 3)
        self.assertTrue(ds.CTX.check_hostname)
        self.assertEqual(ds.CTX.verify_mode, ds.ssl.CERT_REQUIRED)

    def test_scan_parameters_reject_invalid_values(self):
        for options in ({"workers": 0}, {"workers": 33}, {"workers": True}, {"limit": -1},
                        {"count": 20}, {"freq": "../x"}, {"lookback": "1.2"}):
            with self.subTest(options=options), self.assertRaises(InputError): scan.validate_options(**options)

    def test_static_paths_reject_sibling_and_encoded_traversal(self):
        web = self.root / 'web'
        web.mkdir()
        (web / 'index.html').write_text('OK')
        handler = object.__new__(server.Handler)
        sent, errors = [], []
        handler.send_file = sent.append
        handler.send_error = lambda code, *a: errors.append(code)
        with patch.object(server, "WEB", str(web)):
            handler.static('/')
            for path in ('/../web_private/a', '/%2e%2e/web_private/a', '/..\\web_private\\a'):
                handler.static(path)
        self.assertEqual(sent, [(web / 'index.html').resolve()])
        self.assertEqual(errors, [403, 403, 403])

    def test_static_symlink_cannot_escape(self):
        web = self.root / 'web'
        web.mkdir()
        outside = self.root / 'secret.txt'
        outside.write_text('secret')
        try: (web / 'index.html').symlink_to(outside)
        except OSError: self.skipTest('OS does not permit creating symlinks')
        handler = object.__new__(server.Handler)
        errors = []
        handler.send_file = lambda p: self.fail('escaped static root')
        handler.send_error = lambda code, *a: errors.append(code)
        with patch.object(server, 'WEB', str(web)): handler.static('/')
        self.assertEqual(errors, [403])

    def test_engine_hash_can_be_pinned(self):
        with patch.dict(os.environ, {"CHANLUN_ENGINE_SHA256": "not-the-test-engine"}), self.assertRaises(RuntimeError):
            core.require_engine()


class HttpValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def request(self, method, path, body=None):
        c = http.client.HTTPConnection(*self.http.server_address, timeout=5)
        try:
            c.request(method, path, body=body)
            r = c.getresponse()
            return r.status, r.read()
        finally: c.close()

    def test_invalid_posts_never_start_a_scan(self):
        with patch.object(server, 'start_scan', wraps=server.start_scan) as start:
            for body in ('{', '[]', '{"workers":0}', '{"freq":"bad"}', '{"extra":1}', 'x' * 9000):
                self.assertEqual(self.request('POST', '/api/scan', body)[0], 400)
            self.assertFalse(server.JOB['running'])

    def test_invalid_get_parameters_return_400(self):
        for query in ('code=../x', 'code=600000&freq=bad', 'code=600000&lookback=no',
                      'code=600000&report=2'):
            with self.subTest(query=query):
                self.assertEqual(self.request('GET', '/api/stock?' + query)[0], 400)


if __name__ == '__main__': unittest.main()
