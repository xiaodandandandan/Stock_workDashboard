# -*- coding: utf-8 -*-
"""Bundled text-report adapter. Sources and modifications: NOTICE.md.

Only the report helpers are retained; no skill directories or network imports.
"""
import chanlun_core as K

def fmt_dt(s):
    """日期/时间戳展示：8 位 -> 2025-11-26；12 位 -> 2025-11-26 10:30（分钟线）。
    ★ 分钟线时间戳是 12 位，直接用会显示成 202609181500，头部「数据 X → Y」没法看。"""
    s = str(s)
    if len(s) == 12:
        return "%s-%s-%s %s:%s" % (s[:4], s[4:6], s[6:8], s[8:10], s[10:12])
    if len(s) == 8:
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    return s


def fmt(d):
    return fmt_dt(d)


def report(bars, unit="日线"):
    if not bars:
        raise ValueError("报告至少需要一根 K 线")
    st = K.structure(bars)
    last = len(bars) - 1
    cur = bars[last]["close"]
    D = {i: b["date"] for i, b in enumerate(bars)}
    L = []
    L.append("[%s] 最新 %s 收 %.2f" % (unit, fmt(D[last]), cur))
    L.append("共 %d 根，%s → %s" % (len(bars), fmt(D[0]), fmt(D[last])))

    L.append("\n=== 笔序列（近 8 个端点）===")
    for i, t, p in st["bi"][-8:]:
        L.append("  %s  %s %.2f" % (fmt(D[i]), "顶" if t == 1 else "底", p))
    L.append("\n  最近 6 段幅度：")
    for k in range(max(1, len(st["bi"]) - 6), len(st["bi"])):
        p0, p1 = st["bi"][k - 1], st["bi"][k]
        L.append("    %s %.2f -> %s %.2f  %+.1f%%  (%d 根)"
                 % (fmt(D[p0[0]]), p0[2], fmt(D[p1[0]]), p1[2],
                    (p1[2] / p0[2] - 1) * 100, p1[0] - p0[0]))

    L.append("\n=== 中枢（共 %d 个，列后 5 个）===" % len(st["zs"]))
    for i0, i1, zd, zg in st["zs"][-5:]:
        L.append("  %s ~ %s  [%.2f, %.2f]  跨度%d" % (fmt(D[i0]), fmt(D[i1]), zd, zg, i1 - i0))
    ZD = ZG = None
    if st["zs"]:
        z = st["zs"][-1]
        ZD, ZG = z[2], z[3]
        pos = "ZG 上方" if cur > ZG else ("中枢内" if cur > ZD else "已破 ZD")
        L.append("\n  最新中枢 ZD %.2f / ZG %.2f ；现价 %.2f 位于 %s" % (ZD, ZG, cur, pos))
        L.append("  距 ZG %+.2f%% ，距 ZD %+.2f%%" % ((cur / ZG - 1) * 100, (cur / ZD - 1) * 100))
    else:
        L.append("  尚无已识别中枢")

    dif, dea, bar = K.macd([b["close"] for b in bars])
    L.append("\n=== MACD ===")
    if dif:
        L.append("  DIF %.3f  DEA %.3f  柱 %.3f" % (dif[-1], dea[-1], bar[-1]))
    else:
        L.append("  K 线数量不足，暂无 MACD")
    cross = None
    for i in range(last, max(0, last - 40), -1) if dif else ():
        if (dif[i] - dea[i]) * (dif[i - 1] - dea[i - 1]) < 0:
            cross = (i, "金叉" if dif[i] > dea[i] else "死叉")
            break
    L.append("  最近交叉: %s" % ("%s %s" % (cross[1], fmt(D[cross[0]])) if cross else "40 根内无"))
    if st["ar"]:
        L.append("  背驰对比段: %s" % [[fmt(D[a]), fmt(D[b])] for a, b in st["ar"]])

    L.append("\n=== 均线（现价 %.2f）===" % cur)
    for n in (5, 10, 20, 60):
        if len(bars) >= n:
            v = sum(b["close"] for b in bars[-n:]) / n
            L.append("  MA%-3d %.2f  (现价%s %.1f%%)" % (n, v, "高于" if cur > v else "低于", (cur / v - 1) * 100))

    seg = bars[-60:]
    hi_i = max(range(len(seg)), key=lambda i: seg[i]["high"])
    lo_i = min(range(len(seg)), key=lambda i: seg[i]["low"])
    L.append("\n=== 近 60 根区间 ===")
    L.append("  高 %.2f (%s)  低 %.2f (%s)"
             % (seg[hi_i]["high"], fmt(seg[hi_i]["date"]), seg[lo_i]["low"], fmt(seg[lo_i]["date"])))
    L.append("  最新一根 开%.2f 高%.2f 低%.2f 收%.2f 量%d"
             % (bars[last]["open"], bars[last]["high"], bars[last]["low"], cur, bars[last]["volume"]))
    v5 = sum(b["volume"] for b in bars[-5:]) / 5
    v5p = sum(b["volume"] for b in bars[-10:-5]) / 5 if len(bars) >= 10 else v5
    L.append("  近5根均量 %d / 前5根均量 %d  (%.0f%%)" % (v5, v5p, v5 / v5p * 100 if v5p else 0))

    hits = K.detect(bars)
    cand = []
    K.detect(bars, on_candidate=cand.append)
    L.append("\n=== 买点 ===")
    if hits:
        for h in hits:
            L.append("  成立 %s %s  %s" % (h["type"], fmt(h["date"]), h.get("note", "")))
            for w in (h.get("why") or []):
                L.append("      %s %-26s %-30s 门槛 %s"
                         % ("√" if w["ok"] else "×", w["k"], w["v"], w["need"]))
    else:
        L.append("  成立: 无")
    L.append("  二类候选: %s" % (cand or "无"))

    pts = K.find_bi(K.find_fenxing(K.merge_bars([(i, b["high"], b["low"]) for i, b in enumerate(bars)])))
    if pts:
        L.append("\n  最后一笔端点: %s %s %.2f （%s）"
             % (fmt(D[pts[-1]["i"]]), "顶" if pts[-1]["t"] == 1 else "底", pts[-1]["p"],
                "向上笔进行中" if pts[-1]["t"] == -1 else "向下笔进行中"))
    else:
        L.append("\n  尚无已识别笔端点")
    return "\n".join(L), st, ZG, ZD, hits

