/* 榜单：筛选 / tab / 排序 / 扫描。
 *
 * 计数只有一处来源（compute），tab 徽标、统计卡、提示行全从它取 ——
 * 之前踩过「多个入口各算一遍，数字对不上」的坑（筛选器静默失效，徽标 151 vs 提示 156）。
 */

/** @type {ScanPayload} */
let D = { rows: [], generated: '', hit: 0, total_scanned: 0, elapsed: 0, lookback: 5,
  count: 800, freq: 'day', errors: 0, complete: true };

const TABS = [
  ['当日买点', 'buy3', null],
  ['一类买点', 'b', '一类买点'], ['二类买点', 'b', '二类买点'], ['三类买点', 'b', '三类买点'],
  ['一类卖点', 's', '一类卖点'], ['二类卖点', 's', '二类卖点'], ['三类卖点', 's', '三类卖点'],
  ['全部', 'all', null],
];
const BUY_TYPES = ['一类买点', '二类买点', '三类买点'];
const CLS = {
  '一类买点': 'b1', '二类买点': 'b2', '三类买点': 'b3', '中枢突破': 'bx',
  '一类卖点': 's1', '二类卖点': 's2', '三类卖点': 's3', '中枢跌破': 'sx',
};
const AMTLABEL = { 0: '不限', 1: '1亿', 3: '3亿', 5: '5亿', 10: '10亿', 20: '20亿', 50: '50亿' };

let LB = 5;                                    // boot() 里按后端返回的 lookback 更新
const S = {
  tab: 0, fresh: 0, validOnly: true, amt: 0, ind: '', q: '',
  exclST: true, exclBJ: false, trendOnly: false, sort: 'a', dir: -1,
};

const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function fmtDate(d) {
  d = String(d || '');
  return d.length >= 8 ? d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6, 8) : d;
}
function fmtFresh(f) {
  if (f === 0) return '<span class="fr new">当日</span>';
  if (f >= LB) return '<span class="fr">≥' + LB + '日前</span>';
  return '<span class="fr">' + f + '日前</span>';
}
function mkt(code) {
  if (/^(60|68)/.test(code)) return /^68/.test(code) ? '科创' : '沪主板';
  if (/^(00|30)/.test(code)) return /^30/.test(code) ? '创业' : '深主板';
  if (/^(92|8|4)/.test(code)) return '北交所';
  return '其他';
}

/* 单个信号是否通过新鲜度 / 有效性闸门。h = {type,date,stop,trend,fresh,valid} */
function keepSignal(h) {
  if (h.fresh > S.fresh) return false;
  if (S.validOnly && h.valid !== 1) return false;
  return true;
}
/* 指定 tab 下的信号（已过闸门）。k='buy3' 是「买点三类聚合」，不含中枢突破 */
function tabSignals(r, ti) {
  const t = TABS[ti];
  if (!t) return [];                        // tab 索引越界：宁可空列表，也别让整个 refresh 抛掉
  const k = t[1], only = t[2];
  let hs;
  // ★ 这里曾经写成 `hs = r[k]` —— 但 k 是 'b' / 's'（tab 的短标），
  //   行的字段名却是 buy / sell，取到 undefined 后 .filter 直接抛异常。
  //   后果是所有「一/二/三类买点、一/二/三类卖点」tab **点了完全没反应**：
  //   renderTabs 在抛错之前已经执行，所以 tab 高亮会变、列表却停在原地 ——
  //   看起来像「筛选没生效」，实际是渲染中断（见 refresh 的兜底）。
  if (k === 'all') hs = r.buy.concat(r.sell);
  else if (k === 'buy3') hs = r.buy.filter(h => BUY_TYPES.includes(h.type));
  else hs = (k === 'b' ? r.buy : r.sell) || [];
  if (only) hs = hs.filter(h => h.type === only);
  return hs.filter(keepSignal);
}
function signals(r) { return tabSignals(r, S.tab); }

