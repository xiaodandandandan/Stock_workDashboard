#!/usr/bin/env node
/* 用 CDP（Chrome DevTools Protocol）驱动无头 Chrome 做**点击验证**。
 *
 * 为什么需要它：`--screenshot` 只能看静态首屏，「点 tab 没反应」「点了没筛选」
 * 这类问题在静态截图里**完全看不出来**（画面本身是对的）。前端问题的验证必须
 * 能真的点、真的读回 DOM。
 *
 * 用法:
 *   node tools/cdp.js                    # 打开工作台，跑内置的一组交互检查
 *   PAGE_URL=... node tools/cdp.js       # 换目标页
 *
 * 前置: 先起一个带调试端口的 Chrome ——
 *   chrome --headless=new --remote-debugging-port=9333 --user-data-dir=<tmp> <url>
 * 本脚本自己负责连接，不负责起浏览器（见 tools/run-cdp.sh）。
 */
const PORT = +(process.env.CDP_PORT || 9333);

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.waiters = new Map();
    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.id && this.waiters.has(m.id)) {
        const w = this.waiters.get(m.id);
        this.waiters.delete(m.id);
        w(m);
      }
    });
  }
  /* ★ 必须有超时：CDP 若因 origin 校验等原因不回包，await 会永久挂起 ——
     现象是「脚本没有任何输出、被外部超时杀掉」，排查成本极高。 */
  send(method, params = {}, ms = 20000) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      const t = setTimeout(() => {
        this.waiters.delete(id);
        rej(new Error('CDP 超时: ' + method));
      }, ms);
      this.waiters.set(id, m => { clearTimeout(t); res(m); });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate',
      { expression: expr, awaitPromise: true, returnByValue: true });
    const ex = r.result && r.result.exceptionDetails;
    if (ex) throw new Error(ex.exception && ex.exception.description || JSON.stringify(ex));
    return r.result && r.result.result ? r.result.result.value : null;
  }
  async shot(path, w = 1600, h = 1150) {
    await this.send('Emulation.setDeviceMetricsOverride',
      { width: w, height: h, deviceScaleFactor: 1, mobile: false });
    const r = await this.send('Page.captureScreenshot', { format: 'png' });
    require('fs').writeFileSync(path, Buffer.from(r.result.data, 'base64'));
    return path;
  }
}

const connect = url => new Promise((res, rej) => {
  const ws = new WebSocket(url);
  ws.addEventListener('open', () => res(new CDP(ws)));
  ws.addEventListener('error', e => rej(new Error('WS 连接失败: ' + (e.message || ''))));
});
const wait = ms => new Promise(r => setTimeout(r, ms));

/* ★ 这里不能用内置 fetch：本机环境变量里挂着代理，Node 的 fetch 在某些组合下
   会直接 `fetch failed` 且 cause 被吞掉。CDP 是本地回环，用 http 模块直连最稳。 */
const httpJson = path => new Promise((res, rej) => {
  require('http').get({ host: '127.0.0.1', port: PORT, path, timeout: 5000 }, r => {
    let b = '';
    r.on('data', d => (b += d));
    r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(new Error('非 JSON: ' + b.slice(0, 120))); } });
  }).on('error', e => rej(new Error('HTTP ' + path + ' 失败: ' + e.message)))
    .on('timeout', function () { this.destroy(); rej(new Error('HTTP ' + path + ' 超时')); });
});

const PAGE_URL = process.env.PAGE_URL || 'http://127.0.0.1:8765/';
const CHROME = process.env.CHROME_BIN || 'C:/Program Files/Google/Chrome/Application/chrome.exe';

/* ★ 自己起浏览器，不依赖外部脚本先把 Chrome 拉起来：Git Bash 里 `chrome &` 起的
   进程会随命令结束一起被回收，下一条命令再连就是 ECONNREFUSED。detached + unref
   让 browser 独立于本脚本存活，整个流程一条命令跑完。 */
async function ensureCDP() {
  try { await httpJson('/json/version'); return; }
  catch { /* 没在跑，自己起 */ }
  const { spawn } = require('child_process');
  const dir = require('os').tmpdir() + '/cdp-profile-' + Date.now();
  const p = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--remote-debugging-port=' + PORT, '--remote-allow-origins=*',
    '--user-data-dir=' + dir, '--window-size=1600,1150', PAGE_URL],
    { detached: true, stdio: 'ignore' });
  p.unref();
  for (let i = 0; i < 50; i++) {
    await wait(400);
    try { await httpJson('/json/version'); return; } catch { /* 继续等 */ }
  }
  throw new Error('Chrome 调试端口没起来（' + PORT + '）');
}

