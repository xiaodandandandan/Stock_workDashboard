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
import ssl
import urllib.request

from . import SKILLS  # noqa: F401  （保持包初始化顺序：技能路径先注入）

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

HDR_SINA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
HDR_EM = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
HDR_TX = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 周期 → 新浪 scale 值。240 分钟 = 一个交易日 = 日线。
SCALE = {"day": 240, "m60": 60, "m30": 30, "m15": 15, "m5": 5}
UNIT = {"day": "日线", "m60": "60分钟线", "m30": "30分钟线", "m15": "15分钟线", "m5": "5分钟线"}


def _get(url, hdr, dec="utf-8", timeout=25):
    req = urllib.request.Request(url, headers=hdr)
    return urllib.request.urlopen(req, timeout=timeout, context=CTX).read().decode(dec, "ignore")


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
    scale = SCALE[freq]
    u = ("http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
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


def market_list(node="hs_a", pages=60, num=100, timeout=25):
    """新浪全市场股票列表（默认沪深 A）。返回 [{symbol, name, code}]。"""
    from concurrent.futures import ThreadPoolExecutor

    def one(p):
        u = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData?page=%d&num=%d&sort=symbol&asc=1&node=%s"
             % (p, num, node))
        try:
            d = json.loads(_get(u, HDR_SINA, "gbk", timeout))
            return [{"symbol": x["symbol"], "name": x["name"], "code": x["code"]} for x in d]
        except Exception:                                       # noqa: BLE001
            return []

    out = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(one, range(1, pages + 1)):
            out += r
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
        except Exception:                                       # noqa: BLE001
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
        mp[code] = {"name": x.get("SECURITY_NAME_ABBR") or "",
                    "l1": full.split("-")[0] if full else "未分类",
                    "full": full or "未分类"}
    return mp


def market_prefix(code):
    """代码 → 交易所前缀。

    ⚠️ 别漏 ETF 段：**5 开头是上交所 ETF**（510300 沪深300ETF、588000 科创50ETF），
    **1 开头是深交所 ETF/LOF**（159915 创业板ETF）；只认 6/0/3 会把这两段误判成 bj，
    表现是接口「未返回数据」而不是报错，极易误当成数据源故障。
    显式前缀（sh/sz/bj/hk）一律透传 —— 指数必须走这条：sh000001 上证指数与
    sz000001 平安银行代码完全相同。
    """
    c = str(code).strip().lower()
    if c.startswith(("sh", "sz", "bj")) and c[2:].isdigit():
        return c[:2]
    if c.startswith("hk") or (len(c) == 5 and c.isdigit()):
        return "hk"
    if c.startswith(("6", "5")):
        return "sh"
    if c.startswith(("0", "3", "1")):
        return "sz"
    return "bj"


def to_symbol(code):
    c = str(code).strip().lower()
    return c if c.startswith(("sh", "sz", "bj", "hk")) else market_prefix(c) + c


def quote_name(code):
    """腾讯报价取名称（新浪列表覆盖不到 ETF / 榜外代码时兜底）。取不到返回空串。"""
    try:
        txt = _get("http://qt.gtimg.cn/q=%s" % to_symbol(code), HDR_TX, "gbk", 10)
        p = txt.split("~")
        return p[1] if len(p) > 1 and p[1] else ""
    except Exception:                                           # noqa: BLE001
        return ""
