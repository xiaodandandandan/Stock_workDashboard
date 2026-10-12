"""Track immutable scan rows against current quotes without replaying the engine."""
import math
import re
import threading
import time
from datetime import datetime

from . import datasource
from .validation import InputError

SOURCE = "tencent.qt.gtimg.cn"
CACHE_TTL = 60
_QUOTES = {}
_LOCK = threading.Lock()


def _number(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and (not positive or number > 0) else None


def _symbol(value):
    symbol = str(value).strip().lower()
    if not re.fullmatch(r"(?:sh|sz|bj)[0-9]{6}", symbol):
        raise InputError("跟踪代码需为 sh/sz/bj + 6 位数字")
    return symbol


def _cache_day():
    return datetime.now().date().isoformat()


def _change(price, baseline):
    if baseline is None:
        return None, None
    percent = (price / baseline - 1) * 100
    if not math.isfinite(percent):
        return None, None
    return round(price - baseline, 4), round(percent, 2)


def track_rows(rows, codes=None, refresh=False):
    """Daily moves use the quote's previous close; scan moves use snapshot close.

    Only raw price/date/previous_close are cached for 60 seconds in memory, never
    across a new calendar day. A historical row need not remain in the latest scan.
    Each call recomputes both moves and stop checks; rows are never edited.
    change/change_pct remain scan-move aliases for compatibility.
    """
    if codes is not None and isinstance(codes, (str, bytes)):
        raise InputError("跟踪代码需为交易所标识数组")
    selected = {_symbol(code) for code in codes} if codes is not None else None
    by_symbol = {}
    for row in rows:
        if not isinstance(row, dict):
            raise InputError("扫描个股数据无效")
        symbol = _symbol(row.get("symbol", ""))
        if selected is None or symbol in selected:
            if symbol in by_symbol:
                raise InputError("扫描包含重复标的：%s" % symbol)
            by_symbol[symbol] = row

    quotes, errors, missing = {}, {}, []
    now = time.monotonic()
    cache_day = _cache_day()
    with _LOCK:
        # Expired quotes must not grow indefinitely across many scan dates.
        for symbol in list(_QUOTES):
            if now - _QUOTES[symbol][0] >= CACHE_TTL or _QUOTES[symbol][2] != cache_day:
                del _QUOTES[symbol]
        for symbol in by_symbol:
            cached = _QUOTES.get(symbol)
            if not refresh and cached is not None:
                quotes[symbol] = dict(cached[1])
            else:
                missing.append(symbol)
    if missing:
        try:
            fetched = datasource.quote_prices(missing)
            if (not isinstance(fetched, dict) or not isinstance(fetched.get("quotes"), dict)
                    or not isinstance(fetched.get("errors", {}), dict)):
                raise ValueError("当前报价响应无效")
            received = fetched["quotes"]
            errors.update({symbol: message for symbol, message in fetched.get("errors", {}).items()
                           if symbol in missing})
        except Exception as exc:
            received = {}
            errors.update({symbol: "%s: %s" % (type(exc).__name__, exc) for symbol in missing})
        fetched_at = time.monotonic()
        fetched_day = _cache_day()
        with _LOCK:
            for symbol in missing:
                quote = received.get(symbol)
                if not isinstance(quote, dict):
                    errors.setdefault(symbol, "当前报价缺失")
                    # Explicit refresh failures cannot fall back to an old price.
                    _QUOTES.pop(symbol, None)
                    continue
                price = _number(quote.get("price"), positive=True)
                date = quote.get("date")
                try:
                    if not isinstance(date, str) or not re.fullmatch(
                            r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", date):
                        raise ValueError("date")
                    datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    date = None
                if price is None or date is None:
                    errors[symbol] = "当前报价价格或时间无效"
                    _QUOTES.pop(symbol, None)
                    continue
                good = {"price": price, "date": date,
                        "previous_close": _number(quote.get("previous_close"), positive=True)}
                quotes[symbol] = good
                _QUOTES[symbol] = (fetched_at, dict(good), fetched_day)
                errors.pop(symbol, None)

    result = {}
    for symbol, row in by_symbol.items():
        quote = quotes.get(symbol)
        if quote is None:
            result[symbol] = {"price": None, "date": None, "previous_close": None,
                              "daily_change": None, "daily_change_pct": None,
                              "scan_change": None, "scan_change_pct": None,
                              "change": None, "change_pct": None, "status": "unavailable",
                              "daily_status": "unavailable", "scan_status": "unavailable",
                              "buy_stop_breached": None, "breached_types": []}
            continue
        price = quote["price"]
        baseline = _number(row.get("close"), positive=True)
        previous_close = quote["previous_close"]
        breached = []
        for signal in row.get("buy") or []:
            if not isinstance(signal, dict) or signal.get("valid") != 1:
                continue
            stop = _number(signal.get("stop"), positive=True)
            if stop is not None and price <= stop:
                kind = str(signal.get("type") or "买点")
                if kind not in breached:
                    breached.append(kind)
        daily_change, daily_change_pct = _change(price, previous_close)
        scan_change, scan_change_pct = _change(price, baseline)
        daily_status = "ok" if daily_change_pct is not None else "invalid_previous_close"
        scan_status = "ok" if scan_change_pct is not None else "invalid_baseline"
        problems = []
        if daily_status != "ok":
            problems.append("昨收价无效，无法计算当天涨跌")
        if scan_status != "ok":
            problems.append("扫描基准收盘价无效，无法计算扫描后至今涨跌")
        if problems:
            errors[symbol] = "；".join(problems)
        result[symbol] = {"price": price, "date": quote["date"], "previous_close": previous_close,
                          "daily_change": daily_change, "daily_change_pct": daily_change_pct,
                          "scan_change": scan_change, "scan_change_pct": scan_change_pct,
                          "change": scan_change, "change_pct": scan_change_pct,
                          "status": scan_status, "daily_status": daily_status,
                          "scan_status": scan_status,
                          "buy_stop_breached": bool(breached), "breached_types": breached}
    return {"quotes": result, "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "errors": errors, "source": SOURCE}
