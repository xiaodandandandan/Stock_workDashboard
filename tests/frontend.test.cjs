const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, 'web', file), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function payload(symbol = 'sh600000', freq = 'day', n = 150, close = 1000) {
  return {
    symbol, code: symbol.slice(2), name: symbol, freq, unit: freq, bars: 300,
    close: close / 100, amt: 1, cached: false, elapsed_ms: 1, structure: {}, history_total: 0,
    signals: { buy: [], sell: [] }, chart: {
      dates: Array.from({ length: n }, (_, i) => String(20260000 + i)), opt: {}, hist: [],
      pack: { n, d0: '20260000', o: Array(n).fill(close), h: Array(n).fill(close + 100),
        l: Array(n).fill(close - 100), c: Array(n).fill(close), v: Array(n).fill(100),
        bi: [[20, -1, close - 100], [130, 1, close + 100]] },
    },
  };
}

function environment() {
  const elements = new Map(), pending = [];
  let lastDraw = null;
  class Element {
    constructor(id) {
      this.id = id; this.style = {}; this.events = {}; this.dataset = {}; this.disabled = false;
      this.textContent = ''; this.innerHTML = ''; this.clientHeight = 600; this.open = false;
      const classes = new Set();
      this.classList = { add: x => classes.add(x), remove: x => classes.delete(x),
        contains: x => classes.has(x), toggle: (x, on) => on ? classes.add(x) : classes.delete(x) };
      this.parentNode = { replaceChild: n => elements.set(n.id, n) };
    }
    addEventListener(name, fn) { this.events[name] = fn; }
    setAttribute() {}
    querySelectorAll() { return []; }
    appendChild(n) { elements.set(n.id, n); }
    insertAdjacentHTML() {}
    getContext() { return { clearRect() {} }; }
    getBoundingClientRect() { return { left: 0, top: 0, width: 1000, height: 600 }; }
  }
  const el = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
  const ctx = vm.createContext({
    console, AbortController, LAYERS: {}, LAST: {},
    document: { getElementById: el, createElement: () => new Element(''), addEventListener() {} },
    window: { innerHeight: 1000, addEventListener() {} },
    API: { stock: (code, freq, opts, signal) => new Promise((resolve, reject) => pending.push({ code, freq, opts, signal, resolve, reject })) },
    esc: String, fmtDate: String, setupCanvas: () => ({}), draw: (_ctx, d) => { lastDraw = d; },
    bindTip: () => ({ update() {}, hide() {} }),
    requestAnimationFrame: fn => { fn(); return 1; }, layout: () => ({ PL: 52, bw: 10 }),
  });
  for (const file of ['state.js', 'drawer.js', 'zoom.js']) vm.runInContext(read('js/' + file), ctx, { filename: file });
  return {
    ctx, el, pending, Drawer: vm.runInContext('Drawer', ctx), Zoom: vm.runInContext('Zoom', ctx),
    draw: () => lastDraw,
    switchFreq: freq => el('dwFreq').events.click({ target: { closest: () => ({ dataset: { freq } }) } }),
  };
}

test('cached day selection invalidates a pending minute response', async () => {
  const e = environment();
  e.Drawer.open({ code: '600000', symbol: 'sh600000' });
  e.pending.shift().resolve(payload()); await tick();
  e.switchFreq('m60');
  const minute = e.pending.shift();
  e.switchFreq('day'); await tick();
  assert.equal(minute.signal.aborted, true);
  minute.resolve(payload('sh600000', 'm60', 150, 2000)); await tick();
  assert.equal(e.Drawer.snapshot().freq, 'day');
  assert.equal(e.Drawer.snapshot().d.c[0], 1000);
});

test('loading another stock clears the old canvas snapshot and disables zoom', async () => {
  const e = environment();
  e.Drawer.open({ code: '600000' }); e.pending.shift().resolve(payload()); await tick();
  e.Drawer.open({ code: '600001' });
  assert.equal(e.Drawer.snapshot(), null);
  assert.equal(e.el('dwZoom').disabled, true);
  e.Zoom.open();
  assert.equal(e.Zoom.isOpen(), false);
  e.pending.shift().resolve(payload('sh600001')); await tick();
  assert.equal(e.Drawer.snapshot().symbol, 'sh600001');
});

test('closing the drawer rejects a late response even if transport ignores abort', async () => {
  const e = environment();
  e.Drawer.open({ code: '600000' }); const pending = e.pending.shift();
  e.Drawer.close(); pending.resolve(payload()); await tick();
  assert.equal(pending.signal.aborted, true);
  assert.equal(e.Drawer.snapshot(), null);
});

test('long-history response for A cannot overwrite newly opened B', async () => {
  const e = environment();
  e.Drawer.open({ code: '600000', name: 'A' }); e.pending.shift().resolve(payload()); await tick();
  e.Zoom.open();
  const done = e.el('zmMore').events.click(); const a = e.pending.shift();
  e.Zoom.close();
  e.Drawer.open({ code: '600001', name: 'B' }); e.pending.shift().resolve(payload('sh600001', 'day', 150, 2000)); await tick();
  e.Zoom.open();
  a.resolve(payload('sh600000', 'day', 300, 8888)); await done;
  assert.equal(a.signal.aborted, true);
  assert.equal(e.el('zmTitle').textContent, 'B · day');
  assert.equal(e.draw().c[0], 2000);
  assert.equal(e.el('zmMore').style.display, '');
});

