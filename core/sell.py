# -*- coding: utf-8 -*-
"""缠论卖点 —— chanlun_core.detect()（只做买点）的严格镜像。

引擎 `chanlun_core` 只有买点：一类（底背驰）/二类（反弹不破前低）/三类（突破中枢上沿）。
本模块把它逐条镜像成卖点，口径一一对应，不做任何额外放宽：

    买点（引擎原文）                      卖点（本模块镜像）
    ─────────────────────────────────────────────────────────────
    一类 = 下跌段底背驰                    一类 = 上涨段顶背驰
       · 本段底 < 前段底（创新低）            · 本段顶 > 前段顶（创新高）
       · 绿柱面积 |a1| < |a2|*0.85            · 红柱面积 |a1| < |a2|*0.85
       · a2 < 0（前段是下跌）                 · a2 > 0（前段是上涨）
       · 时效 <= RECENT_BARS                  · 同
    二类 = 一类后反弹、回踩不破前低         二类 = 一类后下跌、反弹不破前高
    三类 = 向上突破中枢上沿 ZG              三类 = 向下跌破中枢下沿 ZD
       · 回抽低点 > ZG                        · 反抽高点 < ZD
       · 回撤 >= MIN_PULLBACK                 · 同（幅度）
       · 距 ZG <= MAX_DIST_ZG                 · 距 ZD <= MAX_DIST_ZG

阈值常量全部从 chanlun_core 复用，不另设，保证买卖对称。
"""
from . import require_engine

K = require_engine()             # 缺失时抛带「该放哪个目录」指引的 ImportError
from chanlun_core import (merge_bars, find_fenxing, find_bi, find_zhongshu,
                          macd, area, _why, _ds,
                          RECENT_BARS, MIN_REBOUND, MIN_PULLBACK, MAX_DIST_ZG)

SELL_TYPES = ("一类卖点", "二类卖点", "三类卖点", "中枢跌破")


