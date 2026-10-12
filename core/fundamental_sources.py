"""Bounded, exchange-checked company facts and news from fixed HTTPS providers."""
import html
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

from . import datasource as DS
from .validation import InputError, to_symbol

DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get?"
ANNOUNCEMENTS = "https://np-anotice-stock.eastmoney.com/api/security/ann?"
MEDIA_SEARCH = "https://search-api-web.eastmoney.com/search/jsonp?"
REPORTS = ("RPT_F10_FINANCE_MAINFINADATA", "RPT_DMSK_FN_INCOME",
           "RPT_F10_FINANCE_GBALANCE", "RPT_DMSK_FN_CASHFLOW")
BALANCE_REPORTS = (REPORTS[2], "RPT_F10_FINANCE_BBALANCE", "RPT_F10_FINANCE_IBALANCE",
                   "RPT_F10_FINANCE_SBALANCE")
BUSINESS_REPORT = "RPT_F10_FN_MAINOP"
FIELD_SOURCES = {
    "net_profit_excl": "MAIN.KCFJCXSYJLR / INCOME.DEDUCT_PARENT_NETPROFIT",
    "parent_equity": "完整资产负债表.TOTAL_PARENT_EQUITY（归属于母公司，不用合并权益替代）",
    "goodwill": "完整资产负债表.GOODWILL", "inventory": "完整资产负债表.INVENTORY",
    "accounts_receivable": "完整资产负债表.ACCOUNTS_RECE", "cash": "完整资产负债表.MONETARYFUNDS",
    "undistributed_profit": "完整资产负债表.UNASSIGN_RPOFIT",
    "financing_cashflow": "CASHFLOW.NETCASH_FINANCE", "operating_profit": "INCOME.OPERATE_PROFIT",
    "operating_cost": "INCOME.OPERATE_COST",
}


class SourceError(RuntimeError):
    """An unavailable or inconsistent provider result, never an invented fact."""


def validate_symbol(value):
    symbol = to_symbol(value)
    if not re.fullmatch(r"sh(?:60|68)[0-9]{4}|sz(?:00|30)[0-9]{4}|bj(?:[48][0-9]{5}|92[0-9]{4})", symbol):
        raise InputError("基本面只支持沪深北交易所 A 股")
    return symbol


def _now():
    return datetime.now()


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _text(value, limit=8000):
    if not isinstance(value, str):
        return ""
    return html.unescape(re.sub(r"<[^>]*>", "", value)).strip()[:limit]


def _timestamp(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    # Announcement display_time has a colon before milliseconds.
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?::[0-9]{3})?", text):
        fmt, text = "%Y-%m-%d %H:%M:%S", text[:19]
    elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        fmt = "%Y-%m-%d"
    else:
        return None
    try:
        return datetime.strptime(text, fmt)
    except ValueError:
        return None


def _date(value):
    parsed = _timestamp(value)
    return parsed.strftime("%Y-%m-%d") if parsed else None


def _section(data, url, source="东方财富", status="ok", errors=None):
    result = {"status": status, "data": data, "url": url, "source": source,
              "as_of": _now().strftime("%Y-%m-%d %H:%M:%S")}
    if errors:
        result["errors"] = errors
    return result


def _profile_url(symbol):
    return "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/Index?" + urlencode(
        {"type": "web", "code": symbol.upper()})


def _finance_url(symbol):
    return "https://emweb.securities.eastmoney.com/PC_HSF10/FinanceAnalysis/Index?" + urlencode(
        {"type": "web", "code": symbol.upper()})


def _ann_url(symbol):
    return "https://data.eastmoney.com/notices/stock/" + symbol[2:] + ".html"


def _report(symbol, report, size=8):
    secucode = symbol[2:] + "." + symbol[:2].upper()
    params = {"reportName": report, "columns": "ALL", "filter": '(SECUCODE="%s")' % secucode,
              "sortColumns": "SECUCODE" if report == "RPT_F10_ORG_BASICINFO" else "REPORT_DATE",
              "sortTypes": -1, "pageSize": size, "pageNumber": 1}
    body = json.loads(DS._get(DATACENTER + urlencode(params), DS.HDR_EM, timeout=8))
    if isinstance(body, dict) and body.get("success") is False and body.get("message") == "返回数据为空":
        return []
    if not isinstance(body, dict) or body.get("success") is not True:
        raise SourceError("%s 获取失败：%s" % (report, body.get("message", "响应无效")
                                               if isinstance(body, dict) else "响应无效"))
    result = body.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise SourceError("%s 财经数据结构无效" % report)
    rows = result["data"]
    for row in rows:
        if (not isinstance(row, dict) or row.get("SECUCODE") != secucode
                or row.get("SECURITY_CODE") != symbol[2:]):
            raise SourceError("%s 返回交易所或代码与请求不一致" % report)
    return rows


