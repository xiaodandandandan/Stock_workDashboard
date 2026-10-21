# -*- coding: utf-8 -*-
"""
缠论结构内核 —— 自包含，只依赖标准库。

抽取自 chanlun_scan.py（实测与原件输出逐字节一致）。
输入 bars 为 [{'date','open','high','low','close','amount','volume'}, ...]，
按日期升序。

链路：包含处理 → 分型 → 笔 → 中枢 → 背驰 → 四类买点

★ 贯穿全模块的一条铁律：**合并K线必须携带原始K线索引**。
  分型/笔返回两套坐标：
    mi  合并后序号 —— 用于笔的间距判定（笔的定义建立在合并K线上）
    i   原始K线索引 —— 用于定位画图
  早期版本只存 h/l，画图时用 `mi * len(bars)/len(mb)` 按比例反推位置，
  结果二类买点从 18 只漏到 6 只。别再回退到那种近似。
"""
import struct

# ---------------------------------------------------------------- 参数
LOOKBACK = 250          # 回看交易日数
MIN_GAP = 4             # 笔的最小合并K线间距（标准笔定义）
RECENT_BARS = 60        # 买点必须在此窗口内形成
MIN_REBOUND = 0.03      # 二类：一类低点后的反弹幅度下限（"确认"要件）
MIN_PULLBACK = 0.05     # 三类：突破后的真实回撤幅度下限
MAX_DIST_ZG = 0.30      # 三类：现价距 ZG 的最大距离


# ---------------------------------------------------------------- 数据读取
def parse_day(path, limit=LOOKBACK):
    """通达信 .day 文件：32 字节/条，小端
        date(I) open(I) high(I) low(I) close(I) amount(f) volume(I) reserved(I)
       价格字段存的是「分」，要 /100。

       ★ 通达信「收盘自动下载」会把同一交易日**重复追加**到文件末尾
       （2026-09-15 实测：9/15 15:00 后被写过的 9045 个文件里 5207 个中招）。
       不去重 -> 同一天被当成两根 K 线 -> 包含处理/笔/中枢全错，且**不报任何错**。
       同日期保留成交量最小的记录：通达信「收盘自动下载」会把 9/15、9/16 等交易日
       重复追加到文件末尾，且追加份的**成交量被 ×100**（价格一致）。「保留最后一条」会
       恰好留下损坏份；改取同日期成交量最小者，既能剔除 ×100 损坏份，又兼容正常日内双快照。
    """
    out = []
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return out
    n = len(data) // 32
    buckets = {}  # date -> (volume, rec_bytes)  同日期保留成交量最小记录
    for i in range(n):
        rec = data[i * 32:(i + 1) * 32]
        d = struct.unpack("<I", rec[:4])[0]
        v = struct.unpack("<I", rec[24:28])[0]
        cur = buckets.get(d)
        if cur is None or v < cur[0]:
            buckets[d] = (v, rec)
    for d in sorted(buckets.keys())[-limit:]:
        v, rec = buckets[d]
        o, h, l, c = struct.unpack("<IIII", rec[4:20])
        amt = struct.unpack("<f", rec[20:24])[0]
        out.append({"date": d, "open": o / 100.0, "high": h / 100.0,
                    "low": l / 100.0, "close": c / 100.0,
                    "amount": amt, "volume": v})
    return out


# ---------------------------------------------------------------- MACD
def ema(vals, n):
    k = 2.0 / (n + 1)
    out, prev = [], None
    for v in vals:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd(closes):
    if len(closes) < 35:
        return None, None, None
    e12, e26 = ema(closes, 12), ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    bar = [(a - b) * 2 for a, b in zip(dif, dea)]
    return dif, dea, bar


def area(bar, i0, i1, sign):
    """i0..i1 之间**同号** MACD 柱面积之和。

    ★ sign 只取符号。下跌动能面积必须是「绿柱之和」——
      把红柱也算进去的话面积对比就是错的。
    """
    return sum(b for b in bar[i0:i1 + 1] if (b < 0 if sign < 0 else b > 0))


