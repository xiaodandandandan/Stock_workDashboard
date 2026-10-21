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
import hashlib
from pathlib import Path
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "cache")
WEB = os.path.join(ROOT, "web")

# Engine lookup: an explicit override, otherwise the bundled copy.
# Use CHANLUN_ENGINE_SHA256 to keep a reviewed version instead of silently following upgrades.
ENGINE_DIR = os.environ.get("CHANLUN_ENGINE_DIR")
BUNDLED_ENGINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_engine")
ENGINE_PATHS = [p for p in (ENGINE_DIR, BUNDLED_ENGINE_DIR) if p]

for _p in reversed(ENGINE_PATHS):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


def engine_ready():
    """引擎是否可用。启动时探一次，好给出「缺哪个文件、该放哪」而不是 ImportError 栈。"""
    try:
        require_engine()
        return True, ""
    except (ImportError, RuntimeError) as e:
        return False, str(e)


def engine_hint():
    """给用户的修复指引（只在引擎缺失时调用）。"""
    looked = "\n".join("    %s" % p for p in ENGINE_PATHS)
    return (
        "找不到缠论引擎 chanlun_core.py，已查找：\n%s\n"
        "请恢复项目 core/_engine/ 内的引擎文件，\n"
        "或设环境变量 CHANLUN_ENGINE_DIR 指向存放它的目录。" % looked
    )


@lru_cache(maxsize=8)
def engine_identity(module):
    path = getattr(module, "__file__", None)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else ""
    return {"version": str(getattr(module, "__version__", "unversioned")), "sha256": digest}


def require_engine():
    """导入并返回 chanlun_core；缺失时抛带修复指引的 ImportError。

    直接 `import chanlun_core` 只会甩一句 ModuleNotFoundError，
    新机器上完全看不出该把文件放哪，所以这里把提示接上。
    """
    try:
        import chanlun_core
        identity = engine_identity(chanlun_core)
        expected = os.environ.get("CHANLUN_ENGINE_SHA256")
        if expected and identity["sha256"] != expected.lower():
            raise RuntimeError("缠论引擎与 CHANLUN_ENGINE_SHA256 不匹配，请检查引擎版本")
        return chanlun_core
    except ImportError as e:
        raise ImportError("%s\n（原始错误：%s）" % (engine_hint(), e)) from e