def _balance_rows(symbol):
    # Provider financial statements differ for general companies, banks, insurers
    # and securities firms. Only an explicit empty response triggers the next type;
    # transport, schema or exchange failures must remain visible.
    for report in BALANCE_REPORTS:
        rows = _report(symbol, report, size=48)
        if rows:
            return [dict(row, _source_report=report) for row in rows]
    return []


def fetch_profile(symbol):
    symbol = validate_symbol(symbol)
    rows = _report(symbol, "RPT_F10_ORG_BASICINFO", size=2)
    url = _profile_url(symbol)
    if not rows:
        return _section({}, url, status="empty")
    if len(rows) != 1:
        raise SourceError("公司资料返回重复标的")
    row = rows[0]
    name = _text(row.get("SECURITY_NAME_ABBR"), 80)
    if not name:
        raise SourceError("公司资料缺少证券名称")
    data = {"symbol": symbol, "name": name, "company_name": _text(row.get("ORG_NAME"), 160),
            "industry": _text(row.get("EM2016"), 160),
            "description": _text(row.get("ORG_PROFIE")),
            "business_scope": _text(row.get("BUSINESS_SCOPE")),
            "main_business": _text(row.get("MAIN_BUSINESS")),
            "website": _text(row.get("ORG_WEB"), 200),
            "listing_date": _date(row.get("LISTING_DATE")),
            "data_date": None}
    # The profile API does not provide a documented profile-update timestamp.
    return _section(data, url)


def fetch_financials(symbol):
    symbol = validate_symbol(symbol)
    tables, errors = {}, []
    with ThreadPoolExecutor(max_workers=5) as pool:
        tasks = {report: (pool.submit(_balance_rows, symbol) if report == REPORTS[2]
                          else pool.submit(_report, symbol, report, 48)) for report in REPORTS}
        business_task = pool.submit(_business_segments, symbol)
        for report, task in tasks.items():
            try:
                rows = task.result()
                by_date = {}
                for row in rows:
                    date = _date(row.get("REPORT_DATE"))
                    if date is None:
                        raise SourceError("财报报告期无效")
                    if date in by_date:
                        raise SourceError("财报包含重复报告期")
                    by_date[date] = row
                tables[report] = by_date
            except Exception as exc:
                errors.append("%s: %s" % (report, exc))
        try:
            business = business_task.result()
        except Exception as exc:
            business = {"report_date": None, "items": [], "periods": [], "source": "东方财富财报主营构成",
                        "url": _business_url(symbol), "status": "failed", "error": str(exc)}
            errors.append("主营业务构成: %s" % exc)
    if not tables:
        raise SourceError("；".join(errors))
    today = _now().strftime("%Y-%m-%d")
    dates = sorted({date for table in tables.values() for date in table if date <= today}, reverse=True)
    reports = []
    for date in dates:
        main, income, balance, cashflow = [tables.get(report, {}).get(date, {}) for report in REPORTS]
        currencies = {row.get("CURRENCY") for row in (main, income, balance, cashflow)
                      if row.get("CURRENCY") is not None}
        currency = next(iter(currencies), None) if len(currencies) <= 1 else "mixed"
        if currency not in (None, "CNY"):
            errors.append("%s 的币种不是 CNY，金额未展示" % date)
        def amount(field, table):
            return _number(table.get(field)) if currency in (None, "CNY") else None
        revenue = amount("TOTALOPERATEREVE", main)
        net_profit = amount("PARENTNETPROFIT", main)
        net_profit_excl = amount("KCFJCXSYJLR", main)
        reports.append({
            "report_date": date,
            "announced_date": _date(main.get("NOTICE_DATE") or income.get("NOTICE_DATE")
                                    or balance.get("NOTICE_DATE") or cashflow.get("NOTICE_DATE")),
            "revenue": revenue if revenue is not None else amount("TOTAL_OPERATE_INCOME", income),
            "net_profit": net_profit if net_profit is not None else amount("PARENT_NETPROFIT", income),
            "net_profit_excl": net_profit_excl if net_profit_excl is not None else amount("DEDUCT_PARENT_NETPROFIT", income),
            "revenue_yoy": _number(main.get("TOTALOPERATEREVETZ")),
            "net_profit_yoy": _number(main.get("PARENTNETPROFITTZ")),
            "operating_cashflow": amount("NETCASH_OPERATE", cashflow),
            "roe": _number(main.get("ROEJQ")), "gross_margin": _number(main.get("XSMLL")),
            "debt_ratio": _number(main.get("ZCFZL")), "eps": amount("EPSJB", main),
            "total_assets": amount("TOTAL_ASSETS", balance),
            "total_liabilities": amount("TOTAL_LIABILITIES", balance), "currency": currency or "CNY",
            "parent_equity": amount("TOTAL_PARENT_EQUITY", balance),
            "goodwill": amount("GOODWILL", balance), "inventory": amount("INVENTORY", balance),
            "accounts_receivable": amount("ACCOUNTS_RECE", balance),
            "cash": amount("MONETARYFUNDS", balance), "undistributed_profit": amount("UNASSIGN_RPOFIT", balance),
            "financing_cashflow": amount("NETCASH_FINANCE", cashflow),
            "operating_profit": amount("OPERATE_PROFIT", income), "operating_cost": amount("OPERATE_COST", income),
            "source_reports": {"main": REPORTS[0], "income": REPORTS[1],
                               "balance": balance.get("_source_report", REPORTS[2]), "cashflow": REPORTS[3]},
        })
    annuals = [report for report in reports if report["report_date"].endswith("-12-31")
               and tables.get(REPORTS[0], {}).get(report["report_date"], {}).get("REPORT_TYPE") in (None, "年报")][:10]
    data = {"reports": reports[:8], "annual_reports": annuals, "business_segments": business,
            "annual_coverage": {"available_years": len(annuals), "requested_years": 10,
                                "oldest_period": annuals[-1]["report_date"] if annuals else None,
                                "latest_period": annuals[0]["report_date"] if annuals else None},
            "field_sources": dict(FIELD_SOURCES),
            "verification": {"status": "pending", "note": "东方财富结构化财报数据，尚未逐项回年报原文核验。"}}
    return _section(data, _finance_url(symbol),
                    status="partial" if errors else ("ok" if reports else "empty"), errors=errors)


