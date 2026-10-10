# -*- coding: utf-8 -*-
"""判定层：一批 K 线 → 前端要的全部东西（结构 / 买卖点 / 图数据 / 文字）。

**这一层是工作台的口径所在**。榜单和抽屉都调这里，所以同一个代码在榜上显示的
「三类买点 · 关键位 60.40」和点开后图上画的中枢下沿、卡片里的关键位必然是一个数 ——
两个入口各算各的，迟早会出现「榜上有、图上没有」这种自相矛盾。

买点用技能引擎 `chanlun_core.detect()`；卖点是它的严格镜像 `core.sell.detect_sell()`
（阈值常量共用，不另设）。
"""
from . import require_engine

K = require_engine()             # 缺失时抛带「该放哪个目录」指引的 ImportError
import run_chanlun as R          # noqa: E402  只借它的文字报告模板（单票产物一直在用，保持一致）

from . import sell as S

# 一类买点「趋势要件」的判据键前缀 —— 引擎把它当参考项放进 why 列表末尾
_TREND_KEY = "趋势"

# ★ 真买卖点（一/二/三类）。「中枢突破 / 中枢跌破」是**待验证态**，不是信号本身 ——
#   一段行情里它能报出几十次（全市场实测中枢突破 545 / 中枢跌破 1239），
#   拿它去画「◆卖点」标记属于名不副实：图上的标记必须和榜单 tab 的口径一致。
TRUE_BUY = ("一类买点", "二类买点", "三类买点")
TRUE_SELL = ("一类卖点", "二类卖点", "三类卖点")


def probe(bars):
    """只算最后一根：有没有信号。扫描全市场时先用它筛一遍。

    ★ 价值在于「无信号的票直接跳过整轮回放」—— 回放要对最后 N 根逐根重跑引擎，
      5572 只里绝大多数没有信号，不筛的话扫描耗时至少翻倍。
    """
    return K.detect(bars), S.detect_sell(bars)


def fresh_map(bars, lookback=5):
    """每个信号「首次可察觉距今几根」。

    ★ 引擎报的是「截至最后一根仍成立」的全部信号，混着几十根之前的老信号。
      要收紧成「当日新出现」，只能对最后 lookback+1 根逐根 detect(bars[:i+1])，
      信号 key 第一次出现在哪一轮，就是它的年龄（fresh = n-1-i，0 = 最后一根才报出）。
      窗口外就存在的信号饱和为 lookback。
    """
    n = len(bars)
    fm = {}
    for i in range(max(1, n - 1 - lookback), n):
        sub = bars[:i + 1]
        f = n - 1 - i
        for h in K.detect(sub):
            fm.setdefault(("b", h["type"], str(h["date"])), f)
        for h in S.detect_sell(sub):
            fm.setdefault(("s", h["type"], str(h["date"])), f)
    return fm


