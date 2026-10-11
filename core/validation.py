"""Shared validation for CLI, HTTP and cache keys."""
import re


class InputError(ValueError):
    """Invalid caller input, safe to return as HTTP 400."""


def integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not re.fullmatch(r"-?[0-9]+", str(value)):
        raise InputError("%s 必须是整数" % name)
    n = int(value)
    if not minimum <= n <= maximum:
        raise InputError("%s 必须在 %d～%d 之间" % (name, minimum, maximum))
    return n


def market_prefix(code):
    c = str(code).strip().lower()
    if re.fullmatch(r"(?:sh|sz|bj)[0-9]{6}|hk[0-9]{5}", c):
        return c[:2]
    if re.fullmatch(r"[0-9]{5}", c):
        return "hk"
    if not re.fullmatch(r"[0-9]{6}", c):
        raise InputError("代码需为 6 位数字或 sh/sz/bj + 6 位数字（港股 hk + 5 位数字）")
    if c.startswith(("6", "5")):
        return "sh"
    if c.startswith(("0", "3", "1")):
        return "sz"
    return "bj"


def to_symbol(code):
    """Preserve an explicit exchange; bare codes use the stock/ETF convention."""
    c = str(code).strip().lower()
    prefix = market_prefix(c)
    return c if c.startswith(prefix) else prefix + c