def _business_url(symbol):
    return "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/Index?" + urlencode(
        {"type": "web", "code": symbol.upper()})


def _business_segments(symbol):
    rows = _report(symbol, BUSINESS_REPORT, size=200)
    by_date = {}
    for row in rows:
        date = _date(row.get("REPORT_DATE"))
        if not date:
            raise SourceError("主营构成报告期无效")
        if date <= _now().strftime("%Y-%m-%d"):
            by_date.setdefault(date, []).append(row)
    base = {"source": "东方财富财报主营构成", "url": _business_url(symbol),
            "source_fields": {"name": "ITEM_NAME", "revenue": "MAIN_BUSINESS_INCOME",
                              "gross_margin": "GROSS_RPOFIT_RATIO × 100", "share_pct": "MBI_RATIO × 100"}}
    if not by_date:
        return dict(base, report_date=None, items=[], periods=[], status="empty")
    dates = sorted(by_date, reverse=True)
    present = {str(row.get("MAINOP_TYPE")) for row in by_date[dates[0]]}
    selected_type = next((kind for kind in ("2", "1", "3") if kind in present), None)
    if selected_type is None:
        return dict(base, report_date=dates[0], items=[], periods=[], status="empty",
                    note="未提供可识别的财报分产品、分行业或分地区构成。")
    classification = {"2": "分产品", "1": "分行业", "3": "分地区"}[selected_type]
    periods, skipped = [], []
    for date in dates[:4]:
        items, names = [], set()
        for row in by_date[date]:
            if str(row.get("MAINOP_TYPE")) != selected_type:
                continue
            name = _text(row.get("ITEM_NAME"), 160)
            if not name:
                raise SourceError("主营构成缺少业务名称")
            if name in names:
                raise SourceError("同一报告期与分类包含重复主营业务项")
            names.add(name)
            margin, share = _number(row.get("GROSS_RPOFIT_RATIO")), _number(row.get("MBI_RATIO"))
            items.append({"name": name, "revenue": _number(row.get("MAIN_BUSINESS_INCOME")),
                          "gross_margin": margin * 100 if margin is not None and math.isfinite(margin * 100) else None,
                          "share_pct": share * 100 if share is not None and math.isfinite(share * 100) else None})
        if items:
            items.sort(key=lambda item: (item["revenue"] is not None, item["revenue"] or 0), reverse=True)
            periods.append({"report_date": date, "items": items, "classification": classification})
        else:
            skipped.append(date)
    latest = periods[0]
    return dict(base, report_date=latest["report_date"], items=latest["items"], periods=periods,
                classification=classification, status="ok", skipped_periods=skipped,
                note="各期采用同一财报构成分类，旧期无相同分类则跳过；占比和毛利来自源字段，未读取年报原文。")