/* tab 之外的筛选。tab 计数也走它，保证「徽标数字 = 切过去看到的条数」 */
function basePass(r) {
  if (r.amt < S.amt) return false;
  if (S.ind && r.industry !== S.ind) return false;
  if (S.exclST && /ST/i.test(r.name)) return false;
  if (S.exclBJ && mkt(r.code) === '北交所') return false;
  if (S.q) {
    const q = S.q.toLowerCase();
    if (!(r.code.includes(q) || String(r.name).toLowerCase().includes(q))) return false;
  }
  return true;
}
function pass(r) {
  if (!basePass(r)) return false;
  const hs = signals(r);
  if (!hs.length) return false;
  if (S.trendOnly && !hs.some(h => h.trend === 1)) return false;
  return true;
}
function compute() {
  const tabCnt = new Array(TABS.length).fill(0);
  const typeCnt = {};
  for (const r of D.rows) {
    if (!basePass(r)) continue;
    const buyHs = r.buy.filter(keepSignal), sellHs = r.sell.filter(keepSignal);
    for (const h of buyHs) typeCnt[h.type] = (typeCnt[h.type] || 0) + 1;
    for (const h of sellHs) typeCnt[h.type] = (typeCnt[h.type] || 0) + 1;
    for (let ti = 0; ti < TABS.length; ti++) {
      const t = TABS[ti], k = t[1], only = t[2];
      let hs;
      if (k === 'all') hs = buyHs.concat(sellHs);
      else if (k === 'buy3') hs = buyHs.filter(h => BUY_TYPES.includes(h.type));
      else { hs = (k === 'b' ? buyHs : sellHs); if (only) hs = hs.filter(h => h.type === only); }
      if (!hs.length) continue;
      if (S.trendOnly && !hs.some(h => h.trend === 1)) continue;
      tabCnt[ti]++;
    }
  }
  return { tabCnt, typeCnt };
}

/* ---------------- 渲染 ---------------- */
function renderTabs(counts) {
  document.getElementById('tabs').innerHTML = TABS.map((t, i) =>
    `<div class="tab ${i === S.tab ? 'on' : ''}" data-i="${i}">${t[0]}<span class="cnt">${counts[i]}</span></div>`
  ).join('');
}

function renderIndustry() {
  const list = [...new Set(D.rows.map(r => r.industry))].sort();
  document.getElementById('ind').innerHTML =
    `<option value="">全部行业</option>` +
    list.map(x => `<option value="${esc(x)}" ${x === S.ind ? 'selected' : ''}>${esc(x)}</option>`).join('');
}

function sortedList() {
  const list = D.rows.filter(pass);
  const key = {
    c: r => r.code, n: r => r.name, i: r => r.industry,
    d: r => Math.max(...signals(r).map(h => +h.date), 0),
    fr: r => Math.min(...signals(r).map(h => h.fresh), 99),
    st: r => r.close, p: r => r.close, a: r => r.amt,
  }[S.sort] || (r => r.amt);
  list.sort((x, y) => {
    const a = key(x), b = key(y);
    return typeof a === 'string' ? S.dir * a.localeCompare(b) : S.dir * (a - b);
  });
  return list;
}

function renderTable(list) {
  const tb = document.getElementById('tb');
  tb.innerHTML = list.slice(0, 800).map(r => {
    const hs = signals(r);
    const tags = hs.map(h => {
      const cls = CLS[h.type] || 'bx';
      const tr = h.trend === 1 ? ' tr' : '';
      const dead = h.valid === 1 ? '' : ' dead';
      // data-type：点标签 = 切到该类型的 tab（见 bind 里的委托）。
      // 没有这个，标签就只是装饰 —— 想「只看这个类型」得回到顶上找 tab。
      return `<span class="tag ${cls}${tr}${dead}" data-type="${esc(h.type)}"` +
             ` title="只看「${esc(h.type)}」">${h.type}</span>`;
    }).join('');
    return `<tr class="rowhit" data-symbol="${esc(r.symbol || normalizeSymbol(r.code))}" title="点开看这只票的缠论结构">
      <td class="code">${esc(r.code)}</td>
      <td class="name">${esc(r.name)}<span class="go">展开 ↗</span></td>
      <td><span class="ind">${esc(r.industry)}</span></td>
      <td>${tags}</td>
      <td class="code">${hs.map(h => fmtDate(h.date)).join('<br>')}</td>
      <td>${hs.map(h => fmtFresh(h.fresh)).join('<br>')}</td>
      <td class="num">${hs.map(h => h.stop != null ? (+h.stop).toFixed(2) : '—').join('<br>')}</td>
      <td class="num">${r.close != null ? (+r.close).toFixed(2) : '—'}</td>
      <td class="num">${(+r.amt).toFixed(2)}</td>
    </tr>`;
  }).join('');
  document.getElementById('empty').style.display = list.length ? 'none' : 'block';
}

