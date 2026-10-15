# -*- coding: utf-8 -*-
"""按上涨笔、中枢下沿识别卖点；买点仍由原引擎负责。

一卖：相邻两段上涨笔创新高，后一笔 MACD 正柱面积严格缩小。
二卖：一卖后的下跌笔、反弹笔，反弹高点不超过一卖高点。
三卖：跌破已有中枢 ZD 后，反弹上涨笔的高点严格低于 ZD。

沿用引擎的分型、笔及中枢识别。末端笔可能随行情延伸；历史回放仅使用
当时已经出现的 K 线，first_i 记录首次识别时间，不回填到结构顶点当天。
"""
from . import require_engine

K = require_engine()
from chanlun_core import (merge_bars, find_fenxing, find_bi, find_zhongshu,
                          macd, area, _why, _ds, RECENT_BARS)

SELL_RULES_VERSION = "2026-10-16-v2"
SELL_TYPES = ("一类卖点", "二类卖点", "三类卖点", "中枢跌破")


def _trend_note_up(zs_list, before_i):
    """上涨趋势仅作为参考，不额外限制用户定义的一卖。"""
    zs = [z for z in zs_list if z["i1"] <= before_i]
    chain = []
    for z in reversed(zs):
        if not chain or z["ZG"] < chain[-1]["ZD"]:
            chain.append(z)
    chain.reverse()
    if len(chain) >= 2:
        seg = " → ".join("[%.2f,%.2f]" % (z["ZD"], z["ZG"]) for z in chain[-3:])
        return _why("趋势要件（严格定义·参考）",
                    "%d 个中枢依次上移不重叠：%s" % (len(chain), seg),
                    "≥2 个依次上移", 1)
    return _why("趋势要件（严格定义·参考）",
                "只连出 %d 个中枢（不足趋势）" % len(chain), "≥2 个依次上移", 0)


