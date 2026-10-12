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
  function readout(value, formatter = money) {
    const cls = typeof value === 'number' && Number.isFinite(value) ? value > 0 ? 'fm-positive' : value < 0 ? 'fm-negative' : '' : '';
    return `<span class="${cls}">${esc(formatter(value))}</span>`;
  }
  function table(headings, rows) {
    return `<div class="fm-report-table"><table><thead><tr>${headings.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>` +
      rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('') + '</tbody></table></div>';
  }
  function source(value) {
    return `<div class="fm-source">${link(value.source || '查看数据来源', value.url)}${value.as_of ? ' · 获取时间 ' + esc(value.as_of) : ''}</div>`;
  }
  function state(value) {
    if (value.status === 'failed') return `<div class="fm-warning">来源获取失败：${esc(value.error || '暂不可用')}。可刷新重试。</div>`;
    return value.partial || value.error ? `<div class="fm-warning">部分资料未取得：${esc(value.error || (value.errors || []).join('；'))}</div>` : '';
  }
  function chapter(index, title, content) {
    const numerals = ['一', '二', '三', '四', '五', '六', '七', '八', '九'];
    return `<section id="fmChapter${index}" class="fm-section fm-chapter"><h3><span>${numerals[index - 1]}</span>${title}</h3>${content}</section>`;
  }
  function chart(id, label) {
    return `<div id="${id}" class="fm-chart" role="img" aria-label="${esc(label)}"></div><div id="fmCaption${id.slice(-1)}" class="fm-caption"></div>`;
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
  function render(data) {
    FundamentalCharts.dispose();
    const { profile: company, financials: accounts, valuation: quotes, news: updates } = data.sections;
    const facts = accounts.data, latest = facts.reports?.[0] || facts.annual_reports?.[0] || {}, annual = facts.annual_reports || [];
    const report = data.report || {}, coverage = report.annual_coverage || {}, business = facts.business_segments || {};
    const reportList = rows => table(['报告期', '营业收入', '营收同比', '归母净利润', '扣非归母净利润', '经营现金流', '毛利率'], rows.map(r => [
      esc(r.report_date), esc(money(r.revenue)), readout(r.revenue_yoy, v => number(v, '%')),
      readout(r.net_profit), readout(r.net_profit_excl), readout(r.operating_cashflow), esc(number(r.gross_margin, '%')),
    ]));
    const summaryRows = (report.conclusions || []).map(item => [esc(item.title), esc(item.evidence),
      `${esc(item.interpretation)}<div class="fm-news-meta">${esc(item.kind || '规则解读')}</div>`]);
    const kpis = (report.kpis || []).map(item => {
      const formatted = item.unit === '元' ? money(item.value) : number(item.value, item.unit || '');
      const cls = typeof item.value === 'number' && ['net_profit', 'net_profit_excl', 'operating_cashflow', 'annual_net_profit_excl_sum'].includes(item.field) ?
        item.value < 0 ? 'fm-negative' : item.value > 0 ? 'fm-positive' : '' : '';
      return `<div class="fm-metric"><div class="k">${esc(item.label)}</div><div class="v ${cls}">${esc(formatted)}</div><div class="fm-news-meta">${esc(item.report_date || latest.report_date || '期次待核验')}</div></div>`;
    }).join('');
    const profileContent = company.status === 'ok' ? profile(company.data) : '<div class="hint">暂无公司业务资料。</div>';
    const mixRows = (business.items || []).map(item => [esc(item.name), esc(money(item.revenue)), esc(number(item.share_pct, '%')), esc(number(item.gross_margin, '%'))]);
    const riskRows = (report.risk_exposure || []).map(item => [esc(item.name), esc(money(item.amount)), esc(number(item.ratio, ' 倍')), esc([item.status_label || item.status, item.notice].filter(Boolean).join(' · '))]);
    const cashRows = (report.cash_quality || []).map(item => [esc(item.report_date), readout(item.operating_cashflow), readout(item.net_profit),
      readout(item.financing_cashflow), esc(number(item.cash_content, ' 倍')), esc(item.status_label || item.status)]);
    const regulatoryRows = (report.regulatory_checks || []).map(item => [esc(item.label), '待核验', esc(item.evidence), link('核验入口', item.url)]);
    const scenarioRows = (report.scenarios || []).map(item => [esc(item.label), esc(item.condition), esc(item.path), esc(item.judgement)]);
    const checklistRows = (report.checklist || []).map(item => [esc(item.label), esc(item.value ?? '—'), esc(item.evidence), esc(item.status_label || item.status)]);
    const nav = ['结论摘要', '收入结构', '盈利质量', '净资产', '风险敞口', '现金流', '估值', '情景分析', '跟踪清单'];
    el('fmTitle').textContent = `${data.name || stock.name || stock.code} · 基本面分析`;
    el('fmMeta').textContent = `${data.symbol} · ${report.label || '板块待核验'} · ${company.data.industry || '行业待核验'} · 最新财报 ${latest.report_date || '暂无'} · 人民币 · ${data.cache?.hit ? '近期缓存' : '本次获取'} ${data.generated}`;
    const warning = data.status === 'failed' ? '<div class="fm-warning">暂未取得可用资料；各来源错误见下方，刷新可重试。</div>' :
      data.status === 'partial' ? '<div class="fm-warning">部分来源暂无数据或获取失败，已取得的资料仍可查看。</div>' : '';
    el('fmBody').innerHTML = warning +
      `<div class="fm-tagline"><b>${esc(report.headline || '已取得资料按指标列示，结论需核验原文。')}</b><div>数据截止 ${esc(quotes.data.date || data.generated)} · 最新披露 ${esc(latest.announced_date || '待核验')}</div><span class="fm-verification">原文核验：待核验</span></div>` +
      `<div class="fm-nav" aria-label="基本面章节">${nav.map((title, i) => `<button data-fm-jump="fmChapter${i + 1}">${title}</button>`).join('')}<button data-fm-jump="fmNews">公告与新闻</button></div>` +
      chapter(1, '结论摘要', source(accounts) + state(accounts) + `<div class="fm-metrics fm-kpis">${kpis}</div>` +
        (summaryRows.length ? table(['结论要点', '数字证据', '解读与性质'], summaryRows) : '<div class="hint">暂无足够财报数据形成结论。</div>') +
        `<div class="fm-note">${esc(report.verification?.notice || '公开数据库数据尚未逐项核对年报 PDF 原文；自动计算与来源读数分开展示，缺失不视为零。')}</div>`) +
      chapter(2, '收入结构与毛利质量', source(company) + state(company) +
        `<details class="fm-company"><summary>公司业务与经营范围</summary>${profileContent}</details>` +
        `<div class="fm-metrics">${metric('最新营业收入', money(latest.revenue))}${metric('最新毛利率', number(latest.gross_margin, '%'))}${metric('主营构成报告期', business.report_date || '暂无')}</div>` +
        (business.url ? `<div class="fm-source">${link(business.source || '主营业务构成来源', business.url)} · ${esc(business.classification || '来源分类')}</div>` : '') +
        (business.error ? `<div class="fm-warning">主营构成：${esc(business.error)}</div>` : '') + chart('chart1', '主营业务收入占比') +
        (mixRows.length ? table(['业务板块', '收入', '收入占比', '毛利率'], mixRows) : '<div class="hint">暂无主营业务构成；公司行业标签不能代替收入结构。</div>')) +
      chapter(3, '年度业绩全景与盈利质量', source(accounts) +
        `<div class="fm-note">近十年为目标范围，当前取得 ${esc(coverage.count ?? annual.length)} 期完整年报，${esc(coverage.start || '—')} 至 ${esc(coverage.end || '—')}。${esc(coverage.notice || '年报与季度累计读数分开，缺失年份不插值。')}${coverage.missing_years?.length ? ' 未取得年度：' + esc(coverage.missing_years.join('、')) : ''}</div>` +
        chart('chart2', '年度营业收入、归母与扣非净利润') + (annual.length ? reportList(annual) : '<div class="hint">暂无完整年报序列。</div>') +
        `<details class="fm-recent"><summary>最新季度与近期财报事实（最多 8 期）</summary><div class="fm-note">报告期 ${esc(latest.report_date || '暂无')} · 披露日期 ${esc(latest.announced_date || '待核验')}。收入、利润和现金流为该期累计口径，须对照上年同期；归母与扣非并列，差额不等于已核验的非经常性损益明细。</div>` + reportList(facts.reports || []) +
        `<div class="fm-metrics">${metric('ROE', number(latest.roe, '%'))}${metric('资产负债率', number(latest.debt_ratio, '%'))}${metric('每股收益（元）', number(latest.eps))}</div></details>`) +
      chapter(4, '净资产安全垫与商誉', source(accounts) +
        `<div class="fm-metrics">${metric('归母净资产', money(latest.parent_equity))}${metric('商誉', money(latest.goodwill))}${metric('商誉 / 归母净资产', latest.parent_equity > 0 && typeof latest.goodwill === 'number' ? number(latest.goodwill / latest.parent_equity, ' 倍') : '不适用 / 待核验')}</div>` +
        chart('chart3', '年度归母净资产与商誉') +
        '<div class="fm-note">使用归属于母公司股东的权益；不以总资产减总负债替代。净资产为零、负值或缺失时不计算敞口倍数，账面净资产不代表可立即变现的安全垫。</div>') +
      chapter(5, '资产质量与风险敞口', source(accounts) + chart('chart4', '商誉、存货、应收与对外担保敞口') +
        table(['项目', '金额', '占归母净资产', '口径与核验状态'], riskRows) +
        '<div class="fm-note">商誉、存货和应收为账面存量，不能相加视作已经发生的损失；担保缺失不代表没有担保，减值准备及担保责任需回年报附注核验。</div>') +
      chapter(6, '现金流质量', source(accounts) + chart('chart5', '年度经营现金流、归母利润与筹资现金流') +
        table(['报告期', '经营现金流', '归母净利润', '筹资现金流', '现金含量', '读法'], cashRows) +
        '<div class="fm-note">现金含量 = 同期经营现金流 ÷ 正归母净利润。利润非正时不计算；银行等金融机构需结合存贷款及资金业务，不能套用一般企业评分。</div>') +
      chapter(7, '估值锚点与有效性', source(quotes) + state(quotes) + valuation(quotes.data) +
        `<ul class="fm-observations">${(report.valuation_notes || []).map(note => `<li>${esc(note)}</li>`).join('')}</ul>`) +
      chapter(8, '条件情景分析', '<div class="fm-note">以下是待验证的条件路径，未赋予概率或选定基准情景；不提供目标价。</div>' +
        table(['情景', '触发条件', '盈利路径', '对应判断'], scenarioRows)) +
      chapter(9, '跟踪清单与规则核验', table(['跟踪指标', '当前读数', '核验要点', '状态'], checklistRows) +
        '<h4>审计、风险警示与隐藏事项</h4>' + table(['核验项目', '状态', '当前证据与缺口', '原文入口'], regulatoryRows) +
        `<div class="fm-links">${(report.verification?.links || []).map(entry => link(entry.label, entry.url)).join('')}</div>`) +
      `<section id="fmNews" class="fm-section"><h3>近期公司动态 · 公告与新闻</h3>${source(updates)}${state(updates)}${news(updates.data)}</section>` +
      `<section class="fm-section fm-disclosure"><h3>数据来源与报告口径</h3><div class="fm-links">${(data.sources || []).map(entry => link(entry.label, entry.url)).join('')}</div>` +
      '<p>财报与行情时点分别标注；金额以人民币元为基准，金额图表统一为亿元，收入占比图使用百分比。年报与季度累计数据分开展示，自动计算不等同于原文核验；基本面与短期股价走势无必然关系。</p><p>以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。</p></section>';
    FundamentalCharts.render(data);
  }
  async function load(refresh = false) {
    if (!active || !stock) return;
    const row = stock, ticket = gate.begin();
    FundamentalCharts.dispose();
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
    FundamentalCharts.dispose();
    stock = null;
    el('fundamentals').classList.remove('on');
    el('fundamentals').setAttribute('aria-hidden', 'true');
    if (opener?.isConnected) opener.focus(); else el('btnScan').focus();
    opener = null;
  }
  el('fmClose').addEventListener('click', close);
  el('fmRefresh').addEventListener('click', () => load(true));
  el('fmBody').addEventListener('click', e => {
    const jump = e.target.closest?.('[data-fm-jump]');
    const target = jump && document.getElementById(jump.dataset.fmJump);
    if (target) {
      const body = el('fmBody'), navigation = body.querySelector('.fm-nav');
      const top = body.scrollTop + target.getBoundingClientRect().top - body.getBoundingClientRect().top - (navigation?.getBoundingClientRect().height || 0) - 10;
      body.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }
  });
  el('fundamentals').addEventListener('click', e => { if (e.target === el('fundamentals')) close(); });
  document.addEventListener('keydown', e => {
    if (!active) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); close(); }
    if (e.key === 'Tab') {
      const focusable = [...el('fundamentals').querySelectorAll('button:not(:disabled), a[href], summary')].filter(node => node.getClientRects().length);
      if (!focusable.length) { e.preventDefault(); return; }
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === el('fundamentals'))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || document.activeElement === el('fundamentals'))) { e.preventDefault(); first.focus(); }
    }
  });
  window.Fundamentals = { open, close, isOpen };
  return { open, close, isOpen };
})();