function renderStats(t) {
  const n = k => t.typeCnt[k] || 0;
  const scope = S.fresh >= 99 ? '全窗口' : (S.fresh === 0 ? '当日' : `近${S.fresh + 1}日`);
  const tabOf = name => TABS.findIndex(x => x[2] === name);
  const cards = [
    ['全市场扫描', D.total_scanned, '', -1],
    ['有信号', D.hit, '', -1],
    ['一类买点', n('一类买点'), 'up', tabOf('一类买点')],
    ['二类买点', n('二类买点'), 'up', tabOf('二类买点')],
    ['三类买点', n('三类买点'), 'up', tabOf('三类买点')],
    ['一类卖点', n('一类卖点'), 'dn', tabOf('一类卖点')],
    ['二类卖点', n('二类卖点'), 'dn', tabOf('二类卖点')],
    ['三类卖点', n('三类卖点'), 'dn', tabOf('三类卖点')],
  ];
  document.getElementById('stats').innerHTML = cards.map(([k, v, c, ti]) =>
    `<div class="st" ${ti >= 0 ? `data-tab="${ti}" title="只看${k}"` : ''}>
       <div class="k">${k}<span style="opacity:.6"> · ${scope}</span></div>
       <div class="v ${c}">${v}</div>
     </div>`).join('');
  document.getElementById('sub').textContent =
    `数据时间 ${D.generated}　·　${D.freq || 'day'} ${D.count || 800} 根　·　扫描耗时 ${D.elapsed}s` +
    (D.errors ? `　·　⚠ ${D.errors} 只分析失败，榜单不完整` : '') +
    (D.industry_status?.status === 'failed' ? '　·　⚠ 行业获取失败，请重新扫描' :
      (D.industry_status?.missing ? `　·　⚠ ${D.industry_status.missing} 只缺少行业分类` : '')) +
    `　·　新鲜度回溯窗口 ${LB} 根　·　买点 chanlun_core.detect()　卖点严格镜像`;
}

function renderFoot() {
  document.getElementById('foot').innerHTML =
    `<b>点开个股</b>：点表格任意一行，右侧滑出该票的缠论 K 线（笔 / 分型 / 中枢 / 买点 / 卖点 / 均线 / MACD 七个图层可开关），` +
    `顶部可切 <b>日线 / 60 分钟 / 30 分钟</b> —— 日线直接读扫描时已算好的缓存，` +
    `分钟线是切过去才向后端要的（现取现算，约 0.3 s）。<br>` +
    `<b>新鲜度</b>：信号「首次可察觉距今几根」。做法是对最后 ${LB} 根逐根重跑引擎（<code>detect(bars[:i+1])</code>），` +
    `信号第一次出现的那一轮就是它的年龄 —— <b>「当日」= 最后一根 K 线才首次报出</b>，不是「信号日期是今天」。<br>` +
    `<b>未失效</b>：买点要求现价仍在失效位之上、卖点仍在之下。引擎对一类买点不校验当前价，` +
    `报出后价格若跌破本段底、笔端点未确认，信号仍会挂着 —— 那些是「已失效」，默认过滤（标签上仍可见删除线）。<br>` +
    `口径：一类=背驰 / 二类=回踩不破前低 / 三类=突破中枢上沿回抽不破；卖点为严格镜像。` +
    `「趋势背驰」标签 = 中枢回溯链达到 ≥2 个依次下移/上移不重叠的严格缠论趋势要求，<b>只筛这个标签更接近教科书定义的一类买卖点</b>。<br>` +
    `行业为东财 EM2016 一级分类；成交额 = 收盘价 × 成交量（新浪源）。` +
    `⚠️ 卖点是买点的机械镜像，<b>尚未经过独立的历史胜率统计</b>，请当作结构提示而非已校准信号。`;
}

