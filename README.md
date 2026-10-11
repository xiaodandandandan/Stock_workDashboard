# Stock_workDashboard · 缠论全市场工作台

A 股全市场缠论扫描 → 榜单 → 点开单票看结构。运行层使用 Python 标准库 + 原生 Canvas，
本地起一个 HTTP 服务，浏览器里就是一个完整的工作台。

**为什么是服务而不是一个 HTML 文件**：要求「点开个股后再现算 60 / 30 分钟」，
浏览器就得能实时向后端要数据；`file://` 下 `fetch` 会被同源策略挡掉。
所以拆成「本地服务 + 一组 API + 单页前端」。

---

## 快速开始

需要 Python 3.10+，并先按下方「依赖说明」配置外部缠论引擎。运行网页不需要 Node；
Node 20+ 和 TypeScript 仅用于开发检查。

```bash
python 启动.py                 # 起服务并打开浏览器，默认 127.0.0.1:8765
python 启动.py --scan          # 起来后自动跑一次全市场扫描
python 启动.py --port 9000
```

只想命令行跑扫描：

```bash
python scan.py                 # 全市场（沪深 A）
python scan.py --limit 300     # 只扫前 300 只，试跑
python scan.py --workers 20    # 并发数（默认 20）
python scan.py --count 800     # 每只取多少根日 K（默认 800）
```

首次打开时没有榜单数据，点页面右上角「重新扫描」或直接 `python 启动.py --scan`。
全市场约 5500 只、并发 20 大致 **8 分钟**（受行情源限速影响）。

---

## 分层

```
数据   core/datasource.py   新浪 K 线/列表 · 东财行业 · 腾讯报价
       core/store.py        本地缓存读写（cache/，原子写）
计算   core/engine.py       结构打包 + 买卖点 + 新鲜度/失效判定
       core/sell.py         卖点（买点引擎的镜像）
服务   server.py            路由 + 扫描任务状态 + JSON 序列化（不写计算）
       scan.py              全市场扫描（也可被 server 当函数调用）
前端   web/index.html       单页骨架
       web/js/api.js        接口封装
       web/js/state.js      请求代次、取消、symbol 规范化、笔端点切片
       web/js/types.d.ts    API / 信号 / K 线 / 核心状态的类型约定
       web/js/app.js        榜单、类型 tab 筛选、排序
       web/js/drawer.js     右侧抽屉：K 线 + 逐信号卡片
       web/js/zoom.js       全屏放大视图（滚轮缩放 / 拖动平移 / 区间预设）
       web/vendor/kline_canvas.js   Canvas 绘图层（笔 / 分型 / 中枢 / 买卖点 / 均线 / MACD）
```

**上层只依赖下层，不允许反向**。`server.py` 里一旦出现计算逻辑，
后面每加一个组件都要动服务 —— 新增东西按下表放：

| 想加什么 | 加在哪里 |
|---|---|
| 新数据源（另一个行情源、财报、公告） | `core/datasource.py` |
| 新指标 / 形态 / 信号 | `core/engine.py`（榜单和抽屉会同时拿到，口径自动一致） |
| 新界面模块 | `web/js/` 新建文件 + `server.py` 加路由；别往 `index.html` 堆内联脚本 |
| 新缓存类型 | `core/store.py` |

---

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 工作台页面 |
| GET | `/<静态资源>` | `web/` 下的 css / js / vendor |
| GET | `/api/health` | 服务与数据状态 |
| GET | `/api/scan` | 最近一次扫描结果 |
| POST | `/api/scan` | 触发全市场扫描（后台跑，立即返回） |
| GET | `/api/scan/status` | 扫描进度 |
| GET | `/api/stock?code=&freq=day\|m60\|m30` | 单票结构 + 信号 |
| GET | `/api/industry` | 行业映射 |

日 K 在扫描时已落盘（命中的票），点开秒回；60 / 30 分钟现取现算。

`code` 支持裸股票代码及完整标识（例如 `sz000001` / `sh000001`）；服务端、缓存和前端
统一使用完整标识，返回值中的 `code` 仅用于显示。单票响应还包含 `freq`、`data_version`、
`engine`（版本与源码 SHA-256）和 `source`（源数据口径）。行情沿用数据源默认口径，
本项目没有额外进行复权处理；成交额按收盘价乘成交量估算，界面明确标为「估算成交额」。

POST 扫描参数：`limit` 0～10000、`workers` 1～32、`count` 120～5000、`lookback` 0～250、
`freq` 为 `day/m60/m30/m15/m5`。单票 `lookback` 为 30～5000，`report` 为 0 或 1。
非法输入返回 HTTP 400，扫描请求体上限 8 KiB。

---

## 几个刻意的取舍

