# Stock_workDashboard · 缠论全市场工作台

在本机扫描 A 股买卖点，按行业和信号筛选标的，再打开 K 线核对结构与判据。

后端使用 Python 标准库，前端使用原生 JavaScript 和 Canvas。页面无需构建；开发时通过 JSDoc、TypeScript 检查和回归测试约束关键接口。

> **运行前须知：** 本仓库不包含缠论核心引擎 `chanlun_core.py`，需要自行提供兼容的引擎文件。只克隆仓库还不能直接启动。

## 可以做什么

- **全市场扫描**：获取沪、深、北在市 A 股，识别一、二、三类买卖点及中枢突破、跌破等结构状态。
- **组合筛选**：按信号类型、新鲜度、是否失效、行业、估算成交额、ST、北交所和趋势背驰筛选，也支持代码或名称搜索。
- **个股分析**：展开日线、60 分钟、30 分钟 K 线，查看笔、分型、中枢、买卖点、均线和 MACD。
- **图表复盘**：全屏缩放、拖动、切换区间，结合历史信号编号核对成立理由。
- **本地保存**：缓存 K 线与扫描快照；刷新页面或重启服务后可继续读取已保存榜单。

## 安装与启动

### 1. 准备环境

| 项目 | 要求 |
| --- | --- |
| Python | 3.10 或更高版本 |
| 缠论引擎 | 兼容的 `chanlun_core.py` 及其依赖，自行提供 |
| 浏览器 | 支持 Canvas 和 Fetch 的现代浏览器 |
| 网络 | 服务进程能够通过 HTTPS 访问新浪、东方财富和腾讯行情接口 |
| Node.js | 仅开发检查需要，20 或更高版本；日常运行不需要 |

```bash
git clone https://github.com/xiaodandandandan/Stock_workDashboard.git
cd Stock_workDashboard
```

### 2. 配置引擎

推荐通过 `CHANLUN_ENGINE_DIR` 指向引擎所在目录。目录内应直接包含 `chanlun_core.py`。

Windows PowerShell：

```powershell
$env:CHANLUN_ENGINE_DIR = 'D:\chanlun-engine'
```

macOS / Linux：

```bash
export CHANLUN_ENGINE_DIR="$HOME/chanlun-engine"
```

也可以把引擎放到项目的 `core/_engine/` 目录。程序的查找优先级为：

1. `CHANLUN_ENGINE_DIR` 指定的目录。
2. 项目内的 `core/_engine/`。
3. `~/.workbuddy/skills/chanlun-kline/scripts/`。
4. `~/.workbuddy/skills/astock-chanlun-bridge/scripts/`。

展开“文字报告”还需要 `run_chanlun.py` 及其依赖；缺少报告模块不会影响扫描和图表。

### 3. 启动服务

```bash
python 启动.py
```

默认打开 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)。首次使用时点击右上角“重新扫描”，完成后自动显示榜单。

常用启动方式：

```bash
# 启动后自动扫描
python 启动.py --scan

# 使用其他端口
python 启动.py --port 8766

# 只启动服务，不自动打开浏览器
python server.py --host 127.0.0.1 --port 8765 --no-browser
```

保持启动终端打开，按 `Ctrl+C` 停止服务。默认只监听本机；服务没有用户认证，不适合直接暴露到公网。

## 日常使用

1. **更新榜单**：点击“重新扫描”。扫描在后台执行，页面显示进度；耗时取决于市场数量、行情源响应和本机性能。
2. **缩小范围**：选择买卖点类型，再组合行业、信号新鲜度和成交额等条件。统计卡片和类型标签也可以点击。
3. **核对个股**：点击表格行打开抽屉，切换日线或分钟线，在图下查看信号判据。
4. **查看细节**：点击“放大”或双击图表，用滚轮缩放、拖动平移；`Esc` 关闭当前浮层。

默认筛选为“当日买点、仅未失效、排除 ST”，因此页面可见数量通常少于原始命中数。表格最多显示当前筛选结果的前 800 条。

也可以用链接直接打开个股：

