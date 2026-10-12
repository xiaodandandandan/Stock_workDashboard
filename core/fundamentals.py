"""On-demand fundamental facts and transparent rules, independent of market scans."""
import copy
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from urllib.parse import urlparse

from . import CACHE, datasource, fundamental_sources, store
from .fundamental_report import build_report
from .validation import InputError, to_symbol

LOG = logging.getLogger(__name__)
FUNDAMENTAL_DIR = os.path.join(CACHE, "fundamentals")
CACHE_TTL = 15 * 60
SCHEMA_VERSION = 2
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_SECTIONS = ("profile", "financials", "valuation", "news")
_REPORT_NUMBERS = ("revenue", "net_profit", "net_profit_excl", "revenue_yoy", "net_profit_yoy", "operating_cashflow",
                   "roe", "gross_margin", "debt_ratio", "eps", "total_assets", "total_liabilities", "parent_equity",
                   "goodwill", "inventory", "accounts_receivable", "financing_cashflow", "cash", "undistributed_profit",
                   "operating_profit", "operating_cost")
TITLE_NOTICE = "仅按标题规则初筛，未阅读全文，需核验；可能影响业务或业绩，不代表股价即涨即跌。"
_TITLE_NEGATION = re.compile(
    r"(?:未|尚未|未曾|没有)(?:实际|实施|发生|涉及|出现|进行|被|受|遭)?"
    r"(?:减持|回购|增持|立案|诉讼|违约|处罚|亏损|分红|中标)"
    r"|(?:未收到|尚未收到|未曾收到|没有收到)[^，。；;！？!?\n]{0,12}(?:立案|处罚|诉讼|违约)"
    r"|(?:不存在|没有)(?:重大|任何|相关)?(?:诉讼|违约|立案|处罚|亏损|减持|回购|增持)"
    r"|无(?:重大|相关)?(?:诉讼|违约|立案|处罚|亏损)")


def _symbol(value):
    symbol = to_symbol(value)
    if not re.fullmatch(r"sh(?:60|68)[0-9]{4}|sz(?:00|30)[0-9]{4}|bj(?:[48][0-9]{5}|92[0-9]{4})", symbol):
        raise InputError("基本面分析仅支持沪深北 A 股代码")
    return symbol


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _today():
    return date.today()


def _as_date(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?)?", value):
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _safe_url(value):
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    return value if parsed.scheme == "https" and parsed.netloc and not parsed.username else ""


def _moment(timestamp):
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _money(value):
    value = _number(value)
    if value is None:
        return "未披露"
    if abs(value) >= 100_000_000:
        return "%.2f 亿元" % (value / 100_000_000)
    if abs(value) >= 10_000:
        return "%.2f 万元" % (value / 10_000)
    return "%.2f 元" % value


def _signal(title, direction, reason, evidence, kind="事实"):
    return {"title": title, "direction": direction, "reason": reason, "evidence": evidence,
            "kind": kind, "method": "已披露财务数据规则解读"}


