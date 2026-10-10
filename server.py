#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""缠论工作台 —— 本地服务。

**为什么必须是服务**：「点开个股后再算 60 / 30 分钟」意味着浏览器要能实时向后端要数据。
静态 HTML 用 file:// 打开时 fetch 会被同源策略挡掉，做不到按需计算。
所以工作台从「一个 HTML 文件」变成「一个本地服务 + 一组 API」。

端点:
    GET  /                        工作台页面（web/index.html）
    GET  /<静态资源>               web/ 下的 css / js / vendor
    GET  /api/health               服务与数据状态
    GET  /api/scan                 最近一次扫描结果
    POST /api/scan                 触发一次全市场扫描（后台跑，立即返回）
    GET  /api/scan/status          扫描进度
    GET  /api/stock?code=&freq=    单票：日 K 走缓存秒回，分钟线现取现算

零第三方依赖，只用标准库。
"""
import argparse
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import WEB, CACHE                              # noqa: E402
from core import datasource as DS                        # noqa: E402
from core import engine as E                             # noqa: E402
from core import store as ST                             # noqa: E402

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

# 「历史信号段」是切片复算的结果（约 0.9 s / 票）。同一只票同周期在同一天内结果不变，
# 按 (freq, code, 最后一根日期) 缓存 —— 第二次点开同一只票就是 0 成本。
_HIST = {}
_HIST_LOCK = threading.Lock()


def cached_history(freq, code, bars):
    key = (freq, code, str(bars[-1]["date"]))
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
                    _IND = DS.industry_map()
                except Exception:                            # noqa: BLE001
                    _IND = {}
    return _IND


def scan_meta():
    """从最近一次扫描结果里取 code → 行 的索引（名称 / 行业 / 成交额）。"""
    d = ST.load_scan() or {}
    return {r["code"]: r for r in d.get("rows", [])}, d


# ---------------------------------------------------------------- 单票
def stock_payload(code, freq="day", lookback=250, with_report=False):
    """单票数据。日 K 优先吃扫描时的缓存（秒回），分钟线现取现算。"""
    t0 = time.perf_counter()
    sym = DS.to_symbol(code)
    code = sym[2:] if sym[:2] in ("sh", "sz", "bj") else code

    cached = True
    bars = ST.load_kline(freq, code)
    if not bars:
        cached = False
        bars = DS.sina_kline(sym, freq, 800)
        try:
            ST.save_kline(freq, code, bars)
        except Exception:                                    # noqa: BLE001
            pass
    if len(bars) < 30:
        raise RuntimeError("K 线不足（%d 根），无法做结构分析" % len(bars))

    # 抽屉要展示该级别历史上全部信号段（含已失效）并显示「存活 N 根 / 期内 ±x%」——
    # 只能切片复算，结果按天缓存。榜单（scan.py）不走这条路：那边每只票都算的话
    # 全市场要多花 80 分钟。
    hist = cached_history(freq, code, bars)
    a = E.analyze(bars, unit=DS.UNIT.get(freq, freq), lookback=lookback,
                  with_report=with_report, history=hist)
    if not a:
        raise RuntimeError("K 线不足，无法打包结构")

    rows, scand = scan_meta()
    row = rows.get(code) or {}
    name = row.get("name") or DS.quote_name(code)
    ind = row.get("industry")
    ind_full = row.get("industry_full")
    if not ind:
        m = industry_lookup().get(code) or {}
        ind, ind_full = m.get("l1", "未分类"), m.get("full", "未分类")

    last = bars[-1]
    a.update({
        "code": code, "symbol": sym, "name": name,
        "industry": ind or "未分类", "industry_full": ind_full or "未分类",
        "amt": round(last["close"] * last["volume"] / 1e8, 3),
        "scan_at": scand.get("generated", ""),
        "in_scan": code in rows,
        "cached": cached,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    })
    return a


# ---------------------------------------------------------------- 扫描任务
def start_scan(limit=0, workers=20, freq="day", count=800, lookback=5):
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
            import scan as SC
            p = SC.run_scan(limit=limit, freq=freq, count=count, lookback=lookback,
                            workers=workers, progress=progress)
            with JOB_LOCK:
                JOB.update({"result": {"hit": p["hit"], "total": p["total_scanned"],
                                       "elapsed": p["elapsed"],
                                       "generated": p["generated"]}})
        except Exception as e:                               # noqa: BLE001
            with JOB_LOCK:
                JOB["error"] = "%s: %s" % (type(e).__name__, e)
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

    def log_message(self, fmt, *args):
        pass                                                 # 静音：默认每请求一行太吵

    # ---- 工具
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
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
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- 路由
    def do_GET(self):
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        try:
            if p.startswith("/api/"):
                return self.api_get(p, qs)
            return self.static(p)
        except Exception as e:                               # noqa: BLE001
            traceback.print_exc()
            self.send_json({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/scan":
            n = int(self.headers.get("Content-Length") or 0)
            body = {}
            if n:
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                except Exception:                            # noqa: BLE001
                    body = {}
            started = start_scan(limit=int(body.get("limit") or 0),
                                 workers=int(body.get("workers") or 20),
                                 freq=body.get("freq") or "day")
            return self.send_json({"started": started,
                                   "reason": "" if started else "已有扫描在跑"})
        self.send_error(404, "Not Found")

    # ---- API
    def api_get(self, p, qs):
        def one(k, d=""):
            return (qs.get(k) or [d])[0]

        if p == "/api/health":
            s = ST.load_scan()
            return self.send_json({
                "ok": True, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "scan": {"generated": (s or {}).get("generated", ""),
                         "hit": (s or {}).get("hit", 0),
                         "total": (s or {}).get("total_scanned", 0),
                         "elapsed": (s or {}).get("elapsed", 0)} if s else None,
                "cached_kline": sum(len(f) for _, _, f in os.walk(os.path.join(CACHE, "kline"))),
                "job": {k: v for k, v in JOB.items() if k != "result"},
            })

        if p == "/api/scan":
            s = ST.load_scan()
            if not s:
                return self.send_json({"error": "还没有扫描结果，先 POST /api/scan"}, 404)
            return self.send_json(s)

        if p == "/api/scan/status":
            with JOB_LOCK:
                j = dict(JOB)
            j["elapsed"] = round((j["finished"] or time.time()) - j["started"], 1) if j["started"] else 0
            return self.send_json(j)

        if p == "/api/stock":
            code = one("code").strip()
            if not code:
                return self.send_json({"error": "缺少 code"}, 400)
            freq = one("freq", "day")
            if freq not in DS.SCALE:
                return self.send_json({"error": "未知周期 %s" % freq}, 400)
            return self.send_json(stock_payload(
                code, freq, lookback=int(one("lookback", "250") or 250),
                with_report=one("report") == "1"))

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
        rel = p.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(WEB, rel))
        if not full.startswith(os.path.normpath(WEB)):       # 防目录穿越
            self.send_error(403, "Forbidden")
            return
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        self.send_file(full)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    url = "http://%s:%d/" % (a.host, a.port)
    print("缠论工作台已启动 → %s" % url)
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
