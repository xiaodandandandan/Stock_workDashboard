from datetime import date, datetime
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from core import fundamentals as fundamental
from core.validation import InputError


def report(period="2026-06-30", profit=400_000, yoy=100):
    return {"report_date": period, "announced_date": period[:4] + "-08-20", "revenue": 8_000_000,
            "net_profit": profit, "revenue_yoy": 10, "net_profit_yoy": yoy,
            "operating_cashflow": 300_000, "roe": 5, "gross_margin": 20, "debt_ratio": 60, "eps": 0.2}


def source_section(kind, symbol):
    if kind == "profile":
        data = {"symbol": symbol, "name": "公司 " + symbol, "industry": "制造业", "description": "公司经营范围"}
    elif kind == "financials":
        data = {"reports": [report(), report("2025-06-30", 200_000)]}
    elif kind == "valuation":
        data = {"symbol": symbol, "name": symbol, "price": 10, "date": "2026-10-12 15:00:00", "pe_ttm": 15, "pb": 2}
    else:
        data = {"items": [{"title": "公司签订重大合同公告", "date": "2026-10-12 10:00:00",
                           "url": "https://example.com/notice", "source": "公告原文", "kind": "公告", "summary": ""}],
                "coverage": {"announcements": {"status": "ok"}, "media_news": {"status": "empty"}}}
    return {"status": "ok", "data": data, "as_of": "2026-10-12", "source": "测试来源 " + kind,
            "url": "https://example.com/" + kind}


class FundamentalsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "fundamentals"
        self.clock = datetime(2026, 10, 12, 16).timestamp()
        self.sources = {}
        for attribute, value in (("FUNDAMENTAL_DIR", str(self.directory)),):
            change = patch.object(fundamental, attribute, value)
            change.start()
            self.addCleanup(change.stop)
        for attribute, value in (("_today", date(2026, 10, 12)),):
            change = patch.object(fundamental, attribute, return_value=value)
            change.start()
            self.addCleanup(change.stop)
        clock = patch.object(fundamental.time, "time", return_value=self.clock)
        self.time = clock.start()
        self.addCleanup(clock.stop)
        logging = patch.object(fundamental.LOG, "warning")
        logging.start()
        self.addCleanup(logging.stop)
        for kind in ("profile", "financials", "valuation", "news"):
            fetch = patch.object(fundamental.fundamental_sources, "fetch_" + kind,
                                 side_effect=lambda symbol, kind=kind: source_section(kind, symbol), create=True)
            self.sources[kind] = fetch.start()
            self.addCleanup(fetch.stop)

    def test_wrong_market_codes_rejected_before_any_source(self):
        for symbol in ("../x", "sh000001", "sh510300", "sz159915", "sz688001", "bj600000", "hk00700", "", "１２３４５６"):
            with self.subTest(symbol=symbol), self.assertRaises(InputError):
                fundamental.fundamental_payload(symbol)
        for fetch in self.sources.values():
            fetch.assert_not_called()
        with self.assertRaises(InputError):
            fundamental.fundamental_payload("sh600000", refresh="false")

    def test_complete_payload_has_facts_and_explicit_title_inference(self):
        result = fundamental.fundamental_payload("600000")
        self.assertEqual(result["symbol"], "sh600000")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(set(result["sections"]), {"profile", "financials", "valuation", "news"})
        self.assertTrue(result["cache"]["persisted"])
        self.assertFalse(result["cache"]["hit"])
        self.assertTrue((self.directory / "sh600000.json").is_file())
        signals = result["sections"]["financials"]["data"]["signals"]
        self.assertTrue(all(signal["kind"] == "事实" for signal in signals))
        item = result["sections"]["news"]["data"]["items"][0]
        self.assertEqual(item["impact"]["direction"], "偏积极")
        self.assertEqual(item["impact"]["kind"], "推断")
        self.assertIn("未阅读全文", item["impact"]["notice"])
        self.assertEqual(item["summary"], "")
        self.assertEqual(len(result["sources"]), 4)

    def test_cache_reuses_disk_payload_and_returns_unshared_objects(self):
        original = fundamental.fundamental_payload("sh600000")
        original["sections"]["financials"]["data"]["reports"][0]["net_profit"] = -999
        cached = fundamental.fundamental_payload("sh600000")
        self.assertTrue(cached["cache"]["hit"])
        self.assertEqual(cached["sections"]["financials"]["data"]["reports"][0]["net_profit"], 400_000)
        for fetch in self.sources.values():
            self.assertEqual(fetch.call_count, 1)

    def test_cache_expires_at_boundary_and_force_refresh_fetches(self):
        fundamental.fundamental_payload("sh600000")
        self.time.return_value = self.clock + fundamental.CACHE_TTL - 1
        self.assertTrue(fundamental.fundamental_payload("sh600000")["cache"]["hit"])
        self.time.return_value = self.clock + fundamental.CACHE_TTL
        self.assertFalse(fundamental.fundamental_payload("sh600000")["cache"]["hit"])
        self.assertFalse(fundamental.fundamental_payload("sh600000", refresh=True)["cache"]["hit"])
        for fetch in self.sources.values():
            self.assertEqual(fetch.call_count, 3)

    def test_cache_wrong_symbol_future_timestamp_and_schema_are_not_reused(self):
        fundamental.fundamental_payload("sh600000")
        path = str(self.directory / "sh600000.json")
        for field, invalid in (("symbol", "sz000001"), ("saved", self.clock + 1), ("schema", 999)):
            cached = fundamental.store.read_json(path)
            cached[field] = invalid
            fundamental.store.write_json(path, cached)
            result = fundamental.fundamental_payload("sh600000")
            self.assertFalse(result["cache"]["hit"])
        for fetch in self.sources.values():
            self.assertEqual(fetch.call_count, 4)

    def test_partial_failure_preserves_successful_sections_and_can_cache(self):
        self.sources["news"].side_effect = RuntimeError("news timeout")
        first = fundamental.fundamental_payload("sh600000")
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["sections"]["news"]["status"], "failed")
        self.assertIn("news timeout", first["sections"]["news"]["error"])
        self.assertTrue(first["sections"]["financials"]["data"]["reports"])
        self.assertTrue(fundamental.fundamental_payload("sh600000")["cache"]["hit"])

    def test_partial_financial_source_preserves_reports_and_error(self):
        section = source_section("financials", "sh600000")
        section.update(status="partial", errors=["cashflow timeout"])
        self.sources["financials"].side_effect = None
        self.sources["financials"].return_value = section
        result = fundamental.fundamental_payload("sh600000")
        financial = result["sections"]["financials"]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(financial["status"], "ok")
        self.assertTrue(financial["partial"])
        self.assertTrue(financial["data"]["reports"])
        self.assertIn("cashflow timeout", financial["errors"])

    def test_all_failed_is_not_cached_and_does_not_hide_retry(self):
        for fetch in self.sources.values():
            fetch.side_effect = RuntimeError("unavailable")
        for _ in range(2):
            result = fundamental.fundamental_payload("sh600000")
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["cache"]["persisted"])
        self.assertFalse((self.directory / "sh600000.json").exists())
        for fetch in self.sources.values():
            self.assertEqual(fetch.call_count, 2)

    def test_force_refresh_all_failure_removes_old_successful_cache(self):
        fundamental.fundamental_payload("sh600000")
        for fetch in self.sources.values():
            fetch.side_effect = RuntimeError("offline")
        result = fundamental.fundamental_payload("sh600000", refresh=True)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["cache"]["hit"])
        self.assertFalse((self.directory / "sh600000.json").exists())
        self.assertEqual(fundamental.fundamental_payload("sh600000")["status"], "failed")

    def test_empty_sources_are_distinct_from_transport_failure(self):
        for fetch in self.sources.values():
            fetch.side_effect = None
            fetch.return_value = {"status": "empty", "data": {}, "as_of": "", "url": "", "source": "无数据"}
        result = fundamental.fundamental_payload("sh600000")
        self.assertEqual(result["status"], "empty")
        self.assertFalse(result["cache"]["persisted"])
        self.assertTrue(all(section["status"] == "empty" for section in result["sections"].values()))

    def test_future_invalid_and_duplicate_report_dates_are_filtered(self):
        section = source_section("financials", "sh600000")
        section["data"]["reports"] = [report("2025-06-30"), report("2026-06-30"), report("2026-06-30"),
                                       report("2027-06-30"), report("2026-02-30"),
                                       dict(report("2026-09-30"), announced_date="2026-10-13")]
        self.sources["financials"].side_effect = None
        self.sources["financials"].return_value = section
        result = fundamental.fundamental_payload("sh600000")
        data = result["sections"]["financials"]["data"]
        self.assertEqual([r["report_date"] for r in data["reports"]], ["2026-06-30", "2025-06-30"])
        self.assertEqual(data["rejected_reports"], 3)

    def test_extended_financial_fields_and_complete_annual_periods_are_normalized(self):
        section = source_section("financials", "sh600000")
        current = section["data"]["reports"][0]
        current.update(net_profit_excl="-5000", parent_equity="3000000", goodwill="100000",
                       inventory="250000", accounts_receivable=None, financing_cashflow="50000",
                       cash="200000", undistributed_profit="-10000", operating_profit="20000",
                       operating_cost="5000000", source_reports={"balance": "bank_B_table"})
        annual = [dict(report("%s-12-31" % year), announced_date="%s-03-30" % (year + 1),
                       net_profit_excl=str(year * 100), parent_equity=1_000_000)
                  for year in range(2013, 2026)]
        annual += [report("2026-06-30"), report("2027-12-31"),
                   dict(report("2024-12-31"), announced_date="2024-08-20")]
        section["data"].update(annual_reports=annual, field_sources={"parent_equity": "TOTAL_PARENT_EQUITY"})
        self.sources["financials"].side_effect = None
        self.sources["financials"].return_value = section
        result = fundamental.fundamental_payload("sh600000")
        data = result["sections"]["financials"]["data"]
        self.assertEqual(result["schema"], 2)
        self.assertEqual([r["report_date"] for r in data["annual_reports"]],
                         ["%s-12-31" % year for year in range(2025, 2015, -1)])
        self.assertEqual(data["rejected_annual_reports"], 3)
        latest = data["reports"][0]
        self.assertEqual(latest["net_profit_excl"], -5000)
        self.assertEqual(latest["parent_equity"], 3_000_000)
        self.assertEqual(latest["source_reports"], {"balance": "bank_B_table"})
        self.assertEqual(data["field_sources"], {"parent_equity": "TOTAL_PARENT_EQUITY"})
        self.assertEqual(result["report"]["annual_coverage"]["count"], 10)
        self.assertEqual(result["report"]["verification"]["status"], "pending")

    def test_annual_data_remains_usable_when_recent_quarter_section_is_empty(self):
        section = source_section("financials", "sh600000")
        section["data"]["reports"] = []
        section["data"]["annual_reports"] = [dict(report("2025-12-31"), announced_date="2026-03-30", net_profit_excl=100)]
        self.sources["financials"].side_effect = None
        self.sources["financials"].return_value = section
        result = fundamental.fundamental_payload("sh600000")
        self.assertEqual(result["sections"]["financials"]["status"], "ok")
        self.assertEqual(result["report"]["latest_report_date"], "2025-12-31")
        self.assertEqual(result["report"]["kpis"][1]["value"], 100)

    def test_business_mix_keeps_actual_period_and_one_classification_without_zero_filling(self):
        section = source_section("financials", "sh600000")
        business = {"report_date": "2025-12-31", "classification": "产品", "status": "ok", "source": "主营构成",
                    "url": "https://example.com/business", "items": [{"name": "产品 A", "revenue": "100000",
                                                                          "gross_margin": "3.53", "share_pct": "99.75"}],
                    "periods": [{"report_date": "2024-12-31", "classification": "产品", "items": [{"name": "产品 A", "revenue": None}]},
                                {"report_date": "2023-12-31", "classification": "行业", "items": [{"name": "贸易", "revenue": 50}]},
                                {"report_date": "2027-06-30", "classification": "产品", "items": []}]}
        section["data"]["business_segments"] = business
        self.sources["financials"].side_effect = None
        self.sources["financials"].return_value = section
        result = fundamental.fundamental_payload("sh600000")
        data = result["report"]["business_segments"]
        self.assertEqual(result["report"]["latest_report_date"], "2026-06-30")
        self.assertEqual(data["report_date"], "2025-12-31")
        self.assertEqual([period["report_date"] for period in data["periods"]], ["2025-12-31", "2024-12-31"])
        self.assertEqual(data["incomparable_periods"], 1)
        self.assertEqual(data["items"][0]["share_pct"], 99.75)
        self.assertEqual(data["items"][0]["gross_margin"], 3.53)
        self.assertIsNone(data["periods"][1]["items"][0]["revenue"])

    def test_report_cache_arrays_links_and_old_schema_force_clean_refetch(self):
        fundamental.fundamental_payload("sh600000")
        path = str(self.directory / "sh600000.json")
        fields = ("kpis", "conclusions", "risk_exposure", "cash_quality", "scenarios", "checklist", "regulatory_checks", "valuation_notes")
        for field in fields:
            with self.subTest(field=field):
                cached = fundamental.store.read_json(path)
                cached["payload"]["report"][field] = [None]
                fundamental.store.write_json(path, cached)
                self.assertFalse(fundamental.fundamental_payload("sh600000")["cache"]["hit"])
        for invalid in (None, [None], [{"label": "unsafe", "url": "javascript:alert(1)"}]):
            with self.subTest(links=invalid):
                cached = fundamental.store.read_json(path)
                cached["payload"]["report"]["verification"]["links"] = invalid
                fundamental.store.write_json(path, cached)
                self.assertFalse(fundamental.fundamental_payload("sh600000")["cache"]["hit"])
        cached = fundamental.store.read_json(path)
        cached["schema"] = 1
        fundamental.store.write_json(path, cached)
        upgraded = fundamental.fundamental_payload("sh600000")
        self.assertFalse(upgraded["cache"]["hit"])
        self.assertEqual(upgraded["schema"], 2)

    def test_negative_profit_with_positive_yoy_does_not_become_profit_growth(self):
        signals = fundamental.financial_signals([report(profit=-400_000, yoy=25)])
        profit = next(signal for signal in signals if signal["title"] == "归母净利润")
        growth = next(signal for signal in signals if signal["title"] == "归母净利润同比")
        self.assertEqual(profit["direction"], "偏负面")
        self.assertIn("亏损", profit["reason"])
        self.assertEqual(growth["direction"], "需核验")
        self.assertIn("负基数", growth["reason"])

    def test_negative_base_compares_profit_amounts_and_zero_is_not_profit(self):
        previous = report("2025-06-30", -400_000)
        narrowed = fundamental.financial_signals([report(profit=-200_000), previous])
        self.assertTrue(any(signal["title"] == "同比亏损收窄" for signal in narrowed))
        zero = fundamental.financial_signals([report(profit=0), previous])
        self.assertTrue(any(signal["title"] == "同比亏损收窄至零" for signal in zero))
        self.assertFalse(any(signal["title"] == "净利润同比扭亏" for signal in zero))
        turned = fundamental.financial_signals([report(profit=200_000), previous])
        self.assertTrue(any(signal["title"] == "净利润同比扭亏" for signal in turned))

    def test_bank_metrics_have_no_common_enterprise_risk_threshold(self):
        bank = dict(report(), operating_cashflow=-100_000_000, gross_margin=None, debt_ratio=95, roe=-1)
        signals = fundamental.financial_signals([bank], {"industry": "银行"})
        cash = next(signal for signal in signals if signal["title"] == "经营活动现金流")
        debt = next(signal for signal in signals if signal["title"] == "资产负债率")
        self.assertEqual(cash["direction"], "中性")
        self.assertIn("银行", cash["reason"])
        self.assertEqual(debt["direction"], "中性")
        self.assertNotIn("高风险", str(signals))

    def test_title_keywords_negation_and_mixed_directions_require_verification(self):
        self.assertEqual(fundamental.title_impact("业绩预增公告")["direction"], "偏积极")
        self.assertEqual(fundamental.title_impact("亏损扩大公告")["direction"], "偏负面")
        for title in ("回购及股东减持公告", "终止回购计划", "未中标重大合同", "未实施增持计划", "本年度不分红", "年度股东大会通知"):
            with self.subTest(title=title):
                impact = fundamental.title_impact(title)
                self.assertEqual(impact["direction"], "需核验")
                self.assertIn("未阅读全文", impact["notice"])

    def test_explicit_negated_risk_and_actions_do_not_get_direction_from_keywords(self):
        for title in ("减持计划届满未减持", "未收到立案通知", "尚未收到监管部门的立案调查通知",
                      "公司不存在诉讼", "不存在重大诉讼事项", "公司无违约情况", "没有发生违约",
                      "回购期限届满未回购", "未被处罚", "未涉及诉讼"):
            with self.subTest(title=title):
                impact = fundamental.title_impact(title)
                self.assertEqual(impact["direction"], "需核验")
                self.assertIn("否定", impact["reason"])
        # A company-name character is not grammatical negation.
        self.assertEqual(fundamental.title_impact("无锡公司发布股东减持公告")["direction"], "偏负面")
        self.assertEqual(fundamental.title_impact("无锡公司发布股份回购公告")["direction"], "偏积极")

    def test_news_coverage_failure_and_future_items_are_explicit(self):
        section = source_section("news", "sh600000")
        section["data"]["coverage"]["media_news"] = {"status": "failed", "error": "timeout"}
        section["data"]["items"] += [dict(section["data"]["items"][0], date="2026-10-13 10:00:00"),
                                        dict(section["data"]["items"][0], date="2026-02-30 10:00:00")]
        self.sources["news"].side_effect = None
        self.sources["news"].return_value = section
        result = fundamental.fundamental_payload("sh600000")
        news = result["sections"]["news"]
        self.assertEqual(result["status"], "partial")
        self.assertTrue(news["partial"])
        self.assertEqual(len(news["data"]["items"]), 1)
        self.assertEqual(news["data"]["rejected_items"], 2)
        self.assertIn("media_news", news["error"])

    def test_news_all_sources_failed_retains_coverage_in_partial_page(self):
        section = source_section("news", "sh600000")
        section["status"] = "failed"
        section["errors"] = ["announcements: timeout", "media_news: offline"]
        section["data"]["items"] = []
        section["data"]["coverage"] = {
            "announcements": {"status": "failed", "error": "timeout"},
            "media_news": {"status": "failed", "error": "offline"}}
        self.sources["news"].side_effect = None
        self.sources["news"].return_value = section
        result = fundamental.fundamental_payload("sh600000")
        self.assertEqual(result["status"], "partial")
        news = result["sections"]["news"]
        self.assertEqual(news["status"], "failed")
        self.assertEqual(news["data"]["coverage"]["media_news"]["error"], "offline")

    def test_corrupted_cached_sections_refetch_and_malformed_source_is_local_failure(self):
        fundamental.fundamental_payload("sh600000")
        path = str(self.directory / "sh600000.json")
        cached = fundamental.store.read_json(path)
        cached["payload"]["sections"]["financials"]["data"]["reports"] = None
        fundamental.store.write_json(path, cached)
        self.sources["profile"].side_effect = None
        self.sources["profile"].return_value = []
        result = fundamental.fundamental_payload("sh600000")
        self.assertFalse(result["cache"]["hit"])
        self.assertEqual(result["sections"]["profile"]["status"], "failed")
        self.assertTrue(result["sections"]["financials"]["data"]["reports"])
        self.assertEqual(result["status"], "partial")

    def test_mismatched_provider_symbol_fails_only_that_section(self):
        self.sources["profile"].side_effect = lambda symbol: source_section("profile", "sz000001")
        result = fundamental.fundamental_payload("sh600000")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sections"]["profile"]["status"], "failed")
        self.assertEqual(result["sections"]["valuation"]["data"]["symbol"], "sh600000")

    def test_disk_failure_does_not_hide_successful_source_data(self):
        with patch.object(fundamental.store, "write_json", side_effect=OSError("disk full")):
            result = fundamental.fundamental_payload("sh600000")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["cache"]["persisted"])
        self.assertIn("disk full", result["cache"]["error"])
        self.assertTrue(result["sections"]["financials"]["data"]["reports"])

    def test_concurrent_symbols_fetch_independently_and_keep_cache_isolated(self):
        barrier = threading.Barrier(2)
        results, errors = {}, []

        def profile(symbol):
            barrier.wait(5)
            return source_section("profile", symbol)

        self.sources["profile"].side_effect = profile

        def fetch(symbol):
            try:
                results[symbol] = fundamental.fundamental_payload(symbol)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=fetch, args=(symbol,)) for symbol in ("sh600000", "sz000001")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        for symbol, result in results.items():
            self.assertEqual(result["sections"]["profile"]["data"]["symbol"], symbol)
            self.assertEqual(fundamental.store.read_json(str(self.directory / (symbol + ".json")))["symbol"], symbol)

    def test_concurrent_same_symbol_shares_the_completed_disk_cache(self):
        started, release = threading.Event(), threading.Event()
        results, errors = [], []

        def profile(symbol):
            started.set()
            if not release.wait(5):
                raise TimeoutError("test release")
            return source_section("profile", symbol)

        self.sources["profile"].side_effect = profile

        def fetch():
            try:
                results.append(fundamental.fundamental_payload("sh600000"))
            except Exception as exc:
                errors.append(exc)

        first, second = threading.Thread(target=fetch), threading.Thread(target=fetch)
        first.start()
        self.assertTrue(started.wait(5))
        second.start()
        release.set()
        first.join(10)
        second.join(10)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertFalse(errors)
        self.assertEqual(sorted(result["cache"]["hit"] for result in results), [False, True])
        for source in self.sources.values():
            self.assertEqual(source.call_count, 1)


if __name__ == "__main__":
    unittest.main()
