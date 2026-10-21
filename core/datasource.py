# -*- coding: utf-8 -*-
"""取数层：行情、行业、名称。零第三方依赖。

数据源的选择不是随便挑的，每一条都是踩过坑之后定的（详见 SKILL.md）：

  · **日 K 和分钟 K 都用新浪**（`getKLineData`，scale=240/60/30）——
    同一个接口、同一套复权口径，才能保证「榜单上显示的关键位」和
    「点开后图上画的中枢 / 前低」是同一批数据。腾讯 `fqkline` 曾在
    ~6000 次请求后被限流封禁（501），不适合做工作台的唯一源。
  · **全市场列表**用新浪 `Market_Center.getHQNodeData`（GBK 编码）。
  · **行业**用东财 `RPT_F10_ORG_BASICINFO`（`datacenter-web` 域名可达，
    `push2*` 域名在本机不通）—— 一次拉全量做映射，不要按股逐个查。
  · **名称兜底**用腾讯 `qt.gtimg.cn` 报价（新浪列表只覆盖沪深 A，
    指定代码可能是 ETF / 榜外票）。
"""
import json
import logging
import math
import re
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
from datetime import datetime

from .validation import InputError, integer, market_prefix, to_symbol

CTX = ssl.create_default_context()
LOG = logging.getLogger(__name__)

HDR_SINA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
HDR_EM = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
HDR_TX = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 周期 → 新浪 scale 值。240 分钟 = 一个交易日 = 日线。
SCALE = {"day": 240, "m60": 60, "m30": 30, "m15": 15, "m5": 5}
UNIT = {"day": "日线", "m60": "60分钟线", "m30": "30分钟线", "m15": "15分钟线", "m5": "5分钟线"}
PROVENANCE = {"provider": "sina.getKLineData", "adjustment": "provider_default",
              "amount": "close_times_volume"}


def _get(url, hdr, dec="utf-8", timeout=25):
    """Bounded retries for transport failures/rate limits; never disable TLS checks."""
    for attempt in range(3):
        req = urllib.request.Request(url, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as response:
                return response.read().decode(dec)
        except urllib.error.HTTPError as e:
            if e.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if isinstance(getattr(e, "reason", e), ssl.SSLCertVerificationError) or attempt == 2:
                raise
        LOG.warning("行情请求重试 %d/2: %s", attempt + 1, url)
        time.sleep(0.25 * (2 ** attempt))


def validate_freq(freq):
    if not isinstance(freq, str) or freq not in SCALE:
        raise InputError("未知周期 %s" % freq)
    return freq


def _ts(raw, scale):
    """行情时间戳 → 整数。日线 8 位（20261010），分钟线 12 位（202610101500）。

    ★ 分钟线务必截到 12 位：新浪给的是 "2026-10-10 15:00:00"（带秒），
      直接全部塞进去会得到 14 位，而 kline_canvas.js 的横轴标签只认 8/12 位，
      多出来的两位会让日期显示成 20261010150000 这种没法读的东西。
    """
    s = str(raw).replace("-", "").replace(":", "").replace(" ", "")
    return int(s[:12] if scale != 240 else s[:8])


def sina_kline(symbol, freq="day", count=800):
    """新浪 K 线。symbol 形如 sz002693 / sh601138。

    返回按时间升序的 bars：{date, open, high, low, close, volume}
    （volume 单位是**股**，腾讯的 fqkline 是手，别混用）。
    """
    symbol = to_symbol(symbol)
    scale = SCALE[validate_freq(freq)]
    count = integer(count, "count", 30, 5000)
    u = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "CN_MarketData.getKLineData?symbol=%s&scale=%d&ma=no&datalen=%d"
         % (symbol, scale, count))
    d = json.loads(_get(u, HDR_SINA))
    if not d:
        # 新浪对「代码前缀错 / 停牌 / 无此标的」一律返回字面量 null（HTTP 仍 200），
        # 静默返回空列表会让调用方以为「这只票就是没数据」—— 明确报出来。
        raise RuntimeError("新浪无 %s 的 %s 数据（检查交易所前缀，或该标的已退市/停牌）"
                           % (symbol, UNIT.get(freq, freq)))
    return [{"date": _ts(x["day"], scale),
             "open": float(x["open"]), "high": float(x["high"]),
             "low": float(x["low"]), "close": float(x["close"]),
             "volume": float(x.get("volume") or 0)} for x in d]