# ---------------------------------------------------------------- 包含 → 分型 → 笔 → 中枢
def merge_bars(bars):
    """包含处理。输入 [(idx, high, low)...]，
       输出 [{'h','l','i0','i1','ih','il'}, ...]

       i0..i1  该合并K线覆盖的原始索引区间（闭区间）
       ih/il   区间内最高价 / 最低价出现的**原始索引**
    """
    res = []
    for idx, h, l in bars:
        hit = res and ((h >= res[-1]["h"] and l <= res[-1]["l"])
                       or (h <= res[-1]["h"] and l >= res[-1]["l"]))
        if hit:
            d = res[-1]
            # 方向由前两根合并K线的高低关系决定（不是当前这根的涨跌）
            up = h > d["h"] if len(res) < 2 else d["h"] > res[-2]["h"]
            if up:
                if h >= d["h"]:
                    d["h"] = h; d["ih"] = idx
                if l >= d["l"]:
                    d["l"] = l; d["il"] = idx
            else:
                if h <= d["h"]:
                    d["h"] = h; d["ih"] = idx
                if l <= d["l"]:
                    d["l"] = l; d["il"] = idx
            d["i1"] = idx
        else:
            res.append({"h": h, "l": l, "i0": idx, "i1": idx, "ih": idx, "il": idx})
    return res


def find_fenxing(mb):
    """分型：中间合并K线的高低点严格高于/低于左右相邻。
       返回 [{'mi','i','t','p'}]，t=1 顶分型 / -1 底分型。
    """
    fx = []
    for k in range(1, len(mb) - 1):
        c, p, n = mb[k], mb[k - 1], mb[k + 1]
        if c["h"] > p["h"] and c["h"] > n["h"]:
            fx.append({"mi": k, "i": c["ih"], "t": 1, "p": c["h"]})
        elif c["l"] < p["l"] and c["l"] < n["l"]:
            fx.append({"mi": k, "i": c["il"], "t": -1, "p": c["l"]})
    return fx


def find_bi(fx, min_gap=MIN_GAP):
    """笔：相邻异型分型，合并间距 >= min_gap，且顶必须高于底。
       间距判定用 mi；返回的 i 是原始索引，直接拿来画图。
    """
    if len(fx) < 2:
        return []
    pts, last = [], None
    for f in fx:
        if last is None:
            last = f; continue
        if f["t"] == last["t"]:                       # 同型：取更极端的一个
            if (f["t"] == 1 and f["p"] > last["p"]) or (f["t"] == -1 and f["p"] < last["p"]):
                last = f
            continue
        if f["mi"] - last["mi"] < min_gap:
            continue
        ok = (last["t"] == -1 and f["p"] > last["p"]) or (last["t"] == 1 and f["p"] < last["p"])
        if not ok:
            continue
        pts.append(last)
        last = f
    if last is not None:
        pts.append(last)
    return pts


def find_zhongshu(pts):
    """中枢：连续三笔的重叠区间，ZD = max(各笔低点)、ZG = min(各笔高点)。"""
    zs = []
    for k in range(len(pts) - 3):
        segs = [(pts[k]["p"], pts[k + 1]["p"]),
                (pts[k + 1]["p"], pts[k + 2]["p"]),
                (pts[k + 2]["p"], pts[k + 3]["p"])]
        ZD = max(min(a, b) for a, b in segs)
        ZG = min(max(a, b) for a, b in segs)
        if ZD < ZG:
            zs.append({"ZD": ZD, "ZG": ZG, "i0": pts[k]["i"], "i1": pts[k + 3]["i"], "k": k})
    return zs


# ---------------------------------------------------------------- 买点判定
def _ds(d):
    """日期/时间戳展示：8 位 -> 2026-08-25；12 位 -> 2026-08-25 10:00。

    ★ 判据明细里会带上原始 date，直接打出来是 202608251000 —— 用户读不了。
      这里不能复用 interactive_html.fmt_dt（本模块要求零依赖、独立可用）。
    """
    s = str(d)
    if len(s) == 12:
        return "%s-%s-%s %s:%s" % (s[:4], s[4:6], s[6:8], s[8:10], s[10:12])
    if len(s) == 8:
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    return s


def _why(k, v, need, ok):
    """买点判据的一条：k = 要件 / v = 实测 / need = 门槛 / ok = 是否通过。

    ★ 为什么要把它带在命中里：图上的「◆ 买点」只是一个结论，用户看不到
      它凭什么成立。把每条判据的**实测值 vs 阈值**一并输出，图表底部就能
      逐条核对——这也是复盘时唯一能验证「引擎是不是漏判」的方式。
    """
    return {"k": k, "v": v, "need": need, "ok": 1 if ok else 0}