async function main() {
  await ensureCDP();
  const list = await httpJson('/json/list');
  const page = list.find(t => t.type === 'page' && t.url.startsWith(PAGE_URL.slice(0, 24)))
            || list.find(t => t.type === 'page');
  if (!page) throw new Error('没有 page target —— Chrome 起来了吗？');
  console.log('目标页:', page.url);
  const c = await connect(page.webSocketDebuggerUrl);
  await c.send('Runtime.enable');
  await c.send('Page.enable');

  // 等首屏数据落地
  for (let i = 0; i < 60; i++) {
    const n = await c.eval("document.querySelectorAll('#tb tr').length");
    if (n) break;
    await wait(400);
  }

  const SNAP = `(() => {
    const rows = [...document.querySelectorAll('#tb tr')];
    const tags = rows.map(r => [...r.querySelectorAll('.tag')].map(t => t.textContent).join('/'));
    const cnt = {};
    rows.forEach(t => { cnt[t] = (cnt[t] || 0) + 1; });
    return {
      tabOn: (document.querySelector('.tab.on') || {}).textContent || '',
      n: rows.length,
      hint: document.getElementById('hint').textContent,
      tagSummary: Object.entries(
        rows.map(r => [...r.querySelectorAll('.tag')].map(t => t.textContent).join('|'))
      ).reduce((a, [, v]) => (a[v] = (a[v] || 0) + 1, a), {}),
      first: rows.length ? [...rows[0].children].map(td => td.textContent.trim()).join(' · ') : '',
    };
  })()`;

  const log = (tag, o) => console.log('\n=== ' + tag + ' ===\n' + JSON.stringify(o, null, 1));

  // EXPR 模式：直接对页面求值（调试单点问题用，比一遍遍改脚本快）。
  // EXPR_FILE 走文件，免去 shell 引号转义 —— 表达式里带引号/换行时只能用它。
  const expr = process.env.EXPR
    || (process.env.EXPR_FILE ? require('fs').readFileSync(process.env.EXPR_FILE, 'utf8') : null);
  if (expr) {
    log('EXPR', await c.eval(expr));
    if (process.env.SHOT) await c.shot(process.env.SHOT, +(process.env.SHOT_W || 1600),
                                       +(process.env.SHOT_H || 1150));
    return;
  }

  log('初始状态', await c.eval(SNAP));

  await c.eval("document.querySelectorAll('.tab')[1].click()");
  log('点 tab[1]「一类买点」', await c.eval(SNAP));

  await c.eval("document.querySelectorAll('.tab')[4].click()");
  log('点 tab[4]「一类卖点」', await c.eval(SNAP));

  await c.eval("document.querySelectorAll('.st[data-tab]')[0].click()");
  log('点统计卡[0]（一类买点）', await c.eval(SNAP));

  await c.eval("document.querySelectorAll('.tab')[0].click()");     // 回到「当日买点」
  log('点表格里的类型标签', await c.eval(`(() => {
    const tg = document.querySelector('#tb .tag[data-type]');
    if (!tg) return '(没有标签)';
    const t = tg.dataset.type;
    tg.click();
    const tags = [...new Set([...document.querySelectorAll('#tb .tag')].map(x => x.dataset.type))];
    return { clicked: t, tabOn: (document.querySelector('.tab.on') || {}).textContent || '',
             rows: document.querySelectorAll('#tb tr').length, onlyTags: tags.join(',') };
  })()`));

  // ---- 抽屉：每个信号的判据明细 ----
  const picks = await c.eval(`(() => {
    const out = {};
    for (const r of D.rows) {
      for (const h of r.buy || [])  if (!out.b && h.type === '一类买点') out.b = {code:r.code, name:r.name};
      for (const h of r.sell || []) if (!out.s && h.type === '一类卖点') out.s = {code:r.code, name:r.name};
    }
    return out;
  })()`);

  const DW = `(() => {
    const card = document.getElementById('dwCard');
    const wi = [...card.querySelectorAll('.wi')];
    return {
      meta: document.getElementById('dwMeta').innerText.replace(/\\n/g, ' '),
      // 卡片 = 图上编号的逐条展开，卡片数应当等于窗口内信号数
      sigCards: wi.length,
      histMarks: (window.LAST && LAST.hist || []).length,
      whyRows: card.querySelectorAll('table.wt tr').length,
      heads: [...card.querySelectorAll('.why-h')].map(x => x.textContent.trim()).join(' / '),
      layersOn: Object.entries(LAYERS).filter(([, v]) => v).map(([k]) => k).join(','),
      first: wi.length ? wi[0].innerText.replace(/\\n+/g, ' | ').slice(0, 320) : '(无信号卡)',
    };
  })()`;

  for (const [k, p] of Object.entries(picks || {})) {
    await c.eval(`Drawer.open({code:${JSON.stringify(p.code)}, name:${JSON.stringify(p.name)}})`);
    await wait(2800);
    log(`抽屉 [${k === 'b' ? '有买点' : '有卖点'}] · ` + p.name, await c.eval(DW));
  }
  if (!Object.keys(picks || {}).length) console.log('\n（没找到样本票，跳过抽屉检查）');

  const shot = process.env.SHOT || 'archive/screenshots/cdp_check.png';
  await c.shot(shot);
  console.log('\n截图 →', shot);
}

/* ★ 跑完必须主动 exit：WebSocket 还开着，事件循环不会自己结束，
   进程会一直挂着直到被外部超时杀掉 —— 表现是「脚本明明打完了日志，
   命令却迟迟不返回」，而且此时管道里没 flush 的输出还会一起丢掉。 */
const done = () => process.exit(0);

main().then(done, e => { console.error('FAILED:', e.message); process.exit(1); });
