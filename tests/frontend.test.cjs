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
  const elements = new Map(), pending = [], frames = new Map();
  let lastDraw = null, drawCount = 0, nextFrame = 1;
  class Element {
    constructor(id) {
      this.id = id; this.style = {}; this.events = {}; this.dataset = {}; this.disabled = false;
      this.textContent = ''; this.innerHTML = ''; this.clientHeight = 600; this.open = false;
      this.capturedPointers = new Set();
      const classes = new Set();
      this.classList = { add: x => classes.add(x), remove: x => classes.delete(x),
        contains: x => classes.has(x), toggle: (x, on) => on ? classes.add(x) : classes.delete(x) };
      this.parentNode = { replaceChild: n => elements.set(n.id, n) };
    }
    addEventListener(name, fn) { this.events[name] = fn; }
    setPointerCapture(id) { this.capturedPointers.add(id); }
    releasePointerCapture(id) { this.capturedPointers.delete(id); }
    hasPointerCapture(id) { return this.capturedPointers.has(id); }
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
    esc: String, fmtDate: String, setupCanvas: () => ({}), draw: (_ctx, d) => { lastDraw = d; drawCount++; },
    bindTip: () => ({ update() {}, hide() {} }),
    requestAnimationFrame: fn => { const id = nextFrame++; frames.set(id, fn); return id; },
    cancelAnimationFrame: id => frames.delete(id), layout: () => ({ PL: 52, bw: 10 }),
  });
  for (const file of ['state.js', 'drawer.js', 'zoom.js']) vm.runInContext(read('js/' + file), ctx, { filename: file });
  return {
    ctx, el, pending, Drawer: vm.runInContext('Drawer', ctx), Zoom: vm.runInContext('Zoom', ctx),
    draw: () => lastDraw,
    drawCount: () => drawCount,
    pendingFrames: () => frames.size,
    flushFrames: () => {
      const batch = [...frames.values()]; frames.clear();
      for (const fn of batch) fn(0);
    },
    pointer: (name, values = {}) => el('zmcv').events[name]?.({
      pointerId: 1, pointerType: 'mouse', button: 0, buttons: name === 'pointerup' ? 0 : 1,
      clientX: 500, clientY: 200, preventDefault() {}, ...values,
    }),
    switchFreq: freq => el('dwFreq').events.click({ target: { closest: () => ({ dataset: { freq } }) } }),
  };
}

async function openZoom(data = payload()) {
  const e = environment();
  e.Drawer.open({ code: data.code, symbol: data.symbol });
  e.pending.shift().resolve(data); await tick();
  e.Zoom.open();
  return e;
}