def _trend_note(zs_list, before_i):
    """一类买点的『趋势』要件（严格缠论定义）参考核验。

    引擎判一类只用「最后两段下跌笔 + 创新低 + 面积收缩」，是个简化：
    严格定义要求那是**下跌趋势**（≥2 个同级别中枢依次下移且不重叠）。
    这里在 `before_i` 之前做一次参考性核验，供人工对照。

    ★ 截断点必须取**本段底**（i_b1），不能取前段底（i_b2）：
      下跌趋势的中枢序列要覆盖到最后一段下跌，而最后那个中枢往往很晚才成形
      （其 i1 就落在低点附近）。用 i_b2 截断会把它们整段排除，实测顺络电子
      30 分钟 7/21 那个一类买点因此被误判成「不足趋势」。
    ★ 取法必须用「贪心回溯成链」，不能直接取最后 3 个中枢：
      实测（顺络电子 30 分钟）find_zhongshu 是「三笔重叠」近似，一段行情会产出
      十几个**互相高度重叠**的伪中枢 —— 直接取最后 3 个必然全是重叠的近乎同一个
      中枢，于是恒判「无趋势」，把真趋势冤枉掉。贪心回溯（要求 ZG < 上一层 ZD，
      即严格下移且不重叠）才还原出真实的中枢下移序列。
    ★ 仍只作参考、不参与判定：伪中枢密度太高，拿它当硬门槛会误杀真信号。
    """
    zs = [z for z in zs_list if z["i1"] <= before_i]
    chain = []
    for z in reversed(zs):
        # ★ 方向：从最新往回找，越早的中枢必须**越高**（ZD > 上一层 ZG）。
        #   写反了会连出一条「逐级上移」的链 —— 那是上涨的中枢序列，
        #   却会被当成下跌趋势的凭证打上 ✓（实测踩过）。
        if not chain or z["ZD"] > chain[-1]["ZG"]:
            chain.append(z)
    chain.reverse()
    if len(chain) >= 2:
        seg = " → ".join("[%.2f,%.2f]" % (z["ZD"], z["ZG"]) for z in chain[-3:])
        return _why("趋势要件（严格定义·参考）",
                    "%d 个中枢依次下移不重叠：%s" % (len(chain), seg),
                    "≥2 个依次下移", 1)
    return _why("趋势要件（严格定义·参考）",
                "只连出 %d 个中枢（不足趋势）" % len(chain), "≥2 个依次下移", 0)


