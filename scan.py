# -*- coding: utf-8 -*-
"""全市场扫描：拉列表 → 并发取日 K → 买卖点判定 → 挂行业 → 落盘（含日 K 缓存）。

用法:
    python scan.py                    # 全市场（沪深 A）
    python scan.py --limit 300        # 只扫前 300 只（试跑）
    python scan.py --workers 12       # 并发数
    python scan.py --count 800        # 取多少根日 K

也作为函数被 server.py 调用（`run_scan(progress=...)`），所以**没有副作用写在模块顶层**，
进度通过回调抛出去，服务端再转成任务状态。

两个让全市场跑得动的设计：
  · **先探测再回放**：只算最后一根，没有信号的票直接跳过新鲜度回放（省掉 ≥80% 的计算）。
  · **命中才落盘日 K**：只给有信号的票写 cache/kline/day/，点开秒出；没命中的票
    榜单里根本看不到，存了也是死数据。
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import datasource as DS                        # noqa: E402
from core import engine as E                             # noqa: E402
from core import store as ST                             # noqa: E402

MIN_BARS = 120


def scan_one(item, freq="day", count=800, lookback=5, cache_kline=True):
    """单票：取数 → 探测 → 判定 → （命中则）缓存日 K。返回行字典或 None。"""
    sym, code, name = item["symbol"], item["code"], item["name"]
    try:
        bars = DS.sina_kline(sym, freq, count)
    except Exception as e:                                   # noqa: BLE001
        return {"code": code, "_error": type(e).__name__}
    if len(bars) < MIN_BARS:
        return None

    hits, sells = E.probe(bars)
    if not hits and not sells:
        return None                                          # 无信号 → 不回放、不落盘

    a = E.analyze(bars, unit=DS.UNIT.get(freq, freq), lookback=250, scan_lookback=lookback)
    if not a:
        return None
    if cache_kline:
        try:
            ST.save_kline(freq, code, bars)
        except Exception:                                    # noqa: BLE001
            pass                                             # 缓存失败不影响扫描结果

    last = bars[-1]
    return {
        "code": code, "name": name, "symbol": sym, "industry": "未分类",
        "industry_full": "未分类", "close": round(last["close"], 2),
        "date": str(last["date"]),
        "amt": round(last["close"] * last["volume"] / 1e8, 3),   # 亿元（volume 单位=股）
        "buy": a["signals"]["buy"], "sell": a["signals"]["sell"],
    }


def run_scan(limit=0, freq="day", count=800, lookback=5, workers=20,
             progress=None, cache_kline=True):
    """全市场扫描主流程。progress(stage, done, total, msg) 可选。"""
    def say(stage, done=0, total=0, msg=""):
        if progress:
            progress(stage, done, total, msg)

    t0 = time.perf_counter()
    say("list", 0, 0, "拉取全市场列表…")
    lst = DS.market_list()
    if limit:
        lst = lst[:limit]
    say("list", len(lst), len(lst), "列表 %d 只（%.1fs）" % (len(lst), time.perf_counter() - t0))

    say("scan", 0, len(lst), "并发扫描 %d 只…" % len(lst))
    rows, errs = [], 0
    t1 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(scan_one, x, freq, count, lookback, cache_kline): x for x in lst}
        for done, f in enumerate(as_completed(futs), 1):
            if done % 500 == 0 or done == len(lst):
                say("scan", done, len(lst), "%.0fs" % (time.perf_counter() - t1))
            try:
                r = f.result()
            except Exception:                                # noqa: BLE001
                errs += 1
                continue
            if r is None:
                continue
            if "_error" in r:
                errs += 1
                continue
            rows.append(r)
    say("scan", len(lst), len(lst), "命中 %d 只 / 失败 %d（%.1fs）"
        % (len(rows), errs, time.perf_counter() - t1))

    say("industry", 0, 0, "挂行业…")
    t2 = time.perf_counter()
    try:
        ind = DS.industry_map()
    except Exception:                                        # noqa: BLE001
        ind = {}
    for r in rows:
        m = ind.get(r["code"]) or {}
        r["industry"] = m.get("l1", "未分类")
        r["industry_full"] = m.get("full", "未分类")
    say("industry", 0, 0, "行业 %d 条（%.1fs）" % (len(ind), time.perf_counter() - t2))

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "freq": freq, "count": count, "lookback": lookback,
        "total_scanned": len(lst), "hit": len(rows), "errors": errs,
        "elapsed": round(time.perf_counter() - t0, 1),
        "rows": rows,
    }
    say("save", 0, 0, "落盘…")
    ST.save_scan(payload)
    say("done", len(rows), len(lst), "完成 %.1fs" % payload["elapsed"])
    return payload


def summarize(payload):
    from collections import Counter
    bc, sc, stocks = Counter(), Counter(), set()
    for r in payload["rows"]:
        for h in r["buy"]:
            bc[h["type"]] += 1
            if h["fresh"] == 0 and h["valid"]:
                stocks.add(r["code"])
        for h in r["sell"]:
            sc[h["type"]] += 1
    return {"buy": dict(bc), "sell": dict(sc), "today_valid_stocks": len(stocks)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只扫前 N 只（试跑）")
    ap.add_argument("--freq", default="day", choices=list(DS.SCALE))
    ap.add_argument("--count", type=int, default=800)
    ap.add_argument("--lookback", type=int, default=5)
    ap.add_argument("--workers", type=int, default=20,
                    help="并发数；20 是全市场实测最快且未触发封禁的值（10 约慢一倍）")
    ap.add_argument("--no-kline-cache", action="store_true", help="不落盘日 K 缓存")
    a = ap.parse_args()

    last = [""]

    def show(stage, done, total, msg):
        line = "[%s] %s" % (stage, msg)
        if line != last[0]:
            last[0] = line
            print(line, flush=True)

    p = run_scan(a.limit, a.freq, a.count, a.lookback, a.workers, show,
                 cache_kline=not a.no_kline_cache)
    s = summarize(p)
    print("\n完成 %ss  扫描 %d / 命中 %d / 失败 %d"
          % (p["elapsed"], p["total_scanned"], p["hit"], p["errors"]))
    print("  买点:", s["buy"])
    print("  卖点:", s["sell"])
    print("  ★ 当日且未失效的买点涉及 %d 只" % s["today_valid_stocks"])
    print("  结果: cache/scan/latest.json   日K缓存: cache/kline/day/")


if __name__ == "__main__":
    main()
