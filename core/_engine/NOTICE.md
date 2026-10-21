# 内置引擎来源

本目录是运行依赖，不需要安装或保留本机 WorkBuddy 技能包。

## chanlun_core.py

- 来源：本机 `~/.workbuddy/skills/chanlun-kline/scripts/chanlun_core.py`。
- 技能包元数据版本：1.0.3。源文件未声明 Python 模块版本。
- 保持源文件字节不变，SHA-256：`61679c24c3af70cab9cc9d7d231fa9b68e4be654f2323f98b98098013fcda774`。
- 源文件说明其抽取自 `chanlun_scan.py`；未提供可核验的原作者或上游仓库地址。
- 技能包声明许可证为 MIT，随附标准许可文本见 [LICENSE.chanlun-kline](LICENSE.chanlun-kline)。保留了原文件全部头部说明。

## run_chanlun.py

- `fmt_dt` 摘自上述技能包的 `interactive_html.py`。
- `fmt`、`report` 摘自本机 `~/.workbuddy/skills/astock-chanlun-bridge/scripts/run_chanlun.py`。
- 仅纳入文字报告实际调用的函数；移除个人技能路径检查、联网取数、独立 HTML 生成及命令行功能。因此无需 `interactive_html.py`、其他技能包或额外前端文件。
- 适配无中枢、无笔、短 K 线数据的报告输出；核心买点算法未修改。
- 桥接技能本地资料未声明独立许可证或作者，不能据此推断它与核心技能使用同一许可证；这里记录直接来源，不宣称已确认其上游授权链。

以上文件仅依赖 Python 标准库及本目录内模块。图表的 Canvas / ECharts 文件已随项目保存在 `web/vendor/`；Node.js 与 TypeScript 仅用于开发检查。