class MarketListError(RuntimeError):
    pass


def market_list(node="hs_a", pages=60, num=100, timeout=25):
    """All requested pages must succeed; an error is never an empty page."""
    from concurrent.futures import ThreadPoolExecutor

    def one(p):
        u = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData?page=%d&num=%d&sort=symbol&asc=1&node=%s"
             % (p, num, node))
        try:
            d = json.loads(_get(u, HDR_SINA, "gbk", timeout))
            # Sina returns null/[] for pages after the end of the market.
            if d is None:
                d = []
            if not isinstance(d, list):
                raise ValueError("列表响应不是数组")
            return [{"symbol": to_symbol(x["symbol"]), "name": x["name"],
                     "code": to_symbol(x["symbol"])[2:]} for x in d], None
        except Exception as e:
            LOG.warning("市场列表第 %d 页失败", p, exc_info=True)
            return [], "第 %d 页: %s: %s" % (p, type(e).__name__, e)

    out, errors, last = [], [], []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for r, error in ex.map(one, range(1, pages + 1)):
            out += r
            last = r
            if error:
                errors.append(error)
    if errors:
        raise MarketListError("市场列表不完整（失败 %d 页）；%s" % (len(errors), "; ".join(errors[:3])))
    if not out:
        raise MarketListError("市场列表为空，保留上次成功扫描")
    if len(last) >= num:
        raise MarketListError("市场列表达到分页上限，无法确认完整性")
    if len({x["symbol"] for x in out}) != len(out):
        raise MarketListError("市场列表包含重复标的，无法确认完整性")
    return out


class IndustryError(RuntimeError):
    pass


def industry_map(pages=50, size=500):
    """在市 A 股行业 → {symbol: {'name','l1','full'}}，按服务端分页数获取。"""
    from concurrent.futures import ThreadPoolExecutor

    def one(p):
        params = {"reportName": "RPT_F10_ORG_BASICINFO",
                  "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,EM2016",
                  "filter": '(SECURITY_TYPE="A股")(LISTING_STATE="0")',
                  "sortColumns": "SECUCODE", "sortTypes": 1,
                  "pageSize": size, "pageNumber": p}
        u = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + urlencode(params)
        try:
            j = json.loads(_get(u, HDR_EM))
            if not isinstance(j, dict) or j.get("success") is not True:
                raise ValueError(j.get("message", "接口响应无效") if isinstance(j, dict) else "接口响应无效")
            result = j.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise ValueError("缺少行业列表")
            if (type(result.get("pages")) is not int or type(result.get("count")) is not int
                    or result["pages"] < 1 or result["count"] < 1):
                raise ValueError("行业分页信息无效或列表为空")
            return result
        except Exception as exc:
            raise IndustryError("行业第 %d 页获取失败：%s" % (p, exc)) from exc

    first = one(1)
    total_pages, count = first["pages"], first["count"]
    if total_pages > pages or total_pages != (count + size - 1) // size:
        raise IndustryError("行业列表超出分页上限或分页信息不一致")
    rows = list(first["data"])
    with ThreadPoolExecutor(max_workers=10) as ex:
        for result in ex.map(one, range(2, total_pages + 1)):
            if result["pages"] != total_pages or result["count"] != count:
                raise IndustryError("行业列表在分页期间发生变化，请重试")
            rows.extend(result["data"])
    if len(rows) != count:
        raise IndustryError("行业列表不完整：收到 %d / %d 条" % (len(rows), count))
    mp = {}
    for x in rows:
        if not isinstance(x, dict):
            LOG.warning("跳过格式无效的行业记录")
            continue
        # The unfiltered report also contains overseas/delisted securities. Never
        # infer their exchange from a bare code, or let one unsupported row erase A shares.
        match = re.fullmatch(r"([0-9]{6})\.(SH|SZ|BJ)", str(x.get("SECUCODE", "")))
        if not match or x.get("SECURITY_CODE") != match[1]:
            LOG.warning("跳过非 A 股或标识不一致的行业记录: %s", x.get("SECUCODE"))
            continue
        symbol = to_symbol(match[2].lower() + match[1])
        full = str(x.get("EM2016") or "").strip()
        if not full or not full.split("-")[0].strip():
            continue
        if symbol in mp:
            raise IndustryError("行业列表包含重复标的：%s" % symbol)
        mp[symbol] = {"name": x.get("SECURITY_NAME_ABBR") or "",
                      "l1": full.split("-")[0].strip(), "full": full}
    if not mp:
        raise IndustryError("行业接口未返回可用的 A 股分类")
    return mp


