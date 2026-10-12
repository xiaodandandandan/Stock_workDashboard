/* 本地图表库按需加载；数据缺失保留 null，不补零、不混用年报和季报。 */
const FundamentalCharts = (() => {
  const C = { up: '#d9363e', down: '#1a9c6b', neutral: '#3b6fd4', aux: '#8fa3bf', ink: '#5b6b7f' };
  let library = null, revision = 0, charts = [], observer = null;
  const value = x => typeof x === 'number' && Number.isFinite(x) ? x : null;
  const billion = x => value(x) === null ? null : x / 1e8;
  const list = x => Array.isArray(x) ? x : [];
  function base(labels, names, unit = '亿元') {
    return {
      animation: false,
      aria: { enabled: true },
      tooltip: { trigger: 'axis', renderMode: 'richText', confine: true },
      legend: { data: names, bottom: 0, type: 'scroll', textStyle: { color: C.ink, fontSize: 11 } },
      grid: { left: 54, right: 35, top: 35, bottom: 58, containLabel: true },
      xAxis: { type: 'category', data: labels, axisLabel: { color: C.ink, fontSize: 11, hideOverlap: true } },
      yAxis: { type: 'value', name: unit, axisLabel: { color: C.ink, fontSize: 11 }, splitLine: { lineStyle: { color: '#eef2f7' } } },
    };
  }
  function series(name, type, values, color, extra = {}) {
    return { name, type, data: values, connectNulls: false, itemStyle: { color }, lineStyle: { color, width: 2 }, ...extra };
  }
  function buildOptions(data) {
    const financial = data.sections.financials.data;
    const availableAnnual = list(financial.annual_reports).filter(r => /-12-31$/.test(r.report_date)).slice().sort((a, b) => a.report_date.localeCompare(b.report_date));
    const annual = [];
    if (availableAnnual.length) {
      const byYear = new Map(availableAnnual.map(row => [Number(row.report_date.slice(0, 4)), row]));
      for (let year = Number(availableAnnual[0].report_date.slice(0, 4)); year <= Number(availableAnnual.at(-1).report_date.slice(0, 4)); year++) {
        annual.push(byYear.get(year) || { report_date: `${year}-12-31` });
      }
    }
    const years = annual.map(r => r.report_date.slice(0, 4));
    const read = field => annual.map(r => billion(r[field]));
    const business = financial.business_segments || data.report?.business_segments || {};
    const periods = list(business.periods).length ? business.periods : (list(business.items).length ? [business] : []);
    const ordered = periods.slice().sort((a, b) => String(a.report_date).localeCompare(String(b.report_date)));
    const names = [...new Set(ordered.flatMap(p => list(p.items).map(item => String(item.name))))];
    const palettes = ['#3b6fd4', '#8fa3bf', '#9d76c1', '#47a3b3', '#c69347', '#d47c92'];
    let mix = null;
    if (ordered.length && names.length && ordered.some(p => list(p.items).some(i => value(i.share_pct) !== null))) {
      mix = base(ordered.map(p => p.report_date), names, '收入占比（%）');
      mix.yAxis.max = 100;
      mix.series = names.map((name, index) => series(name, 'bar', ordered.map(p => {
        const item = list(p.items).find(item => item.name === name);
        return item ? value(item.share_pct) : null;
      }), palettes[index % palettes.length], { stack: 'revenue', barMaxWidth: 80 }));
    }
    const performance = base(years, ['营业收入', '归母净利润', '扣非归母净利润']);
    performance.grid.right = 54;
    performance.yAxis = [
      { ...performance.yAxis, name: '营收（亿元）' },
      { ...performance.yAxis, name: '利润（亿元）', splitLine: { show: false } },
    ];
    const profits = [...read('net_profit'), ...read('net_profit_excl')].filter(v => v !== null);
    const profitMin = Math.min(0, ...profits), profitMax = Math.max(0, ...profits);
    // Finite boundaries avoid ECharts line gradients receiving unbounded coordinates.
    if (profitMax > profitMin) performance.visualMap = {
      type: 'piecewise', show: false, seriesIndex: [1, 2], dimension: 1, min: profitMin, max: profitMax,
      pieces: [{ gte: profitMin, lt: 0, color: C.down }, { gte: 0, lte: profitMax, color: C.up }],
    };
    performance.series = [series('营业收入', 'bar', read('revenue'), C.neutral, { barMaxWidth: 36 }),
      series('归母净利润', 'line', read('net_profit'), C.up, { yAxisIndex: 1 }),
      series('扣非归母净利润', 'line', read('net_profit_excl'), C.down, { yAxisIndex: 1, lineStyle: { color: C.down, type: 'dashed', width: 2 } })];
    const equity = base(years, ['归母净资产', '商誉']);
    equity.grid.right = 54;
    equity.yAxis = [{ ...equity.yAxis, name: '净资产（亿元）' }, { ...equity.yAxis, name: '商誉（亿元）', splitLine: { show: false } }];
    equity.series = [series('归母净资产', 'bar', annual.map(r => ({ value: billion(r.parent_equity), itemStyle: { color: r.parent_equity < 0 ? C.down : C.neutral } })), C.neutral, { barMaxWidth: 36 }),
      series('商誉', 'line', read('goodwill'), C.aux, { yAxisIndex: 1 })];
    const latest = list(financial.reports)[0] || list(financial.annual_reports)[0] || {};
    const exposure = list(data.report?.risk_exposure);
    let risk = null;
    if (exposure.some(item => value(item.amount) !== null)) {
      risk = base([], [], '项目');
      risk.legend.show = false;
      risk.xAxis = { type: 'value', name: '亿元', axisLabel: { color: C.ink, fontSize: 11 } };
      risk.yAxis = { type: 'category', data: exposure.map(item => item.name), axisLabel: { color: C.ink, fontSize: 11 } };
      risk.series = [series('账面金额', 'bar', exposure.map(item => ({ value: billion(item.amount), itemStyle: { color: item.amount === 0 ? C.up : C.aux } })), C.aux, { barMaxWidth: 40 })];
      if (value(latest.parent_equity) !== null && latest.parent_equity > 0) risk.series[0].markLine = {
        silent: true, symbol: 'none', lineStyle: { color: C.neutral, type: 'dashed' },
        label: { formatter: '归母净资产', position: 'insideEndTop', color: C.ink }, data: [{ xAxis: billion(latest.parent_equity) }],
      };
    }
    const cash = base(years, ['经营现金流', '归母净利润', '筹资现金流']);
    const amountColor = (field, fallback) => annual.map(r => ({ value: billion(r[field]), itemStyle: { color: value(r[field]) === null ? fallback : r[field] < 0 ? C.down : C.up } }));
    cash.series = [series('经营现金流', 'bar', amountColor('operating_cashflow', C.neutral), C.neutral, { barMaxWidth: 22 }),
      series('归母净利润', 'bar', amountColor('net_profit', C.up), C.up, { barMaxWidth: 22 }),
      series('筹资现金流', 'line', read('financing_cashflow'), C.aux, { lineStyle: { color: C.aux, type: 'dashed', width: 2 } })];
    const available = fields => annual.length && annual.some(r => fields.some(field => value(r[field]) !== null));
    return [
      { id: 'chart1', option: mix, empty: business.status === 'failed' ? '主营业务构成获取失败；请查看来源状态。' : '暂无可绘制的主营业务构成。', caption: `收入占比为同一分类口径；取得 ${ordered.length} 个报告期。缺失不补零，行业分类不作为主营收入结构。` },
      { id: 'chart2', option: available(['revenue', 'net_profit', 'net_profit_excl']) ? performance : null, empty: '暂无完整年度数据，不能用季度累计读数拼成年报。', caption: `完整年报 ${availableAnnual.length} 期；金额为人民币亿元。营收与利润分轴，缺失年份保留空白断点。` },
      { id: 'chart3', option: available(['parent_equity', 'goodwill']) ? equity : null, empty: '暂无归母净资产和商誉年度序列。', caption: '归母净资产与商誉为期末余额，均为人民币亿元；采用独立数值轴，无固定商誉上限。' },
      { id: 'chart4', option: risk, empty: '风险敞口数据待核验，未把缺失余额当作零。', caption: `最新报告期 ${latest.report_date || '暂无'}；单位为人民币亿元。参考线仅表示归母净资产，不表示减值或担保损失已经发生。` },
      { id: 'chart5', option: available(['operating_cashflow', 'net_profit', 'financing_cashflow']) ? cash : null, empty: '暂无可比较的年度现金流数据。', caption: '完整年度累计口径，单位为人民币亿元；红色为正值、绿色为负值，筹资现金流为中性读数。' },
    ];
  }
  function ensureLibrary() {
    if (window.echarts) return Promise.resolve(window.echarts);
    if (!library) library = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'vendor/echarts.min.js';
      script.onload = () => {
        if (window.echarts) resolve(window.echarts);
        else { library = null; script.remove(); reject(new Error('图表库未初始化')); }
      };
      script.onerror = () => { library = null; script.remove(); reject(new Error('本地图表库加载失败')); };
      document.head.appendChild(script);
    });
    return library;
  }
  function dispose() {
    revision++;
    observer?.disconnect(); observer = null;
    charts.forEach(chart => chart.dispose()); charts = [];
  }
  async function render(data) {
    dispose();
    const ticket = revision, options = buildOptions(data);
    const visible = options.filter(item => item.option);
    options.forEach(item => {
      const cap = document.getElementById('fmCaption' + item.id.slice(-1));
      if (cap) cap.textContent = item.caption;
      if (!item.option) {
        const container = document.getElementById(item.id);
        if (container) { container.classList.add('fm-chart-empty'); container.textContent = item.empty; }
      }
    });
    if (!visible.length) return;
    try {
      const engine = await ensureLibrary();
      if (ticket !== revision) return;
      visible.forEach(item => {
        const container = document.getElementById(item.id);
        if (container) {
          let chart = null;
          try {
            container.classList.remove('fm-chart-empty'); container.textContent = '';
            chart = engine.init(container, null, { renderer: 'svg' });
            chart.setOption(item.option); charts.push(chart);
          } catch {
            chart?.dispose();
            container.classList.add('fm-chart-empty');
            container.textContent = '这张图表暂时无法显示，请刷新重试；下方表格仍可查阅。';
          }
        }
      });
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(() => charts.forEach(chart => chart.resize()));
        observer.observe(document.getElementById('fmBody'));
      }
    } catch (error) {
      if (ticket !== revision) return;
      visible.forEach(item => {
        const container = document.getElementById(item.id);
        if (container) { container.classList.add('fm-chart-empty'); container.textContent = `${error.message}；下方表格仍可查阅。`; }
      });
    }
  }
  window.addEventListener?.('resize', () => charts.forEach(chart => chart.resize()));
  return { render, dispose, buildOptions };
})();