def _trend_note_up(zs_list, before_i):
    """一类卖点的『上涨趋势』参考核验 —— `_trend_note` 的镜像。

    下跌趋势回溯链要求越早的中枢**越高**（ZD > 上一层 ZG）；
    上涨趋势镜像：越早的中枢**越低**（ZG < 上一层 ZD）。
    """
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
    """四类卖点。返回与 detect() 同构的 [{'type','date','ZG','ZD','stop','close','note','why'}]。

    `stop` 语义镜像：买点是「跌破即失效的结构位」（取低点），
    卖点是「涨破即失效的结构位」（取高点）。
    """
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    last_i = len(bars) - 1

    mb = merge_bars([(i, highs[i], lows[i]) for i in range(len(bars))])
    fx = find_fenxing(mb)
    pts = find_bi(fx)
    res = []

    # ---- 跌破中枢下沿 / 三类卖点（镜像 detect 的第一段）
    if len(pts) >= 4:
        zs_list = find_zhongshu(pts)
        if zs_list:
            z = zs_list[-1]
            i1 = z["i1"]
            if i1 < last_i:
                cur = closes[last_i]
                brk = None
                for j in range(i1, last_i + 1):
                    if closes[j] < z["ZD"]:
                        brk = j
                        break
                if brk is not None and (last_i - brk) <= 20 and cur < z["ZD"]:
                    seg_h = highs[brk:last_i + 1]
                    seg_l = lows[brk:last_i + 1]
                    brk_lo = min(seg_l)
                    lo_pos = brk + seg_l.index(brk_lo)
                    rb_hi = max(seg_h)
                    hi_pos = brk + seg_h.index(rb_hi)
                    rb = (rb_hi - brk_lo) / brk_lo if brk_lo > 0 else 0.0
                    dist = (z["ZD"] - cur) / z["ZD"] if z["ZD"] > 0 else 1.0
                    if (rb_hi < z["ZD"] and last_i - brk >= 2
                            and rb >= MIN_PULLBACK
                            and dist <= MAX_DIST_ZG):
                        res.append({"type": "三类卖点", "date": bars[hi_pos]["date"],
                                    "ZG": z["ZG"], "ZD": z["ZD"], "stop": z["ZD"],
                                    "close": cur,
                                    "note": f"反抽{rb * 100:.0f}%不破ZD 距ZD{dist * 100:.0f}%"
                                            f"{'（反抽后已新低）' if hi_pos < lo_pos else ''}",
                                    "why": [
                                        _why("① 向下突破中枢",
                                             "%s 收盘 %.2f 跌破 ZD"
                                             % (_ds(bars[brk]["date"]), closes[brk]),
                                             "收盘 < ZD %.2f" % z["ZD"], 1),
                                        _why("② 反抽不回到中枢内",
                                             "反抽高点 %.2f" % rb_hi,
                                             "< ZD %.2f" % z["ZD"], rb_hi < z["ZD"]),
                                        _why("③ 反抽幅度（防假跌破）",
                                             "%.1f%%" % (rb * 100),
                                             "≥ %.0f%%" % (MIN_PULLBACK * 100),
                                             rb >= MIN_PULLBACK),
                                        _why("④ 离 ZD 不能太远（避免杀跌追空）",
                                             "%.1f%%" % (dist * 100),
                                             "≤ %.0f%%" % (MAX_DIST_ZG * 100),
                                             dist <= MAX_DIST_ZG),
                                    ]})
                    elif last_i - brk <= 5:
                        res.append({"type": "中枢跌破", "date": bars[brk]["date"],
                                    "ZG": z["ZG"], "ZD": z["ZD"], "stop": z["ZD"],
                                    "close": cur, "note": "跌破下沿待验证"})

    # ---- 一类 / 二类卖点：上涨笔的 MACD 红柱面积背驰
    _, _, bar = macd(closes)
    if bar and len(pts) >= 4:
        zs_all = find_zhongshu(pts)
        ups = []
        for k in range(len(pts) - 1):
            if pts[k]["t"] == -1 and pts[k + 1]["t"] == 1:
                ups.append((pts[k], pts[k + 1]))
        if len(ups) >= 2:
            (b2, t2), (b1, t1) = ups[-2], ups[-1]
            i_b2, i_t2 = b2["i"], t2["i"]
            i_b1, i_t1 = b1["i"], t1["i"]
            a2 = area(bar, i_b2, i_t2, 1)
            a1 = area(bar, i_b1, i_t1, 1)
            high1 = highs[i_t1]
            new_high = t1["p"] > t2["p"]
            if new_high and abs(a1) < abs(a2) * 0.85 and a2 > 0 and (last_i - i_t1) <= RECENT_BARS:
                res.append({"type": "一类卖点", "date": bars[i_t1]["date"],
                            "ZG": None, "ZD": None, "stop": high1,
                            "close": closes[last_i],
                            "note": f"顶背驰 面积 {abs(a1):.2f} < {abs(a2):.2f}",
                            "why": [
                                _why("① 创新高（上涨段的高点要更高）",
                                     f"本段顶 %.2f > 前段顶 %.2f" % (t1["p"], t2["p"]),
                                     "本段顶 > 前段顶", new_high),
                                _why("② 力度衰竭（MACD 红柱面积收缩）",
                                     "%.2f / %.2f = %.1f%%"
                                     % (abs(a1), abs(a2), abs(a1) / abs(a2) * 100),
                                     "< 85%", abs(a1) < abs(a2) * 0.85),
                                _why("③ 前段必须是上涨段",
                                     "A2 面积 %.2f" % a2, "A2 > 0", a2 > 0),
                                _why("④ 时效（信号不能太老）",
                                     f"距今 %d 根" % (last_i - i_t1),
                                     "≤ %d 根" % RECENT_BARS,
                                     (last_i - i_t1) <= RECENT_BARS),
                                _trend_note_up(zs_all, i_t1),
                            ]})
                if last_i - i_t1 >= 3:
                    seg_l = min(lows[i_t1:last_i + 1])
                    drop = (high1 - seg_l) / high1 if high1 > 0 else 0
                    if drop >= MIN_REBOUND:
                        tail = highs[i_t1 + 1:last_i + 1]
                        j_rel = tail.index(max(tail))
                        j = i_t1 + 1 + j_rel
                        trough_rel = lows[i_t1:last_i + 1].index(seg_l)
                        if trough_rel > 0 and j > i_t1 + trough_rel and high1 > 0:
                            pull_hi = tail[j_rel]
                            if pull_hi < high1 and j >= i_t1 + 2:
                                res.append({"type": "二类卖点", "date": bars[j]["date"],
                                            "ZG": None, "ZD": None, "stop": high1,
                                            "close": closes[last_i],
                                            "note": f"下跌{drop * 100:.0f}%后反弹{pull_hi:.2f}不破一类高点",
                                            "why": [
                                                _why("① 前提：一类卖点已成立",
                                                     "顶 %.2f（%s）"
                                                     % (high1, _ds(bars[i_t1]["date"])),
                                                     "必须先有一类卖点", 1),
                                                _why("② 下跌幅度够（确认转折）",
                                                     "-%.1f%%（%.2f → %.2f）"
                                                     % (drop * 100, high1, seg_l),
                                                     "≥ %.1f%%" % (MIN_REBOUND * 100),
                                                     drop >= MIN_REBOUND),
                                                _why("③ 反弹不破一类高点",
                                                     "反弹高点 %.2f" % pull_hi,
                                                     "< 一类高点 %.2f" % high1,
                                                     pull_hi < high1),
                                                _why("④ 反弹发生在下跌低点之后",
                                                     "低点后第 %d 根" % (j - (i_t1 + trough_rel)),
                                                     "必须在下跌低点之后",
                                                     j > i_t1 + trough_rel),
                                            ]})
                        else:
                            if on_candidate is not None and closes[last_i] < high1:
                                on_candidate({"type": "二类候选",
                                              "date": bars[i_t1]["date"],
                                              "close": closes[last_i],
                                              "ZG": None, "ZD": None, "stop": high1,
                                              "note": f"下跌{drop * 100:.0f}%后未反弹，涨破{high1:.2f}即失效"})
    return res


def scan_signals_sell(bars, min_bars=120, step=1, types=None):
    """`chanlun_core.scan_signals()` 的卖点镜像 —— 逐时点切片复算 detect_sell()。

    合并规则与原版逐条一致：同一 type 且报出时点连续（cut 间隔 ≤ step）的，视为
    同一个信号段（= 该信号的存活期）。每段记录 first_i / last_i / alive / sig_date。

    ★ 买点侧直接调引擎的 `scan_signals`，卖点侧必须自己镜像一份 —— 少了它，
      「存活 N 根」这个数在卖点卡片上就只能空着，或者退回用 fresh 顶替。
      fresh 是「距今几根」，对窗口外的老信号会饱和成 lookback，拿它当存活根数是错的。

    ★ 成本：800 根日 K 约 0.44 s（和买点侧同量级）。这是**抽屉专用**的重活，
      全市场扫描绝不能调 —— 5572 只 × 0.9 s 就是 80 分钟。
    """
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
            prev = last.get(t)
            if prev is not None and cut - prev[0] <= step:
                g = segs[prev[1]]
                g["last_i"] = cut - 1
                g["alive"] = cut - 1 - g["first_i"]
                g["sig_date"] = h["date"]
                g["stop"] = h.get("stop")
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
            last[t] = (cut, idx)
    segs.sort(key=lambda g: g["first_i"])
    return segs
