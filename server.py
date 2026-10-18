#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""缠论全市场扫描 —— 本地服务。

**为什么必须是服务**：「点开个股后再算 60 / 30 分钟」意味着浏览器要能实时向后端要数据。
静态 HTML 用 file:// 打开时 fetch 会被同源策略挡掉，做不到按需计算。
所以应用从「一个 HTML 文件」变成「一个本地服务 + 一组 API」。

端点:
    GET  /                        全市场扫描页面（web/index.html）
    GET  /<静态资源>               web/ 下的 css / js / vendor
    GET  /api/health               服务与数据状态
    GET  /api/scan?snapshot=       最新或指定历史扫描结果
    GET  /api/scans                扫描日期与批次目录
    GET  /api/tracking             所选快照个股相对扫描价的当前涨跌
    POST /api/scan                 触发一次全市场扫描（后台跑，立即返回）
    GET  /api/scan/status          扫描进度
    GET  /api/stock?code=&freq=    单票：日 K 走缓存秒回，分钟线现取现算
    GET  /api/fundamentals?code=&refresh=  单票基本面与公司动态，按需获取并缓存

零第三方依赖，只用标准库。
"""
import argparse
import json
import logging
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import WEB, CACHE                              # noqa: E402
from core import datasource as DS                        # noqa: E402
from core import engine as E                             # noqa: E402
from core import store as ST                             # noqa: E402
from core import tracking as TR                          # noqa: E402
from core import fundamentals as FA                      # noqa: E402
from core import engine_identity                         # noqa: E402
from core.validation import InputError, integer          # noqa: E402

LOG = logging.getLogger(__name__)

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
        ".ico": "image/x-icon", ".woff2": "font/woff2"}

# 扫描任务状态。单机单用户，用一把锁 + 一个字典就够，不引入任务队列。
JOB = {"running": False, "stage": "", "done": 0, "total": 0, "msg": "",
       "started": 0.0, "finished": 0.0, "result": None, "error": ""}
JOB_LOCK = threading.Lock()

# 行业映射进程级缓存：一次拉全量约 1.7 s，没必要每开一只票就拉一遍
_IND = None
_IND_LOCK = threading.Lock()

# Cache by complete data content, including intrabar and historical corrections.
_HIST = {}
_HIST_LOCK = threading.Lock()


def cached_history(freq, code, bars, analysis_version=None):
    version = analysis_version or E.analysis_identity()["analysis_version"]
    key = (freq, DS.to_symbol(code), ST.bars_version(bars), version)
    with _HIST_LOCK:
        hit = _HIST.get(key)
    if hit is not None:
        return hit
    h = E.scan_history(bars)
    with _HIST_LOCK:
        if len(_HIST) > 400:            # 单机单用户，满了直接清空即可，不必做 LRU
            _HIST.clear()
        _HIST[key] = h
    return h


def industry_lookup():
    global _IND
    if _IND is None:
        with _IND_LOCK:
            if _IND is None:
                try:
                    mapping = DS.industry_map()
                    if mapping:
                        _IND = mapping
                except Exception:                            # noqa: BLE001
                    LOG.warning("行业查询失败，下次请求会重试", exc_info=True)
    return _IND or {}


def scan_meta():
    """Full exchange-qualified symbols also support existing scan snapshots."""
    d = ST.load_scan() or {}
    return {DS.to_symbol(r.get("symbol") or r["code"]): r for r in d.get("rows", [])}, d


# ---------------------------------------------------------------- 单票
def stock_payload(code, freq="day", lookback=250, with_report=False):
    """单票数据。日 K 优先吃扫描时的缓存（秒回），分钟线现取现算。"""
    t0 = time.perf_counter()
    sym = DS.to_symbol(code)
    code = sym[2:]
    freq = DS.validate_freq(freq)
    lookback = integer(lookback, "lookback", 30, 5000)
    analysis = E.analysis_identity()

    cached = True
    bars = ST.load_kline(freq, sym)
    if not bars:
        cached = False
        bars = DS.sina_kline(sym, freq, 800)
        try:
            ST.save_kline(freq, sym, bars)
        except Exception:                                    # noqa: BLE001
            LOG.warning("缓存写入失败: %s %s", sym, freq, exc_info=True)
    if len(bars) < 30:
        raise RuntimeError("K 线不足（%d 根），无法做结构分析" % len(bars))

    # 抽屉要展示该级别历史上全部信号段（含已失效）并显示「存活 N 根 / 期内 ±x%」——
    # 只能切片复算，结果按数据版本缓存。榜单（scan.py）不走这条路：那边每只票都算的话
    # 全市场要多花 80 分钟。
    hist = cached_history(freq, sym, bars, analysis_version=analysis["analysis_version"])
    a = E.analyze(bars, unit=DS.UNIT.get(freq, freq), lookback=lookback,
                  with_report=with_report, history=hist)
    if not a:
        raise RuntimeError("K 线不足，无法打包结构")

    rows, scand = scan_meta()
    row = rows.get(sym) or {}
    name = row.get("name") or DS.quote_name(sym)
    ind = row.get("industry")
    ind_full = row.get("industry_full")
    if not ind or ind == "未分类":
        m = industry_lookup().get(sym) or {}
        ind, ind_full = m.get("l1", "未分类"), m.get("full", "未分类")

    last = bars[-1]
    a.update({
        "code": code, "symbol": sym, "name": name,
        "freq": freq, "data_version": ST.bars_version(bars),
        "engine": engine_identity(E.K),
        "source": DS.PROVENANCE,
        "industry": ind or "未分类", "industry_full": ind_full or "未分类",
        "amt": round(last["close"] * last["volume"] / 1e8, 3),
        "scan_at": scand.get("generated", ""),
        "in_scan": sym in rows,
        "cached": cached,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        **analysis,
    })
    return a


# ---------------------------------------------------------------- 扫描任务
def start_scan(limit=0, workers=32, freq="day", count=800, lookback=5):
    import scan as SC
    options = SC.validate_options(limit, workers, freq, count, lookback)
    with JOB_LOCK:
        if JOB["running"]:
            return False
        JOB.update({"running": True, "stage": "start", "done": 0, "total": 0,
                    "msg": "启动…", "started": time.time(), "finished": 0,
                    "result": None, "error": ""})

    def progress(stage, done, total, msg):
        with JOB_LOCK:
            JOB.update({"stage": stage, "done": done, "total": total, "msg": msg})

    def work():
        try:
            p = SC.run_scan(**options, progress=progress)
            with JOB_LOCK:
                JOB.update({"result": {"hit": p["hit"], "total": p["total_scanned"],
                                       "elapsed": p["elapsed"],
                                       "errors": p["errors"], "complete": p["complete"],
                                       "industry_status": p["industry_status"],
                                       "generated": p["generated"],
                                       "analysis_version": p["analysis_version"],
                                       "sell_rules_version": p["sell_rules_version"]}})
        except Exception as e:                               # noqa: BLE001
            with JOB_LOCK:
                JOB["error"] = "%s: %s" % (type(e).__name__, e)
                JOB["stage"] = "failed"
            traceback.print_exc()
        finally:
            with JOB_LOCK:
                JOB["running"] = False
                JOB["finished"] = time.time()
                if not JOB["error"]:
                    JOB["stage"] = "done"

    threading.Thread(target=work, daemon=True).start()
    return True


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "ChanlunWorkbench"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        pass                                                 # 静音：默认每请求一行太吵

    # ---- 工具
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def send_file(self, full):
        ext = os.path.splitext(full)[1].lower()
        try:
            with open(full, "rb") as fp:
                body = fp.read()
        except OSError:
            self.send_error(404, "Not Found")
            return
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        # 静态资源一律 no-cache：这是本地单机工具，改完刷新就该生效，
        # 让人「改了没反应、要清缓存」的代价远大于省下的一点 IO。
        self.send_header("Cache-Control", "no-cache")
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    # ---- 路由
    def do_GET(self):
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        try:
            if p.startswith("/api/"):
                return self.api_get(p, qs)
            return self.static(p)
        except InputError as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:                               # noqa: BLE001
            traceback.print_exc()
            self.send_json({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/scan":
            try:
                n = integer(self.headers.get("Content-Length", "0"), "Content-Length", 0, 8192)
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                if not isinstance(body, dict):
                    raise InputError("请求体必须是 JSON 对象")
                allowed = {"limit", "workers", "freq", "count", "lookback"}
                if set(body) - allowed:
                    raise InputError("未知扫描参数")
                started = start_scan(**body)
            except (InputError, UnicodeError, json.JSONDecodeError) as e:
                self.close_connection = True
                return self.send_json({"error": str(e)}, 400)
            return self.send_json({"started": started,
                                   "reason": "" if started else "已有扫描在跑"})
        self.send_error(404, "Not Found")

    # ---- API
    def api_get(self, p, qs):
        def one(k, d=""):
            return (qs.get(k) or [d])[0]

        if p == "/api/health":
            s = ST.load_scan()
            with JOB_LOCK:
                job = {k: v for k, v in JOB.items() if k != "result"}
            return self.send_json({
                "ok": True, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "scan": {"generated": (s or {}).get("generated", ""),
                         "hit": (s or {}).get("hit", 0),
                         "total": (s or {}).get("total_scanned", 0),
                         "elapsed": (s or {}).get("elapsed", 0),
                         **{field: s[field] for field in ("analysis_version", "sell_rules_version") if field in s}} if s else None,
                "cached_kline": sum(len(f) for _, _, f in os.walk(os.path.join(CACHE, "kline"))),
                "job": job, "last_attempt": ST.load_scan_attempt(),
                "engine": engine_identity(E.K),
                **E.analysis_identity(),
            })

        if p == "/api/scans":
            return self.send_json({"snapshots": ST.list_scans()})

        if p == "/api/scan":
            snapshot = one("snapshot").strip()
            s = ST.load_scan(snapshot or None)
            if not s:
                return self.send_json({"error": "扫描快照不存在" if snapshot else
                                      "还没有扫描结果，先 POST /api/scan"}, 404)
            return self.send_json(s)

        if p == "/api/tracking":
            snapshot = one("snapshot").strip()
            refresh = one("refresh", "0")
            if refresh not in ("0", "1"):
                raise InputError("refresh 必须是 0 或 1")
            raw_codes = one("codes")
            if not raw_codes or len(raw_codes) > 9000:
                raise InputError("codes 必须指定 1～800 个股票代码")
            codes = raw_codes.split(",")
            if len(codes) > 800 or any(not code for code in codes):
                raise InputError("codes 必须指定 1～800 个股票代码")
            codes = [DS.to_symbol(code) for code in codes]
            s = ST.load_scan(snapshot or None)
            if not s:
                return self.send_json({"error": "扫描快照不存在"}, 404)
            result = TR.track_rows(s.get("rows", []), codes=codes, refresh=refresh == "1")
            result["scan_id"] = s.get("scan_id", snapshot)
            return self.send_json(result)

        if p == "/api/scan/status":
            with JOB_LOCK:
                j = dict(JOB)
            j["elapsed"] = round((j["finished"] or time.time()) - j["started"], 1) if j["started"] else 0
            return self.send_json(j)

        if p == "/api/stock":
            code = one("code").strip()
            if not code:
                return self.send_json({"error": "缺少 code"}, 400)
            freq = DS.validate_freq(one("freq", "day"))
            if one("report", "0") not in ("0", "1"):
                raise InputError("report 必须是 0 或 1")
            return self.send_json(stock_payload(
                code, freq, lookback=integer(one("lookback", "250"), "lookback", 30, 5000),
                with_report=one("report") == "1"))

        if p == "/api/fundamentals":
            code = one("code").strip()
            if not code:
                raise InputError("缺少 code")
            refresh = one("refresh", "0")
            if refresh not in ("0", "1"):
                raise InputError("refresh 必须是 0 或 1")
            return self.send_json(FA.fundamental_payload(code, refresh=refresh == "1"))

        if p == "/api/industry":
            mp = industry_lookup()
            cnt = {}
            for v in mp.values():
                cnt[v["l1"]] = cnt.get(v["l1"], 0) + 1
            return self.send_json({"count": len(mp),
                                   "l1": sorted(cnt.items(), key=lambda x: -x[1])})

        return self.send_json({"error": "未知接口 %s" % p}, 404)

    # ---- 静态
    def static(self, p):
        try:
            root = Path(WEB).resolve()
            rel = unquote(p).replace("\\", "/").lstrip("/") or "index.html"
            full = (root / rel).resolve()
            if full.is_dir():
                full = (full / "index.html").resolve()
            if not full.is_relative_to(root):
                raise ValueError("outside static root")
        except (OSError, ValueError, RuntimeError):
            self.send_error(403, "Forbidden")
            return
        self.send_file(full)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    url = "http://%s:%d/" % (a.host, a.port)
    print("缠论全市场扫描已启动 → %s" % url)
    s = ST.load_scan()
    print("  扫描数据: %s" % (("%s（命中 %d / %d）" % (s["generated"], s["hit"], s["total_scanned"]))
                              if s else "无 —— 打开页面后点「重新扫描」"))
    if not a.no_browser:
        threading.Timer(0.6, lambda: __import__("webbrowser").open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