def detect(bars, on_candidate=None):
    """四类买点。返回 [{'type','date','ZG','ZD','stop','close','note','why'}]

      on_candidate(dict) 可选回调：收集「二类候选」（一类后已反弹、但回踩
      还没走出合格结构的票）。候选**不是买点**，必须单独输出，绝不能混进
      四类分表——否则共鸣榜会把"还没成立的结构"也算成已成立。

      why = [{'k','v','need','ok'}] 判据明细，供图表底部逐条展示「凭什么成立」。
    """
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    last_i = len(bars) - 1

    mb = merge_bars([(i, highs[i], lows[i]) for i in range(len(bars))])
    fx = find_fenxing(mb)
    pts = find_bi(fx)
    res = []

    # ---- 中枢突破 / 三类买点
    if len(pts) >= 4:
        zs_list = find_zhongshu(pts)
        if zs_list:
            z = zs_list[-1]
            i1 = z["i1"]
            if i1 < last_i:
                cur = closes[last_i]
                brk = None
                for j in range(i1, last_i + 1):
                    if closes[j] > z["ZG"]:
                        brk = j; break
                if brk is not None and (last_i - brk) <= 20 and cur > z["ZG"]:
                    seg_h = highs[brk:last_i + 1]; seg_l = lows[brk:last_i + 1]
                    brk_hi = max(seg_h); hi_pos = brk + seg_h.index(brk_hi)
                    pull_lo = min(seg_l); lo_pos = brk + seg_l.index(pull_lo)
                    dd = (brk_hi - pull_lo) / brk_hi if brk_hi > 0 else 0.0
                    dist = (cur - z["ZG"]) / z["ZG"] if z["ZG"] > 0 else 1.0
                    # 三类三要件：① 真实回撤>=5% ② 回踩不破 ZG ③ 距 ZG<=30%
                    # ★ 回踩与新高谁先谁后，不影响三买成立：
                    #   先冲高再回落（lo_pos > hi_pos）= 标准回抽；
                    #   先回踩不破 ZG、再创新高（lo_pos < hi_pos）= 三买已走出，更强。
                    #   原先要求 lo_pos > hi_pos，会把「突破 → 回踩 → 再新高」整类漏掉
                    #   （实测漏掉 60 分钟级别的 9/17 三买）。防伪靠 pull_lo > ZG：
                    #   突破后没回踩一路涨时，pull_lo 会落在 ZG 下方，自然被过滤。
                    if (pull_lo > z["ZG"] and last_i - brk >= 2
                            and dd >= MIN_PULLBACK
                            and dist <= MAX_DIST_ZG):
                        res.append({"type": "三类买点", "date": bars[lo_pos]["date"],
                                    "ZG": z["ZG"], "ZD": z["ZD"], "stop": z["ZG"],
                                    "close": cur,
                                    "note": f"回抽{dd * 100:.0f}%不破ZG 距ZG{dist * 100:.0f}%"
                                            f"{'（回踩后已新高）' if lo_pos < hi_pos else ''}",
                                    "why": [
                                        _why("① 向上突破中枢",
                                             "%s 收盘 %.2f 站上 ZG"
                                             % (_ds(bars[brk]["date"]), closes[brk]),
                                             "收盘 > ZG %.2f" % z["ZG"], 1),
                                        _why("② 回抽不回到中枢内",
                                             f"回抽低点 %.2f" % pull_lo,
                                             "> ZG %.2f" % z["ZG"], pull_lo > z["ZG"]),
                                        _why("③ 回撤幅度（防假突破）",
                                             "%.1f%%" % (dd * 100),
                                             "≥ %.0f%%" % (MIN_PULLBACK * 100),
                                             dd >= MIN_PULLBACK),
                                        _why("④ 离 ZG 不能太远（避免追高）",
                                             "%.1f%%" % (dist * 100),
                                             "≤ %.0f%%" % (MAX_DIST_ZG * 100),
                                             dist <= MAX_DIST_ZG),
                                        _why("⑤ 回踩与新高次序",
                                             "回踩后已创新高 %.2f" % brk_hi if lo_pos < hi_pos
                                             else "先冲高 %.2f 再回落" % brk_hi,
                                             "", 1),
                                    ]})
                    elif last_i - brk <= 5:
                        res.append({"type": "中枢突破", "date": bars[brk]["date"],
                                    "ZG": z["ZG"], "ZD": z["ZD"], "stop": z["ZG"],
                                    "close": cur, "note": "放量离开上沿待验证"})

    # ---- 一类 / 二类：下跌笔的 MACD 面积背驰
    _, _, bar = macd(closes)
    if bar and len(pts) >= 4:
        zs_all = find_zhongshu(pts)
        downs = []
        for k in range(len(pts) - 1):
            if pts[k]["t"] == 1 and pts[k + 1]["t"] == -1:
                downs.append((pts[k], pts[k + 1]))
        if len(downs) >= 2:
            (t2, b2), (t1, b1) = downs[-2], downs[-1]
            i_t2, i_b2 = t2["i"], b2["i"]
            i_t1, i_b1 = t1["i"], b1["i"]
            a2 = area(bar, i_t2, i_b2, -1)
            a1 = area(bar, i_t1, i_b1, -1)
            low1 = lows[i_b1]
            new_low = b1["p"] < b2["p"]
            if new_low and abs(a1) < abs(a2) * 0.85 and a2 < 0 and (last_i - i_b1) <= RECENT_BARS:
                res.append({"type": "一类买点", "date": bars[i_b1]["date"],
                            "ZG": None, "ZD": None, "stop": low1,
                            "close": closes[last_i],
                            "note": f"底背驰 面积 {abs(a1):.2f} < {abs(a2):.2f}",
                            "why": [
                                _why("① 创新低（下跌段的低点要更低）",
                                     f"本段底 %.2f < 前段底 %.2f" % (b1["p"], b2["p"]),
                                     "本段底 < 前段底", new_low),
                                _why("② 力度衰竭（MACD 绿柱面积收缩）",
                                     "%.2f / %.2f = %.1f%%"
                                     % (abs(a1), abs(a2), abs(a1) / abs(a2) * 100),
                                     "< 85%", abs(a1) < abs(a2) * 0.85),
                                _why("③ 前段必须是下跌段",
                                     "A2 面积 %.2f" % a2, "A2 < 0", a2 < 0),
                                _why("④ 时效（信号不能太老）",
                                     f"距今 %d 根" % (last_i - i_b1),
                                     "≤ %d 根" % RECENT_BARS,
                                     (last_i - i_b1) <= RECENT_BARS),
                                _trend_note(zs_all, i_b1),
                            ]})
                if last_i - i_b1 >= 3:
                    seg_h = max(highs[i_b1:last_i + 1])
                    rebound = (seg_h - low1) / low1 if low1 > 0 else 0
                    if rebound >= MIN_REBOUND:
                        tail = lows[i_b1 + 1:last_i + 1]
                        j_rel = tail.index(min(tail))
                        j = i_b1 + 1 + j_rel
                        peak_rel = highs[i_b1:last_i + 1].index(seg_h)
                        # ★【关键】peak_rel 必须与 j 比较，否则「先冲高再回落」形同虚设：
                        #   不比较时，一类低点刚过、还在磨底的低点会被当成"回踩低点"，
                        #   那不是回踩、是没起来 —— 实测伪二类从 6 只放大到 33 只（+450%）。
                        if peak_rel > 0 and j > i_b1 + peak_rel and low1 > 0:
                            pull_lo = tail[j_rel]
                            if pull_lo > low1 and j >= i_b1 + 2:
                                # 日期取**回踩低点当日**，不能用最后一根K线 ——
                                # 否则所有二类信号时间戳相同，时间集中度统计必然失真。
                                res.append({"type": "二类买点", "date": bars[j]["date"],
                                            "ZG": None, "ZD": None, "stop": low1,
                                            "close": closes[last_i],
                                            "note": f"反弹{rebound * 100:.0f}%后回踩{pull_lo:.2f}不破一类低点",
                                            "why": [
                                                _why("① 前提：一类买点已成立",
                                                     "底 %.2f（%s）"
                                                     % (low1, _ds(bars[i_b1]["date"])),
                                                     "必须先有一类买点", 1),
                                                _why("② 反弹幅度够（确认转折）",
                                                     "+%.1f%%（%.2f → %.2f）"
                                                     % (rebound * 100, low1, seg_h),
                                                     "≥ %.1f%%" % (MIN_REBOUND * 100),
                                                     rebound >= MIN_REBOUND),
                                                _why("③ 回踩不破一类低点",
                                                     "回踩低点 %.2f" % pull_lo,
                                                     "> 一类低点 %.2f" % low1,
                                                     pull_lo > low1),
                                                _why("④ 回踩发生在反弹高点之后",
                                                     "高点后第 %d 根" % (j - (i_b1 + peak_rel)),
                                                     "必须在反弹高点之后",
                                                     j > i_b1 + peak_rel),
                                            ]})
                            # 回踩破前低 → 直接淘汰，不进候选
                        else:
                            # 回踩低点出现在反弹高点之前 = 还没走过像样的回踩。
                            # 不是买点，但是二类的「预备役」。
                            if on_candidate is not None and closes[last_i] > low1:
                                on_candidate({"type": "二类候选",
                                              "date": bars[i_b1]["date"],
                                              "close": closes[last_i],
                                              "ZG": None, "ZD": None, "stop": low1,
                                              "note": f"反弹{rebound * 100:.0f}%后未回踩，回落不破{low1:.2f}即成二类"})
    return res