// ★ 不能叫 redraw：vendor/kline_canvas.js 里已有全局 redraw()（画布重绘），
//   后加载的同名函数会把它整个盖掉 —— 抽屉就再也画不出图（实测踩过：
//   canvas 停在默认 300x150，页面上留一块空白，看着像「这只票没结构」）。
function refresh() {
  // ★ 渲染出错必须**看得见**。之前 tabSignals 抛异常时，tab 高亮已经更新、
  //   列表停在旧内容 —— 表面上像「筛选没生效」，完全查不出是代码炸了。
  //   把异常显性化到提示行，同类问题下次一眼可见。
  try {
    render();
  } catch (e) {
    console.error(e);
    document.getElementById('hint').innerHTML =
      `<b style="color:var(--up)">界面渲染出错：${esc(e.message)}</b>` +
      `　——　上方列表不是当前筛选条件的结果，请把这条信息反馈。`;
  }
}

function render() {
  const { tabCnt, typeCnt } = compute();
  renderTabs(tabCnt);
  renderIndustry();
  renderStats({ tabCnt, typeCnt });
  const list = sortedList();
  renderTable(list);
  document.getElementById('hint').textContent =
    `筛选出 ${list.length} 只（原始命中 ${D.hit} 只）　·　` +
    `新鲜度：${S.fresh >= 99 ? '不限' : (S.fresh === 0 ? '仅当日' : `近 ${S.fresh + 1} 日`)}　·　` +
    `${S.validOnly ? '仅未失效' : '含已失效'}　·　${S.trendOnly ? '仅趋势背驰' : '全部背驰'}` +
    `　·　点任意一行看该票结构`;
  renderFoot();
}

/* ---------------- 事件 ---------------- */
let bound = false;
function bind() {
  if (bound) return;                 // boot() 可能被调用多次（扫描完刷新），事件只绑一次
  bound = true;
  document.getElementById('tabs').addEventListener('click', e => {
    const t = e.target.closest('.tab'); if (!t) return;
    S.tab = +t.dataset.i; refresh();
  });
  document.getElementById('stats').addEventListener('click', e => {
    const c = e.target.closest('.st'); if (!c || c.dataset.tab == null) return;
    S.tab = +c.dataset.tab; refresh();
  });
  const on = (id, ev, fn) => document.getElementById(id).addEventListener(ev, fn);
  on('fresh', 'change', e => { S.fresh = +e.target.value; refresh(); });
  on('amt', 'change', e => { S.amt = +e.target.value; refresh(); });
  on('ind', 'change', e => { S.ind = e.target.value; refresh(); });
  on('q', 'input', e => { S.q = e.target.value.trim(); refresh(); });
  on('validOnly', 'change', e => { S.validOnly = e.target.checked; refresh(); });
  on('exclST', 'change', e => { S.exclST = e.target.checked; refresh(); });
  on('exclBJ', 'change', e => { S.exclBJ = e.target.checked; refresh(); });
  on('trendOnly', 'change', e => { S.trendOnly = e.target.checked; refresh(); });
  on('reset', 'click', () => {
    Object.assign(S, { tab: 0, fresh: 0, validOnly: true, amt: 0, ind: '', q: '',
                       exclST: true, exclBJ: false, trendOnly: false, sort: 'a', dir: -1 });
    document.getElementById('fresh').value = '0';
    document.getElementById('amt').value = '0';
    document.getElementById('q').value = '';
    for (const [id, v] of [['validOnly', true], ['exclST', true], ['exclBJ', false], ['trendOnly', false]])
      document.getElementById(id).checked = v;
    refresh();
  });
  document.querySelectorAll('th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.s;
      if (S.sort === k) S.dir = -S.dir;
      else { S.sort = k; S.dir = ['a', 'd', 'p', 'st', 'fr'].includes(k) ? -1 : 1; }
      refresh();
    });
  });
  document.getElementById('tb').addEventListener('click', e => {
    const tg = e.target.closest('.tag[data-type]');
    if (tg) {                                  // 点类型标签 = 按该类型筛选，不展开抽屉
      const ti = TABS.findIndex(t => t[2] === tg.dataset.type);
      if (ti >= 0) { S.tab = ti; refresh(); }
      return;
    }
    const tr = e.target.closest('tr[data-symbol]'); if (!tr) return;
    const row = D.rows.find(r => (r.symbol || normalizeSymbol(r.code)) === tr.dataset.symbol);
    if (row) Drawer.open(row, D.freq || 'day');
  });
  on('btnScan', 'click', startScan);
}