def _announcements(symbol, now):
    params = {"sr": -1, "page_size": 10, "page_index": 1, "ann_type": "A", "stock_list": symbol[2:]}
    body = json.loads(DS._get(ANNOUNCEMENTS + urlencode(params), DS.HDR_EM, timeout=8))
    if (not isinstance(body, dict) or body.get("success") != 1 or not isinstance(body.get("data"), dict)
            or not isinstance(body["data"].get("list"), list)):
        raise SourceError("公告接口响应无效")
    market, tag = {"sh": ("1", "SHA"), "sz": ("0", "SZA"), "bj": ("0", "BJA")}[symbol[:2]]
    items, company_name = [], ""
    for row in body["data"]["list"]:
        if not isinstance(row, dict) or not isinstance(row.get("codes"), list):
            raise SourceError("公告缺少证券标识")
        matches = [code for code in row["codes"] if isinstance(code, dict)
                   and code.get("stock_code") == symbol[2:] and str(code.get("market_code")) == market
                   and tag in str(code.get("ann_type", "")).split(",")]
        if not matches and row["codes"] and all(isinstance(code, dict)
                and code.get("stock_code") == symbol[2:] and code.get("ann_type") == "INV"
                for code in row["codes"]):
            # This endpoint also returns investor-relations entries without an A-share
            # exchange tag; exclude those rather than guessing their exchange.
            continue
        if not matches or not _text(matches[0].get("short_name"), 80):
            raise SourceError("公告证券名称、代码或交易所与请求不一致")
        company_name = _text(matches[0]["short_name"], 80)
        date = _timestamp(row.get("display_time")) or _timestamp(row.get("notice_date"))
        title, article = _text(row.get("title"), 500), row.get("art_code", "")
        if not date or not title or not re.fullmatch(r"AN[0-9]{18}", str(article)):
            raise SourceError("公告标题、发布时间或编号无效")
        if not now - timedelta(days=30) <= date <= now:
            continue
        items.append({"title": title, "date": date.strftime("%Y-%m-%d %H:%M:%S"),
                      "notice_date": _date(row.get("notice_date")), "symbol": symbol,
                      "company_name": company_name, "source": "东方财富公司公告", "kind": "公告",
                      "url": "https://data.eastmoney.com/notices/detail/%s/%s.html" % (symbol[2:], article),
                      "summary": ""})
    return sorted(items, key=lambda item: item["date"], reverse=True)[:10], company_name


def _media_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if (parsed.scheme not in ("http", "https") or parsed.hostname not in
            ("finance.eastmoney.com", "stock.eastmoney.com") or parsed.username or parsed.password
            or parsed.netloc != parsed.hostname or not re.fullmatch(r"/a/[0-9]{18}\.html", parsed.path)):
        return None
    return "https://" + parsed.hostname + parsed.path


def _media_rows(symbol):
    param = {"uid": "", "keyword": symbol[2:], "type": ["cmsArticleWebOld"], "client": "web",
             "clientType": "web", "clientVersion": "curr", "param": {"cmsArticleWebOld": {
                 "searchScope": "default", "sort": "latest", "pageIndex": 1, "pageSize": 100,
                 "preTag": "", "postTag": ""}}}
    url = MEDIA_SEARCH + urlencode({"cb": "callback", "param": json.dumps(param, ensure_ascii=False,
                                                                           separators=(",", ":"))})
    text = DS._get(url, DS.HDR_EM, timeout=8)
    match = re.fullmatch(r"\s*callback\((.*)\);?\s*", text, re.S)
    if not match:
        raise SourceError("媒体新闻响应不是预期 JSONP")
    body = json.loads(match[1])
    if (not isinstance(body, dict) or body.get("code") != 0 or not isinstance(body.get("result"), dict)
            or not isinstance(body["result"].get("cmsArticleWebOld"), list)):
        raise SourceError("媒体新闻接口响应无效")
    return body["result"]["cmsArticleWebOld"]