**先探测再回放。** 全市场扫描只算最后一根，没有信号的票直接跳过新鲜度回放，
省掉 ≥ 80% 的计算量；**命中才落盘日 K**，没命中的票榜单里看不到，存了也是死数据。

**信号带「首次报出」和「失效」两套状态。** `first_i` 是这根信号第一次被报出的下标，
不是信号日；`alive` 是持续报出的跨度，`ret` 是这段跨度内的价格涨跌。
历史卡片的 `live` 表示引擎是否一直报出到最后一根，界面称「引擎持续报出 / 已停止报出」。
榜单的 `valid` 则检查现价是否越过失效位。这是两种不同条件，不能把历史 `live` 直接当成
价格层面的有效性或策略收益。历史卡片按绘图窗口展示，窗口外的信号给出数量提示。

**切片复算只发生在抽屉里。** 榜单用快版扫描，抽屉点开时才做完整回放，
按 `(周期, 完整 symbol, 全部 K 线内容摘要)` 缓存。相同数据复用回放结果；盘中 OHLC、
历史数据或输入长度变化会重新计算，不会让新图表使用旧历史信号。

**失败扫描不覆盖正常榜单。** 列表任一分页失败、列表为空、达到分页上限但仍有满页数据、
或列表包含重复标的时，扫描失败。单票获取/计算成功率低于 95% 时同样不发布结果。
成功率达到 95% 但仍有失败时，响应 `complete=false`，界面显示失败数量。
诊断写入 `cache/scan/attempt.json`，可通过 `/api/health` 的 `last_attempt` 查看；
无信号、K 线不足、取数失败与计算失败分别统计。成功分析但没有信号仍可发布空榜单。

**缓存升级。** K 线缓存使用 schema 2 和 `day/sh600000.json` 这样的完整标识文件名。
旧的 `day/600000.json` 不再读取，也不猜测迁移其身份；下次扫描/打开时自动重新获取。
已有扫描榜单仍可读取。并发写入各自创建临时文件，成功后原子替换目标。

**放大视图是浮层，不是把抽屉画布画大。** 抽屉最宽 60vw，250 根挤在 1000px 里
一根不到 4px，笔的折点、中枢边界全糊在一起。放大要的是能缩能移。

**绘图层有项目内修改**（文件头有注明）：买卖信号、标签、tooltip、窗口切片和边界裁剪。
缩放时保留相邻的窗外笔端点，绘制按实际坐标裁剪，保留原线段斜率。
同步上游版本时不要整文件覆盖。

---

## 依赖说明

运行需要缠论引擎 `chanlun_core.py`。本机默认从这个位置加载（`core/__init__.py`）：

```
~/.workbuddy/skills/chanlun-kline/scripts/
```

外部引擎不在这个仓库中；只有源码仓库不足以完成新机器安装，需要自行提供可用的引擎文件。
本地卖点实现依赖该引擎的函数和常量，因此升级引擎后仍需验证买卖两侧的兼容性。

**换机器 / 想独立运行**：把 `chanlun_core.py`（和可选的 `run_chanlun.py`）放进
`core/_engine/`，或设环境变量 `CHANLUN_ENGINE_DIR` 指向存放它们的目录，
程序依次查找「环境变量 → `core/_engine/` → chanlun-kline 技能目录 → astock-chanlun-bridge 技能目录」。
`run_chanlun.py` 及其依赖仅在展开文字报告时加载；缺失时扫描和图表仍可使用。

建议固定经过验证的引擎副本，并用 `CHANLUN_ENGINE_SHA256` 锁定其内容。先查看当前摘要：

```bash
python -c "from core import require_engine, engine_identity; print(engine_identity(require_engine())['sha256'])"
```

将该值记录到自己的部署配置，通过环境变量设置（PowerShell：`$env:CHANLUN_ENGINE_SHA256='摘要'`）。
不匹配时程序拒绝启动。接口记录的是本次进程加载时的引擎身份，更新引擎需要重启服务。

## 开发检查

保留原生页面，采用 JSDoc + `@ts-check` 渐进增加类型检查；当前严格检查 `api.js` 与
`state.js`，其余页面模块逐步迁移。运行时不需要构建步骤，不引入 Vue。

```bash
npm ci --ignore-scripts
npm run typecheck
npm test
python -B -m unittest discover -s tests -v
```

GitHub Actions 配置执行上述检查。后端回归使用明确标注的测试引擎替身，验证服务、缓存、
扫描和接口边界；前端回归使用最小 DOM 和受控 Promise 验证竞态。这些测试不验证缠论
算法本身或历史收益。Windows 未开启符号链接权限时会跳过对应文件系统测试，Linux CI 会执行。

行情数据来自新浪 / 东财 / 腾讯的公开接口，仅供研究，不构成投资建议。