function extendedHistory(data, n = 300) {
  const longer = payload(data.symbol, data.freq, n, data.chart.pack.c[0]);
  const start = Number(data.chart.dates[0]) - (n - data.chart.dates.length);
  longer.chart.dates = Array.from({ length: n }, (_, i) => String(start + i));
  longer.chart.pack.d0 = longer.chart.dates[0];
  return longer;
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

test('drawer replaces stale unclassified label with fetched industry', async () => {
  const e = environment();
  e.Drawer.open({ code: '600000', industry: '未分类' });
  e.pending.shift().resolve({ ...payload(), industry: '金融' }); await tick();
  assert.equal(e.el('dwInd').textContent, '金融');
});

test('drawer shows scan tracking while chart analysis is pending or unavailable', async () => {
  const e = environment();
  e.Drawer.open({ code: '600000', scan_at: '2026-10-09 15:00:00', close: 10,
    tracking: { price: 9, change_pct: -10, date: '2026-10-12 15:00:00', buy_stop_breached: true } });
  assert.match(e.el('dwMeta').innerHTML, /扫描后至今涨跌幅.*-10\.00%/);
  assert.match(e.el('dwMeta').innerHTML, /触及原买点失效位/);
  e.pending.shift().reject(new Error('chart unavailable')); await tick();
  assert.match(e.el('dwMeta').innerHTML, /跟踪扫描 2026-10-09/);
  assert.match(e.el('dwCard').innerHTML, /chart unavailable/);
});

test('drawer receives quotes before and after chart load without refetching or redrawing', async () => {
  const e = environment(), scanAt = '2026-10-09 15:00:00';
  e.Drawer.open({ code: '600000', scan_at: scanAt, close: 10 });
  assert.match(e.el('dwMeta').innerHTML, /扫描后至今涨跌幅.*—/);
  const chartRequest = e.pending.shift();
  const quote = { price: 12, change_pct: 20, date: '2026-10-12 15:00:00', buy_stop_breached: false };
  e.Drawer.updateTracking('sh600000', quote, scanAt);
  assert.match(e.el('dwMeta').innerHTML, /price-up">\+20\.00%/);
  assert.match(e.el('dwMeta').innerHTML, /K 线载入中/);
  assert.equal(e.pending.length, 0);
  assert.equal(e.drawCount(), 0);
  e.Drawer.updateTracking('sh600000', { ...quote, price: 9, change_pct: -10 }, scanAt);
  assert.match(e.el('dwMeta').innerHTML, /price-down">-10\.00%/);
  chartRequest.resolve(payload()); await tick();
  assert.match(e.el('dwMeta').innerHTML, /price-down">-10\.00%/);
  assert.match(e.el('dwMeta').innerHTML, /K 线末价/);
  const draws = e.drawCount();
  assert.equal(draws, 1);
  e.Drawer.updateTracking('sh600000', { ...quote, price: 10.5, change_pct: 5 }, scanAt);
  assert.match(e.el('dwMeta').innerHTML, /price-up">\+5\.00%/);
  assert.match(e.el('dwMeta').innerHTML, /K 线末价/);
  assert.equal(e.pending.length, 0);
  assert.equal(e.drawCount(), draws);
  e.Drawer.updateTracking('sh600000', undefined, scanAt);
  assert.match(e.el('dwMeta').innerHTML, /扫描后至今涨跌幅.*—/);
  assert.match(e.el('dwMeta').innerHTML, /报价时间 未获取/);
  assert.match(e.el('dwMeta').innerHTML, /K 线末价/);
  assert.equal(e.drawCount(), draws);
});

test('drawer tracking updates preserve chart failure state', async () => {
  const e = environment(), scanAt = '2026-10-09 15:00:00';
  e.Drawer.open({ code: '600000', symbol: 'sh600000', scan_at: scanAt, close: 10 });
  e.pending.shift().reject(new Error('chart unavailable')); await tick();
  const card = e.el('dwCard').innerHTML;
  e.Drawer.updateTracking('sh600000', { price: 9, change_pct: -10,
    date: '2026-10-12 15:00:00', buy_stop_breached: true }, scanAt);
  assert.match(e.el('dwMeta').innerHTML, /price-down">-10\.00%/);
  assert.match(e.el('dwMeta').innerHTML, /触及原买点失效位/);
  assert.doesNotMatch(e.el('dwMeta').innerHTML, /K 线载入中|K 线末价/);
  assert.equal(e.el('dwCard').innerHTML, card);
  assert.equal(e.pending.length, 0);
  assert.equal(e.drawCount(), 0);
});

test('drawer ignores tracking for another exchange, scan, invalid symbol, or closed view', () => {
  const e = environment(), scanAt = '2026-10-09 15:00:00';
  e.Drawer.open({ code: '000001', symbol: 'sh000001', scan_at: scanAt, close: 3000 });
  const before = e.el('dwMeta').innerHTML;
  const quote = { price: 3300, change_pct: 10, date: '2026-10-12 15:00:00' };
  for (const [symbol, stamp] of [['sz000001', scanAt], ['sh000001', '2026-10-10 15:00:00'],
                               ['invalid', scanAt]]) {
    e.Drawer.updateTracking(symbol, quote, stamp);
    assert.equal(e.el('dwMeta').innerHTML, before);
  }
  e.Drawer.close();
  e.Drawer.updateTracking('sh000001', quote, scanAt);
  assert.equal(e.el('dwMeta').innerHTML, before);
  assert.equal(e.drawCount(), 0);
});

test('scan header exposes failed and partial industry data', () => {
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '', style: {} });
    return elements.get(id);
  };
  const ctx = vm.createContext({ console, AbortController, clearTimeout,
    document: { getElementById: el },
    API: { scan: () => new Promise(() => {}) } });
  vm.runInContext(read('js/state.js'), ctx);
  vm.runInContext(read('js/app.js'), ctx);
  vm.runInContext("D.industry_status = { status: 'failed', missing: 4 }; renderStats({ typeCnt: {} });", ctx);
  assert.match(el('sub').textContent, /行业获取失败/);
  vm.runInContext("D.industry_status = { status: 'partial', missing: 2 }; renderStats({ typeCnt: {} });", ctx);
  assert.match(el('sub').textContent, /2 只缺少行业分类/);
  vm.runInContext("D.industry_status = { status: 'ok', missing: 0 }; renderStats({ typeCnt: {} });", ctx);
  assert.doesNotMatch(el('sub').textContent, /行业获取失败|缺少行业分类/);
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

test('longer history preserves the current visible length and starting date', async () => {
  const data = payload(), e = await openZoom(data);
  e.pointer('pointerdown'); e.pointer('pointermove', { clientX: 550 }); e.flushFrames();
  e.pointer('pointerup', { clientX: 550 });
  const start = e.draw().d0;
  const done = e.el('zmMore').events.click();
  e.pending.shift().resolve(extendedHistory(data)); await done;
  assert.equal(e.draw().n, 120);
  assert.equal(e.draw().d0, start);
  assert.match(e.el('zmRange').textContent, /120 \/ 300/);
  assert.equal(e.el('zmMore').style.display, 'none');
});

test('longer history without the old starting date keeps the visible length at the latest bars', async () => {
  const e = await openZoom();
  const done = e.el('zmMore').events.click();
  const longer = payload('sh600000', 'day', 300);
  longer.chart.dates = Array.from({ length: 300 }, (_, i) => String(20300000 + i));
  longer.chart.pack.d0 = longer.chart.dates[0];
  e.pending.shift().resolve(longer); await done;
  assert.equal(e.draw().n, 120);
  assert.equal(e.draw().d0, longer.chart.dates[180]);
});

test('zoom opens at the latest 120 bars and shorter data stays within its available range', async () => {
  const data = payload(), e = await openZoom(data);
  assert.equal(e.draw().n, 120);
  assert.equal(e.draw().d0, data.chart.dates[30]);
  assert.match(e.el('zmRange').textContent, /120 \/ 150/);
  assert.equal(e.el('zmcv').style.cursor, 'grab');
  const short = payload('sh600000', 'day', 80), small = await openZoom(short);
  assert.equal(small.draw().n, 80);
  assert.equal(small.draw().d0, short.chart.dates[0]);
  assert.match(small.el('zmRange').textContent, /80 \/ 80/);
});

test('reset returns to the initial latest window so panning remains available', async () => {
  const data = payload(), e = await openZoom(data);
  e.el('zmPresets').events.click({ target: { closest: () => ({ dataset: { len: '0' } }) } });
  assert.equal(e.draw().n, 150);
  e.el('zmReset').events.click();
  assert.equal(e.draw().n, 120);
  assert.equal(e.draw().d0, data.chart.dates[30]);
  e.pointer('pointerdown'); e.pointer('pointermove', { clientX: 550 }); e.flushFrames();
  assert.equal(e.draw().d0, data.chart.dates[25]);
});

test('dragging repaints successive animation frames and coalesces pointer movements within one frame', async () => {
  const e = await openZoom(), cv = e.el('zmcv');
  const start = e.draw().d0;
  e.pointer('pointerdown');
  assert.equal(cv.hasPointerCapture(1), true);
  assert.equal(cv.style.cursor, 'grabbing');
  e.pointer('pointermove', { clientX: 520 });
  e.pointer('pointermove', { clientX: 550 });
  assert.equal(e.pendingFrames(), 1);
  assert.equal(e.draw().d0, start);
  e.flushFrames();
  const first = e.draw().d0;
  assert.equal(first, String(Number(start) - 5));
  e.pointer('pointermove', { clientX: 600 });
  assert.equal(e.pendingFrames(), 1);
  e.flushFrames();
  assert.equal(e.draw().d0, String(Number(start) - 10));
  assert.notEqual(e.draw().d0, first);
  e.pointer('pointerup', { clientX: 600 });
  assert.equal(cv.style.cursor, 'grab');
});

test('closing and reopening zoom clears old touch pointers and cancels pending frames', async () => {
  const e = await openZoom();
  e.pointer('pointerdown', { pointerType: 'touch', pointerId: 11 });
  e.pointer('pointermove', { pointerType: 'touch', pointerId: 11, clientX: 550 });
  assert.equal(e.pendingFrames(), 1);
  e.Zoom.close();
  assert.equal(e.pendingFrames(), 0);
  e.Zoom.open();
  const start = e.draw().d0;
  e.pointer('pointerdown', { pointerType: 'touch', pointerId: 12 });
  e.pointer('pointermove', { pointerType: 'touch', pointerId: 12, clientX: 550 });
  e.flushFrames();
  assert.equal(e.draw().n, 120);
  assert.equal(e.draw().d0, String(Number(start) - 5));
});

test('lost pointer capture ends the old drag so the next drag continues normally', async () => {
  const e = await openZoom(), cv = e.el('zmcv');
  e.pointer('pointerdown'); e.pointer('pointermove', { clientX: 550 }); e.flushFrames();
  const first = e.draw().d0;
  cv.releasePointerCapture(1);
  e.pointer('lostpointercapture', { buttons: 0 });
  assert.equal(cv.style.cursor, 'grab');
  e.pointer('pointermove', { clientX: 700, buttons: 0 });
  assert.equal(e.pendingFrames(), 0);
  e.pointer('pointerdown'); e.pointer('pointermove', { clientX: 550 }); e.flushFrames();
  assert.equal(e.draw().d0, String(Number(first) - 5));
});

test('one remaining finger can pan immediately after a two-finger pinch ends', async () => {
  const e = await openZoom();
  e.pointer('pointerdown', { pointerType: 'touch', pointerId: 10, clientX: 500 });
  e.pointer('pointerdown', { pointerType: 'touch', pointerId: 20, clientX: 600 });
  e.pointer('pointermove', { pointerType: 'touch', pointerId: 20, clientX: 620 }); e.flushFrames();
  const pinchedStart = e.draw().d0, length = e.draw().n;
  assert.equal(length, 100);
  e.pointer('pointerup', { pointerType: 'touch', pointerId: 20, clientX: 620 });
  e.pointer('pointermove', { pointerType: 'touch', pointerId: 10, clientX: 550 }); e.flushFrames();
  assert.equal(e.draw().n, length);
  assert.equal(e.draw().d0, String(Number(pinchedStart) - 5));
});

test('right-button presses do not begin chart dragging', async () => {
  const e = await openZoom(), start = e.draw().d0;
  e.pointer('pointerdown', { button: 2, buttons: 2 });
  e.pointer('pointermove', { clientX: 550, buttons: 2 });
  assert.equal(e.pendingFrames(), 0);
  assert.equal(e.draw().d0, start);
  assert.equal(e.el('zmcv').hasPointerCapture(1), false);
});

test('all-bars mode cannot move outside its data and a shorter window clamps at both ends', async () => {
  const data = payload(), e = await openZoom(data);
  const preset = len => e.el('zmPresets').events.click({
    target: { closest: () => ({ dataset: { len: String(len) } }) },
  });
  preset(0);
  e.pointer('pointerdown');
  e.pointer('pointermove', { clientX: 1500 }); e.flushFrames();
  assert.equal(e.draw().n, 150);
  assert.equal(e.draw().d0, data.chart.dates[0]);
  e.pointer('pointermove', { clientX: -500 }); e.flushFrames();
  assert.equal(e.draw().d0, data.chart.dates[0]);
  e.pointer('pointerup');
  preset(60);
  e.pointer('pointerdown');
  e.pointer('pointermove', { clientX: 1600 }); e.flushFrames();
  assert.equal(e.draw().d0, data.chart.dates[0]);
  e.pointer('pointermove', { clientX: -500 }); e.flushFrames();
  assert.equal(e.draw().n, 60);
  assert.equal(e.draw().d0, data.chart.dates[90]);
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

function scanFixture(id, price = 10) {
  return { scan_id: id, generated: '2026-10-09 15:00:00', hit: 1, total_scanned: 1, freq: 'day',
    rows: [{ code: '600000', symbol: 'sh600000', name: id, industry: '金融', close: price, amt: 2,
      buy: [{ type: '一类买点', date: '20261009', fresh: 0, valid: 1, stop: 9 }], sell: [] }] };
}
function appEnvironment() {
  const elements = new Map(), scans = [], tracking = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '', style: {}, value: '',
      events: {}, classList: { toggle() {} }, addEventListener(name, fn) { this.events[name] = fn; } });
    return elements.get(id);
  };
  const ctx = vm.createContext({ console, AbortController, URLSearchParams, location: { search: '' },
    setTimeout: () => 0, clearTimeout() {},
    document: { getElementById: el, querySelectorAll: () => [] },
    Drawer: { openCode: null, close() {} },
    API: {
      scan: (id, signal) => new Promise(resolve => scans.push({ id, signal, resolve })),
      scans: async () => ({ snapshots: [{ id: 'A', date: '2026-10-09', generated: '2026-10-09 15:00:00', hit: 1, freq: 'day' }] }),
      scanStatus: async () => ({ running: false }),
      tracking: (id, codes, force, signal) => new Promise(resolve => tracking.push({ id, codes, force, signal, resolve })),
    },
  });
  vm.runInContext(read('js/state.js'), ctx);
  vm.runInContext(read('js/app.js'), ctx);
  return { ctx, el, scans, tracking, run: code => vm.runInContext(code, ctx) };
}

test('switching historical scans rejects a late earlier batch', async () => {
  const e = appEnvironment();
  const a = e.run("loadScan('A')"), pendingA = e.scans.at(-1);
  const b = e.run("loadScan('B')"), pendingB = e.scans.at(-1);
  assert.equal(pendingA.signal.aborted, true);
  pendingB.resolve(scanFixture('B', 20)); await b;
  pendingA.resolve(scanFixture('A')); await a;
  assert.equal(e.run('D.scan_id'), 'B');
  assert.match(e.el('tb').innerHTML, />20\.00</);
});

test('late tracking quotes cannot overwrite a newly selected scan baseline', async () => {
  const e = appEnvironment();
  e.scans[0].resolve(scanFixture('A')); await tick();
  const a = e.run('updateTracking()'), pendingA = e.tracking.at(-1);
  const loadB = e.run("loadScan('B')");
  e.scans.at(-1).resolve(scanFixture('B', 20)); await loadB;
  const b = e.run('updateTracking()'), pendingB = e.tracking.at(-1);
  const quote = { price: 18, date: '2026-10-12 15:00:00', change: -2, change_pct: -10,
    status: 'ok', buy_stop_breached: false, breached_types: [] };
  pendingB.resolve({ quotes: { sh600000: quote }, errors: {}, as_of: 'now' }); await b;
  assert.equal(pendingA.signal.aborted, true);
  pendingA.resolve({ quotes: { sh600000: { ...quote, change: 8, change_pct: 80 } }, errors: {}, as_of: 'old' }); await a;
  assert.equal(e.run('quotes.sh600000.change_pct'), -10);
  assert.equal(e.run('D.rows[0].close'), 20);
  assert.equal(e.run('D.rows[0].buy[0].valid'), 1);
  assert.match(e.el('tb').innerHTML, /price-down">-10\.00%/);
});

test('completing a new scan preserves the historical selection and reset preserves its date', async () => {
  const e = appEnvironment();
  e.scans[0].resolve(scanFixture('A')); await tick();
  e.run("selectedDate='2026-10-09'; selectedSnapshot='A'; S.board='main'");
  const before = e.scans.length, boot = e.run('boot(true)'); await tick();
  e.tracking.at(-1).resolve({ quotes: {}, errors: {}, as_of: 'now' }); await boot;
  assert.equal(e.scans.length, before);
  assert.equal(e.run('D.scan_id'), 'A');
  e.el('reset').events.click();
  assert.equal(e.run('selectedDate'), '2026-10-09');
  assert.equal(e.run('S.board'), '');
});

test('price changes show direction, missing values and current stop breach separately', () => {
  const e = appEnvironment();
  assert.match(e.run('signedPrice(2.5, true)'), /price-up">\+2\.50%/);
  assert.match(e.run('signedPrice(-1)'), /price-down">-1\.00/);
  assert.equal(e.run('signedPrice(null, true)'), '—');
  assert.equal(e.run('signedPrice(NaN)'), '—');
  assert.match(e.run("trackingLabel({buy:[]}, {status:'ok',buy_stop_breached:true,breached_types:['一类买点']})"), /触及失效位/);
  assert.match(e.run("trackingLabel({buy:[]}, {status:'unavailable'})"), /行情未获取/);
});

test('daily changes and cumulative returns render and sort with independent directions', async () => {
  const e = appEnvironment(), data = scanFixture('A', 8);
  data.rows.push({ ...data.rows[0], code: '000001', symbol: 'sz000001', close: 20 });
  e.scans[0].resolve(data); await tick();
  const pending = e.run('updateTracking()');
  const a = { price: 10, previous_close: 11, date: '2026-10-12 15:00:00', daily_change: -1,
    daily_change_pct: -9.09, scan_change: 2, scan_change_pct: 25, change: 2, change_pct: 25,
    status: 'ok', buy_stop_breached: false, breached_types: [] };
  const b = { ...a, price: 16, previous_close: 15, daily_change: 1, daily_change_pct: 6.67,
    scan_change: -4, scan_change_pct: -20, change: -4, change_pct: -20 };
  e.tracking.at(-1).resolve({ quotes: { sh600000: a, sz000001: b }, errors: {}, as_of: 'now' }); await pending;
  const row = e.el('tb').innerHTML.split('data-symbol="sh600000"')[1].split('</tr>')[0];
  assert.match(row, /price-down">-1\.00/);
  assert.match(row, /daily-return-cell[^]*?price-down">-9\.09%/);
  assert.match(row, /scan-return-cell[^]*?price-up">\+25\.00%/);
  e.run("S.sort='dpct'; S.dir=-1");
  assert.equal(e.run('sortedList()[0].symbol'), 'sz000001');
  e.run("S.sort='pct'; S.dir=-1");
  assert.equal(e.run('sortedList()[0].symbol'), 'sh600000');
  e.run("S.sort='chg'; S.dir=-1");
  assert.equal(e.run('sortedList()[0].symbol'), 'sz000001');
});

test('drawer daily and scan returns stay separate when one baseline is unavailable', () => {
  const e = environment(), scanAt = '2026-10-09 15:00:00';
  e.Drawer.open({ code: '600000', symbol: 'sh600000', scan_at: scanAt, close: 8 });
  const q = { price: 10, previous_close: 11, daily_change: -1, daily_change_pct: -9.09,
    scan_change_pct: 25, date: '2026-10-12 15:00:00' };
  e.Drawer.updateTracking('sh600000', q, scanAt);
  assert.match(e.el('dwMeta').innerHTML, /当天涨跌值.*price-down">-1\.00/);
  assert.match(e.el('dwMeta').innerHTML, /当天涨跌幅.*price-down">-9\.09%/);
  assert.match(e.el('dwMeta').innerHTML, /扫描后至今涨跌幅.*price-up">\+25\.00%/);
  e.Drawer.updateTracking('sh600000', { ...q, previous_close: null, daily_change: null, daily_change_pct: null }, scanAt);
  assert.match(e.el('dwMeta').innerHTML, /当天涨跌幅 —/);
  assert.match(e.el('dwMeta').innerHTML, /扫描后至今涨跌幅.*\+25\.00%/);
  e.Drawer.updateTracking('sh600000', { ...q, scan_change_pct: null, change_pct: null }, scanAt);
  assert.match(e.el('dwMeta').innerHTML, /当天涨跌幅.*-9\.09%/);
  assert.match(e.el('dwMeta').innerHTML, /扫描后至今涨跌幅 —/);
});

test('all shipped classic scripts parse', () => {
  for (const dir of ['js', 'vendor']) for (const name of fs.readdirSync(path.join(root, 'web', dir))) {
    if (name.endsWith('.js')) new vm.Script(read(dir + '/' + name), { filename: name });
  }
});
