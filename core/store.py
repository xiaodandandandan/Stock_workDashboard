# -*- coding: utf-8 -*-
"""本地缓存层：K 线落盘 + 扫描结果快照。

**为什么日 K 要落盘**：扫描时数据本来就在内存里，顺手写下来是 0 次网络往返；
而点开个股是个「点了就该有」的动作 —— 重取 0.26 s 单看不慢，但它压在交互路径上，
感官差别很大。缓存之后，榜单里每一只票的日线都是立刻可画的。

**存储用列式**（d/o/h/l/c/v 六个数组，而不是 800 个 {open,high,...} 对象）：
JSON 里重复的键名占了近一半体积，实测同样 500 根从 ~60 KB 降到 ~20 KB。
"""
import json
import os
import time

from . import CACHE

KLINE_DIR = os.path.join(CACHE, "kline")
SCAN_DIR = os.path.join(CACHE, "scan")

# 各类数据的保鲜期（秒）。日线在盘后才变，给半天；分钟线盘中一直在动，给 10 分钟。
TTL = {"day": 6 * 3600, "m60": 600, "m30": 600, "m15": 600, "m5": 600}


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:                                           # noqa: BLE001
        return default


def write_json(path, obj):
    """原子写：先落 .tmp 再 os.replace。

    ★ 服务是多线程的（ThreadingHTTPServer），扫描线程在写、请求线程在读 ——
      直接 open(w) 会让读到半截 JSON（表现为「服务突然报解析错误」，
      而且重试就好，看着像偶发网络问题，极难定位）。os.replace 是原子的。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------- K 线
def kline_path(freq, code):
    return os.path.join(KLINE_DIR, freq, "%s.json" % code)


def save_kline(freq, code, bars):
    write_json(kline_path(freq, code),
               {"saved": int(time.time()), "freq": freq, "code": code,
                "n": len(bars),
                "d": [int(b["date"]) for b in bars],
                "o": [round(b["open"], 2) for b in bars],
                "h": [round(b["high"], 2) for b in bars],
                "l": [round(b["low"], 2) for b in bars],
                "c": [round(b["close"], 2) for b in bars],
                "v": [int(b["volume"]) for b in bars]})


def load_kline(freq, code, max_age=None):
    """读缓存的 K 线；不存在或过期返回 None（调用方负责回源）。"""
    p = kline_path(freq, code)
    if not os.path.exists(p):
        return None
    if max_age is None:
        max_age = TTL.get(freq, 600)
    if time.time() - os.path.getmtime(p) > max_age:
        return None
    o = read_json(p)
    if not o or not o.get("d"):
        return None
    d, op, hi, lo, cl, v = o["d"], o["o"], o["h"], o["l"], o["c"], o["v"]
    return [{"date": d[i], "open": op[i], "high": hi[i], "low": lo[i],
             "close": cl[i], "volume": v[i]} for i in range(len(d))]


# ---------------------------------------------------------------- 扫描结果
def scan_path(stamp=None):
    return os.path.join(SCAN_DIR, (stamp + ".json") if stamp else "latest.json")


def save_scan(payload, keep_snapshots=8):
    """写 latest.json + 一份带日期的快照；快照只留最近 N 份。"""
    write_json(scan_path(), payload)
    stamp = str(payload.get("generated", "")).replace("-", "").replace(":", "").replace(" ", "-")
    if stamp:
        write_json(scan_path(stamp), payload)
        snaps = sorted(f for f in os.listdir(SCAN_DIR)
                       if f.endswith(".json") and f != "latest.json")
        for old in snaps[:-keep_snapshots]:
            try:
                os.remove(os.path.join(SCAN_DIR, old))
            except OSError:
                pass


def load_scan():
    return read_json(scan_path())