def scan_history(bars, min_bars=120, step=1):
    """该级别历史上出现过的**全部真买卖点段**（含已失效）—— 抽屉与图上编号的唯一数据源。

    每段 = {kind, type, n, date, first_i, last_i, alive, live, entry, stop, ret, why, note}

    ★ 为什么要「全部段」而不是只看当前信号：技能单票页的「买点成立理由」区列的
      就是历史上每一个信号，含已经失效的。只列当前仍成立的，一次只出一两张卡 ——
      那正是「不如原来的个股分析」的地方：复盘要看的是这一路报过哪些信号、
      哪个成立后在几根内被打掉、哪个延续了多久。
    ★ live = 该段延续到最后一根（至今仍有效）；不是「现价是否跌破结构位」，
      两者会不一致，卡片上的状态 chip 用 live（与技能页同口径）。
    ★ 成本：800 根日 K 买卖各约 0.44 s。**只允许抽屉路径调用** ——
      全市场扫描走 fresh_map（快版），5572 只 × 0.9 s = 80 分钟。
    """
    out = []
    n = len(bars)
    pairs = (("b", K.scan_signals(bars, min_bars=min_bars, step=step, types=TRUE_BUY)),
             ("s", S.scan_signals_sell(bars, min_bars=min_bars, step=step, types=TRUE_SELL)))
    for kind, segs in pairs:
        for g in segs:
            i0, i1 = g["first_i"], g["last_i"]
            if not (0 <= i0 < n and 0 <= i1 < n):
                continue
            e = bars[i0]["close"]
            out.append({
                "kind": kind, "type": g["type"], "n": _NUM.get(g["type"], "?"),
                "date": str(g["sig_date"]),
                "first_i": i0, "last_i": i1, "alive": g["alive"],
                "live": 1 if i1 >= n - 1 else 0,
                "entry": round(e, 2), "stop": g.get("stop"),
                # 期内 = 首次报出 → 失效那根。失效之后再怎么走都不属于这个信号的能力范围，
                # 拿「至今涨跌」去衡量会把已失效信号的运气算进来。
                "ret": round((bars[i1]["close"] / e - 1) * 100, 1) if e else 0.0,
                "why": g.get("why") or [],
                "note": g.get("note", ""),
            })
    out.sort(key=lambda x: x["first_i"])
    return out


def life_index(hist):
    """history 列表 → {(kind, type, 信号日): 段}，用于给「当前信号」补 alive / ret。"""
    return {(h["kind"], h["type"], h["date"]): h for h in (hist or [])}


def pack_signals(hs, kind, fm, px, lookback=5, bars=None, life=None):
    """信号 → 扁平字典（前端直接消费）。kind: 'b' 买 / 's' 卖。

    ★ why 必须原样带出去：图上一个「◆买点」只是结论，用户看不到它凭什么成立。
      每条判据的**实测值 vs 门槛**一并给前端，抽屉里才能逐条核对 ——
      这也是复盘时唯一能验证「引擎是不是漏判 / 放宽了条件」的方式。
      （技能 interactive_html.why_block 就是这么做的，抽屉不能比它少。）
    """
    out = []
    last_i = len(bars) - 1 if bars else None
    for h in hs:
        why = h.get("why") or []
        tr = why[-1] if why and str(why[-1].get("k", "")).startswith(_TREND_KEY) else None
        stop = h.get("stop")
        if stop:
            # 买点：现价跌破结构位即失效；卖点：涨破即失效。
            # ★ 引擎对一类买点不校验当前价 —— 报出后价格若跌破本段底、笔端点又没确认，
            #   信号还会挂着，那就是「已失效」，必须自己判。
            valid = 1 if ((px > stop) if kind == "b" else (px < stop)) else 0
        else:
            valid = 1
        fresh = fm.get((kind, h["type"], str(h["date"])), lookback)
        # 首次报出下标 = 距今根数倒推。图上打编号、算「信号确认价」都用它 ——
        # 不能用信号日（那是结构点，往往在很左边，标记会跑到跟理由对不上的地方）。
        first_i = (last_i - fresh) if last_i is not None else None
        entry = None
        alive, ret = None, None
        # 抽屉路径带 life：用切片复算的精确值覆盖「倒推值」——
        # 倒推值对窗口外的老信号是错的（fresh 饱和），而卡片要显示「存活 N 根」。
        lk = (life or {}).get((kind, h["type"], str(h["date"])))
        if lk:
            first_i, entry, alive, ret = lk["first_i"], lk["entry"], lk["alive"], lk["ret"]
        elif bars and first_i is not None and 0 <= first_i < len(bars):
            entry = round(bars[first_i]["close"], 2)
        out.append({
            "type": h["type"], "date": str(h["date"]), "stop": stop,
            "ZG": h.get("ZG"), "ZD": h.get("ZD"), "note": h.get("note", ""),
            # -1 = 该类型没有趋势要件（二/三类）；0/1 = 一类买卖点的趋势要件结果
            "trend": (1 if tr["ok"] else 0) if tr else -1,
            "fresh": fresh, "valid": valid,
            "why": why,                 # 判据明细 [{k,v,need,ok}]，逐条核对用
            "first_i": first_i,         # 首次报出下标（相对全量 bars）
            "entry": entry,             # 首次报出当根收盘 = 当时可执行的价
            "alive": alive,             # 存活根数（首次报出 → 失效），仅 life 路径有
            "ret": ret,                 # 期内涨跌 %（首次报出 → 失效），仅 life 路径有
        })
    return out