def detect_sell(bars, on_candidate=None):
    """返回与买点 detect 同构的信号。stop 为涨破即失效的结构位。

    一、二卖允许现价等于前高；三卖触及 ZD 即不成立。二卖的一卖锚点
    独立保存于结构回溯中，不会因出现新反弹笔而丢失，也不会突破后复活。
    """
    if len(bars) < 2:
        return []
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    last_i = len(bars) - 1
    cur = closes[-1]
    pts = find_bi(find_fenxing(merge_bars(
        [(i, highs[i], lows[i]) for i in range(len(bars))])))
    zs_all = find_zhongshu(pts) if len(pts) >= 4 else []
    res = []

    # 后缀最高价用于检查结构位是否曾被突破，避免已失效信号再次复活。
    suffix_high = [float("-inf")] * (len(bars) + 1)
    for i in range(last_i, -1, -1):
        suffix_high[i] = max(highs[i], suffix_high[i + 1])

    # ---- 三卖：已有中枢 -> 下跌笔 -> 反弹上涨笔。
    # 只取出发顶之前已存在的最近中枢，不能使用跌破/反弹本身形成的新中枢。
    third = []
    for k in range(len(pts) - 2):
        top, bottom, rebound = pts[k:k + 3]
        if (top["t"], bottom["t"], rebound["t"]) != (1, -1, 1):
            continue
        ti, bi, ri = top["i"], bottom["i"], rebound["i"]
        if not (0 <= ti < bi < ri <= last_i):
            continue
        centers = [z for z in zs_all if z["i1"] <= ti]
        if not centers:
            continue
        z = max(centers, key=lambda item: item["i1"])
        zd = z["ZD"]
        if not (top["p"] >= zd > bottom["p"] and rebound["p"] < zd):
            continue
        # 破位必须发生在下跌笔中；不把破位 K 线/下跌笔的高点算作反弹。
        brk = next((i for i in range(max(ti, z["i1"] + 1), bi + 1)
                    if closes[i] < zd), None)
        if brk is None or last_i - ri > RECENT_BARS:
            continue
        # 底部 K 线的高点可能属于下跌过程；真正反弹从底点之后开始。
        # 从反弹起若触及/收回 ZD，旧三卖失效，之后下跌不能让它复活。
        if suffix_high[bi + 1] >= zd or cur >= zd:
            continue
        third.append({"type": "三类卖点", "date": bars[ri]["date"],
                      "anchor_date": bars[brk]["date"],
                      "ZG": z["ZG"], "ZD": zd, "stop": zd, "close": cur,
                      "note": "跌破 ZD 后反弹高点 %.2f < ZD %.2f" % (rebound["p"], zd),
                      "why": [
                          _why("① 跌破前已有中枢",
                               "%s 已形成 [%.2f, %.2f]" %
                               (_ds(bars[z["i1"]]["date"]), zd, z["ZG"]),
                               "中枢形成不晚于下跌笔出发顶", 1),
                          _why("② 下跌笔向下跌破中枢",
                               "%s 收盘 %.2f；下跌笔 %.2f → %.2f" %
                               (_ds(bars[brk]["date"]), closes[brk], top["p"], bottom["p"]),
                               "收盘 < ZD %.2f" % zd, 1),
                          _why("③ 反弹上涨笔不能回到中枢",
                               "%s 低点 %.2f → %s 反弹高点 %.2f" %
                               (_ds(bars[bi]["date"]), bottom["p"],
                                _ds(bars[ri]["date"]), rebound["p"]),
                               "反弹高点 < ZD %.2f（触及也不成立）" % zd, 1),
                          _why("④ 时效", "距反弹顶 %d 根" % (last_i - ri),
                               "≤ %d 根" % RECENT_BARS, 1),
                      ]})
    if third:
        res.append(max(third, key=lambda s: str(s["date"])))
    elif zs_all:
        # 保留独立的“中枢跌破”观察状态，不能把尚无上涨反弹笔的下跌算作三卖。
        z = max(zs_all, key=lambda item: item["i1"])
        if z["i1"] < last_i and cur < z["ZD"]:
            brk = last_i
            while brk > z["i1"] + 1 and closes[brk - 1] < z["ZD"]:
                brk -= 1
            if last_i - brk <= 5:
                res.append({"type": "中枢跌破", "date": bars[brk]["date"],
                            "ZG": z["ZG"], "ZD": z["ZD"], "stop": z["ZD"],
                            "close": cur, "note": "跌破下沿，等待上涨笔反弹验证"})

    # ---- 一卖：仅比较相邻两段上涨笔各自的 MACD 正柱面积。
    _, _, histogram = macd(closes)
    ups = []
    if histogram:
        for k in range(len(pts) - 1):
            bottom, top = pts[k:k + 2]
            if (bottom["t"], top["t"]) == (-1, 1):
                ups.append((k, bottom, top, area(histogram, bottom["i"], top["i"], 1)))

    first, second = [], []
    for previous, current in zip(ups, ups[1:]):
        _, old_bottom, old_top, old_area = previous
        k, bottom, top, current_area = current
        ti, high = top["i"], top["p"]
        # 不取绝对值：比较的是正柱面积；前一笔无正柱时没有可比上涨力度。
        if not (top["p"] > old_top["p"] and 0 <= current_area < old_area and old_area > 0):
            continue
        if suffix_high[ti + 1] > high:
            continue
        comparison = [
            _why("① 相邻两段上涨笔创新高",
                 "前笔 %s → %s（顶 %.2f）；本笔 %s → %s（顶 %.2f）" %
                 (_ds(bars[old_bottom["i"]]["date"]), _ds(bars[old_top["i"]]["date"]),
                  old_top["p"], _ds(bars[bottom["i"]]["date"]),
                  _ds(bars[ti]["date"]), high), "本笔顶 > 前笔顶", 1),
            _why("② 上涨笔 MACD 正柱面积缩小",
                 "本笔 %.6g < 前笔 %.6g（比值 %.6g%%）" %
                 (current_area, old_area, current_area / old_area * 100),
                 "本笔正柱面积 < 前笔正柱面积，前笔面积 > 0", 1),
        ]
        if last_i - ti <= RECENT_BARS:
            first.append({"type": "一类卖点", "date": bars[ti]["date"],
                          "ZG": None, "ZD": None, "stop": high, "close": cur,
                          "note": "上涨笔顶背驰：正柱面积 %.6g < %.6g" % (current_area, old_area),
                          "why": comparison + [
                              _why("③ 时效", "距上涨笔顶 %d 根" % (last_i - ti),
                                   "≤ %d 根" % RECENT_BARS, 1),
                              _trend_note_up(zs_all, ti),
                          ]})

        # ---- 二卖：保留上述一卖锚点，紧接它的下跌笔、上涨反弹笔。
        # 之后更低的反弹不会改变这次二卖的日期；父锚点可早于信号时效窗口。
        following = pts[k + 2:k + 4]
        if len(following) == 2:
            low, rebound = following
            li, ri = low["i"], rebound["i"]
            if ((low["t"], rebound["t"]) == (-1, 1)
                    and ti < li < ri and low["p"] < high
                    and low["p"] < rebound["p"] <= high
                    and last_i - ri <= RECENT_BARS):
                second.append({"type": "二类卖点", "date": bars[ri]["date"],
                               "anchor_date": bars[ti]["date"],
                               "ZG": None, "ZD": None, "stop": high, "close": cur,
                               "note": "一卖后反弹 %.2f ≤ 一卖高点 %.2f" % (rebound["p"], high),
                               "why": [
                                   _why("① 前提：一类卖点已成立",
                                        "%s 顶 %.2f；上涨笔正柱面积 %.6g < %.6g" %
                                        (_ds(bars[ti]["date"]), high, current_area, old_area),
                                        "先有创新高且力度缩小的一卖", 1),
                                   _why("② 一卖后先有下跌笔",
                                        "%s 顶 %.2f → %s 底 %.2f" %
                                        (_ds(bars[ti]["date"]), high,
                                         _ds(bars[li]["date"]), low["p"]),
                                        "一卖顶之后出现下跌笔", 1),
                                   _why("③ 之后反弹不超过前高",
                                        "%s 反弹高点 %.2f" %
                                        (_ds(bars[ri]["date"]), rebound["p"]),
                                        "≤ 一卖高点 %.2f（允许等高）" % high, 1),
                                   _why("④ 时效", "距反弹顶 %d 根" % (last_i - ri),
                                        "≤ %d 根" % RECENT_BARS, 1),
                               ]})
        elif on_candidate is not None and k + 2 < len(pts):
            low = pts[k + 2]
            if (low["t"] == -1 and low["p"] < high
                    and last_i - low["i"] <= RECENT_BARS and cur <= high):
                on_candidate({"type": "二类候选", "date": bars[ti]["date"],
                              "close": cur, "ZG": None, "ZD": None, "stop": high,
                              "note": "一卖后已有下跌笔，等待反弹；涨破 %.2f 即失效" % high})
    if first:
        res.append(max(first, key=lambda s: str(s["date"])))
    if second:
        res.append(max(second, key=lambda s: str(s["date"])))
    return res


