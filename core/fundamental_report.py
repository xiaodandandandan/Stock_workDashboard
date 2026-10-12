"""A transparent nine-section report model; source documents remain unverified."""
import copy
import math
from urllib.parse import urlparse


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _money(value):
    value = _number(value)
    if value is None:
        return "未取得"
    if abs(value) >= 100_000_000:
        return "%.2f 亿元" % (value / 100_000_000)
    if abs(value) >= 10_000:
        return "%.2f 万元" % (value / 10_000)
    return "%.2f 元" % value


def _ratio(value, denominator):
    value, denominator = _number(value), _number(denominator)
    if value is None or denominator is None or denominator <= 0:
        return None
    return _number(value / denominator)


def _url(value):
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    return value if parsed.scheme == "https" and parsed.netloc and not parsed.username else ""


def board_for(symbol):
    if symbol.startswith("sh68"):
        return "star", "科创板", "https://www.sse.com.cn/"
    if symbol.startswith("sz30"):
        return "growth", "创业板", "https://www.szse.cn/"
    if symbol.startswith("bj"):
        return "bse", "北交所", "https://www.bse.cn/"
    if symbol.startswith("sh"):
        return "main", "沪主板", "https://www.sse.com.cn/"
    return "main", "深主板", "https://www.szse.cn/"


def _conclusion(title, evidence, interpretation, direction="需核验", kind="规则解读"):
    return {"title": title, "evidence": evidence, "interpretation": interpretation,
            "direction": direction, "kind": kind}