def signals(bars, fm=None, lookback=5, life=None):
    """当前仍成立的全部买卖点（含新鲜度 / 失效判定 / 存活跨度）。"""
    if fm is None:
        fm = fresh_map(bars, lookback)
    px = bars[-1]["close"]
    return {"buy": pack_signals(K.detect(bars), "b", fm, px, lookback, bars, life),
            "sell": pack_signals(S.detect_sell(bars), "s", fm, px, lookback, bars, life)}


# ★ 买卖点都要有：漏掉卖点会让图上的方块显示成「?」（前端 n 取不到就退回标题）。
_NUM = {"一类买点": "1", "二类买点": "2", "三类买点": "3",
        "一类卖点": "1", "二类卖点": "2", "三类卖点": "3"}
_RM = {"一类买点": "Ⅰ", "二类买点": "Ⅱ", "三类买点": "Ⅲ",
       "一类卖点": "Ⅰ", "二类卖点": "Ⅱ", "三类卖点": "Ⅲ"}


def build_hist(hist, base, wn, px=None):
    """历史信号段 → 图上的编号标记（1/2/3）+ tooltip 数据（对应 kline_canvas 的 hist 图层）。

    ★ 编号 = 类型编号（1 一类 / 2 二类 / 3 三类），不是序号 ——
      抽屉里卡片左上角的数字用的也是它，两边必须同源，否则对照会串。
    ★ side 决定颜色（b 紫 / s 青）。
    ★ i 取「首次报出」下标，不是信号日：detect() 是滑动窗口，信号日往往是很早的
      结构低点（笔端点），标在那儿会跟右侧的笔 / 中枢对不上，看着像画错了位置。
    """
    out = []
    for s in (hist or []):
        i = s["first_i"] - base
        if not (0 <= i < wn):
            continue
        out.append({
            "i": i, "side": s["kind"], "t": s["type"], "ty": s["type"],
            "n": s["n"], "rn": _RM.get(s["type"], "?"),
            "d": s["date"], "alive": s["alive"], "live": s["live"],
            "entry": s["entry"], "stop": s.get("stop"), "ret": s.get("ret"),
            "why": s.get("why") or [],      # 判据明细随标记一起给，卡片与图同源
            # tooltip 上的「现价 +x%」= 现价相对该信号报出价。技能单票页同口径。
            "now": round((px / s["entry"] - 1) * 100, 1) if (px and s["entry"]) else 0,
        })
    out.sort(key=lambda x: x["i"])
    return out


def structure(bars):
    """中枢与现价位置。无中枢（次新股）时 ZG/ZD 为 None。"""
    st = K.structure(bars)
    zs = st["zs"]
    cur = bars[-1]["close"]
    if zs:
        ZD, ZG = round(zs[-1][2], 2), round(zs[-1][3], 2)
        pos = "ZG 上方" if cur > ZG else ("中枢内" if cur > ZD else "已破 ZD")
        lo, hi = round(min(z[2] for z in zs), 2), round(max(z[3] for z in zs), 2)
    else:
        ZD = ZG = None
        pos, lo, hi = "无中枢", None, None
    return {"ZG": ZG, "ZD": ZD, "pos": pos, "zsCount": len(zs),
            "zsLow": lo, "zsHigh": hi,
            "biCount": len(st["bi"]), "fxCount": len(st["fx"])}


