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
import re
import stat
import tempfile
import threading
import time
from datetime import datetime

from . import CACHE
from .validation import InputError, to_symbol

LOG = logging.getLogger(__name__)

KLINE_DIR = os.path.join(CACHE, "kline")
SCAN_DIR = os.path.join(CACHE, "scan")
_SCAN_LOCK = threading.RLock()
_SCAN_ID = re.compile(r"[0-9]{8}-[0-9]{6}(?:-[0-9]{3,9})?")
_SCAN_METADATA_CACHE = {}
_SCAN_METADATA_DIRECTORY = None

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
def _validate_scan_id(stamp):
    if not isinstance(stamp, str) or not _SCAN_ID.fullmatch(stamp):
        raise InputError("无效的扫描记录 ID")
    try:
        datetime.strptime(stamp[:15], "%Y%m%d-%H%M%S")
    except ValueError as exc:
        raise InputError("无效的扫描记录日期") from exc
    return stamp


def scan_path(stamp=None):
    name = "latest.json" if stamp is None else _validate_scan_id(stamp) + ".json"
    return os.path.join(SCAN_DIR, name)


def _generated_stamp(generated):
    try:
        parsed = datetime.strptime(generated, "%Y-%m-%d %H:%M:%S")
        if parsed.strftime("%Y-%m-%d %H:%M:%S") != generated:
            return None
        return parsed.strftime("%Y%m%d-%H%M%S")
    except (TypeError, ValueError):
        return None


def _unique_scan_id(base):
    """A second scan in the same second must not replace the first result."""
    base = _validate_scan_id(base)[:15]
    stamp, suffix = base, 0
    while os.path.lexists(scan_path(stamp)):
        suffix += 1
        stamp = "%s-%03d" % (base, suffix)
    return stamp


def _is_scan(payload):
    return (isinstance(payload, dict) and isinstance(payload.get("rows"), list)
            and all(isinstance(row, dict) for row in payload["rows"]))


def _snapshot_names():
    try:
        with os.scandir(SCAN_DIR) as entries:
            names = []
            for entry in entries:
                if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                    continue
                stamp = entry.name[:-5]
                try:
                    _validate_scan_id(stamp)
                except InputError:
                    continue
                names.append(stamp)
            return names
    except FileNotFoundError:
        return []


def _snapshot(stamp):
    path = scan_path(stamp)
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    payload = read_json(path)
    return dict(payload, scan_id=stamp) if _is_scan(payload) else None


def _historical_latest(require_archive=False):
    """Keep a legacy/orphan latest readable and archive it before it is replaced."""
    payload = read_json(scan_path())
    if not _is_scan(payload):
        return None
    stamp = payload.get("scan_id")
    try:
        _validate_scan_id(stamp)
    except InputError:
        stamp = _generated_stamp(payload.get("generated"))
        if stamp is None:
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(scan_path())))
    existing = _snapshot(stamp)
    expected = dict(payload, scan_id=stamp)
    if existing == expected:
        return expected
    # Legacy latest has no stored ID; find a prior migration of a same-second conflict.
    for candidate in sorted(_snapshot_names(), key=_scan_order, reverse=True):
        if candidate[:15] == stamp[:15]:
            expected = dict(payload, scan_id=candidate)
            if _snapshot(candidate) == expected:
                return expected
    stamp = _unique_scan_id(stamp)
    archived = dict(payload, scan_id=stamp)
    try:
        write_json(scan_path(stamp), archived)
    except OSError:
        # A full/read-only disk must not make the last published result disappear.
        LOG.warning("归档已有扫描失败，保留 latest 读取兼容", exc_info=True)
        if require_archive:
            raise
    return archived


def save_scan(payload, keep_snapshots=None):
    """保存独立历史记录后发布 latest；默认长期保留，可显式限制历史数量。"""
    if keep_snapshots is not None and (isinstance(keep_snapshots, bool)
                                      or not isinstance(keep_snapshots, int) or keep_snapshots < 1):
        raise InputError("保留的扫描记录数量必须是正整数")
    if not _is_scan(payload):
        raise InputError("扫描结果缺少有效的个股列表")
    base = _generated_stamp(payload.get("generated"))
    if base is None:
        if payload.get("generated"):
            raise InputError("扫描生成时间格式无效")
        base = time.strftime("%Y%m%d-%H%M%S")
    with _SCAN_LOCK:
        _historical_latest(require_archive=True)
        stamp = _unique_scan_id(base)
        saved = dict(payload, scan_id=stamp)
        snapshot_path = scan_path(stamp)
        write_json(snapshot_path, saved)
        # Publish last: failure to create a snapshot must preserve the prior result.
        try:
            write_json(scan_path(), saved)
        except Exception:
            # The ID belongs only to this attempted save; no previous history is removed.
            try:
                os.unlink(snapshot_path)
            except OSError:
                LOG.warning("发布失败后清理本次扫描快照失败: %s", snapshot_path, exc_info=True)
            raise
        payload["scan_id"] = stamp
        if keep_snapshots is None:
            return
        try:
            _prune_snapshots(keep_snapshots)
        except OSError:
            LOG.warning("清理旧快照失败（榜单已保存）", exc_info=True)


