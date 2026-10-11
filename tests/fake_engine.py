"""Deterministic contract double, NOT an implementation of Chan analysis.

Only tests explicitly install this module. Production never imports it.
"""
import sys
import types


def install():
    k = types.ModuleType("chanlun_core")
    k.__version__ = "test-contract"
    for name in ("merge_bars", "find_fenxing", "find_bi", "find_zhongshu", "detect", "scan_signals"):
        setattr(k, name, lambda *a, **kw: [])
    k.macd = lambda *a: ([], [], [])
    k.area = lambda *a: 0
    k._why = lambda key, value, need, ok: dict(k=key, v=value, need=need, ok=ok)
    k._ds = str
    k.RECENT_BARS, k.MIN_REBOUND, k.MIN_PULLBACK, k.MAX_DIST_ZG = 60, .03, .05, .3
    k.structure = lambda bars: dict(zs=[], bi=[], fx=[])

    def pack(bars, lookback=250):
        win = bars[-lookback:]
        return dict(n=len(win), d0=str(win[0]["date"]),
                    **{key: [round(b[field] * scale) for b in win]
                       for key, field, scale in (("o", "open", 100), ("h", "high", 100),
                                                  ("l", "low", 100), ("c", "close", 100), ("v", "volume", 1))},
                    fx=[], bi=[], zs=[])
    k.pack = pack
    sys.modules["chanlun_core"] = k
    return k