def financial_signals(reports, profile=None):
    """Read disclosed facts; avoid ratings, cross-industry cutoffs and price predictions."""
    if not reports:
        return []
    current = reports[0]
    period = current["report_date"]
    signals = []
    profit = _number(current.get("net_profit"))
    deduct = _number(current.get("net_profit_excl"))
    previous = next((report for report in reports[1:]
                     if report["report_date"][5:] == period[5:]
                     and int(report["report_date"][:4]) == int(period[:4]) - 1), None)
    old_profit = _number((previous or {}).get("net_profit"))
    if profit is not None:
        signals.append(_signal("归母净利润", "偏负面" if profit < 0 else "中性",
                               "本报告期处于亏损状态。" if profit < 0 else
                               ("本报告期归母净利润为零。" if profit == 0 else "本报告期归母净利润为正；盈利不等同于股价上涨。"),
                               "%s：归母净利润 %s；扣非归母净利润 %s" % (period, _money(profit), _money(deduct))))
    profit_yoy = _number(current.get("net_profit_yoy"))
    if profit is not None and old_profit is not None and (profit < 0 or old_profit <= 0):
        if old_profit < 0 < profit:
            title, direction, reason = "净利润同比扭亏", "偏积极", "相比上年同期由亏损转为盈利；需核验是否包含一次性收益。"
        elif old_profit < 0 and profit == 0:
            title, direction, reason = "同比亏损收窄至零", "偏积极", "本期净利润为零，上年同期亏损；不能称本期已盈利。"
        elif profit < 0 and old_profit > 0:
            title, direction, reason = "净利润同比转亏", "偏负面", "相比上年同期由盈利转为亏损。"
        elif profit < 0 and old_profit == 0:
            title, direction, reason = "净利润同比亏损", "偏负面", "上年同期净利润为零，本期净亏损；百分比增速不可比。"
        elif profit < 0 and old_profit < 0:
            improved = profit > old_profit
            title = "同比亏损收窄" if improved else ("同比亏损扩大" if profit < old_profit else "同比亏损持平")
            direction = "偏积极" if improved else ("偏负面" if profit < old_profit else "中性")
            reason = "直接比较同口径亏损金额；当前仍亏损，不按负基数同比百分比推断盈利。"
        else:
            title, direction, reason = "净利润同比比较", "需核验", "上年同期利润为零，百分比增速不能代表可比增长。"
        signals.append(_signal(title, direction, reason,
                               "%s：归母 %s，扣非 %s；%s：归母 %s，扣非 %s" % (
                                   previous["report_date"], _money(old_profit), _money(previous.get("net_profit_excl")),
                                   period, _money(profit), _money(deduct))))
    elif profit_yoy is not None:
        if profit is None:
            direction, reason = "需核验", "归母利润金额未取得，仅有同比不能确认盈利状态；还需并列扣非口径。"
        elif profit < 0:
            direction, reason = "需核验", "本期仍亏损，负基数同比需结合上年同期利润核验，不能仅因同比为正视作增长。"
        else:
            direction = "偏积极" if profit_yoy > 0 else ("偏负面" if profit_yoy < 0 else "中性")
            reason = "归母净利润的同比变动；需结合一次性损益与上年基数核验。"
        signals.append(_signal("归母净利润同比", direction, reason,
                               "%s：披露同比 %.2f%%；归母 %s，扣非 %s" % (period, profit_yoy, _money(profit), _money(deduct))))
    revenue_yoy = _number(current.get("revenue_yoy"))
    if revenue_yoy is not None:
        signals.append(_signal("营业收入同比", "偏积极" if revenue_yoy > 0 else ("偏负面" if revenue_yoy < 0 else "中性"),
                               "披露的收入同比变动，不能据此推断未来增长或股价。",
                               "%s：营业收入 %s，同比 %.2f%%" % (period, _money(current.get("revenue")), revenue_yoy)))
    industry = str((profile or {}).get("industry") or "")
    bank = "银行" in industry
    cash = _number(current.get("operating_cashflow"))
    if cash is not None:
        reason = ("银行现金流受吸收存款、贷款等业务影响，不套用一般企业的现金流评分。" if bank else
                  "现金净流入/流出需结合季节性和营运资本变化，与利润对照核验。")
        signals.append(_signal("经营活动现金流", "中性" if bank or cash >= 0 else "需核验", reason,
                               "%s：经营现金流 %s；归母净利润 %s" % (period, _money(cash), _money(profit))))
    readings = [("净资产收益率", "roe"), ("毛利率", "gross_margin"), ("资产负债率", "debt_ratio")]
    for title, field in readings:
        value = _number(current.get(field))
        if value is not None:
            signals.append(_signal(title, "中性", "仅列示已披露读数；需同业、同口径比较，不使用统一高低阈值评分。",
                                   "%s：%s %.2f%%" % (period, title, value)))
    return signals


def title_impact(title):
    """A reproducible title-only screen; never represented as full-text sentiment."""
    title = str(title or "")
    positive = [word for word in ("预增", "扭亏", "业绩增长", "净利润增长", "中标", "回购", "增持", "分红", "重大合同", "新增订单")
                if word in title]
    negative = [word for word in ("预减", "亏损", "下滑", "减持", "终止", "立案", "处罚", "违约", "诉讼", "退市")
                if word in title]
    uncertain = bool(_TITLE_NEGATION.search(title)) or any(word in title for word in (
        "可能", "风险", "不确定", "问询", "澄清", "取消", "未中标", "未获", "未通过", "不通过", "不予",
        "未实施", "未完成", "未进行", "未增持", "未回购", "不增持", "不回购", "不分红", "不派"))
    if uncertain or (positive and negative):
        direction, reason = "需核验", "标题存在风险、否定或多方向线索，需阅读全文核验，不能按单个关键词定性。"
    elif positive:
        direction, reason = "偏积极", "标题含可能改善收入、利润或股东回报的线索；需核验金额、条件与实际执行情况。"
    elif negative:
        direction, reason = "偏负面", "标题含可能影响经营、利润或股东交易的风险线索；需核验影响范围与实际结果。"
    else:
        direction, reason = "需核验", "标题缺少明确方向或量化证据，不能据此判断影响。"
    hits = positive + negative
    return {"direction": direction, "reason": reason,
            "evidence": "标题关键词：" + "、".join(hits) if hits else "标题：" + title,
            "kind": "推断", "method": "标题规则初筛", "notice": TITLE_NOTICE}


