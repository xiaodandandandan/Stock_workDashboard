/* 按需基本面视图：请求与 K 线、扫描独立，切换股票及关闭时废弃旧响应。 */
const Fundamentals = (() => {
  const gate = new RequestGate();
  const titles = { profile: '公司业务', financials: '财报事实与规则解读', valuation: '当前估值', news: '近期公司动态' };
  let stock = null, active = false, opener = null;
  const el = id => document.getElementById(id);
  const isOpen = () => active;

  function safeUrl(value) {
    try {
      const url = new URL(String(value));
      return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
    } catch { return ''; }
  }
  function link(label, value) {
    const url = safeUrl(value);
    return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : esc(label);
  }
  function number(value, suffix = '') {
    return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) + suffix : '—';
  }
  function money(value) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
    if (Math.abs(value) >= 1e8) return number(value / 1e8, ' 亿');
    if (Math.abs(value) >= 1e4) return number(value / 1e4, ' 万');
    return number(value, ' 元');
  }
  function metric(label, value) {
    return `<div class="fm-metric"><div class="k">${esc(label)}</div><div class="v">${esc(value)}</div></div>`;
  }
  function impact(signal) {
    const direction = signal?.direction;
    const positive = ['偏积极', '偏利好'].includes(direction), negative = ['偏负面', '偏利空'].includes(direction);
    return `<span class="fm-impact ${positive ? 'positive' : negative ? 'negative' : ''}">${positive ? '可能利好' : negative ? '可能利空' : direction === '中性' ? '中性' : '待核验'}</span>`;
  }
  function rule(signal) {
    return `<div class="fm-rule">${impact(signal)}<strong>${esc(signal.title || '规则解读')}</strong>` +
      `<div class="fm-evidence">事实依据：${esc(signal.evidence || '需查阅原文核验')}</div>` +
      `<div>规则解读：${esc(signal.reason || '暂无明确判断')}</div>` +
      (signal.notice ? `<div class="fm-news-meta">${esc(signal.notice)}</div>` : '') + '</div>';
  }
  function profile(data) {
    return `<div class="fm-profile"><div><b>${esc(data.name || stock.name || stock.code)}</b> · ${esc(data.industry || '行业暂无资料')}</div>` +
      `<div>${esc(data.description || '暂无公司简介')}</div>` +
      (data.business_scope ? `<details><summary>经营范围</summary>${esc(data.business_scope)}</details>` : '') +
      (data.website ? `<div>公司网站：${link(data.website, data.website)}</div>` : '') + '</div>';
  }
  function financials(data) {
    const reports = Array.isArray(data.reports) ? data.reports : [], latest = reports[0];
    if (!latest) return '<div class="hint">暂无可用财报。缺失值不视为零。</div>';
    const fields = [
      ['营业收入（元）', money(latest.revenue)], ['营收同比', number(latest.revenue_yoy, '%')],
      ['归母净利润（元）', money(latest.net_profit)], ['净利润同比', number(latest.net_profit_yoy, '%')],
      ['经营现金流（元）', money(latest.operating_cashflow)], ['ROE', number(latest.roe, '%')],
      ['毛利率', number(latest.gross_margin, '%')], ['资产负债率', number(latest.debt_ratio, '%')],
      ['每股收益（元）', number(latest.eps)],
    ];
    return `<div class="hint">最新报告期 ${esc(latest.report_date)} · 披露日期 ${esc(latest.announced_date || '来源未提供')} · 金额为人民币；收入与现金流为该报告期累计口径。</div>` +
      `<div class="fm-metrics">${fields.map(([name, value]) => metric(name, value)).join('')}</div>` +
      `<details><summary>查看近期财报对照</summary><div class="fm-report-table"><table><thead><tr>` +
      '<th>报告期</th><th>营业收入</th><th>营收同比</th><th>归母净利润</th><th>净利同比</th><th>经营现金流</th><th>ROE</th><th>负债率</th>' +
      `</tr></thead><tbody>${reports.map(r => `<tr><td>${esc(r.report_date)}</td><td>${money(r.revenue)}</td><td>${number(r.revenue_yoy, '%')}</td><td>${money(r.net_profit)}</td><td>${number(r.net_profit_yoy, '%')}</td><td>${money(r.operating_cashflow)}</td><td>${number(r.roe, '%')}</td><td>${number(r.debt_ratio, '%')}</td></tr>`).join('')}</tbody></table></div></details>` +
      `<div class="hint" style="margin-top:10px">以下为财报指标规则解读；季度累计数据应对照同期，金融行业指标需结合行业特点。</div>` +
      (Array.isArray(data.signals) ? data.signals.map(rule).join('') : '');
  }
  function valuation(data) {
    return `<div class="hint">行情时间 ${esc(data.date || '来源未提供')} · 估值为行情源口径，财报与行情时间可能不同。</div>` +
      `<div class="fm-metrics">${[
        ['最新价（元）', number(data.price)], ['市盈率 TTM', number(data.pe_ttm)],
        ['市净率', number(data.pb)], ['总市值（元）', money(data.total_market_cap)],
        ['流通市值（元）', money(data.float_market_cap)],
      ].map(([label, value]) => metric(label, value)).join('')}</div>` +
      `<div class="hint">负市盈率通常对应亏损，不能据此判断低估；估值需与同行及增长质量一起核验。</div>`;
  }
  function news(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    const coverage = data.coverage || {};
    const coverageLabels = { announcements: '公司公告', media_news: '媒体新闻' };
    const coverageText = Object.entries(coverage).map(([key, value]) =>
      `<div class="fm-news-meta">${esc(coverageLabels[key] || key)}：${value.status === 'ok' ? '已获取' : value.status === 'empty' ? '近期无符合条件的记录' : '暂不可用'}${value.error ? ' · ' + esc(value.error) : ''}</div>`).join('');
    return '<div class="fm-warning">消息影响仅根据标题规则初筛，未阅读全文。可能利好 / 利空不表示股价必然上涨 / 下跌，请核对公告金额、条件及实施进度。</div>' +
      coverageText + (items.length ? items.map(item =>
        `<article class="fm-news"><div>${link(item.title || '原文', item.url)}</div>` +
        `<div class="fm-news-meta">${esc(item.date || '时间未提供')} · ${esc(item.kind || '动态')} · ${esc(item.source || '来源未提供')}</div>` +
        (item.summary ? `<div class="fm-news-meta">媒体摘要节选：${esc(String(item.summary).slice(0, 50))}${String(item.summary).length > 50 ? '…' : ''}</div>` : '') +
        (item.impact ? rule(item.impact) : '<div class="hint">尚无规则判断，请阅读原文。</div>') + '</article>').join('') : '<div class="hint">近期暂无可用的公司动态。</div>');
  }
  function section(key, value) {
    const warning = value.partial || value.error ? `<div class="fm-warning">部分资料未取得：${esc(value.error || (value.errors || []).join('；') || '请刷新重试')}</div>` : '';
    const failure = `<div class="fm-error">获取失败：${esc(value.error || '该来源暂不可用')}。可点击“刷新数据”重试。</div>`;
    const content = key === 'news' ? (value.status === 'failed' ? failure : '') + news(value.data || {}) :
      value.status === 'failed' ? failure :
      value.status === 'empty' ? '<div class="hint">该来源暂无可用资料，缺失不视为零。</div>' :
        ({ profile, financials, valuation, news }[key])(value.data || {});
    return `<section class="fm-section"><h3>${titles[key]}</h3><div class="fm-source">${link(value.source || '查看来源', value.url)}${value.as_of ? ' · 资料获取时间 ' + esc(value.as_of) : ''}</div>` +
      (value.status === 'failed' ? '' : warning) + content + '</section>';
  }
  function render(data) {
    el('fmTitle').textContent = `${data.name || stock.name || stock.code} · 基本面分析`;
    el('fmMeta').textContent = `${data.symbol} · 生成于 ${data.generated} · ${data.cache?.hit ? '使用近期缓存' : '本次获取'} · 缓存 ${Math.round((data.cache?.ttl_seconds || 900) / 60)} 分钟`;
    const warning = data.status === 'failed' ? '<div class="fm-warning">暂未取得可用资料；各来源错误见下方，刷新可重试。</div>' :
      data.status === 'partial' ? '<div class="fm-warning">部分来源暂无数据或获取失败，已取得的资料仍可查看。</div>' : '';
    el('fmBody').innerHTML = warning + (data.summary?.length ?
      `<section class="fm-section"><h3>本次观察</h3><ul class="fm-summary">${data.summary.map(line => `<li>${esc(line)}</li>`).join('')}</ul></section>` : '') +
      Object.keys(titles).map(key => section(key, data.sections[key])).join('') +
      `<div class="fm-links">${(data.sources || []).map(source => link(source.label, source.url)).join('')}</div>`;
  }
  async function load(refresh = false) {
    if (!active || !stock) return;
    const row = stock, ticket = gate.begin();
    el('fmRefresh').disabled = true;
    el('fmMeta').textContent = '正在获取公司资料、财报、估值与近期动态…';
    el('fmBody').innerHTML = Object.entries(titles).map(([key, title]) =>
      `<section class="fm-section loading"><h3>${title}</h3><div class="fm-loading">正在获取该来源的资料…</div></section>`).join('');
    try {
      const result = await API.fundamentals(row.symbol || row.code, refresh, ticket.signal);
      if (!active || stock !== row || !gate.isCurrent(ticket)) return;
      render(result);
    } catch (error) {
      if (!active || stock !== row || !gate.isCurrent(ticket)) return;
      el('fmMeta').textContent = '基本面加载失败';
      el('fmBody').innerHTML = `<div class="fm-error">${esc(error.message)}。可点击“刷新数据”重试。</div>`;
    } finally {
      if (active && gate.isCurrent(ticket)) el('fmRefresh').disabled = false;
    }
  }
  function open(row, trigger) {
    if (window.Zoom?.isOpen()) Zoom.close();
    stock = { ...row, symbol: normalizeSymbol(row.symbol || row.code) };
    active = true;
    opener = trigger || document.activeElement;
    el('fmTitle').textContent = `${row.name || row.code} · 基本面分析`;
    el('fmBody').scrollTop = 0;
    el('fundamentals').classList.add('on');
    el('fundamentals').setAttribute('aria-hidden', 'false');
    el('fundamentals').focus();
    load(false);
  }
  function close() {
    if (!active) return;
    active = false;
    gate.cancel();
    stock = null;
    el('fundamentals').classList.remove('on');
    el('fundamentals').setAttribute('aria-hidden', 'true');
    if (opener?.isConnected) opener.focus(); else el('btnScan').focus();
    opener = null;
  }
  el('fmClose').addEventListener('click', close);
  el('fmRefresh').addEventListener('click', () => load(true));
  el('fundamentals').addEventListener('click', e => { if (e.target === el('fundamentals')) close(); });
  document.addEventListener('keydown', e => {
    if (!active) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); close(); }
    if (e.key === 'Tab') {
      const focusable = [...el('fundamentals').querySelectorAll('button:not(:disabled), a[href], summary')];
      if (!focusable.length) { e.preventDefault(); return; }
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === el('fundamentals'))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || document.activeElement === el('fundamentals'))) { e.preventDefault(); first.focus(); }
    }
  });
  window.Fundamentals = { open, close, isOpen };
  return { open, close, isOpen };
})();
