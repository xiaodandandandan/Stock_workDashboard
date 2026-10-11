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
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import datasource as DS                        # noqa: E402
from core import engine as E                             # noqa: E402
from core import store as ST                             # noqa: E402
from core import engine_identity                         # noqa: E402
from core.validation import integer                      # noqa: E402

MIN_BARS = 120
MIN_SUCCESS_RATIO = 0.95
LOG = logging.getLogger(__name__)


def validate_options(limit=0, workers=20, freq="day", count=800, lookback=5):
    return {"limit": integer(limit, "limit", 0, 10000),
            "workers": integer(workers, "workers", 1, 32),
            "freq": DS.validate_freq(freq),
            "count": integer(count, "count", MIN_BARS, 5000),
            "lookback": integer(lookback, "lookback", 0, 250)}


def scan_one(item, freq="day", count=800, lookback=5, cache_kline=True):
    """返回命中行、带 _skip 的无信号/数据不足结果，或带 _error 的取数失败。"""
    sym, name = DS.to_symbol(item["symbol"]), item["name"]
    code = sym[2:]
    try:
        bars = DS.sina_kline(sym, freq, count)
    except Exception as e:                                   # noqa: BLE001
        LOG.warning("取数失败: %s %s", sym, freq, exc_info=True)
        return {"symbol": sym, "_stage": "fetch", "_error": "%s: %s" % (type(e).__name__, e)}
    if len(bars) < MIN_BARS:
        return {"_skip": "insufficient_bars"}

    hits, sells = E.probe(bars)
    if not hits and not sells:
        return {"_skip": "no_signal"}

    a = E.analyze(bars, unit=DS.UNIT.get(freq, freq), lookback=250, scan_lookback=lookback)
    if not a:
        raise RuntimeError("引擎未返回结构: %s" % sym)
    if cache_kline:
        try:
            ST.save_kline(freq, sym, bars)
        except Exception:                                    # noqa: BLE001
            LOG.warning("缓存写入失败: %s %s", sym, freq, exc_info=True)

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
    """Publish only after coverage validation; failures have a separate diagnostic file."""
    options = validate_options(limit, workers, freq, count, lookback)
    attempt = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
               "status": "running", "stage": "list", "total_scanned": 0,
               "errors": 0, "error_details": [], "skipped": {"no_signal": 0, "insufficient_bars": 0}}
    try:
        payload = _run_scan(**options, progress=progress, cache_kline=cache_kline, attempt=attempt)
        attempt.update(status="published", hit=payload["hit"], complete=payload["complete"])
        return payload
    except Exception as e:
        attempt.update(status="failed", error="%s: %s" % (type(e).__name__, e))
        LOG.exception("扫描失败，保留已发布榜单")
        raise
    finally:
        try:
            ST.save_scan_attempt(attempt)
        except OSError:
            LOG.exception("保存扫描诊断失败")


def _run_scan(limit, freq, count, lookback, workers, progress, cache_kline, attempt):
    def say(stage, done=0, total=0, msg=""):
        attempt["stage"] = stage
        if progress:
            progress(stage, done, total, msg)

    t0 = time.perf_counter()
    say("list", 0, 0, "拉取全市场列表…")
    lst = DS.market_list()
    if not lst:
        raise RuntimeError("市场列表为空，保留上次成功扫描")
    if limit:
        lst = lst[:limit]
    say("list", len(lst), len(lst), "列表 %d 只（%.1fs）" % (len(lst), time.perf_counter() - t0))

    say("scan", 0, len(lst), "并发扫描 %d 只…" % len(lst))
    attempt["total_scanned"] = len(lst)
    rows, errs = [], 0
    t1 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(scan_one, x, freq, count, lookback, cache_kline): x for x in lst}
        for done, f in enumerate(as_completed(futs), 1):
            if done % 500 == 0 or done == len(lst):
                say("scan", done, len(lst), "%.0fs" % (time.perf_counter() - t1))
            try:
                r = f.result()
            except Exception as e:
                sym = futs[f]["symbol"]
                LOG.exception("计算失败: %s %s", sym, freq)
                r = {"symbol": sym, "_stage": "compute", "_error": "%s: %s" % (type(e).__name__, e)}
            if "_skip" in r:
                attempt["skipped"][r["_skip"]] += 1
                continue
            if "_error" in r:
                errs += 1
                attempt["errors"] = errs
                attempt["error_details"].append({"symbol": r["symbol"], "stage": r["_stage"], "error": r["_error"]})
                continue
            rows.append(r)
    say("scan", len(lst), len(lst), "命中 %d 只 / 失败 %d（%.1fs）"
        % (len(rows), errs, time.perf_counter() - t1))
    if (len(lst) - errs) / len(lst) < MIN_SUCCESS_RATIO:
        raise RuntimeError("扫描失败 %d/%d，成功率不足 %.0f%%；保留上次成功榜单"
                           % (errs, len(lst), MIN_SUCCESS_RATIO * 100))

    say("industry", 0, 0, "挂行业…")
    t2 = time.perf_counter()
    try:
        ind = DS.industry_map()
    except Exception:                                        # noqa: BLE001
        ind = {}
    for r in rows:
        m = ind.get(r["symbol"]) or {}
        r["industry"] = m.get("l1", "未分类")
        r["industry_full"] = m.get("full", "未分类")
    say("industry", 0, 0, "行业 %d 条（%.1fs）" % (len(ind), time.perf_counter() - t2))

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "freq": freq, "count": count, "lookback": lookback,
        "total_scanned": len(lst), "hit": len(rows), "errors": errs,
        "complete": errs == 0, "skipped": attempt["skipped"],
        "engine": engine_identity(E.K),
        "source": DS.PROVENANCE,
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