def _normalized_reports(received, limit, annual=False):
    reports, rejected = [], 0
    if not isinstance(received, list):
        received, rejected = [], 1
    for report in received:
        if not isinstance(report, dict):
            rejected += 1
            continue
        period = _as_date(report.get("report_date"))
        announced_raw = report.get("announced_date")
        announced = _as_date(announced_raw) if announced_raw else None
        if period is None or period > _today() or (announced_raw and (announced is None or announced > _today() or announced < period)):
            rejected += 1
            continue
        if annual and (period.month != 12 or period.day != 31 or period.year >= _today().year):
            rejected += 1
            continue
        normalized = dict(report, report_date=period.isoformat())
        for field in _REPORT_NUMBERS:
            normalized[field] = _number(report.get(field))
        reports.append(normalized)
    reports.sort(key=lambda report: report["report_date"], reverse=True)
    seen, unique = set(), []
    for report in reports:
        if report["report_date"] not in seen:
            unique.append(report)
            seen.add(report["report_date"])
    return unique[:limit], rejected


def _business_period(received):
    if not isinstance(received, dict):
        return None
    period = _as_date(received.get("report_date"))
    if period is None or period > _today():
        return None
    items = []
    for item in received.get("items", []) if isinstance(received.get("items"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            continue
        normalized = dict(item)
        for field in ("revenue", "gross_margin", "share_pct"):
            normalized[field] = _number(item.get(field))
        if normalized["share_pct"] is not None and not 0 <= normalized["share_pct"] <= 100:
            normalized["share_pct"] = None
        items.append(normalized)
    return dict(received, report_date=period.isoformat(), items=items)


def _normalize_business(received):
    raw = received if isinstance(received, dict) else {}
    normalized = _business_period(raw)
    periods = []
    for row in raw.get("periods", []) if isinstance(raw.get("periods"), list) else []:
        period = _business_period(row)
        if period is not None:
            periods.append(period)
    if normalized is not None and not any(row["report_date"] == normalized["report_date"] for row in periods):
        periods.append(normalized)
    periods.sort(key=lambda row: row["report_date"], reverse=True)
    latest = periods[0] if periods else normalized
    classification = (latest or raw).get("classification", "")
    compatible = [row for row in periods if row.get("classification", "") == classification]
    seen, unique = set(), []
    for row in compatible:
        if row["report_date"] not in seen:
            seen.add(row["report_date"])
            unique.append(row)
    result = dict(raw, report_date=(latest or {}).get("report_date"), items=(latest or {}).get("items", []),
                  classification=classification, periods=unique[:4],
                  source=str(raw.get("source") or ""), url=_safe_url(raw.get("url")), error=str(raw.get("error") or ""),
                  incomparable_periods=len(periods) - len(compatible))
    if raw.get("status") not in ("ok", "partial", "empty", "failed"):
        result["status"] = "ok" if result["items"] else "empty"
    elif not result["items"] and raw.get("status") in ("ok", "partial"):
        result["status"] = "empty"
    result["notice"] = "仅比较同一业务分类；未取得的构成不以行业标签或零值代替。"
    return result


def _normalize_financials(section, profile):
    data = section["data"]
    data["reports"], rejected = _normalized_reports(data.get("reports", []), 8)
    data["annual_reports"], rejected_annual = _normalized_reports(data.get("annual_reports", []), 10, annual=True)
    data["business_segments"] = _normalize_business(data.get("business_segments"))
    data["signals"] = financial_signals(data["reports"], profile)
    data["rejected_reports"] = rejected
    data["rejected_annual_reports"] = rejected_annual
    data["notice"] = "财务数据按披露报告期读取；不同季度累计口径不可直接环比，未作统一行业评分或股价预测。"
    if not data["reports"] and not data["annual_reports"] and section["status"] != "failed":
        section["status"] = "empty"


def _normalize_news(section, symbol):
    data = section["data"]
    items, rejected = [], 0
    received = data.get("items", [])
    if not isinstance(received, list):
        received, rejected = [], 1
    for item in received:
        if (not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"].strip()
                or (item.get("symbol") is not None and item["symbol"] != symbol)):
            rejected += 1
            continue
        published = _as_date(item.get("date"))
        if published is None or published > _today():
            rejected += 1
            continue
        current = dict(item)
        current["url"] = _safe_url(current.get("url"))
        current["impact"] = title_impact(current["title"])
        items.append(current)
    items.sort(key=lambda item: item["date"], reverse=True)
    data["items"] = items[:30]
    data["rejected_items"] = rejected
    data["notice"] = TITLE_NOTICE
    coverage = data.get("coverage", {})
    if isinstance(coverage, dict):
        incomplete = [kind for kind, result in coverage.items()
                      if isinstance(result, dict) and result.get("status") == "failed"]
        if incomplete:
            section["partial"] = True
            message = "未获取：" + "、".join(incomplete)
            section["error"] = "; ".join(filter(None, [section["error"], message]))
            section.setdefault("errors", []).append(message)
    if not items and section["status"] != "failed":
        section["status"] = "empty"


def _valuation(symbol):
    fetch = getattr(fundamental_sources, "fetch_valuation", None)
    if fetch is not None:
        return fetch(symbol)
    result = datasource.quote_prices([symbol])
    quote = result.get("quotes", {}).get(symbol)
    if quote is None:
        raise RuntimeError(result.get("errors", {}).get(symbol, "最新报价缺失"))
    return {"status": "ok", "data": {"price": quote.get("price"), "date": quote.get("date"),
                                          "previous_close": quote.get("previous_close"), "pe_ttm": None, "pb": None,
                                          "notice": "当前来源只提供报价；未计算或推断市盈率、市净率。"},
            "as_of": quote.get("date", ""), "url": "https://qt.gtimg.cn/", "source": "腾讯行情"}


def _fetch_section(kind, symbol):
    try:
        fetch = _valuation if kind == "valuation" else getattr(fundamental_sources, "fetch_" + kind)
        result = fetch(symbol)
        if not isinstance(result, dict) or result.get("status") not in ("ok", "empty", "partial", "failed") or not isinstance(result.get("data"), dict):
            raise ValueError("数据源响应结构无效")
        data = copy.deepcopy(result["data"])
        if data.get("symbol") is not None and data["symbol"] != symbol:
            raise ValueError("数据源标的不匹配")
        errors = result.get("errors", [])
        if not isinstance(errors, list):
            errors = [errors]
        error = "; ".join(str(part) for part in [result.get("error")] + errors if part)
        return {"status": "ok" if result["status"] == "partial" else result["status"], "data": data,
                "partial": result["status"] == "partial", "error": error,
                "errors": [str(part) for part in [result.get("error")] + errors if part],
                "as_of": str(result.get("as_of") or ""), "url": _safe_url(result.get("url")),
                "source": str(result.get("source") or kind)}
    except Exception as exc:
        LOG.warning("基本面来源失败: %s %s: %s", symbol, kind, exc)
        message = "%s: %s" % (type(exc).__name__, exc)
        return {"status": "failed", "partial": False, "data": {}, "error": message, "errors": [message],
                "as_of": "", "url": "", "source": kind}


def _lock_for(symbol):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(symbol, threading.Lock())


def _valid_cached_report(report):
    if not isinstance(report, dict):
        return False
    verification = report.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "pending":
        return False
    links = verification.get("links")
    if not isinstance(links, list) or any(not isinstance(link, dict) or not isinstance(link.get("label"), str)
                                          or not _safe_url(link.get("url")) for link in links):
        return False
    for field in ("kpis", "conclusions", "risk_exposure", "cash_quality", "scenarios", "checklist", "regulatory_checks"):
        entries = report.get(field)
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            return False
    return (isinstance(report.get("valuation_notes"), list)
            and all(isinstance(note, str) for note in report["valuation_notes"])
            and isinstance(report.get("annual_coverage"), dict)
            and isinstance(report.get("business_segments"), dict))


def _cached(path, symbol, now):
    if os.path.islink(path):
        return None
    saved = store.read_json(path)
    if not isinstance(saved, dict) or saved.get("schema") != SCHEMA_VERSION or saved.get("symbol") != symbol:
        return None
    stamp = _number(saved.get("saved"))
    payload = saved.get("payload")
    if (stamp is None or not 0 <= now - stamp < CACHE_TTL or not isinstance(payload, dict)
            or payload.get("symbol") != symbol or payload.get("schema") != SCHEMA_VERSION
            or not isinstance(payload.get("name"), str) or _as_date(payload.get("generated")) is None
            or not isinstance(payload.get("summary"), list) or not isinstance(payload.get("sources"), list)):
        return None
    sections = payload.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(_SECTIONS):
        return None
    if any(not isinstance(section, dict) or section.get("status") not in ("ok", "empty", "failed")
           or not isinstance(section.get("data"), dict) for section in sections.values()):
        return None
    if not any(section["status"] == "ok" for section in sections.values()):
        return None
    if (not isinstance(sections["financials"]["data"].get("reports"), list)
            or not isinstance(sections["financials"]["data"].get("annual_reports"), list)
            or not isinstance(sections["financials"]["data"].get("business_segments"), dict)
            or not isinstance(sections["financials"]["data"].get("signals"), list)
            or not isinstance(sections["news"]["data"].get("items"), list)):
        return None
    if not _valid_cached_report(payload.get("report")):
        return None
    cached = copy.deepcopy(payload)
    cached["cache"] = {"hit": True, "persisted": True, "ttl_seconds": CACHE_TTL,
                       "saved_at": _moment(stamp), "expires_at": _moment(stamp + CACHE_TTL), "error": ""}
    return cached


def fundamental_payload(symbol, refresh=False):
    """Fetch up to four independent sources only when requested, with a 15-minute cache."""
    symbol = _symbol(symbol)
    if not isinstance(refresh, bool):
        raise InputError("刷新标记需为布尔值")
    path = os.path.join(FUNDAMENTAL_DIR, symbol + ".json")
    with _lock_for(symbol):
        now = time.time()
        cached = None if refresh else _cached(path, symbol, now)
        if cached is not None:
            return cached
        sections = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(_fetch_section, kind, symbol): kind for kind in _SECTIONS}
            for future in as_completed(pending):
                sections[pending[future]] = future.result()
        _normalize_financials(sections["financials"], sections["profile"]["data"])
        _normalize_news(sections["news"], symbol)
        for field in ("price", "pe_ttm", "pb", "total_market_cap", "float_market_cap"):
            sections["valuation"]["data"][field] = _number(sections["valuation"]["data"].get(field))
        sections["valuation"]["data"]["notice"] = "估值为来源披露的时点读数；负市盈率不能按数值小认定低估，需结合同业与利润口径核验。"
        ordered = {kind: sections[kind] for kind in _SECTIONS}
        ok = any(section["status"] == "ok" for section in ordered.values())
        failed = any(section["status"] == "failed" or section.get("partial") for section in ordered.values())
        summary = [signal["title"] + "：" + signal["evidence"] for signal in sections["financials"]["data"].get("signals", [])[:4]]
        if failed:
            summary.append("部分来源未获取：" + "、".join(kind for kind, section in ordered.items()
                                                   if section["status"] == "failed" or section.get("partial")))
        if not summary:
            summary.append("暂无可解读的已披露财务数据，请查看各来源状态与原文。")
        fetched_at = time.time()
        sources = []
        for section in ordered.values():
            entry = {"label": section["source"], "url": section["url"]}
            if entry["url"] and entry not in sources:
                sources.append(entry)
        payload = {"schema": SCHEMA_VERSION, "symbol": symbol,
                   "name": sections["profile"]["data"].get("name") or sections["valuation"]["data"].get("name") or symbol,
                   "generated": _moment(fetched_at), "status": "partial" if ok and failed else ("ok" if ok else ("failed" if failed else "empty")),
                   "sections": ordered, "summary": summary, "sources": sources,
                   "notice": "基本面读数和标题规则仅供核验，不构成统一评分、投资建议或股价预测。",
                   "cache": {"hit": False, "persisted": False, "ttl_seconds": CACHE_TTL,
                             "saved_at": _moment(fetched_at), "expires_at": _moment(fetched_at + CACHE_TTL), "error": ""}}
        payload["report"] = build_report(symbol, ordered, payload["generated"])
        if ok:
            try:
                payload["cache"]["persisted"] = True
                store.write_json(path, {"schema": SCHEMA_VERSION, "symbol": symbol, "saved": fetched_at, "payload": payload})
            except (OSError, ValueError, TypeError) as exc:
                payload["cache"]["persisted"] = False
                payload["cache"]["error"] = "%s: %s" % (type(exc).__name__, exc)
                LOG.warning("基本面缓存写入失败: %s", symbol, exc_info=True)
        elif refresh:
            # An explicit refresh failure must not resurrect a prior successful cache.
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError:
                LOG.warning("移除旧基本面缓存失败: %s", symbol, exc_info=True)
        return payload