test('same-stock long-history request updates normally', async () => {
  const e = environment();
  e.Drawer.open({ code: '600000' }); e.pending.shift().resolve(payload()); await tick();
  e.Zoom.open(); const done = e.el('zmMore').events.click();
  e.pending.shift().resolve(payload('sh600000', 'day', 300)); await done;
  assert.equal(e.draw().n, 300);
  assert.equal(e.el('zmMore').style.display, 'none');
});

test('late report from the old frequency is not shown', async () => {
  const e = environment();
  e.Drawer.open({ code: '600000' }); e.pending.shift().resolve(payload()); await tick();
  e.el('dwReportBox').open = true;
  const done = e.el('dwReportBox').events.toggle(); const report = e.pending.shift();
  e.switchFreq('m30'); e.pending.shift().resolve(payload('sh600000', 'm30')); await tick();
  report.resolve({ ...payload(), report: 'OLD DAY REPORT' }); await done;
  assert.equal(e.el('dwReport').textContent, '');
});

test('history cards display the title supplied by the chart contract', async () => {
  const e = environment(), data = payload();
  data.chart.hist = [{ i: 10, side: 's', t: '一类卖点', ty: '一类卖点', n: '1', d: '20260101',
    alive: 2, live: 1, entry: 10, stop: 11, ret: -1, why: [] }];
  e.Drawer.open({ code: '600000' }); e.pending.shift().resolve(data); await tick();
  assert.match(e.el('dwCard').innerHTML, /一类卖点/);
  assert.doesNotMatch(e.el('dwCard').innerHTML, /<b>undefined<\/b>/);
  assert.match(e.el('dwCard').innerHTML, /引擎持续报出/);
});

test('stroke slicing retains the closest outside neighbors', () => {
  const e = environment();
  const slice = vm.runInContext('sliceStrokePoints', e.ctx);
  const points = [[20, -1, 900], [130, 1, 1100], [180, -1, 950]];
  const plain = x => JSON.parse(JSON.stringify(x));
  assert.deepEqual(plain(slice(points, 90, 150)), [[-70, -1, 900], [40, 1, 1100], [90, -1, 950]]);
  assert.deepEqual(plain(slice(points, 40, 80)), [[-20, -1, 900], [90, 1, 1100]]);
  assert.deepEqual(plain(slice([], 40, 80)), []);
});

test('zoom actually passes crossing strokes to the drawing layer', async () => {
  const e = environment();
  e.Drawer.open({ code: '600000' }); e.pending.shift().resolve(payload()); await tick();
  e.Zoom.open();
  e.el('zmPresets').events.click({ target: { closest: () => ({ dataset: { len: '60' } }) } });
  assert.equal(e.draw().n, 60);
  assert.equal(e.draw().bi.length, 2);
  assert.equal(e.draw().bi[0][0], -70);
});

function apiEnvironment(fetch) {
  const ctx = vm.createContext({ AbortController, URL, setTimeout, clearTimeout, location: { origin: 'http://localhost' }, fetch });
  vm.runInContext(read('js/state.js'), ctx);
  vm.runInContext(read('js/api.js'), ctx);
  return vm.runInContext('API', ctx);
}

test('API rejects mismatched symbols, periods and malformed candle arrays', async () => {
  for (const bad of [payload('sz000001'), payload('sh000001', 'm60'), { chart: {} }]) {
    const api = apiEnvironment(async () => ({ ok: true, json: async () => bad }));
    await assert.rejects(api.stock('sh000001', 'day'));
  }
  const bad = payload('sh000001'); bad.chart.pack.c.pop();
  await assert.rejects(apiEnvironment(async () => ({ ok: true, json: async () => bad })).stock('sh000001', 'day'));
});

test('API propagates HTTP errors for scan POSTs', async () => {
  const api = apiEnvironment(async () => ({ ok: false, status: 400, json: async () => ({ error: 'invalid workers' }) }));
  await assert.rejects(api.runScan({ workers: 0 }), err => err.status === 400 && err.message === 'invalid workers');
});

test('API keeps explicit exchange and propagates cancellation', async () => {
  let seen;
  const api = apiEnvironment(async (url, init) => { seen = { url, init }; return { ok: true, json: async () => payload('sh000001') }; });
  const abort = new AbortController(); abort.abort();
  await api.stock('sh000001', 'day', {}, abort.signal);
  assert.equal(seen.url.searchParams.get('code'), 'sh000001');
  assert.equal(seen.init.signal.aborted, true);
});

test('all shipped classic scripts parse', () => {
  for (const dir of ['js', 'vendor']) for (const name of fs.readdirSync(path.join(root, 'web', dir))) {
    if (name.endsWith('.js')) new vm.Script(read(dir + '/' + name), { filename: name });
  }
});
