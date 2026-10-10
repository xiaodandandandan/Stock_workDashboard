# -*- coding: utf-8 -*-
"""缠论工作台 —— 计算层。

分层（上层只依赖下层，不反向）：

    datasource   取数：新浪 K 线（日/分钟）、东财行业、腾讯报价
    engine       判定：结构打包 + 买卖点 + 新鲜度/失效
    store        本地缓存读写（cache/ 目录）

新增数据源、指标、形态都往这一层加，**不要塞进 server.py** ——
server 只做「路由 + JSON 序列化」，一旦把计算写进去，后面每加一个组件都要动服务。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "cache")
WEB = os.path.join(ROOT, "web")

# ★ 缠论引擎（chanlun_core.py）是纯计算、零第三方依赖，本机**引用技能里的那份**
#   而不是拷一份 —— 引擎的修正（笔 / 中枢 / 背驰规则）必须自动流到工作台；复制一份
#   必然慢慢分叉，最后「榜上的信号」和「技能算的信号」不是一个东西，排查成本极高。
#
#   查找顺序（前者命中就不用后者）：
#     1. ~/.workbuddy/skills/chanlun-kline/scripts   ← 本机常规位置（单一真相源）
#     2. $CHANLUN_ENGINE_DIR                          ← 换机器时指过去
#     3. core/_engine/                                ← 想独立运行就把引擎丢这里
SKILLS = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills")
CHANLUN_DIR = os.path.join(SKILLS, "chanlun-kline", "scripts")
BRIDGE_DIR = os.path.join(SKILLS, "astock-chanlun-bridge", "scripts")
ENGINE_DIR = os.environ.get("CHANLUN_ENGINE_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_engine")

for _p in (CHANLUN_DIR, ENGINE_DIR, BRIDGE_DIR):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


def engine_ready():
    """引擎是否可用。启动时探一次，好给出「缺哪个文件、该放哪」而不是 ImportError 栈。"""
    try:
        import chanlun_core  # noqa: F401
        return True, ""
    except ImportError as e:
        return False, str(e)


def engine_hint():
    """给用户的修复指引（只在引擎缺失时调用）。"""
    looked = "\n".join("    %s" % p for p in (CHANLUN_DIR, ENGINE_DIR))
    return (
        "找不到缠论引擎 chanlun_core.py，已查找：\n%s\n"
        "把它（或整个 chanlun-kline/scripts 目录）放进上面的任一目录，\n"
        "或设环境变量 CHANLUN_ENGINE_DIR 指向存放它的目录。" % looked
    )


def require_engine():
    """导入并返回 chanlun_core；缺失时抛带修复指引的 ImportError。

    直接 `import chanlun_core` 只会甩一句 ModuleNotFoundError，
    新机器上完全看不出该把文件放哪，所以这里把提示接上。
    """
    try:
        import chanlun_core
        return chanlun_core
    except ImportError as e:
        raise ImportError("%s\n（原始错误：%s）" % (engine_hint(), e)) from e
