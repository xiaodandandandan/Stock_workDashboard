/* 个股抽屉：同一界面里看结构，不跳转、不丢筛选状态。
 *
 * 数据策略（和用户的要求对齐）：
 *   · 日线：扫描时已经算过并落盘（cache/kline/day/），打开即画，0 次网络往返之外的等待；
 *   · 60 / 30 分钟：**切过去才向后端要**，现取现算（约 0.3 s），算完缓存在服务端；
 *   · 同一只票同周期在一次打开内不重复请求（dataCache）。
 *
 * 画图复用 vendor/kline_canvas.js 的 draw()，只是从「服务端预渲染」变成前端动态渲染。
 */

const Drawer = (() => {
  const LAYER_LABELS = { bi: '笔', fx: '分型', zs: '中枢', buy: '买点', sell: '卖点',
                         histbuy: '信号编号', ma: '均线', macd: 'MACD' };
  // 信号编号图层 = 每个买点在图上打一个紫色 1/2/3 方块（与下方判据卡一一对应）。
  // 数据来自后端 chart.hist，默认开着 —— 图和理由必须能互相印证。
  LAYERS.histbuy = 1;

  /** @type {StockRef | null} */
  let cur = null;
  /** @type {Frequency} */
  let freq = 'day';
  /** @type {Partial<Record<Frequency, StockPayload>>} */
  let cache = {};          // freq → payload
  const chartGate = new RequestGate();
  const reportGate = new RequestGate();
  let active = false;
  let opened = false;

  const el = id => document.getElementById(id);

  /* ---------------- 打开 / 关闭 ---------------- */
  function open(row, f) {
    if (window.Zoom) Zoom.close();
    cur = row;
    active = true;
    freq = f || 'day';
    cache = {};
    el('dwName').textContent = row.name || row.code;
    el('dwCode').textContent = row.code;
    el('dwInd').textContent = row.industry || '';
    el('dwReportBox').open = false;
    el('dwReport').textContent = '';
    el('dwBody').scrollTop = 0;
    syncFreqBtns();
    el('dwMeta').textContent = '载入中…';
    el('dwCard').innerHTML = '';
    el('drawer').classList.add('on');
    el('drawer').setAttribute('aria-hidden', 'false');
    Drawer.openCode = row.symbol || row.code;
    load(freq);
  }

  function close() {
    if (window.Zoom) Zoom.close();
    active = false;
    chartGate.cancel();
    reportGate.cancel();
    clearChart();
    cur = null;
    cache = {};
    el('drawer').classList.remove('on');
    el('drawer').setAttribute('aria-hidden', 'true');
    Drawer.openCode = null;
  }

  function syncFreqBtns() {
    el('dwFreq').querySelectorAll('button').forEach(b =>
      b.classList.toggle('on', b.dataset.freq === freq));
  }

  /* ---------------- 取数 ---------------- */
  function clearChart() {
    LAST.d = null;
    LAST.hist = [];
    const cv = el('mcv');
    if (cv) {
      const empty = document.createElement('canvas');
      empty.id = 'mcv';
      empty.width = cv.width;
      empty.height = cv.height;
      empty.style.width = '100%';
      empty.style.height = cv.style.height || '360px';
      cv.parentNode.replaceChild(empty, cv); // remove old tooltip/double-click closures too
    }
    el('tip').style.display = 'none';
    el('dwZoom').disabled = true;
  }

  async function load(f) {
    const ticket = chartGate.begin(); // also invalidate pending requests on a cache hit
    const stock = cur;
    reportGate.cancel();
    el('dwReportBox').open = false;
    el('dwReport').textContent = '';
    clearChart();
    el('dwCard').innerHTML = '';
    el('dwMeta').textContent = freq === f ? '载入中…' : el('dwMeta').textContent;
    try {
      const d = cache[f] || await API.stock(stock.symbol || stock.code, f, {}, ticket.signal);
      if (!active || !chartGate.isCurrent(ticket) || cur !== stock || freq !== f) return;
      cache[f] = d;
      render(d);
    } catch (e) {
      if (!active || !chartGate.isCurrent(ticket)) return;
      el('dwMeta').innerHTML = '';
      el('dwCard').innerHTML = `<div class="dw-err">载入失败：${esc(e.message)}</div>`;
    }
  }

  /* ---------------- 渲染 ---------------- */
  function render(d) {
    // 头部信息
    const st = d.structure || {};
    el('dwMeta').innerHTML =
      `现价 <b>${d.close != null ? (+d.close).toFixed(2) : '—'}</b>` +
      `　估算成交额 <b>${(+d.amt).toFixed(2)} 亿</b>` +
      `　${esc(d.unit)} <b>${d.bars}</b> 根` +
      (st.ZD != null
        ? `　中枢 <b>ZD ${(+st.ZD).toFixed(2)} / ZG ${(+st.ZG).toFixed(2)}</b>（${esc(st.pos)}）`
        : `　<span style="color:var(--tx3)">无中枢</span>`) +
      `　<span class="badge ${d.cached ? '' : 'hot'}">${d.cached ? '缓存命中' : '实时取数'}</span>` +
      `<span class="badge">${d.elapsed_ms} ms</span>` +
      (d.in_scan ? '' : `<span class="badge">不在当日榜单</span>`);

    el('dwCard').innerHTML = cardHTML(d);
    renderLayers();
    chart(d);
    el('dwZoom').disabled = !LAST.d;
  }

  // 图上编号 = 类型编号（1 一类 / 2 二类 / 3 三类），与卡片左上角的方块同源；
  // 颜色分买点（紫）/ 卖点（青），与图上标记一致 —— 同色同数字 = 同一张卡。
  const NUM = { '一类买点': '1', '二类买点': '2', '三类买点': '3',
                '一类卖点': '1', '二类卖点': '2', '三类卖点': '3' };
  const PEND = { '中枢突破': 1, '中枢跌破': 1 };

  /* 「成立理由」区的三段文案（标题 / 图例 / 脚注）。
     ★ 与技能单票页 `interactive_html.why_block()` 逐字对齐 —— 工作台和单票页
       说的是同一件事，措辞一分叉，看的人就会怀疑口径也分了叉。 */
  const HEAD = { b: '买点成立理由 · 图上 1 / 2 / 3 逐条核对',
                 s: '卖点成立理由 · 图上 1 / 2 / 3 逐条核对' };
  const LEGEND = {
    b: '<b>1</b> 一类买点 = 下跌末段底背驰（创新低 + 力度衰竭）｜ ' +
       '<b>2</b> 二类买点 = 一类之后回踩不破一类低点｜ ' +
       '<b>3</b> 三类买点 = 向上离开中枢后回踩不回中枢内',
    s: '<b>1</b> 一类卖点 = 上涨末段顶背驰（创新高 + 力度衰竭）｜ ' +
       '<b>2</b> 二类卖点 = 一类之后反抽不破一类高点｜ ' +
       '<b>3</b> 三类卖点 = 向下离开中枢后反抽不回中枢内',
  };
  const FOOT = {
    b: '✓ = 该要件实测通过；× = 未通过（标橙的行是参考项，不参与判定，仅供人工对照）。' +
       '一类买点的「趋势要件」是参考项：引擎用「最后两段下跌笔 + 创新低 + 面积收缩」近似，' +
       '严格缠论还要求那是一段下跌趋势（≥2 个同级别中枢依次下移且不重叠）。' +
       '失效位口径：一 / 二类 = 前低，三类 = ZG。',
    s: '✓ = 该要件实测通过；× = 未通过（标橙的行是参考项，不参与判定，仅供人工对照）。' +
       '一类卖点与买点严格镜像，阈值常量共用、未另设。' +
       '失效位口径：一 / 二类 = 前高，三类 = ZD。',
  };

  /* 一个信号 = 一张卡：类型 + 日期 + 状态，判据逐条（要件 / 实测 / 门槛 / 通过与否），
     脚注给信号价、失效位、存活根数、期内涨跌。

     ★ 为什么要逐条：只给一个「一类买点」标签，等于让人只能相信结论、无法核对。
       把引擎的实测值和门槛都摊开，才看得出它凭什么成立、哪条是硬门槛哪条只是参考 ——
       这也是复盘时唯一能验证「引擎是不是漏判 / 放宽了条件」的方式。
       数据来自 chanlun_core 的 why 字段，与图上打点用**同一次** detect()。
     ★ 状态 chip 用 `live`（该段是否延续到最后一根），不是「现价是否跌破失效位」——
       与技能单票页同口径，两处说同一件事不能各用各的判据。 */
  function sigCard(h, kind) {
    const dec = v => (v == null ? '—' : (+v).toFixed(2));
    const why = (h.why || []).map(w =>
      `<tr${w.ok ? '' : ' class="f"'}>` +
      `<td class="r${w.ok ? '' : ' no'}">${w.ok ? '✓' : '×'}</td>` +
      `<td class="k">${esc(w.k)}</td><td>${esc(w.v)}</td>` +
      `<td class="nd">${w.need ? '需 ' + esc(w.need) : ''}</td></tr>`).join('');
    const ret = h.ret == null ? 0 : +h.ret;
    return `<div class="wi">` +
      `<div class="wi-h"><span class="bdg ${kind}${h.n}">${h.n}</span>` +
      `<b>${esc(h.ty || h.t)}</b><span class="dt">${fmtDate(h.d)}</span>` +
      `<span class="tg ${h.live ? 'on' : 'off'}">${h.live ? '引擎持续报出' : '已停止报出'}</span></div>` +
      (why ? `<table class="wt">${why}</table>`
           : '<div class="wnone">引擎未提供判据明细。</div>') +
      `<div class="wi-f">${kind === 'b' ? '入场' : '出场'} ${dec(h.entry)}（信号报出当根收盘）` +
      (h.stop != null ? `　·　${kind === 'b' ? '止损' : '失效位'} ${dec(h.stop)}` : '') +
      `　·　持续 <b>${h.alive}</b> 根　·　期内 <b>${ret >= 0 ? '+' : ''}${ret.toFixed(1)}%</b>` +
      `</div></div>`;
  }

  function whyBlock(kind, list) {
    // 分钟线动辄几十个信号，默认收起（与技能单票页同规则：> 12 条折叠）
    const fold = list.length > 12;
    const inner = list.map(h => sigCard(h, kind)).join('');
    return `<div class="why">
      <div class="why-h">${HEAD[kind]}</div>
      <div class="lg">${LEGEND[kind]}</div>
      ${fold ? `<details class="wfold" open><summary>全部 ${list.length} 条信号明细</summary>${inner}</details>`
             : inner}
      <div class="fn">${FOOT[kind]}</div>
    </div>`;
  }

  /* 卡片区 = 图上编号的逐条展开。**只列绘图窗口内的信号段**，与图上标记一一对应
     （技能单票页的 why_block 拿的也是窗口内的 hist_js，不是全量扫描结果）——
     窗口外的段图上没有编号，列出来对照不上，还会把抽屉拉得极长。 */
  function cardHTML(d) {
    const st = d.structure || {};
    const hist = d.chart.hist || [];
    const buys = hist.filter(h => h.side === 'b');
    const sells = hist.filter(h => h.side === 's');
    const hidden = Math.max(0, (d.history_total || hist.length) - hist.length);
    const body = [];
    if (buys.length) body.push(whyBlock('b', buys));
    if (sells.length) body.push(whyBlock('s', sells));
    if (!hist.length) {
      body.push('<div class="sig-none">该级别绘图窗口内没有买卖点信号。</div>');
    }
    // 待验证态只交代数量，不占卡片位 —— 它没有「成立理由」，图上也不打编号
    const pend = {};
    (d.signals.buy || []).concat(d.signals.sell || []).forEach(h => {
      if (PEND[h.type]) pend[h.type] = (pend[h.type] || 0) + 1;
    });
    const pk = Object.keys(pend);
    const pendTxt = pk.length
      ? `<div class="sig-n" style="margin-top:6px">另有待验证态：` +
        pk.map(k => `${esc(k)} <b>${pend[k]}</b>`).join('　·　') +
        `　个 —— 价格越过中枢边界、尚未回抽确认，不计入图上编号。</div>`
      : '';
    return `<div class="sig">
      <div class="sig-h">${esc(d.unit)} · 本票信号
        <span class="sig-n">图上编号 <b style="color:#7c3aed">紫 = 买点</b>、` +
        `<b style="color:#0d9488">青 = 卖点</b>，同色同数字与下方卡片一一对应` +
        `${hidden ? `　·　另有 ${hidden} 个信号落在绘图窗口外` : ''}</span>
      </div>
      ${body.join('')}
      ${pendTxt}
      <div class="sig-n" style="margin-top:6px;padding-top:6px;border-top:1px dashed var(--line)">
        历史卡片状态表示引擎是否持续报出；榜单「未失效」另外检查现价是否越过失效位。<br>
        笔 ${st.biCount} 段 · 分型 ${st.fxCount} 个 · 中枢 ${st.zsCount} 个（全局结构统计）
      </div>
    </div>`;
  }

  function renderLayers() {
    const box = el('dwLayers');
    box.innerHTML = Object.keys(LAYER_LABELS).map(k =>
      `<label><input type="checkbox" data-k="${k}" ${LAYERS[k] ? 'checked' : ''}>${LAYER_LABELS[k]}</label>`
    ).join('');
  }

  function chart(d) {
    try {
      chart0(d);
    } catch (e) {
      // 画不出来时**必须让人看见**：空白 canvas 会让人以为「这只票没结构」，
      // 而实际是渲染挂了 —— 两者一个是数据结论、一个是故障，不能混。
      console.error(e);
      LAST.d = null;
      el('dwCard').insertAdjacentHTML('beforeend',
        `<div class="dw-err">图表渲染失败：${esc(e.message)}<pre style="margin-top:6px;white-space:pre-wrap;font-size:11px">${esc(e.stack || '')}</pre></div>`);
    }
  }

  function chart0(d) {
    const h = Math.max(360, Math.min(540, Math.round(window.innerHeight * 0.58)));
    // ★ 每次都换一个新 canvas：bindTip 是往元素上挂监听器，复用同一个元素会让
    //   监听器逐次累积（切 5 次周期后一次移动触发 5 次），而且旧闭包还指向旧数据。
    const old = el('mcv');
    const nv = document.createElement('canvas');
    nv.id = 'mcv';
    old.parentNode.replaceChild(nv, old);

    LAST.d = d.chart.pack;
    LAST.dates = d.chart.dates;
    LAST.opt = d.chart.opt || {};
    // hist = 图上打 1/2/3 的买点编号标记。没有它，卡片里那句
    // 「图上紫色编号与下方卡片一一对应」就是空话。
    LAST.hist = d.chart.hist || [];
    LAST.h = h;
    paint();
    bindTip(nv, LAST.d, LAST.dates, LAST.hist);
    // 双击图表直接进放大视图 —— 看到细节糊在一起时第一反应该是双击，不该先去找按钮
    nv.addEventListener('dblclick', () => Zoom.open());
  }


  /* 自己 paint，不走 kline_canvas 的全局 redraw()：
     那个函数写死了 document.getElementById('mcv') 和全局 LAST，是给「一个页面一张图」
     设计的；在 SPA 里既脆又和别的模块抢名字。这里只借用它的 setupCanvas / draw。 */
  function paint() {
    const cv = el('mcv');
    if (!cv || !LAST.d) return;
    draw(setupCanvas(cv, LAST.h || 470), LAST.d, LAST.dates, LAST.opt || {}, LAST.hist || []);
  }

  /* ---------------- 事件（只绑一次） ---------------- */
  function bindOnce() {
    if (opened) return;
    opened = true;

    el('dwClose').addEventListener('click', close);
    document.addEventListener('keydown', e => {
      // ★ 放大视图开着时 Esc 先关它，不要顺手把抽屉也关了 —— 抽屉在下面（监听先绑、
      //   先触发），只能用 Zoom 的状态来判断该由谁响应。
      if (e.key !== 'Escape') return;
      if (window.Zoom && Zoom.isOpen && Zoom.isOpen()) return;
      if (el('drawer').classList.contains('on')) close();
    });

    el('dwZoom').addEventListener('click', () => Zoom.open());

    el('dwFreq').addEventListener('click', e => {
      const b = e.target.closest('button[data-freq]');
      if (!b || b.disabled) return;
      freq = b.dataset.freq;
      syncFreqBtns();
      load(freq);
    });

    el('dwLayers').addEventListener('change', e => {
      const k = e.target.dataset.k;
      if (!k) return;
      LAYERS[k] = e.target.checked ? 1 : 0;
      if (LAST.d) paint();           // 图层开关只重绘，不重新取数
    });

    el('dwReportBox').addEventListener('toggle', async () => {
      if (!el('dwReportBox').open) { reportGate.cancel(); if (el('dwReport').textContent === '载入中…') el('dwReport').textContent = ''; return; }
      if (!active || !cur || el('dwReport').textContent) return;
      const ticket = reportGate.begin(), stock = cur, period = freq;
      el('dwReport').textContent = '载入中…';
      try {
        const d = await API.stock(stock.symbol || stock.code, period, { report: 1 }, ticket.signal);
        if (!reportGate.isCurrent(ticket) || !active || cur !== stock || freq !== period) return;
        el('dwReport').textContent = d.report || '（无）';
      } catch (e) {
        if (!reportGate.isCurrent(ticket) || !active) return;
        el('dwReport').textContent = '加载失败：' + e.message;
      }
    });

    window.addEventListener('resize', () => {
      if (el('drawer').classList.contains('on') && LAST.d) paint();
    });
  }

  /* ---------------- 对外接口（放大视图用） ----------------
     放大视图和抽屉共用同一份 LAST / LAYERS，所以它需要三样东西：
       snapshot()   当前票的数据快照（pack + 日期轴 + opt + 图上编号）
       syncLayers() 在放大视图里改了图层后，把抽屉的勾选框同步过去
       paint()      同步完把抽屉那张图重画一遍 —— 两边显示同一个 LAYERS，
                     不一致的话「关掉放大视图发现抽屉还是旧图层」会让人以为开关坏了
     ★ 新组件要读抽屉的数据，一律走 snapshot()，不要去摸 LAST / cache 这些内部变量。 */
  function snapshot() {
    if (!active || !LAST.d || !cur) return null;
    const p = cache[freq];
    if (!p || LAST.d !== p.chart.pack) return null;
    return {
      code: cur.code, symbol: p.symbol, name: cur.name || p.name, freq: freq,
      unit: p.unit || '', bars: p.bars || 0,
      d: LAST.d, dates: LAST.dates, opt: LAST.opt, hist: LAST.hist,
    };
  }

  function syncLayers() {
    el('dwLayers').querySelectorAll('input[data-k]')
      .forEach(i => { i.checked = !!LAYERS[i.dataset.k]; });
  }

  bindOnce();
  return { open, close, snapshot, syncLayers, paint, LAYER_LABELS, openCode: null };
})();