def scan_signals_sell(bars, min_bars=120, step=1, types=None):
    """按当时可知的 K 线逐时点复算；不同结构锚点分开记录存活期。

    first_i / last_i 是报出时点，sig_date 是结构顶点日期。仅供个股历史
    分析调用；全市场扫描不做逐根完整回放。
    """
    if step < 1:
        raise ValueError("step must be positive")
    if len(bars) < 40:
        return []
    min_bars = max(20, min(min_bars, len(bars) - 1))
    last = {}
    segs = []
    for cut in range(min_bars, len(bars) + 1, step):
        for h in detect_sell(bars[:cut]):
            t = h["type"]
            if types is not None and t not in types:
                continue
            identity = (t, str(h["date"]), str(h.get("anchor_date", "")),
                        h.get("stop"), h.get("ZG"), h.get("ZD"))
            prev = last.get(identity)
            if prev is not None and cut - prev[0] <= step:
                g = segs[prev[1]]
                g["last_i"] = cut - 1
                g["alive"] = cut - 1 - g["first_i"]
                g["note"] = h.get("note", "")
                g["why"] = h.get("why")
                idx = prev[1]
            else:
                g = {"type": t, "first_i": cut - 1, "last_i": cut - 1, "alive": 0,
                     "sig_date": h["date"], "stop": h.get("stop"),
                     "ZG": h.get("ZG"), "ZD": h.get("ZD"),
                     "note": h.get("note", ""), "why": h.get("why")}
                segs.append(g)
                idx = len(segs) - 1
            last[identity] = (cut, idx)
    segs.sort(key=lambda g: g["first_i"])
    return segs
