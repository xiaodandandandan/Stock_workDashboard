import copy
import unittest

from core.fundamental_report import build_report


def financial_row(period="2026-06-30", **values):
    row = {"report_date": period, "net_profit": 100_000_000, "net_profit_excl": 80_000_000,
           "revenue": 1_000_000_000, "operating_cashflow": 120_000_000, "financing_cashflow": 20_000_000,
           "parent_equity": 500_000_000, "goodwill": 100_000_000, "inventory": 50_000_000,
           "accounts_receivable": 150_000_000, "undistributed_profit": -100_000_000, "gross_margin": 20}
    row.update(values)
    return row


def report_sections(latest=None, annual=None, industry="制造业"):
    return {"profile": {"data": {"industry": industry}},
            "financials": {"url": "https://example.com/finance", "data": {
                "reports": [latest if latest is not None else financial_row()],
                "annual_reports": annual if annual is not None else [financial_row("2025-12-31"), financial_row("2024-12-31")],
                "business_segments": {"report_date": "2026-06-30", "items": [{"name": "产品", "revenue": 100}], "periods": []}}},
            "valuation": {"data": {"pe_ttm": 15, "pb": 2}}}


class FundamentalReportTests(unittest.TestCase):
    def build(self, latest=None, annual=None, industry="制造业", symbol="sh600000"):
        return build_report(symbol, report_sections(latest, annual, industry), "2026-10-13 16:00:00")

    def test_six_kpis_pair_parent_profit_and_excluding_non_recurring(self):
        result = self.build()
        self.assertEqual(len(result["kpis"]), 6)
        self.assertEqual([item["field"] for item in result["kpis"][:2]], ["net_profit", "net_profit_excl"])
        self.assertEqual([item["unit"] for item in result["kpis"][:2]], ["元", "元"])
        self.assertEqual(result["non_recurring_difference"], 20_000_000)
        self.assertIn("归母", result["conclusions"][0]["evidence"])
        self.assertIn("扣非", result["conclusions"][0]["evidence"])
        difference = next(item for item in result["conclusions"] if item["title"] == "归母与扣非差额")
        self.assertIn("未核验明细", difference["interpretation"])
        self.assertIn("不能归因", difference["interpretation"])

    def test_parent_profit_positive_deduct_loss_does_not_claim_operating_gross_loss(self):
        result = self.build(financial_row(net_profit=100_000_000, net_profit_excl=-5_000_000))
        self.assertIn("归母盈利、扣非仍亏", result["headline"])
        self.assertEqual(result["non_recurring_difference"], 105_000_000)
        self.assertNotIn("主营毛利为负", str(result))
        self.assertNotIn("主业必然亏损", str(result))

    def test_missing_deduct_is_not_zero_and_incomplete_annual_sum_not_imputed(self):
        result = self.build(financial_row(net_profit_excl=None),
                            [financial_row("2025-12-31", net_profit_excl=None), financial_row("2024-12-31")])
        self.assertIsNone(result["non_recurring_difference"])
        self.assertIsNone(result["kpis"][1]["value"])
        total = next(item for item in result["kpis"] if item["field"] == "annual_net_profit_excl_sum")
        self.assertIsNone(total["value"])
        self.assertIn("口径未齐", result["headline"])

    def test_risk_exposure_uses_parent_equity_and_does_not_total_potential_losses(self):
        result = self.build(financial_row(total_assets=9_000_000_000, total_liabilities=1_000_000_000))
        risks = {item["field"]: item for item in result["risk_exposure"]}
        self.assertEqual(risks["goodwill"]["ratio"], 0.2)
        self.assertEqual(risks["accounts_receivable"]["ratio"], 0.3)
        self.assertIsNone(risks["external_guarantees"]["amount"])
        self.assertIsNone(risks["external_guarantees"]["ratio"])
        self.assertEqual(risks["external_guarantees"]["status"], "pending")
        self.assertIn("不能当作已发生减值", risks["goodwill"]["notice"])
        self.assertNotIn("total_risk_loss", result)

    def test_non_positive_or_missing_parent_equity_disables_ratios(self):
        for equity in (None, 0, -50_000_000):
            with self.subTest(equity=equity):
                result = self.build(financial_row(parent_equity=equity, total_assets=1_000_000_000, total_liabilities=10))
                self.assertTrue(all(item["ratio"] is None for item in result["risk_exposure"]))
                expected = "missing" if equity is None else "invalid_denominator"
                self.assertEqual(result["risk_exposure"][0]["status"], expected)
                self.assertTrue(any("PB 不能" in note for note in result["valuation_notes"]))

    def test_cash_content_only_uses_positive_profit_and_matching_annual_period(self):
        annual = [financial_row("2025-12-31", net_profit=-100, operating_cashflow=-200),
                  financial_row("2024-12-31", net_profit=0, operating_cashflow=100),
                  financial_row("2023-12-31", net_profit=100, operating_cashflow=-50),
                  financial_row("2022-12-31", net_profit=100, operating_cashflow=200)]
        result = self.build(annual=annual)
        cash = {row["report_date"]: row for row in result["cash_quality"]}
        self.assertIsNone(cash["2025-12-31"]["cash_content"])
        self.assertIsNone(cash["2024-12-31"]["cash_content"])
        self.assertEqual(cash["2023-12-31"]["cash_content"], -0.5)
        self.assertEqual(cash["2022-12-31"]["cash_content"], 2)
        self.assertEqual(cash["2025-12-31"]["status"], "not_applicable")
        self.assertNotIn("2026-06-30", cash)

    def test_financial_industry_cash_has_no_ordinary_enterprise_score(self):
        for industry in ("银行", "证券", "保险", "多元金融"):
            with self.subTest(industry=industry):
                result = self.build(financial_row(operating_cashflow=-100, financing_cashflow=500), industry=industry)
                cash = next(item for item in result["conclusions"] if item["title"] == "利润与现金对照")
                self.assertEqual(cash["direction"], "中性")
                self.assertIn("金融行业", cash["interpretation"])
                self.assertNotIn("高风险", str(result))

    def test_dividend_and_regulatory_items_remain_pending_instead_of_legal_verdict(self):
        result = self.build(financial_row(parent_equity=-10, undistributed_profit=-100))
        checks = {item["label"]: item for item in result["regulatory_checks"]}
        self.assertTrue(all(item["status"] == "pending" for item in checks.values()))
        self.assertIn("母公司可分配利润", checks["分红能力"]["evidence"])
        self.assertIn("不能据合并数", checks["分红能力"]["evidence"])
        self.assertNotIn("法定不具备分红条件", str(result))
        self.assertNotIn("已触发退市", str(result))
        self.assertEqual(result["verification"]["status"], "pending")

    def test_board_rules_links_are_bound_to_the_correct_exchange(self):
        cases = {"sh600000": ("main", "沪主板", "sse.com.cn"), "sz000001": ("main", "深主板", "szse.cn"),
                 "sh688001": ("star", "科创板", "sse.com.cn"), "sz300001": ("growth", "创业板", "szse.cn"),
                 "bj920007": ("bse", "北交所", "bse.cn")}
        for symbol, expected in cases.items():
            with self.subTest(symbol=symbol):
                result = self.build(symbol=symbol)
                self.assertEqual((result["board"], result["label"]), expected[:2])
                self.assertIn(expected[2], result["regulatory_checks"][0]["url"])

    def test_report_construction_does_not_change_facts_or_keep_unsafe_link(self):
        sections = report_sections()
        sections["financials"]["url"] = "javascript:alert(1)"
        original = copy.deepcopy(sections)
        result = build_report("sh600000", sections, "2026-10-13")
        self.assertEqual(sections, original)
        result["business_segments"]["items"][0]["revenue"] = 0
        self.assertEqual(sections, original)
        self.assertTrue(all(link["url"].startswith("https://") for link in result["verification"]["links"]))

    def test_annual_coverage_is_actual_years_and_missing_not_zero(self):
        result = self.build(annual=[financial_row("2025-12-31"), financial_row("2023-12-31")])
        coverage = result["annual_coverage"]
        self.assertEqual((coverage["count"], coverage["start"], coverage["end"]), (2, "2023", "2025"))
        self.assertIn(2024, coverage["missing_years"])
        self.assertEqual(coverage["target_years"], 10)
        self.assertIn("缺失不补零", coverage["notice"])
        self.assertEqual(len(result["cash_quality"]), 2)

    def test_scenarios_and_checklist_are_conditions_not_target_prices(self):
        result = self.build()
        self.assertEqual(len(result["scenarios"]), 3)
        self.assertEqual(len(result["checklist"]), 7)
        self.assertTrue(all(item["condition"] and item["path"] and item["judgement"] for item in result["scenarios"]))
        self.assertTrue(all(item["status_label"] for item in result["checklist"]))
        self.assertNotIn("target_price", result)
        self.assertNotIn("建议买入", str(result))
        self.assertNotIn("建议卖出", str(result))


if __name__ == "__main__":
    unittest.main()