def build_report(symbol, sections, generated):
    """Use normalized facts only; no thresholds, inferred missing figures or legal verdicts."""
    financial = sections.get("financials", {})
    data = financial.get("data", {})
    profile = sections.get("profile", {}).get("data", {})
    valuation = sections.get("valuation", {}).get("data", {})
    reports = data.get("reports", [])
    annual = data.get("annual_reports", [])
    latest = reports[0] if reports else (annual[0] if annual else {})
    period = latest.get("report_date", "")
    profit, deduct = _number(latest.get("net_profit")), _number(latest.get("net_profit_excl"))
    equity, ocf = _number(latest.get("parent_equity")), _number(latest.get("operating_cashflow"))
    financing = _number(latest.get("financing_cashflow"))
    financial_industry = any(word in str(profile.get("industry") or "") for word in ("银行", "保险", "证券", "金融", "信托", "多元金融"))
    board, label, exchange_url = board_for(symbol)
    if profit is None or deduct is None:
        headline = "归母与扣非口径未齐，盈利质量待核验"
    elif profit > 0 and deduct < 0:
        headline = "归母盈利、扣非仍亏，利润差额需核验"
    elif profit < 0 and deduct < 0:
        headline = "归母与扣非均亏损，关注权益与现金变化"
    elif profit > 0 and deduct > 0:
        headline = "归母与扣非均盈利，现金兑现仍需对照"
    else:
        headline = "归母与扣非状态分化，按同口径持续跟踪"
    yearly_deducts = [_number(row.get("net_profit_excl")) for row in annual]
    deducted_sum = _number(sum(yearly_deducts)) if yearly_deducts and all(value is not None for value in yearly_deducts) else None
    kpis = []
    for title, field in (("归母净利润", "net_profit"), ("扣非归母净利润", "net_profit_excl"),
                         ("经营现金流", "operating_cashflow"), ("归母净资产", "parent_equity")):
        kpis.append({"label": title, "value": _number(latest.get(field)), "unit": "元", "field": field,
                     "report_date": period})
    kpis += [{"label": "已取年度扣非累计", "value": deducted_sum, "unit": "元", "field": "annual_net_profit_excl_sum",
              "report_date": "%s 个已取得完整年度" % len(annual)},
             {"label": "最新毛利率", "value": _number(latest.get("gross_margin")), "unit": "%", "field": "gross_margin",
              "report_date": period}]
    pair = "%s：归母 %s，扣非 %s" % (period or "报告期未取得", _money(profit), _money(deduct))
    conclusions = [_conclusion("盈利与扣非对照", pair,
                              "两项必须成对比较；扣非缺失时不能确认盈利质量。" if deduct is None or profit is None else
                              "归母与扣非的差额不等于经营能力改善，具体损益来源须读非经常性损益明细。",
                              "需核验", "数据事实")]
    difference = _number(profit - deduct) if profit is not None and deduct is not None else None
    if difference is not None:
        conclusions.append(_conclusion("归母与扣非差额", "归母减扣非 = %s" % _money(difference),
                                       "仅为两项披露数据的算术差额；未核验明细，不能归因于补助、处置或重组收益。"))
    if equity is not None:
        conclusions.append(_conclusion("归母权益读数", "%s：归母净资产 %s" % (period, _money(equity)),
                                       "归母权益为负，需结合所属板块规则和审计原文核验，尚未判断监管触发。" if equity < 0 else
                                       "净资产金额是账面读数，不代表可变现安全垫；资产减值及表外事项仍待核验。",
                                       "偏负面" if equity < 0 else "中性", "数据事实"))
    if ocf is not None:
        cash_evidence = "%s：经营现金流 %s，归母 %s，扣非 %s，筹资现金流 %s" % (
            period, _money(ocf), _money(profit), _money(deduct), _money(financing))
        interpretation = ("金融行业现金流结构不同，不套用一般企业现金含量评分。" if financial_industry else
                          "经营净流出与筹资净流入同时存在；需核验融资构成，筹资现金流不等同于借款。"
                          if ocf < 0 and financing is not None and financing > 0 else
                          "与同一报告期利润对照，结合营运资本和季节性解释，不比较不同季度累计金额。")
        conclusions.append(_conclusion("利润与现金对照", cash_evidence, interpretation,
                                       "中性" if financial_industry else "需核验"))
    risk_exposure = []
    for name, field in (("商誉", "goodwill"), ("存货", "inventory"), ("应收账款", "accounts_receivable")):
        amount = _number(latest.get(field))
        ratio = _ratio(amount, equity)
        status = "missing" if amount is None or equity is None else "invalid_denominator" if equity <= 0 else "available"
        notice = ("归母净资产未取得，不使用总资产减负债替代。" if equity is None else
                  "归母净资产非正，比值不适用。" if equity <= 0 else
                  "比值是账面相对量，不能当作已发生减值或损失；需核验附注与准备金。")
        risk_exposure.append({"name": name, "field": field, "amount": amount, "ratio": ratio,
                              "status": status, "status_label": {"missing": "数据缺失", "invalid_denominator": "分母不适用", "available": "已取得"}[status],
                              "notice": notice, "report_date": period})
    risk_exposure.append({"name": "对外担保", "field": "external_guarantees", "amount": None, "ratio": None,
                          "status": "pending", "status_label": "待核验", "notice": "未核验年报或有事项与最新担保公告，缺失不等于零。", "report_date": period})
    cash_quality = []
    for row in sorted(annual, key=lambda row: row["report_date"]):
        row_profit = _number(row.get("net_profit"))
        row_cash = _number(row.get("operating_cashflow"))
        content = _ratio(row_cash, row_profit)
        status = "missing" if row_cash is None or row_profit is None else "not_applicable" if row_profit <= 0 else "available"
        cash_quality.append({"report_date": row["report_date"], "operating_cashflow": row_cash,
                             "net_profit": row_profit, "net_profit_excl": _number(row.get("net_profit_excl")),
                             "financing_cashflow": _number(row.get("financing_cashflow")), "cash_content": content,
                             "status": status, "status_label": {"missing": "数据缺失", "not_applicable": "净利非正不适用", "available": "可计算"}[status],
                             "notice": "净利润非正不计算现金含量；金融行业不使用统一阈值评分。"})
    years = sorted({int(row["report_date"][:4]) for row in annual})
    missing_years = [year for year in range(years[-1] - 9, years[-1] + 1) if year not in years] if years else []
    annual_coverage = {"count": len(years), "start": str(years[0]) if years else "", "end": str(years[-1]) if years else "",
                       "target_years": 10, "missing_years": missing_years,
                       "notice": "仅有 %s 个完整年度；缺失不补零，最新季度/半年数据不与完整年度混画。" % len(years)}
    segments = copy.deepcopy(data.get("business_segments", {}))
    valuation_notes = ["PE-TTM、PB 为行情时点读数，财报期可能不同；未使用股价目标或买卖建议。",
                       "PE 需核验持续盈利与扣非；PB 需核验归母权益及资产可变现性，不以倍数机械判贵便宜。"]
    if _number(valuation.get("pe_ttm")) is not None and _number(valuation["pe_ttm"]) <= 0:
        valuation_notes.append("PE-TTM 非正，不能把数值低当作便宜；需核验 TTM 盈利口径。")
    if equity is None or equity <= 0:
        valuation_notes.append("归母净资产缺失或非正，PB 不能作为有效资产价格锚；未以总权益替代。")
    valuation_notes.append("缺少足够同口径原文时，不估算年化毛利倍数、股息能力或估值分位。")
    regulatory_checks = [
        {"label": "利润与营业收入组合指标", "status": "pending", "evidence": pair + "；营业收入 " + _money(latest.get("revenue")) + "；未核验本板块最新规则及扣除口径。", "url": exchange_url},
        {"label": "归母净资产监管口径", "status": "pending", "evidence": "归母净资产 " + _money(equity) + "；未核验年度审计及最新规则，不直接判触发。", "url": exchange_url},
        {"label": "财务与内控审计意见", "status": "pending", "evidence": "未读取审计报告、内控报告原文，不能认定标准无保留意见。", "url": "https://www.cninfo.com.cn/"},
        {"label": "可转债与对外担保", "status": "pending", "evidence": "剩余规模、转股价、回售条件、到期日与担保余额均待原文核验，不能断言不存在。", "url": "https://www.cninfo.com.cn/"},
        {"label": "股权冻结、诉讼与重组", "status": "pending", "evidence": "未完成最新公告及进展核查；标题筛查不能替代全文。", "url": "https://www.cninfo.com.cn/"},
        {"label": "分红能力", "status": "pending", "evidence": "合并未分配利润 " + _money(latest.get("undistributed_profit")) + "；须核验母公司可分配利润、法定要求及方案，不能据合并数断言能否分红。", "url": "https://www.cninfo.com.cn/"},
        {"label": "历史 ST 与股东户数", "status": "pending", "evidence": "未完成历史记录与最新户数核查；未取得不等于没有异常。", "url": exchange_url},
    ]
    scenarios = [
        {"label": "改善情景", "condition": "后续同口径扣非持续为正，且经营现金流兑现", "path": "盈利与现金同时改善后，再核验是否由主营驱动。", "judgement": "条件尚未发生不视为事实，不给股价目标。"},
        {"label": "维持情景", "condition": "仅按当前已披露数值，未假设持续性改善", "path": pair + "；经营现金流 " + _money(ocf), "judgement": "以原文核验及后续同报告期同比作为更新依据。"},
        {"label": "承压情景", "condition": "后续扣非持续亏损、现金净流出，或经审计确认新增减值", "path": "核验亏损、减值对归母权益的实际影响与资金安排。", "judgement": "监管后果须按所属板块最新规则核验，不能据假设直接认定退市。"},
    ]
    checklist = [
        {"label": "归母与扣非（每季）", "value": pair, "evidence": "同口径并列，差额来源需读损益明细。", "status": "available" if profit is not None and deduct is not None else "pending"},
        {"label": "收入结构与毛利（半年/年报）", "value": "主营业务条目 %s 项" % len(segments.get("items", [])), "evidence": "不使用行业标签替代主营业务构成。", "status": "available" if segments.get("items") else "pending"},
        {"label": "经营与筹资现金（每季）", "value": "经营 " + _money(ocf) + "；筹资 " + _money(financing), "evidence": "仅正归母利润计算现金含量，金融行业另核口径。", "status": "available" if ocf is not None and financing is not None else "pending"},
        {"label": "归母权益与商誉（每季）", "value": "权益 " + _money(equity) + "；商誉 " + _money(latest.get("goodwill")), "evidence": "比值使用归母权益；减值事实需核附注。", "status": "available" if equity is not None else "pending"},
        {"label": "存货、应收与担保（每季/年报）", "value": "存货 " + _money(latest.get("inventory")) + "；应收 " + _money(latest.get("accounts_receivable")), "evidence": "担保余额与准备金尚待原文核验。", "status": "pending"},
        {"label": "审计、规则与可转债（公告更新）", "value": "待原文核验", "evidence": "核验最新上市规则、审计意见、兑付与回售条款。", "status": "pending"},
        {"label": "一手数字核对（每次更新）", "value": "未完成", "evidence": "公开数据库读数不等于逐项核对年报 PDF。", "status": "pending"},
    ]
    for item in checklist:
        item["status_label"] = "已取得" if item["status"] == "available" else "待核验"
    links = [{"label": "巨潮公告原文入口", "url": "https://www.cninfo.com.cn/"},
             {"label": label + "规则入口", "url": exchange_url}]
    financial_url = _url(financial.get("url"))
    if financial_url:
        links.append({"label": "财经数据来源", "url": financial_url})
    return {"board": board, "label": label, "latest_report_date": period, "headline": headline,
            "verification": {"status": "pending", "notice": "数字来自公开数据库，未逐项核对年报 PDF；审计、规则与隐藏事项尚待一手原文核验。", "links": links},
            "kpis": kpis, "conclusions": conclusions, "risk_exposure": risk_exposure, "cash_quality": cash_quality,
            "scenarios": scenarios, "checklist": checklist, "valuation_notes": valuation_notes,
            "regulatory_checks": regulatory_checks, "annual_coverage": annual_coverage, "business_segments": segments,
            "non_recurring_difference": difference, "generated": generated,
            "notice": "金额单位为元，ratio/cash_content 为倍数；归母/扣非均为同一报告期。未知不补零，不输出法律结论或操作建议。"}