```text
http://127.0.0.1:8765/?open=sh600000&freq=day
http://127.0.0.1:8765/?open=sz000001&freq=m60
```

建议使用带交易所前缀的代码，避免混淆：`sh000001` 是上证指数，`sz000001` 是平安银行。榜单扫描范围为 A 股；单票查询能否获取其他标的取决于行情源。

### 命令行扫描

```bash
python scan.py                              # 全市场，日线，默认 800 根 K 线
python scan.py --limit 300 --workers 12      # 只扫描列表前 300 只，试跑
python scan.py --freq m60 --count 800        # 扫描 60 分钟周期
```

| 参数 | 默认值 | 范围 / 说明 |
| --- | --- | --- |
| `--limit` | `0` | 0～10000；0 表示不限制数量 |
| `--workers` | `32` | 1～32；并发取数与计算数量 |
| `--freq` | `day` | `day`、`m60`、`m30`、`m15`、`m5` |
| `--count` | `800` | 120～5000；向数据源请求的 K 线根数，实际返回可能更少 |
| `--lookback` | `5` | 0～250；信号新鲜度回溯窗口 |
| `--no-kline-cache` | 关闭 | 本次扫描不保存命中股票的 K 线 |

页面“重新扫描”使用默认扫描参数。自定义周期或样本数量可通过命令行或 API 提交。

## 数据口径与状态

| 数据 | 来源与处理 |
| --- | --- |
| 股票列表、日线和分钟 K 线 | 新浪公开行情接口；项目不额外执行复权处理 |
| 行业 | 东方财富 `RPT_F10_ORG_BASICINFO` 的 `EM2016` 分类；只获取在市 A 股，按交易所和代码匹配 |
| 股票名称兜底 | 腾讯报价接口 |
| 估算成交额 | 收盘价 × 成交量，再换算为亿元；不是逐笔成交金额之和 |
| 买点 | 外部引擎 `chanlun_core.detect()` |
| 卖点 | 项目内 `core/sell.py`，镜像买点规则 |

行业列表展示一级分类，例如“金融”“交运设备”；接口还保留完整分类路径。

### 如何理解信号

- **当日 / 新鲜度**：按输入 K 线计数，“当日”表示最后一根 K 线才首次报出，不一定是电脑上的今天。分钟周期应理解为对应周期的根数。
- **未失效**：检查现价是否越过信号的失效位。
- **引擎持续报出**：历史信号是否一直被引擎识别到最后一根，与“未失效”是不同条件。
- **历史信号涨跌**：描述信号报出期间的价格变化，不等同于可执行交易策略的收益。

### 扫描失败时如何处理

- 市场列表缺页、为空、重复或达到分页上限时，不发布新榜单。
- 单票取数或计算的成功率低于 95% 时，保留上次成功榜单。
- 成功率达到 95% 但仍有失败时，可以发布，`complete=false`，页面提示失败数量。
- 无信号和 K 线不足分别统计，不混入取数、计算失败数量。
- 行业状态通过 `industry_status` 单独记录。行业接口失败或部分股票缺少分类时，页面明确提示；`complete` 仅描述取数与计算覆盖情况。

## 缓存与更新

运行数据保存在项目的 `cache/` 下，已被 Git 忽略：

```text
cache/
├── kline/
│   ├── day/sh600000.json       # 使用完整股票标识，避免交易所代码冲突
│   └── m60/sh600000.json
└── scan/
    ├── latest.json             # 最近一次成功发布的榜单
    ├── attempt.json            # 最近一次扫描诊断
    └── 时间戳.json              # 最近 8 份榜单快照
```

日线 K 线缓存默认有效 6 小时，分钟线为 10 分钟。单票打开时会按有效期判断是否重新取数；重新扫描会重新请求行情。榜单本身不会定时更新，需主动扫描。

旧版裸代码文件名（例如 `day/600000.json`）不会继续使用，下次访问会重新获取并保存为完整标识。缓存写入采用独立临时文件和原子替换；历史信号缓存还校验整批 K 线内容，避免数据修订后复用旧信号。

## HTTP API