def _media_news(symbol, name, now, rows):
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title, summary = _text(row.get("title"), 500), _text(row.get("content"), 1500)
        text = title + " " + summary
        # Query matches alone do not establish company relevance. Require its verified
        # name or an explicit exchange-qualified ticker, not an arbitrary six-digit number.
        ticker = re.search(r"(?<![A-Za-z0-9])(?:%s\.%s|%s)(?![A-Za-z0-9])" %
                           (symbol[2:], symbol[:2].upper(), symbol.upper()), text, re.I)
        date, link = _timestamp(row.get("date")), _media_url(row.get("url"))
        if (not title or ((not name or name not in text) and not ticker) or not date or not link
                or not now - timedelta(days=30) <= date <= now):
            continue
        items.append({"title": title, "summary": summary, "date": date.strftime("%Y-%m-%d %H:%M:%S"),
                      "url": link, "source": _text(row.get("mediaName"), 100) or "东方财富资讯",
                      "kind": "新闻", "symbol": symbol, "company_name": name})
    return sorted(items, key=lambda item: item["date"], reverse=True)[:10]


def fetch_news(symbol):
    symbol = validate_symbol(symbol)
    now, items, coverage, name = _now(), [], {}, ""
    media_url = "https://so.eastmoney.com/news/s?" + urlencode({"keyword": symbol[2:]})
    # Both requests start together. If announcements cannot verify a name, only
    # explicit exchange-qualified tickers establish relevance for media results.
    with ThreadPoolExecutor(max_workers=2) as pool:
        ann_task = pool.submit(_announcements, symbol, now)
        media_task = pool.submit(_media_rows, symbol)
        try:
            announcements, name = ann_task.result()
            items.extend(announcements)
            coverage["announcements"] = {"status": "ok" if announcements else "empty", "url": _ann_url(symbol)}
        except Exception as exc:
            coverage["announcements"] = {"status": "failed", "url": _ann_url(symbol), "error": str(exc)}
        try:
            media = _media_news(symbol, name, now, media_task.result())
            items.extend(media)
            coverage["media_news"] = {"status": "ok" if media else "empty", "url": media_url}
        except Exception as exc:
            coverage["media_news"] = {"status": "failed", "url": media_url, "error": str(exc)}
    errors = ["%s: %s" % (key, section["error"]) for key, section in coverage.items()
              if section["status"] == "failed"]
    unique = {item["url"]: item for item in items}
    data = {"items": sorted(unique.values(), key=lambda item: item["date"], reverse=True),
            "coverage": coverage, "window_days": 30,
            "note": "公告及新闻摘要覆盖近30天；媒体按检索候选筛选关联并排序，未读取全文，可能不完整。"}
    status = "failed" if len(errors) == 2 else "partial" if errors else "ok" if items else "empty"
    return _section(data, media_url, status=status, errors=errors)


def fetch_valuation(symbol):
    symbol = validate_symbol(symbol)
    text = DS._get("https://qt.gtimg.cn/q=" + symbol, DS.HDR_TX, "gbk", 8)
    match = re.fullmatch(r'\s*v_%s="([^"\r\n]*)";\s*' % symbol, text)
    if not match:
        raise SourceError("估值报价标识不一致")
    fields = match[1].split("~")
    if (len(fields) <= 46 or fields[0] != {"sh": "1", "sz": "51", "bj": "62"}[symbol[:2]]
            or fields[2] != symbol[2:]):
        raise SourceError("估值报价交易所或代码不一致")
    price, date = _number(fields[3]), None
    if re.fullmatch(r"[0-9]{14}", fields[30]):
        try:
            date = datetime.strptime(fields[30], "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    if price is None or price <= 0 or date is None:
        raise SourceError("估值报价价格或时间无效")
    def capitalization(index):
        value = _number(fields[index])
        return value * 1e8 if value is not None and value > 0 and math.isfinite(value * 1e8) else None
    data = {"symbol": symbol, "name": _text(fields[1], 80), "price": price, "date": date,
            "pe_ttm": _number(fields[39]), "pb": _number(fields[46]),
            "float_market_cap": capitalization(44), "total_market_cap": capitalization(45)}
    for field in ("pe_ttm", "pb"):
        if data[field] == 0:
            data[field] = None
    return _section(data, "https://gu.qq.com/" + symbol, source="腾讯行情")