def _prune_snapshots(keep_snapshots):
    snaps = sorted(_snapshot_names(), key=_scan_order)
    for old in snaps[:-keep_snapshots]:
        try:
            os.remove(scan_path(old))
        except OSError:
            LOG.warning("清理旧快照失败: %s", old, exc_info=True)


def _scan_order(stamp):
    return stamp[:15], int(stamp[16:]) if len(stamp) > 15 else 0


def load_scan(stamp=None):
    """读取最新或指定的历史扫描；未知记录为 None，非法 ID 抛 InputError。"""
    if stamp is not None:
        _validate_scan_id(stamp)
    with _SCAN_LOCK:
        if stamp is not None:
            saved = _snapshot(stamp)
            if saved is not None:
                return saved
        latest = _historical_latest()
        if stamp is None or (latest is not None and latest["scan_id"] == stamp):
            return latest
        return None


def list_scans():
    """按执行时间倒序列出成功保存的扫描，不包含失败诊断和损坏文件。"""
    global _SCAN_METADATA_DIRECTORY
    with _SCAN_LOCK:
        directory = os.path.abspath(SCAN_DIR)
        if directory != _SCAN_METADATA_DIRECTORY:
            _SCAN_METADATA_CACHE.clear()
            _SCAN_METADATA_DIRECTORY = directory
        latest = _historical_latest()
        stamps = _snapshot_names()
        present = {os.path.abspath(scan_path(stamp)) for stamp in stamps}
        for removed in set(_SCAN_METADATA_CACHE) - present:
            del _SCAN_METADATA_CACHE[removed]
        scans = {}
        for stamp in stamps:
            metadata = _snapshot_metadata(stamp)
            if metadata is not None:
                scans[stamp] = metadata
        if latest is not None:
            scans[latest["scan_id"]] = _scan_metadata(latest["scan_id"], latest)
        return [scans[stamp] for stamp in sorted(scans, key=_scan_order, reverse=True)]


def _snapshot_metadata(stamp):
    """Cache small summaries only; archive rows never remain in the catalog cache."""
    path = os.path.abspath(scan_path(stamp))
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        _SCAN_METADATA_CACHE.pop(path, None)
        return None
    if not stat.S_ISREG(info.st_mode):
        _SCAN_METADATA_CACHE.pop(path, None)
        return None
    version = (info.st_mtime_ns, info.st_size, info.st_ctime_ns, info.st_ino)
    cached = _SCAN_METADATA_CACHE.get(path)
    if cached is not None and cached[0] == version:
        return dict(cached[1]) if cached[1] is not None else None
    payload = _snapshot(stamp)
    metadata = _scan_metadata(stamp, payload) if payload is not None else None
    # Negative entries prevent repeatedly parsing an unchanged broken file.
    _SCAN_METADATA_CACHE[path] = (version, metadata)
    return dict(metadata) if metadata is not None else None


def _scan_metadata(stamp, payload):
    generated = payload.get("generated")
    if _generated_stamp(generated) is None:
        generated = datetime.strptime(stamp[:15], "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    metadata = {"id": stamp, "generated": generated, "date": generated[:10],
                "freq": payload.get("freq", "day"), "hit": payload.get("hit", len(payload["rows"])),
                "total_scanned": payload.get("total_scanned", 0)}
    for field in ("analysis_version", "sell_rules_version"):
        if isinstance(payload.get(field), str) and payload[field]:
            metadata[field] = payload[field]
    return metadata


def save_scan_attempt(payload):
    """Failure/coverage diagnostics never replace the last published scan."""
    write_json(os.path.join(SCAN_DIR, "attempt.json"), payload)


def load_scan_attempt():
    return read_json(os.path.join(SCAN_DIR, "attempt.json"))