/* ---------------- 扫描 ---------------- */
let polling = null;
function setScanSt(txt, on) {
  const el = document.getElementById('scanSt');
  el.textContent = txt || '';
  el.classList.toggle('on', !!on);
}
async function startScan() {
  const btn = document.getElementById('btnScan');
  btn.disabled = true;
  try {
    const r = await API.runScan({});
    if (!r.started) setScanSt(r.reason || '已有扫描在跑', true);
    pollScan();
  } catch (e) {
    setScanSt('启动失败：' + e.message, true);
    btn.disabled = false;
  }
}
function pollScan() {
  clearTimeout(polling);
  polling = setTimeout(async () => {
    let j;
    try { j = await API.scanStatus(); }
    catch (e) { setScanSt('状态查询失败：' + e.message, true); document.getElementById('btnScan').disabled = false; return; }
    if (j.running) {
      const pct = j.total ? ` ${j.done}/${j.total}` : '';
      setScanSt(`${j.stage}${pct}　${j.msg}`, true);
      pollScan();
      return;
    }
    document.getElementById('btnScan').disabled = false;
    if (j.error) { setScanSt('扫描失败：' + j.error, true); return; }
    const result = j.result || {};
    const industryWarning = result.industry_status?.status === 'failed' ? ' · 行业获取失败，请重试' :
      (result.industry_status?.missing ? ` · ${result.industry_status.missing} 只缺少行业分类` : '');
    setScanSt(`扫描完成：命中 ${result.hit} 只 · ${result.elapsed}s` +
      (result.errors ? ` · ${result.errors} 只失败，结果不完整` : '') + industryWarning,
      !!result.errors || !!industryWarning);
    await boot(true);
  }, 900);
}

/* ---------------- 启动 ---------------- */
async function boot(silent) {
  let ok = true;
  try {
    D = await API.scan();
  } catch (e) {
    ok = false;
    document.getElementById('sub').textContent =
      '还没有扫描结果 —— 点右上角「重新扫描」跑一次全市场（沪深 A 约 2~4 分钟）';
    setScanSt(e.status === 404 ? '待扫描' : ('读取失败：' + e.message), true);
  }
  // ★ bind() 无条件调用：首次使用时本来就没有扫描结果，如果这时不绑事件，
  //   「重新扫描」按钮永远点不动 —— 页面直接死锁在空状态。
  bind();
  if (ok) { LB = D.lookback ?? 5; refresh(); }
  // Reconnect to an already running scan, including first launch via --scan.
  if (!silent) {
    try {
      const job = await API.scanStatus();
      if (job.running) { document.getElementById('btnScan').disabled = true; pollScan(); }
    } catch (e) { console.warn('读取扫描状态失败', e); }
  }
  // ?open=601138[&freq=m60] —— 直接展开某只票的抽屉（调试 / 分享具体标的时用）
  const qs = new URLSearchParams(location.search);
  const want = qs.get('open');
  if (want && !Drawer.openCode) {
    try {
      const symbol = normalizeSymbol(want);
      const r = D.rows.find(x => (x.symbol || normalizeSymbol(x.code)) === symbol) ||
        { code: symbol.slice(2), symbol, name: '', industry: '' };
      Drawer.open(r, qs.get('freq') || 'day');
    } catch (e) { setScanSt(e.message, true); }
  }
}
boot();