def quote_name(code):
    """腾讯报价取名称（新浪列表覆盖不到 ETF / 榜外代码时兜底）。取不到返回空串。"""
    try:
        txt = _get("https://qt.gtimg.cn/q=%s" % to_symbol(code), HDR_TX, "gbk", 10)
        p = txt.split("~")
        return p[1] if len(p) > 1 and p[1] else ""
    except Exception:                                           # noqa: BLE001
        LOG.warning("名称查询失败: %s", code, exc_info=True)
        return ""


def quote_prices(symbols):
    """腾讯当前报价，100 个一批、最多 4 个批次并发；错误按标的返回。

    不回填 0 或日 K 缓存。变量名、交易所编号、代码与报价时间均须对应请求，
    防止同一纯代码在不同交易所之间串用。price/date 是提供方的现价与行情时间，
    previous_close 是报价字段中的昨收；昨收缺失不应让有效现价一起丢失。
    """
    from concurrent.futures import ThreadPoolExecutor

    if isinstance(symbols, (str, bytes)):
        raise InputError("报价代码需为交易所标识数组")
    requested, unique = [], set()
    for raw in symbols:
        symbol = str(raw).strip().lower()
        if not re.fullmatch(r"(?:sh|sz|bj)[0-9]{6}", symbol):
            raise InputError("报价代码需为 sh/sz/bj + 6 位数字")
        if symbol not in unique:
            requested.append(symbol)
            unique.add(symbol)
    if not requested:
        return {"quotes": {}, "errors": {}}

    exchange_ids = {"sh": "1", "sz": "51", "bj": "62"}

    def batch(group):
        quotes, errors, seen = {}, {}, set()
        try:
            txt = _get("https://qt.gtimg.cn/q=" + ",".join(group), HDR_TX, "gbk", 10)
            if not isinstance(txt, str):
                raise ValueError("报价响应不是文本")
        except Exception as exc:
            LOG.warning("当前报价批次获取失败", exc_info=True)
            return {}, {symbol: "%s: %s" % (type(exc).__name__, exc) for symbol in group}
        wanted = set(group)
        for line in txt.splitlines():
            match = re.fullmatch(r'\s*v_((?:sh|sz|bj)[0-9]{6})="([^"\r\n]*)";\s*', line)
            if not match or match[1] not in wanted:
                continue
            symbol = match[1]
            if symbol in seen:
                quotes.pop(symbol, None)
                errors[symbol] = "报价响应包含重复标的"
                continue
            seen.add(symbol)
            fields = match[2].split("~")
            try:
                if (len(fields) <= 30 or fields[0] != exchange_ids[symbol[:2]]
                        or fields[2] != symbol[2:]):
                    raise ValueError("报价交易所或代码不一致")
                price = float(fields[3])
                if not math.isfinite(price) or price <= 0:
                    raise ValueError("报价价格无效")
                if not re.fullmatch(r"[0-9]{14}", fields[30]):
                    raise ValueError("报价时间格式无效")
                date = datetime.strptime(fields[30], "%Y%m%d%H%M%S")
                try:
                    previous_close = float(fields[4])
                    if not math.isfinite(previous_close) or previous_close <= 0:
                        previous_close = None
                except (TypeError, ValueError, OverflowError):
                    previous_close = None
                quotes[symbol] = {"price": price, "date": date.strftime("%Y-%m-%d %H:%M:%S"),
                                  "previous_close": previous_close}
            except (TypeError, ValueError) as exc:
                errors[symbol] = str(exc)
        for symbol in group:
            if symbol not in quotes and symbol not in errors:
                errors[symbol] = "当前报价缺失"
        return quotes, errors

    groups = [requested[i:i + 100] for i in range(0, len(requested), 100)]
    quotes, errors = {}, {}
    with ThreadPoolExecutor(max_workers=min(4, len(groups))) as executor:
        for group_quotes, group_errors in executor.map(batch, groups):
            quotes.update(group_quotes)
            errors.update(group_errors)
    return {"quotes": quotes, "errors": errors}
