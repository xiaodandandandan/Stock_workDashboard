# -*- coding: utf-8 -*-
"""本地缓存层：K 线落盘 + 扫描结果快照。

**为什么日 K 要落盘**：扫描时数据本来就在内存里，顺手写下来是 0 次网络往返；
而点开个股是个「点了就该有」的动作 —— 重取 0.26 s 单看不慢，但它压在交互路径上，
感官差别很大。缓存之后，榜单里每一只票的日线都是立刻可画的。

**存储用列式**（d/o/h/l/c/v 六个数组，而不是 800 个 {open,high,...} 对象）：
JSON 里重复的键名占了近一半体积，实测同样 500 根从 ~60 KB 降到 ~20 KB。
"""
import json
import hashlib
import logging
import os
import tempfile
import time

from . import CACHE
from .validation import InputError, to_symbol

LOG = logging.getLogger(__name__)

KLINE_DIR = os.path.join(CACHE, "kline")
SCAN_DIR = os.path.join(CACHE, "scan")

# Cache lifetime in seconds; both daily and intraday bars can change during trading.
TTL = {"day": 6 * 3600, "m60": 600, "m30": 600, "m15": 600, "m5": 600}


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        LOG.warning("读取缓存失败: %s", path, exc_info=True)
        return default


def write_json(path, obj):
    """原子写：先落 .tmp 再 os.replace。

    ★ 服务是多线程的（ThreadingHTTPServer），扫描线程在写、请求线程在读 ——
      直接 open(w) 会让读到半截 JSON（表现为「服务突然报解析错误」，
      而且重试就好，看着像偶发网络问题，极难定位）。os.replace 是原子的。
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = None
    try:
        # Each writer owns its temporary file; readers see an entire old/new file.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".cache-", suffix=".tmp", delete=False) as fp:
            tmp = fp.name
            json.dump(obj, fp, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            fp.flush()
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- K 线
def kline_path(freq, code):
    if freq not in TTL:
        raise InputError("未知周期 %s" % freq)
    return os.path.join(KLINE_DIR, freq, "%s.json" % to_symbol(code))


def bars_version(bars):
    """Includes every bar, so corrections and window length changes invalidate derivatives."""
    data = json.dumps(bars, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(data).hexdigest()


def save_kline(freq, code, bars):
    symbol = to_symbol(code)
    write_json(kline_path(freq, code),
               {"schema": 2, "saved": int(time.time()), "freq": freq, "symbol": symbol,
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
    try:
        modified = os.path.getmtime(p)
    except FileNotFoundError:
        return None
    if max_age is None:
        max_age = TTL.get(freq, 600)
    if time.time() - modified > max_age:
        return None
    o = read_json(p)
    if not o or o.get("schema") != 2 or o.get("symbol") != to_symbol(code) or not o.get("d"):
        return None
    try:
        d, op, hi, lo, cl, v = (o[k] for k in ("d", "o", "h", "l", "c", "v"))
        if any(len(a) != len(d) for a in (op, hi, lo, cl, v)):
            raise ValueError("K 线列长度不一致")
        return [{"date": d[i], "open": op[i], "high": hi[i], "low": lo[i],
                 "close": cl[i], "volume": v[i]} for i in range(len(d))]
    except (KeyError, TypeError, ValueError):
        LOG.warning("K 线缓存损坏: %s", p, exc_info=True)
        return None


# ---------------------------------------------------------------- 扫描结果
def scan_path(stamp=None):
    return os.path.join(SCAN_DIR, (stamp + ".json") if stamp else "latest.json")


def save_scan(payload, keep_snapshots=8):
    """写 latest.json + 一份带日期的快照；快照只留最近 N 份。"""
    stamp = str(payload.get("generated", "")).replace("-", "").replace(":", "").replace(" ", "-")
    if stamp:
        write_json(scan_path(stamp), payload)
    # Publish last: failure to create a snapshot must not replace the prior result.
    write_json(scan_path(), payload)
    if stamp:
        try:
            _prune_snapshots(keep_snapshots)
        except OSError:
            LOG.warning("清理旧快照失败（榜单已保存）", exc_info=True)


def _prune_snapshots(keep_snapshots):
    snaps = sorted(f for f in os.listdir(SCAN_DIR)
                   if f.endswith(".json") and f not in ("latest.json", "attempt.json"))
    for old in snaps[:-keep_snapshots]:
        try:
            os.remove(os.path.join(SCAN_DIR, old))
        except OSError:
            LOG.warning("清理旧快照失败: %s", old, exc_info=True)


def load_scan():
    return read_json(scan_path())


def save_scan_attempt(payload):
    """Failure/coverage diagnostics never replace the last published scan."""
    write_json(os.path.join(SCAN_DIR, "attempt.json"), payload)


def load_scan_attempt():
    return read_json(os.path.join(SCAN_DIR, "attempt.json"))