| 方法 | 路径 | 返回 / 用途 |
| --- | --- | --- |
| GET | `/api/health` | 服务状态、引擎摘要、最近扫描与诊断 |
| GET | `/api/scan` | 最新榜单；尚无结果时返回 404 |
| POST | `/api/scan` | 提交后台扫描；已有任务时返回 `started=false` |
| GET | `/api/scan/status` | 当前扫描阶段、进度、错误和完成摘要 |
| GET | `/api/stock?code=sh600000&freq=day` | 单票结构、信号及图表数据 |
| GET | `/api/industry` | 行业映射数量和一级分类计数 |

POST 扫描接受 JSON 对象：

```json
{"limit":300,"workers":12,"freq":"day","count":800,"lookback":5}
```

单票接口支持 `lookback=30..5000` 和 `report=0|1`。非法参数返回 HTTP 400，扫描请求体最多 8 KiB。

单票响应包含完整 `symbol`、`freq`、`data_version`、`engine` 和 `source`。扫描响应包含 `errors`、`complete`、`skipped` 与 `industry_status`，接入时应同时检查这些字段。

## 开发与验证

```bash
npm ci --ignore-scripts
npm run typecheck
npm test
python -B -m unittest discover -s tests -v
```

[GitHub Actions](https://github.com/xiaodandandandan/Stock_workDashboard/actions/workflows/checks.yml) 在推送和 Pull Request 时运行这些检查。

当前严格类型检查覆盖 `web/js/api.js` 与 `web/js/state.js`。前端测试覆盖异步请求、周期切换、抽屉状态和图表窗口；后端测试覆盖缓存、参数、分页、行业匹配、扫描发布和静态路径边界。

后端测试使用明确标注的测试引擎替身，无需安装外部引擎，但不能据此判断真实缠论算法或历史收益。Windows 没有符号链接权限时会跳过对应测试，Linux CI 执行该测试。

### 代码导航

```text
启动.py                     启动服务并打开浏览器
server.py                   HTTP 路由、单票查询、后台任务状态
scan.py                     全市场扫描与结果发布
core/
├── datasource.py           行情、行业与名称查询
├── validation.py           参数与股票标识校验
├── engine.py               结构、信号、历史回放与图表打包
├── sell.py                 卖点规则
└── store.py                缓存、快照与原子写入
web/
├── index.html              页面结构
├── js/                     榜单、请求状态、个股抽屉和缩放交互
└── vendor/kline_canvas.js   Canvas 图表绘制
tests/                      前后端回归测试
```

新增数据源放在 `datasource.py`，结构和信号逻辑放在计算层，HTTP 处理放在 `server.py`，界面交互放在 `web/js/`。绘图层有项目内修改，同步上游前需要对照差异。

### 固定引擎版本

查询当前引擎的 SHA-256：

```bash
python -c "from core import require_engine, engine_identity; print(engine_identity(require_engine())['sha256'])"
```

将输出设置为环境变量 `CHANLUN_ENGINE_SHA256` 后，启动时会校验引擎文件。升级引擎后需重启服务，并验证买卖两侧兼容性。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 找不到 `chanlun_core` | 检查引擎路径，确保目录直接包含 `chanlun_core.py` |
| 页面能打开，但扫描报 `WinError 10013` | 检查服务进程的联网权限、沙箱或防火墙限制；页面可访问不代表后端能访问行情源 |
| 行业全部是“未分类” | 更新代码后重新扫描；查看页面的行业警告、`/api/industry` 和 `cache/scan/attempt.json` |
| 表格为空，但命中数不为 0 | 点击“重置”，检查新鲜度、未失效、ST 和行业筛选条件 |
| 端口已被占用 / 打开的是旧版本 | 停止原服务或使用 `--port 8766`，再打开对应端口 |
| 文字报告失败，但图表正常 | 检查可选的 `run_chanlun.py` 及其依赖 |
| 更新代码后页面仍是旧内容 | 重启 Python 服务并刷新浏览器；需要更新数据时再运行扫描 |

本项目用于行情研究与结构复盘，不提供自动交易。信号未经独立的策略收益验证，不构成投资建议。