def analyze(bars, unit="日线", lookback=250, scan_lookback=5, with_report=False,
            with_history=False, history=None):
    """一批 K 线 → 抽屉需要的全部数据。

    chart.opt 里的 buyIdx / sellIdx 是**窗口相对下标**（已减掉 pack 的偏移）：
    前端 X(i) 只认窗口内索引，不减的话只要 lookback < 全量根数，
    标记就会被越界钳制画到最右边一根上 —— 图上看着有标记、位置全错。

    with_history=True：额外做一遍切片复算，产出该级别**历史上全部信号段**
    （`out["history"]`，含已失效）与图上全部编号。
    **只给抽屉用**（+0.9 s / 票，服务端按天缓存）；扫描路径必须保持 False。
    """
    d = K.pack(bars, lookback=lookback)
    if not d:
        return None
    dates = [str(b["date"]) for b in bars]
    hits, sells = K.detect(bars), S.detect_sell(bars)
    fm = fresh_map(bars, scan_lookback)
    if history is None and with_history:
        history = scan_history(bars)
    life = life_index(history)
    px = bars[-1]["close"]

    try:
        base = dates.index(str(d["d0"]))
    except ValueError:
        base = max(0, len(dates) - d["n"])

    def widx(dt):
        s = str(dt)
        return (dates.index(s) - base) if s in dates else None

    st = structure(bars)
    opt = {}
    if st["ZD"] is not None:
        opt["zd"], opt["zg"] = st["ZD"], st["ZG"]
    tb = [h for h in hits if h["type"] in TRUE_BUY]
    ts = [h for h in sells if h["type"] in TRUE_SELL]
    if tb:
        i = widx(tb[-1]["date"])
        if i is not None and 0 <= i < d["n"]:
            opt["buyIdx"] = i
        if tb[-1].get("stop"):
            opt["stop"] = tb[-1]["stop"]
    if ts:
        i = widx(ts[-1]["date"])
        if i is not None and 0 <= i < d["n"]:
            opt["sellIdx"] = i
        if ts[-1].get("stop"):
            opt["sellStop"] = ts[-1]["stop"]
    # ★ opt.stop 只有**买点票**才给：绘图层把它标成「止损」横线，那是买点语境的词。
    #   曾经这里在没有买点时拿 ZD 兜底，结果纯卖点票的图上会出现一条写着「止损」的线 ——
    #   语义是错的（卖点的失效位是涨破，不是跌破）。图上已经有 ZG/ZD 线，
    #   兜底那条既冗余又误导，去掉。
    #   另外有信号时必须保留信号自带的 stop：三类买点的 stop 在 **ZG 下方**，
    #   与 ZD 差着整整一个中枢高度，用 ZD 覆盖掉就废了。

    pb = pack_signals(hits, "b", fm, px, scan_lookback, bars, life)
    ps = pack_signals(sells, "s", fm, px, scan_lookback, bars, life)
    out = {
        "close": round(px, 2), "date": str(bars[-1]["date"]),
        "bars": len(bars), "unit": unit,
        "structure": st,
        "signals": {"buy": pb, "sell": ps},
        # hist = 图上的编号标记（买点紫 / 卖点青，数字 = 一/二/三类），自带判据明细，
        # 与卡片一一对应：同色的同一个数字 = 同一张卡。
        # ★ 只放**落在绘图窗口内**的段 —— 与技能单票页同口径（why_block 拿的就是
        #   hist_js）。窗口外的段图上没有编号，列出来对照不上，还会把抽屉拉得极长。
        "chart": {"pack": d, "dates": dates, "opt": opt,
                  "hist": build_hist(history, base, d["n"], px)},
    }
    if history is not None:
        # 供前端在脚注里交代「还有 N 个落在绘图窗口外」
        out["history_total"] = len(history)
    if with_report:
        try:
            out["report"] = R.report(bars, unit=unit)[0]
        except Exception as e:                                  # noqa: BLE001
            out["report"] = "（文字报告生成失败：%s）" % e
    return out
