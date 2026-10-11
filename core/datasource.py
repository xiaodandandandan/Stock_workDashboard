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
import ssl
import time
import urllib.error
import urllib.request

from . import SKILLS  # noqa: F401  （保持包初始化顺序：技能路径先注入）
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


def industry_map(pages=50, size=500):
    """东财全市场行业归属 → {code: {'name','l1','full'}}（并发，一次拉全量）。"""
    from concurrent.futures import ThreadPoolExecutor

    def one(p):
        u = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_ORG_BASICINFO"
             "&columns=SECURITY_CODE,SECURITY_NAME_ABBR,EM2016&pageSize=%d&pageNumber=%d" % (size, p))
        try:
            j = json.loads(_get(u, HDR_EM))
            return ((j.get("result") or {}).get("data") or [])
        except Exception:
            LOG.warning("行业第 %d 页获取失败", p, exc_info=True)
            return []

    rows = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(one, range(1, pages + 1)):
            rows += r
    mp = {}
    for x in rows:
        code = x.get("SECURITY_CODE")
        if not code:
            continue
        full = x.get("EM2016") or ""
        mp[to_symbol(code)] = {"name": x.get("SECURITY_NAME_ABBR") or "",
                    "l1": full.split("-")[0] if full else "未分类",
                    "full": full or "未分类"}
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