# ---------------------------------------------------------------- 可视化结构
def structure(bars, with_macd=True):
    """摊平成紧凑数组，索引是 bars 内的**绝对下标**，供画图用。

      fx   [[i, t, p]]              分型    t=1 顶 / -1 底
      bi   [[i, t, p]]              笔端点（最后一笔可能尚未确认）
      zs   [[i0, i1, ZD, ZG]]       中枢
      md   [柱*1000 取整] 或 None
      ar   [[a2起,a2止],[a1起,a1止]] 或 None

    ★ 这里不重算买点 —— 买点以 detect() 为准。两者共用同一套
      merge/fx/bi/zs，所以图上画的笔和判定用的笔必然一致。
      绝对不要在展示层另写一套画图算法。
    """
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    mb = merge_bars([(i, highs[i], lows[i]) for i in range(len(bars))])
    fx = find_fenxing(mb)
    pts = find_bi(fx)
    zs = find_zhongshu(pts) if len(pts) >= 4 else []

    out = {
        "fx": [[f["i"], f["t"], round(f["p"], 2)] for f in fx],
        "bi": [[p["i"], p["t"], round(p["p"], 2)] for p in pts],
        "zs": [[z["i0"], z["i1"], round(z["ZD"], 2), round(z["ZG"], 2)] for z in zs],
        "md": None,
        "ar": None,
    }
    if with_macd:
        _, _, bar = macd(closes)
        if bar:
            out["md"] = [round(x, 3) for x in bar]
        if bar and len(pts) >= 4:
            downs = [(pts[k], pts[k + 1]) for k in range(len(pts) - 1)
                     if pts[k]["t"] == 1 and pts[k + 1]["t"] == -1]
            if len(downs) >= 2:
                (t2, b2), (t1, b1) = downs[-2], downs[-1]
                out["ar"] = [[t2["i"], b2["i"]], [t1["i"], b1["i"]]]
    return out


