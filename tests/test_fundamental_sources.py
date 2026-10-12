import json
import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from core import fundamental_sources as sources
from core.validation import InputError


def fact(symbol="sh600519", **values):
    return {"SECUCODE": symbol[2:] + "." + symbol[:2].upper(), "SECURITY_CODE": symbol[2:], **values}


def report_body(rows, success=True):
    return {"success": success, "result": {"data": rows}}


def financial_get(tables):
    def get(url, *args, **kwargs):
        assert url.startswith(sources.DATACENTER)
        report = parse_qs(urlparse(url).query)["reportName"][0]
        value = tables.get(report, [])
        if isinstance(value, Exception):
            raise value
        return json.dumps(report_body(value))
    return get


def announcement(symbol="sh600519", name="贵州茅台", date="2026-10-12 18:00:00:000", article="AN202610121830369891"):
    market, tag = {"sh": ("1", "SHA"), "sz": ("0", "SZA"), "bj": ("0", "BJA")}[symbol[:2]]
    return {"codes": [{"stock_code": symbol[2:], "short_name": name,
                       "market_code": market, "ann_type": "A," + tag}],
            "title": name + ":回购股份公告", "art_code": article,
            "display_time": date, "notice_date": "2026-10-13 00:00:00"}


def article(title="贵州茅台回购股份", date="2026-10-12 17:00:00", code="202610123892645349", **values):
    return {"title": title, "date": date, "content": "<em>贵州茅台</em>公告提及回购股份，全文请见原文。",
            "mediaName": "证券时报网", "url": "http://finance.eastmoney.com/a/" + code + ".html", **values}


def news_get(ann, media):
    def get(url, *args, **kwargs):
        if url.startswith(sources.ANNOUNCEMENTS):
            if isinstance(ann, Exception):
                raise ann
            return json.dumps({"success": 1, "data": {"list": ann}})
        if url.startswith(sources.MEDIA_SEARCH):
            if isinstance(media, Exception):
                raise media
            return "callback(" + json.dumps({"code": 0, "result": {"cmsArticleWebOld": media}}) + ");"
        raise AssertionError("Unexpected provider URL: " + url)
    return get


class FundamentalSourceTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(sources, "_now", return_value=datetime(2026, 10, 12, 20))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_symbols_cover_supported_boards_and_reject_non_stocks_or_urls(self):
        for value, expected in [("600519", "sh600519"), ("000001", "sz000001"),
                                ("sh688004", "sh688004"), ("bj920008", "bj920008"), ("831000", "bj831000")]:
            self.assertEqual(sources.validate_symbol(value), expected)
        for value in ("sh000001", "510300", "159915", "00700", "sz600519", "https://localhost/x",
                      "600519&filter=bad", "../x"):
            with self.subTest(value=value), self.assertRaises(InputError):
                sources.fetch_profile(value)

    def test_profile_maps_only_verified_fields_and_fixed_exchange_query(self):
        row = fact(SECURITY_NAME_ABBR="贵州茅台", ORG_NAME="贵州茅台酒股份有限公司", EM2016="食品饮料-白酒",
                   ORG_PROFIE="  公司简介  ", ORG_PROFILE="另一摘要", MAIN_BUSINESS="生产白酒",
                   BUSINESS_SCOPE="白酒销售", ORG_WEB="www.example.com", MAX_DATE="2026-06-30 00:00:00")
        with patch.object(sources.DS, "_get", return_value=json.dumps(report_body([row]))) as get:
            result = sources.fetch_profile("sh600519")
        self.assertEqual(result["data"]["name"], "贵州茅台")
        self.assertEqual(result["data"]["description"], "公司简介")
        self.assertIsNone(result["data"]["data_date"])
        self.assertEqual(result["status"], "ok")
        query = parse_qs(urlparse(get.call_args.args[0]).query)
        self.assertEqual(query["filter"], ['(SECUCODE="600519.SH")'])
        self.assertEqual(get.call_args.kwargs["timeout"], 8)

    def test_report_rejects_wrong_exchange_code_duplicate_company_and_schema(self):
        responses = [report_body([fact("sz600519", SECURITY_NAME_ABBR="错误公司")]),
                     report_body([fact(SECURITY_CODE="600520")]), report_body([fact(), fact()]),
                     {"success": True, "result": None}, {"success": False, "message": "failed"}]
        for body in responses:
            with self.subTest(body=body), patch.object(sources.DS, "_get", return_value=json.dumps(body)), \
                 self.assertRaises(sources.SourceError):
                sources.fetch_profile("sh600519")

    def test_empty_profile_is_explicit(self):
        with patch.object(sources.DS, "_get", return_value=json.dumps(report_body([]))):
            result = sources.fetch_profile("sh600519")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["data"], {})

    def test_financial_join_uses_report_period_and_keeps_negative_zero_and_missing(self):
        latest, previous = "2026-06-30 00:00:00", "2026-03-31 00:00:00"
        tables = {
            sources.REPORTS[0]: [fact(REPORT_DATE=latest, NOTICE_DATE="2026-08-15 00:00:00", CURRENCY="CNY",
                                      TOTALOPERATEREVE=0, PARENTNETPROFIT=-10, EPSJB=0, ROEJQ=-2,
                                      XSMLL=None, ZCFZL=20, TOTALOPERATEREVETZ=-5, PARENTNETPROFITTZ=None),
                                fact(REPORT_DATE=previous, TOTALOPERATEREVE=200, PARENTNETPROFIT=20)],
            sources.REPORTS[1]: [fact(REPORT_DATE=latest, TOTAL_OPERATE_INCOME=999, PARENT_NETPROFIT=999)],
            sources.REPORTS[2]: [fact(REPORT_DATE=latest, TOTAL_ASSETS=100, TOTAL_LIABILITIES=20)],
            sources.REPORTS[3]: [fact(REPORT_DATE=previous, NETCASH_OPERATE=-50)],
        }
        with patch.object(sources.DS, "_get", side_effect=financial_get(tables)):
            result = sources.fetch_financials("sh600519")
        reports = result["data"]["reports"]
        self.assertEqual([r["report_date"] for r in reports], ["2026-06-30", "2026-03-31"])
        self.assertEqual((reports[0]["revenue"], reports[0]["net_profit"], reports[0]["eps"]), (0, -10, 0))
        self.assertIsNone(reports[0]["operating_cashflow"])
        self.assertEqual(reports[1]["operating_cashflow"], -50)
        self.assertIsNone(reports[0]["gross_margin"])
        self.assertIsNone(reports[0]["net_profit_yoy"])
        self.assertEqual(reports[0]["revenue_yoy"], -5)

    def test_financial_partial_failure_retains_others_but_all_failure_raises(self):
        tables = {sources.REPORTS[0]: [fact(REPORT_DATE="2026-06-30", TOTALOPERATEREVE=100)],
                  sources.REPORTS[3]: TimeoutError("cashflow offline")}
        with patch.object(sources.DS, "_get", side_effect=financial_get(tables)):
            result = sources.fetch_financials("sh600519")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["data"]["reports"][0]["revenue"], 100)
        self.assertIn("cashflow offline", result["errors"][0])
        with patch.object(sources.DS, "_get", side_effect=TimeoutError("offline")), \
             self.assertRaises(sources.SourceError):
            sources.fetch_financials("sh600519")

    def test_financial_duplicate_period_and_wrong_symbol_do_not_leak_values(self):
        for rows in ([fact(REPORT_DATE="2026-06-30"), fact(REPORT_DATE="2026-06-30")],
                     [fact("sz600519", REPORT_DATE="2026-06-30", TOTALOPERATEREVE=999)]):
            with self.subTest(rows=rows), patch.object(sources.DS, "_get", side_effect=financial_get(
                    {sources.REPORTS[0]: rows})):
                result = sources.fetch_financials("sh600519")
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["data"]["reports"], [])

    def test_financial_currency_and_nonfinite_values_are_not_presented_as_yuan(self):
        tables = {sources.REPORTS[0]: [fact(REPORT_DATE="2026-06-30", CURRENCY="USD", TOTALOPERATEREVE=100,
                                               PARENTNETPROFIT=10, ROEJQ=float("nan"))]}
        with patch.object(sources.DS, "_get", side_effect=financial_get(tables)):
            result = sources.fetch_financials("sh600519")
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["data"]["reports"][0]["revenue"])
        self.assertIsNone(result["data"]["reports"][0]["roe"])

    def test_extended_values_use_deducted_parent_profit_and_parent_equity_not_total_equity(self):
        date = "2025-12-31"
        tables = {
            sources.REPORTS[0]: [fact(REPORT_DATE=date, REPORT_TYPE="年报", CURRENCY="CNY",
                                      PARENTNETPROFIT=20, KCFJCXSYJLR=-5)],
            sources.REPORTS[1]: [fact(REPORT_DATE=date, OPERATE_PROFIT=12, OPERATE_COST=30)],
            sources.REPORTS[2]: [fact(REPORT_DATE=date, TOTAL_ASSETS=500, TOTAL_LIABILITIES=100,
                                      TOTAL_EQUITY=400, TOTAL_PARENT_EQUITY=300, GOODWILL=40,
                                      INVENTORY=50, ACCOUNTS_RECE=60, MONETARYFUNDS=70, UNASSIGN_RPOFIT=-80)],
            sources.REPORTS[3]: [fact(REPORT_DATE=date, NETCASH_OPERATE=-10, NETCASH_FINANCE=15)],
        }
        with patch.object(sources.DS, "_get", side_effect=financial_get(tables)):
            data = sources.fetch_financials("sh600519")["data"]
        latest = data["reports"][0]
        self.assertEqual((latest["net_profit"], latest["net_profit_excl"]), (20, -5))
        self.assertEqual(latest["parent_equity"], 300)
        self.assertNotEqual(latest["parent_equity"], latest["total_assets"] - latest["total_liabilities"])
        self.assertEqual((latest["operating_cashflow"], latest["financing_cashflow"]), (-10, 15))
        for key, expected in {"goodwill": 40, "inventory": 50, "accounts_receivable": 60,
                              "cash": 70, "undistributed_profit": -80, "operating_profit": 12,
                              "operating_cost": 30}.items():
            self.assertEqual(latest[key], expected)
        self.assertEqual(data["annual_reports"][0], latest)
        self.assertEqual(data["verification"]["status"], "pending")
        self.assertIn("TOTAL_PARENT_EQUITY", data["field_sources"]["parent_equity"])

    def test_missing_parent_equity_is_unknown_even_with_assets_liabilities_and_total_equity(self):
        tables = {sources.REPORTS[0]: [fact(REPORT_DATE="2025-12-31", PARENTNETPROFIT=0)],
                  sources.REPORTS[1]: [fact(REPORT_DATE="2025-12-31", DEDUCT_PARENT_NETPROFIT=0)],
                  sources.REPORTS[2]: [fact(REPORT_DATE="2025-12-31", TOTAL_ASSETS=500,
                                            TOTAL_LIABILITIES=100, TOTAL_EQUITY=400, GOODWILL=None)]}
        with patch.object(sources.DS, "_get", side_effect=financial_get(tables)):
            latest = sources.fetch_financials("sh600519")["data"]["reports"][0]
        self.assertEqual(latest["net_profit_excl"], 0)
        self.assertIsNone(latest["parent_equity"])
        self.assertIsNone(latest["goodwill"])
        self.assertIsNone(latest["cash"])
        self.assertIsNone(latest["financing_cashflow"])

    def test_annual_series_is_separate_ten_completed_years_and_requests_48_periods_once(self):
        all_rows = [fact(REPORT_DATE="%d-%s" % (year, period),
                         REPORT_TYPE="年报" if period == "12-31" else "中报",
                         TOTALOPERATEREVE=year, PARENTNETPROFIT=year - 2000)
                    for year in range(2014, 2026) for period in ("06-30", "12-31")]
        all_rows += [fact(REPORT_DATE="2026-12-31", REPORT_TYPE="年报", TOTALOPERATEREVE=999)]
        tables = {sources.REPORTS[0]: all_rows}
        with patch.object(sources.DS, "_get", side_effect=financial_get(tables)) as get:
            data = sources.fetch_financials("sh600519")["data"]
        self.assertEqual(len(data["reports"]), 8)
        self.assertEqual(len(data["annual_reports"]), 10)
        self.assertEqual([r["report_date"] for r in data["annual_reports"]],
                         ["%d-12-31" % year for year in range(2025, 2015, -1)])
        self.assertEqual(data["annual_coverage"]["available_years"], 10)
        queries = [parse_qs(urlparse(call.args[0]).query) for call in get.call_args_list]
        main_queries = [q for q in queries if q["reportName"] == [sources.REPORTS[0]]]
        self.assertEqual(len(main_queries), 1)
        self.assertEqual(main_queries[0]["pageSize"], ["48"])

    def test_explicit_fourth_quarter_report_cannot_be_presented_as_a_complete_annual_report(self):
        tables = {sources.REPORTS[0]: [fact(REPORT_DATE="2025-12-31", REPORT_TYPE="四季报"),
                                       fact(REPORT_DATE="2024-12-31", REPORT_TYPE="年报")]}
        with patch.object(sources.DS, "_get", side_effect=financial_get(tables)):
            data = sources.fetch_financials("sh600519")["data"]
        self.assertEqual([r["report_date"] for r in data["annual_reports"]], ["2024-12-31"])

    def test_full_bank_statement_maps_parent_equity_and_keeps_bank_cash_as_unknown(self):
        tables = {sources.REPORTS[0]: [fact("sz000001", REPORT_DATE="2026-06-30")],
                  sources.BALANCE_REPORTS[1]: [fact("sz000001", REPORT_DATE="2026-06-30",
                        TOTAL_PARENT_EQUITY=300, TOTAL_EQUITY=350, GOODWILL=50, CASH_DEPOSIT_PBC=100)]}
        with patch.object(sources.DS, "_get", side_effect=financial_get(tables)):
            latest = sources.fetch_financials("sz000001")["data"]["reports"][0]
        self.assertEqual(latest["parent_equity"], 300)
        self.assertEqual(latest["goodwill"], 50)
        self.assertIsNone(latest["cash"])
        self.assertEqual(latest["source_reports"]["balance"], sources.BALANCE_REPORTS[1])

    def test_business_segments_use_one_dimension_across_periods_and_convert_ratios(self):
        dates = ["2026-06-30", "2025-12-31", "2025-06-30", "2024-12-31"]
        segments = [fact(REPORT_DATE=date, MAINOP_TYPE=kind, ITEM_NAME=name,
                         MAIN_BUSINESS_INCOME=income, MBI_RATIO=share, GROSS_RPOFIT_RATIO=margin)
                    for date, kind, name, income, share, margin in [
                        (dates[0], "2", "产品甲", 90, .9, .5), (dates[0], "2", "产品乙", 10, .1, -.2),
                        (dates[0], "1", "制造业", 100, 1, .43), (dates[0], "3", "国内", 100, 1, .43),
                        (dates[1], "2", "产品甲", 60, .6, .3), (dates[1], "2", "产品乙", 40, .4, None),
                        (dates[2], "3", "国内", 100, 1, .2), (dates[3], "2", "产品甲", 80, 1, .25)]]
        with patch.object(sources.DS, "_get", side_effect=financial_get({sources.BUSINESS_REPORT: segments})):
            business = sources.fetch_financials("sh600519")["data"]["business_segments"]
        self.assertEqual(business["classification"], "分产品")
        self.assertEqual([p["report_date"] for p in business["periods"]], [dates[0], dates[1], dates[3]])
        self.assertEqual(business["skipped_periods"], [dates[2]])
        self.assertEqual([i["name"] for i in business["items"]], ["产品甲", "产品乙"])
        self.assertEqual(sum(i["share_pct"] for i in business["items"]), 100)
        self.assertEqual(business["items"][0]["gross_margin"], 50)
        self.assertEqual(business["items"][1]["gross_margin"], -20)
        self.assertIsNone(business["periods"][1]["items"][1]["gross_margin"])

    def test_business_empty_failure_and_duplicate_do_not_hide_financial_reports(self):
        main = [fact(REPORT_DATE="2026-06-30", TOTALOPERATEREVE=100)]
        with patch.object(sources.DS, "_get", side_effect=financial_get({sources.REPORTS[0]: main})):
            data = sources.fetch_financials("sh600519")["data"]
        self.assertEqual(data["business_segments"]["status"], "empty")
        self.assertEqual(data["reports"][0]["revenue"], 100)
        duplicate = fact(REPORT_DATE="2026-06-30", MAINOP_TYPE="2", ITEM_NAME="重复产品", MAIN_BUSINESS_INCOME=100)
        for value in (TimeoutError("segments offline"), [duplicate, duplicate]):
            with self.subTest(value=value), patch.object(sources.DS, "_get", side_effect=financial_get(
                    {sources.REPORTS[0]: main, sources.BUSINESS_REPORT: value})):
                result = sources.fetch_financials("sh600519")
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["data"]["business_segments"]["status"], "failed")
            self.assertEqual(result["data"]["reports"][0]["revenue"], 100)

    def test_news_preserves_actual_publication_time_and_provider_summary(self):
        with patch.object(sources.DS, "_get", side_effect=news_get([announcement()], [article()])):
            result = sources.fetch_news("sh600519")
        items = result["data"]["items"]
        self.assertEqual([x["kind"] for x in items], ["公告", "新闻"])
        self.assertEqual(items[0]["date"], "2026-10-12 18:00:00")
        self.assertEqual(items[0]["notice_date"], "2026-10-13")
        self.assertEqual(items[0]["summary"], "")
        self.assertEqual(items[1]["source"], "证券时报网")
        self.assertNotIn("<em>", items[1]["summary"])
        self.assertTrue(items[1]["url"].startswith("https://finance.eastmoney.com/"))
        self.assertEqual(result["data"]["coverage"]["media_news"]["status"], "ok")

    def test_bj_announcement_checks_ann_type_in_addition_to_shared_sz_market(self):
        bj = announcement("bj920008", "成电光信")
        with patch.object(sources.DS, "_get", side_effect=news_get([bj], [])):
            good = sources.fetch_news("bj920008")
        self.assertEqual(good["data"]["coverage"]["announcements"]["status"], "ok")
        bj["codes"][0]["ann_type"] = "A,SZA"
        with patch.object(sources.DS, "_get", side_effect=news_get([bj], [])):
            wrong = sources.fetch_news("bj920008")
        self.assertEqual(wrong["data"]["coverage"]["announcements"]["status"], "failed")
        self.assertEqual(wrong["data"]["items"], [])

    def test_investor_relations_without_exchange_tag_does_not_erase_valid_announcements(self):
        valid = announcement("sz000001", "平安银行")
        unqualified = announcement("sz000001", "平安银行", article="AN202610121830369892")
        unqualified["codes"][0]["ann_type"] = "INV"
        with patch.object(sources.DS, "_get", side_effect=news_get([valid, unqualified], [])):
            result = sources.fetch_news("sz000001")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["data"]["items"]), 1)
        self.assertEqual(result["data"]["coverage"]["announcements"]["status"], "ok")

    def test_news_rejects_unrelated_malicious_stale_and_future_articles(self):
        rows = [article(title="另一家企业", content="编号600519是一个无关联数字"),
                article(url="https://finance.eastmoney.com.evil/a/202610123892645349.html"),
                article(date="2026-09-01 10:00:00"), article(date="2026-10-13 10:00:00"),
                article(url="javascript:alert(1)"), article(url="https://127.0.0.1/a/202610123892645349.html")]
        old = announcement(date="2026-09-01 10:00:00:000")
        with patch.object(sources.DS, "_get", side_effect=news_get([old], rows)):
            result = sources.fetch_news("sh600519")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["data"]["items"], [])

    def test_one_news_source_failure_does_not_hide_other_and_both_fail_keep_coverage(self):
        with patch.object(sources.DS, "_get", side_effect=news_get([announcement()], TimeoutError("news offline"))):
            result = sources.fetch_news("sh600519")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["data"]["items"]), 1)
        self.assertEqual(result["data"]["coverage"]["media_news"]["status"], "failed")
        with patch.object(sources.DS, "_get", side_effect=news_get(TimeoutError("ann offline"),
                [article(title="600519.SH公司动态", content="明确证券标识")])):
            result = sources.fetch_news("sh600519")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["data"]["items"][0]["kind"], "新闻")
        with patch.object(sources.DS, "_get", side_effect=TimeoutError("offline")):
            failed = sources.fetch_news("sh600519")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual({x["status"] for x in failed["data"]["coverage"].values()}, {"failed"})

    def test_news_merges_deduplicates_sorts_and_caps_media_at_ten(self):
        media = [article(date="2026-10-12 %02d:00:00" % i, code="2026101200000000%02d" % i)
                 for i in range(15)]
        media.append(media[-1])
        with patch.object(sources.DS, "_get", side_effect=news_get([announcement()], media)):
            result = sources.fetch_news("sh600519")
        items = result["data"]["items"]
        self.assertLessEqual(sum(x["kind"] == "新闻" for x in items), 10)
        self.assertEqual(len({item["url"] for item in items}), len(items))
        self.assertEqual([item["date"] for item in items], sorted([item["date"] for item in items], reverse=True))

    def test_valuation_exchange_and_units_negative_pe_and_nulls(self):
        fields = [""] * 47
        for index, value in {0: "1", 1: "博汇科技", 2: "688004", 3: "37.25", 30: "20261012161451",
                             39: "-113.14", 44: "29.83", 45: "29.83", 46: "5.32"}.items():
            fields[index] = value
        def raw():
            return 'v_sh688004="' + "~".join(fields) + '";\n'
        with patch.object(sources.DS, "_get", return_value=raw()):
            result = sources.fetch_valuation("sh688004")
        self.assertEqual(result["data"]["pe_ttm"], -113.14)
        self.assertEqual(result["data"]["total_market_cap"], 2983000000)
        self.assertEqual(result["data"]["pb"], 5.32)
        fields[39], fields[44], fields[46] = "nan", "0", "0"
        with patch.object(sources.DS, "_get", return_value=raw()):
            values = sources.fetch_valuation("sh688004")["data"]
        self.assertIsNone(values["pe_ttm"])
        self.assertIsNone(values["float_market_cap"])
        self.assertIsNone(values["pb"])
        fields[0] = "51"
        with patch.object(sources.DS, "_get", return_value=raw()), self.assertRaises(sources.SourceError):
            sources.fetch_valuation("sh688004")


if __name__ == "__main__":
    unittest.main()