def pack(bars, lookback=130, cut=2):
    """列式打包，供前端直接消费。

      ★【图榜同源】结构必须在**全量 bars** 上算，再把索引减去窗口偏移
        对齐到展示窗；不能只在窗口内算 —— 那样图上画的笔和判定买点用的
        笔不是同一套，会出现"图上看着是二买、榜上却没有"。

      价格 ×100 取整、MACD 柱 ×1000 取整，前端统一除以常量即可，
      省掉浮点传输误差。
    """
    if len(bars) < 20:
        return None
    off = max(0, len(bars) - lookback)
    win = bars[off:]
    out = {
        # ★ d0 必须转成 str：前端 dates 是字符串数组，若这里留 int，
        #   JS 的 dates.indexOf(d.d0) 严格比较必然返回 -1 → 回退 start=0，
        #   于是 X 轴标签与悬停十字光标日期整体错位到「数据最前面」那一段
        #   （图看着正常，日期全错）。见 kline_canvas.js 的 dateStart()。
        "d0": str(win[0]["date"]), "n": len(win),
        "o": [round(b["open"] * 100) for b in win],
        "h": [round(b["high"] * 100) for b in win],
        "l": [round(b["low"] * 100) for b in win],
        "c": [round(b["close"] * 100) for b in win],
        "v": [int(b["volume"]) for b in win],
    }
    st = structure(bars)
    out["fx"] = [[f[0] - off, f[1], round(f[2] * 100)] for f in st["fx"] if f[0] >= off]
    out["bi"] = [[b[0] - off, b[1], round(b[2] * 100)] for b in st["bi"] if b[0] >= off]
    out["zs"] = [[max(0, z[0] - off), z[1] - off, round(z[2] * 100), round(z[3] * 100)]
                 for z in st["zs"] if z[1] >= off]
    if st["md"]:
        out["md"] = [int(round(x * 1000)) for x in st["md"][off:]]
    if st["ar"]:
        out["ar"] = [[max(0, st["ar"][0][0] - off), max(0, st["ar"][0][1] - off)],
                     [max(0, st["ar"][1][0] - off), max(0, st["ar"][1][1] - off)]]
    return out


# ---------------------------------------------------------------- 历史信号扫描
def scan_signals(bars, min_bars=120, step=1, types=None):
    """逐时点切片复算 detect()，返回历史上出现过的**信号段**（复盘专用）。

    types: 只保留这些类型，None = 全部。★ 复盘建议只留买点（一类/二类/三类）——
           「中枢突破」是待验证状态，一段行情里能报出几十次，标在图上会糊成一片。

    ★ 为什么必须切片：detect() 每次只比「最后两组下跌段」，是个随新 K 线右移的
      滑动窗口 —— 它回答的是「此刻是否成立」，**不是历史扫描器**。信号一旦滑出
      窗口就再也查不到。所以「那个位置当时到底有没有信号」只能逐根切片重算。

    合并规则：同一 type 且报出时点连续（cut 间隔 ≤ step）的，视为**同一个信号段**
    （即该信号的存活期）。每段记录：
        first_i / last_i   首次 / 末次报出的 bars 下标
        alive              存活根数（last_i - first_i）
        sig_date           末次报出的信号日期（最成熟的那一次）
    """
    if len(bars) < 40:
        return []
    min_bars = max(20, min(min_bars, len(bars) - 1))
    last = {}          # type -> (上次报出的 cut, 段索引)
    segs = []
    for cut in range(min_bars, len(bars) + 1, step):
        for h in detect(bars[:cut]):
            t = h["type"]
            if types is not None and t not in types:
                continue
            prev = last.get(t)
            if prev is not None and cut - prev[0] <= step:
                g = segs[prev[1]]
                g["last_i"] = cut - 1
                g["alive"] = cut - 1 - g["first_i"]
                g["sig_date"] = h["date"]           # 取最新的一次，最成熟
                g["stop"] = h.get("stop")
                g["note"] = h.get("note", "")
                g["why"] = h.get("why")             # 判据明细同样取最新（最成熟）那次
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
